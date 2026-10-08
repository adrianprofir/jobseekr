import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.db.models.deletion import RestrictedError
from django.db.models.functions import Lower
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .capture import board_name, capture_posting
from .capture.samples import find_sample
from .forms import (
    ApplicationForm,
    CompanyForm,
    DocumentForm,
    NoteForm,
    SnoozeForm,
    StatusForm,
)
from .models import CLOSED_STATUSES, Application, ApplicationEvent, Company, Document, Status
from .reminders import due_reminders, upcoming_reminders
from .summary import build_weekly_summary, parse_week, week_start

logger = logging.getLogger(__name__)

STATUS_ORDER = Case(
    *[When(status=value, then=Value(i)) for i, value in enumerate(Status.values)],
    output_field=IntegerField(),
)

SORT_OPTIONS = {
    "-date_applied": ("Newest applied", [F("date_applied").desc(nulls_last=True), "-created_at"]),
    "date_applied": ("Oldest applied", [F("date_applied").asc(nulls_last=True), "created_at"]),
    "-updated_at": ("Recently updated", ["-updated_at"]),
    "next_follow_up": (
        "Follow-up soonest",
        [F("next_follow_up").asc(nulls_last=True), F("date_applied").desc(nulls_last=True)],
    ),
    "company": ("Company A-Z", ["company__name", "role_title"]),
    "status": ("Pipeline stage", ["status_order", F("date_applied").desc(nulls_last=True)]),
}
DEFAULT_SORT = "-date_applied"


@login_not_required
@require_GET
def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


def application_list(request):
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", DEFAULT_SORT)
    if sort not in SORT_OPTIONS:
        sort = DEFAULT_SORT

    owned = Application.objects.filter(owner=request.user)
    applications = owned.select_related("company", "cv", "cover_letter").annotate(
        status_order=STATUS_ORDER
    )
    if query:
        applications = applications.filter(
            Q(company__name__icontains=query)
            | Q(role_title__icontains=query)
            | Q(location__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(source__icontains=query)
            | Q(notes__icontains=query)
        )
    status_counts = dict(
        applications.order_by()
        .values_list("status")
        .annotate(n=Count("pk"))
        .values_list("status", "n")
    )
    if status == "open":
        applications = applications.open()
    elif status in Status.values:
        applications = applications.filter(status=status)
    else:
        status = ""
    applications = applications.order_by(*SORT_OPTIONS[sort][1])

    total = sum(status_counts.values())
    closed = sum(status_counts.get(s, 0) for s in CLOSED_STATUSES)
    status_filters = [
        ("", "All", total),
        ("open", "Open", total - closed),
        *[(value, label, status_counts.get(value, 0)) for value, label in Status.choices],
    ]
    return render(
        request,
        "tracker/application_list.html",
        {
            "applications": applications,
            "attention": due_reminders(request.user),
            "status": status,
            "status_filters": status_filters,
            "query": query,
            "sort": sort,
            "sort_options": [(key, label) for key, (label, _) in SORT_OPTIONS.items()],
            "has_any": owned.exists(),
            "stale_after_days": settings.STALE_AFTER_DAYS,
            "snooze_form": SnoozeForm(),
        },
    )


def can_fetch(url):
    """Whether capture can read this link: any link when live, only the samples otherwise."""
    if settings.CAPTURE_MODE == "samples":
        return find_sample(url) is not None
    return bool(url)


def application_detail(request, pk):
    application = get_object_or_404(
        Application.objects.filter(owner=request.user).select_related(
            "company", "cv", "cover_letter"
        ),
        pk=pk,
    )
    return render(
        request,
        "tracker/application_detail.html",
        {
            "application": application,
            "can_fetch_posting": can_fetch(application.posting_url),
            "events": application.events.all(),
            "status_form": StatusForm(initial={"status": application.status}),
            "note_form": NoteForm(),
        },
    )


def _form_context(form, **extra):
    owner = form.owner
    return {
        "form": form,
        "companies": Company.objects.filter(owner=owner).values_list("name", flat=True),
        "sources": sorted(
            set(settings.SOURCE_SUGGESTIONS)
            | set(
                Application.objects.filter(owner=owner)
                .exclude(source="")
                .values_list("source", flat=True)
                .distinct()
            )
        ),
        **extra,
    }


def _changed_labels(form):
    """Human-readable names of the fields an edit changed, for the history."""
    ignore = {"new_cv_label", "new_cover_letter_label", "status"}
    if form.cleaned_data.get("status") in CLOSED_STATUSES:
        ignore.add("next_follow_up")
    aliases = {"new_cv": "cv", "new_cover_letter": "cover_letter"}
    labels = []
    for name in form.changed_data:
        if name in ignore:
            continue
        name = aliases.get(name, name)
        label = str(form.fields[name].label)
        label = label[0].lower() + label[1:]
        if label not in labels:
            labels.append(label)
    return labels


# Form fields a captured posting can fill, with the names used in messages.
CAPTURED_FIELDS = {
    "role_title": "role",
    "company_name": "company",
    "location": "location",
    "work_mode": "work mode",
    "salary": "salary",
    "source": "source",
    "posting_text": "a copy of the ad",
}
FILE_FIELDS = {"new_cv", "new_cover_letter"}


@dataclass
class Capture:
    """What fetching a posting found, for the notice above the form."""

    url: str
    filled: list = field(default_factory=list)
    error: str = ""
    duplicates: list = field(default_factory=list)
    files_dropped: bool = False


def _captured_values(owner, posting):
    company = posting.company
    companies = Company.objects.filter(owner=owner)
    if company and (existing := companies.filter(name__iexact=company).first()):
        company = existing.name
    return {
        "role_title": posting.role_title,
        "company_name": company,
        "location": posting.location,
        "work_mode": posting.work_mode,
        "salary": posting.salary,
        "source": posting.source,
        "posting_text": posting.description,
    }


def _duplicates(owner, url, exclude_pk=None):
    base = url.rstrip("/")
    return list(
        Application.objects.filter(owner=owner, posting_url__in={url, base, base + "/"})
        .exclude(pk=exclude_pk)
        .select_related("company")
    )


def _capture_form(owner, url, initial=None, instance=None):
    """An unsaved form with the blank fields filled in from the posting at `url`.

    Nothing is saved: the user reviews the values and submits the form.
    """
    initial = dict(initial or {})
    try:
        url = forms.URLField(assume_scheme="https").clean(url)
    except ValidationError:
        capture = Capture(url=url, error="Enter a valid web address to fetch.")
        return ApplicationForm(initial=initial, instance=instance, owner=owner), capture

    result = capture_posting(url)
    capture = Capture(url=result.url, error=result.error)
    initial["posting_url"] = result.url
    current = ApplicationForm(initial=initial, instance=instance, owner=owner)
    if result.posting:
        for name, value in _captured_values(owner, result.posting).items():
            if value and not current[name].value():
                initial[name] = value
                capture.filled.append(CAPTURED_FIELDS[name])
    elif (source := board_name(result.url)) and not current["source"].value():
        # The board is known from the link even when it would not show the page.
        initial["source"] = source
    capture.duplicates = _duplicates(owner, result.url, exclude_pk=instance and instance.pk)
    return ApplicationForm(initial=initial, instance=instance, owner=owner), capture


def _capture_from_post(request, instance=None):
    """Handle the form's "Fetch details" button: keep what was typed, fill the gaps."""
    typed = {
        name: request.POST.get(name)
        for name in ApplicationForm.base_fields
        if name not in FILE_FIELDS and name in request.POST
    }
    form, capture = _capture_form(request.user, typed.get("posting_url", ""), typed, instance)
    capture.files_dropped = bool(request.FILES)
    return form, capture


@require_http_methods(["GET", "POST"])
def application_create(request):
    initial = {}
    if company_id := request.GET.get("company"):
        company = Company.objects.filter(owner=request.user, pk=company_id).first()
        if company:
            initial["company_name"] = company.name
    capture = None
    if request.method == "POST" and "fetch" in request.POST:
        form, capture = _capture_from_post(request)
    elif request.method == "POST":
        form = ApplicationForm(request.POST, request.FILES, initial=initial, owner=request.user)
        if form.is_valid():
            application = form.save()
            application.events.create(
                kind=ApplicationEvent.Kind.CREATED,
                to_status=application.status,
                message=f"Added as {application.get_status_display().lower()}.",
            )
            logger.info("Application %s created: %s", application.pk, application)
            messages.success(request, f"Saved {application}.")
            return redirect(application)
    elif url := request.GET.get("url", "").strip():
        form, capture = _capture_form(request.user, url, initial)
    else:
        form = ApplicationForm(initial=initial, owner=request.user)
    return render(request, "tracker/application_form.html", _form_context(form, capture=capture))


@require_http_methods(["GET", "POST"])
def application_edit(request, pk):
    if request.method == "POST" and "fetch" not in request.POST:
        return _save_application_edit(request, pk)
    # Fetching a posting can take seconds, so it runs without a transaction or row lock.
    application = get_object_or_404(Application.objects.filter(owner=request.user), pk=pk)
    capture = None
    if request.method == "POST":
        form, capture = _capture_from_post(request, instance=application)
    elif request.GET.get("fetch") and application.posting_url:
        form, capture = _capture_form(request.user, application.posting_url, instance=application)
    else:
        form = ApplicationForm(instance=application, owner=request.user)
    return render(
        request,
        "tracker/application_form.html",
        _form_context(form, application=application, capture=capture),
    )


@transaction.atomic
def _save_application_edit(request, pk):
    application = get_object_or_404(
        Application.objects.filter(owner=request.user).select_for_update(), pk=pk
    )
    old_status = application.status
    form = ApplicationForm(request.POST, request.FILES, instance=application, owner=request.user)
    if not form.is_valid():
        return render(
            request,
            "tracker/application_form.html",
            _form_context(form, application=application),
        )
    application = form.save()
    if application.status != old_status:
        application.events.create(
            kind=ApplicationEvent.Kind.STATUS,
            from_status=old_status,
            to_status=application.status,
        )
    if changed := _changed_labels(form):
        application.events.create(
            kind=ApplicationEvent.Kind.EDITED, message="Changed " + ", ".join(changed) + "."
        )
    logger.info("Application %s edited: %s", application.pk, form.changed_data)
    messages.success(request, "Changes saved.")
    return redirect(application)


@require_POST
@transaction.atomic
def application_status(request, pk):
    application = get_object_or_404(
        Application.objects.filter(owner=request.user).select_for_update(), pk=pk
    )
    form = StatusForm(request.POST)
    if form.is_valid():
        old_label = application.get_status_display()
        if application.change_status(form.cleaned_data["status"], form.cleaned_data["note"]):
            logger.info(
                "Application %s status %s -> %s", application.pk, old_label, application.status
            )
            messages.success(request, f"Status changed to {application.get_status_display()}.")
        else:
            messages.info(request, "Status unchanged.")
    else:
        messages.error(request, "Pick a valid status.")
    return redirect(application)


@require_POST
@transaction.atomic
def application_note(request, pk):
    application = get_object_or_404(
        Application.objects.filter(owner=request.user).select_for_update(), pk=pk
    )
    form = NoteForm(request.POST)
    if not form.is_valid():
        if request.POST.get("message", "").strip():
            messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
        else:
            messages.error(request, "Write a note before saving.")
        return redirect(application)
    message = form.cleaned_data["message"]
    follow_up = form.cleaned_data["next_follow_up"]
    if not application.is_closed and follow_up and follow_up != application.next_follow_up:
        application.next_follow_up = follow_up
        application.save(update_fields=["next_follow_up", "updated_at"])
        message += f"\nNext follow-up set to {date_format(follow_up)}."
    else:
        application.save(update_fields=["updated_at"])
    application.events.create(kind=ApplicationEvent.Kind.NOTE, message=message)
    messages.success(request, "Note added.")
    return redirect(application)


@require_http_methods(["GET", "POST"])
def application_delete(request, pk):
    application = get_object_or_404(
        Application.objects.filter(owner=request.user).select_related("company"), pk=pk
    )
    if request.method == "POST":
        label = str(application)
        application.delete()
        logger.info("Application %s deleted: %s", pk, label)
        messages.success(request, f"Deleted {label}. Its documents are still in the library.")
        return redirect("application-list")
    return render(request, "tracker/application_confirm_delete.html", {"application": application})


def follow_up_list(request):
    today = timezone.localdate()
    due = due_reminders(request.user, today)
    sections = [
        ("overdue", "Overdue", [r for r in due if r.kind == "overdue"]),
        ("today", "Due today", [r for r in due if r.kind == "today"]),
        ("quiet", "Gone quiet", [r for r in due if r.kind == "quiet"]),
        ("upcoming", "Coming up", upcoming_reminders(request.user, today)),
    ]
    return render(
        request,
        "tracker/follow_up_list.html",
        {
            "sections": sections,
            "has_any": any(reminders for _, _, reminders in sections),
            "stale_after_days": settings.STALE_AFTER_DAYS,
            "snooze_form": SnoozeForm(),
        },
    )


def _redirect_back(request, default):
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(default)


@require_POST
def follow_up_done(request, pk):
    application = get_object_or_404(Application.objects.filter(owner=request.user), pk=pk)
    try:
        application.complete_follow_up()
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        logger.info("Application %s follow-up done", application.pk)
        messages.success(request, f"Logged a follow-up on {application}.")
    return _redirect_back(request, "follow-up-list")


@require_POST
def follow_up_snooze(request, pk):
    application = get_object_or_404(Application.objects.filter(owner=request.user), pk=pk)
    form = SnoozeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick how long to snooze for.")
        return _redirect_back(request, "follow-up-list")
    try:
        application.snooze_follow_up(form.cleaned_data["days"])
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        until = application.next_follow_up
        logger.info("Application %s follow-up snoozed until %s", application.pk, until)
        messages.success(request, f"Snoozed {application} until {date_format(until)}.")
    return _redirect_back(request, "follow-up-list")


def weekly_summary(request):
    today = timezone.localdate()
    # Like the e-mail, default to the week containing yesterday: on a Monday
    # that is the week just finished rather than one that has barely started.
    default = week_start(today - timedelta(days=1))
    start = parse_week(request.GET.get("week", "")) or default
    if start > today:
        start = default
    return render(
        request,
        "tracker/summary.html",
        {
            "summary": build_weekly_summary(request.user, start, today),
            "email_configured": bool(settings.WEEKLY_SUMMARY_EMAIL_ENABLED and request.user.email),
            "snooze_form": SnoozeForm(),
        },
    )


def company_list(request):
    # Meta.ordering is not applied to aggregated queries, so order explicitly.
    companies = (
        Company.objects.filter(owner=request.user)
        .order_by(Lower("name"))
        .annotate(
            application_count=Count("applications"),
            open_count=Count(
                "applications",
                filter=Q(applications__status__in=set(Status.values) - CLOSED_STATUSES),
            ),
        )
    )
    return render(request, "tracker/company_list.html", {"companies": companies})


def company_detail(request, pk):
    company = get_object_or_404(Company.objects.filter(owner=request.user), pk=pk)
    return render(
        request,
        "tracker/company_detail.html",
        {"company": company, "applications": company.applications.all()},
    )


@require_http_methods(["GET", "POST"])
def company_edit(request, pk):
    company = get_object_or_404(Company.objects.filter(owner=request.user), pk=pk)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company saved.")
        return redirect(company)
    return render(request, "tracker/company_form.html", {"form": form, "company": company})


@require_http_methods(["GET", "POST"])
def company_delete(request, pk):
    company = get_object_or_404(Company.objects.filter(owner=request.user), pk=pk)
    if company.applications.exists():
        messages.error(request, "Delete or move this company's applications first.")
        return redirect(company)
    if request.method == "POST":
        try:
            company.delete()
        except RestrictedError:
            messages.error(request, "Delete or move this company's applications first.")
            return redirect(company)
        messages.success(request, f"Deleted {company.name}.")
        return redirect("company-list")
    return render(request, "tracker/company_confirm_delete.html", {"company": company})


def document_list(request):
    documents = Document.objects.filter(owner=request.user).prefetch_related(
        "cv_applications__company", "cover_letter_applications__company"
    )
    sections = {kind: [] for kind in Document.Kind}
    for document in documents:
        used_in = [*document.cv_applications.all(), *document.cover_letter_applications.all()]
        sections[document.kind].append((document, used_in))
    return render(
        request,
        "tracker/document_list.html",
        {
            "sections": [(kind.value, kind.label + "s", sections[kind]) for kind in Document.Kind],
        },
    )


@require_http_methods(["GET", "POST"])
def document_upload(request):
    if not settings.DOCUMENT_UPLOADS_ENABLED:
        # The page explains why; a POST from an old tab is refused the same way.
        status = 403 if request.method == "POST" else 200
        return render(request, "tracker/document_form.html", status=status)
    form = DocumentForm(
        request.POST or None,
        request.FILES or None,
        initial={"kind": request.GET.get("kind", Document.Kind.CV)},
    )
    if request.method == "POST" and form.is_valid():
        document = form.save(owner=request.user)
        logger.info("Document %s uploaded: %s", document.pk, document.original_filename)
        messages.success(request, f"Uploaded {document.label}.")
        return redirect("document-list")
    return render(request, "tracker/document_form.html", {"form": form})


@require_GET
def document_download(request, pk):
    document = get_object_or_404(Document.objects.filter(owner=request.user), pk=pk)
    try:
        handle = document.file.open("rb")
    except FileNotFoundError:
        logger.error("Document %s file missing from storage: %s", document.pk, document.file.name)
        raise Http404("The file for this document is missing from storage.") from None
    is_pdf = document.extension == "pdf"
    response = FileResponse(
        handle,
        as_attachment=not is_pdf or "download" in request.GET,
        filename=document.original_filename,
    )
    response["Cache-Control"] = "private, no-store"
    return response


@require_http_methods(["GET", "POST"])
def document_delete(request, pk):
    documents = Document.objects.filter(owner=request.user)
    document = get_object_or_404(documents, pk=pk)
    if document.applications().exists():
        messages.error(request, "This document was sent with an application and cannot be deleted.")
        return redirect("document-list")
    if request.method == "POST":
        try:
            with transaction.atomic():
                document = get_object_or_404(documents.select_for_update(), pk=pk)
                # The stored file goes after the commit (tracker.models.delete_document_file).
                document.delete()
        except RestrictedError:
            messages.error(
                request, "This document was sent with an application and cannot be deleted."
            )
            return redirect("document-list")
        logger.info("Document %s deleted: %s", pk, document.original_filename)
        messages.success(request, f"Deleted {document.label}.")
        return redirect("document-list")
    return render(request, "tracker/document_confirm_delete.html", {"document": document})
