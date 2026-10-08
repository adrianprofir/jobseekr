from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from tracker.models import Application, ApplicationEvent, Company, Document, Status

from .conftest import make_docx, make_pdf

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "name,args",
    [
        ("application-list", []),
        ("application-create", []),
        ("application-detail", [1]),
        ("application-edit", [1]),
        ("company-list", []),
        ("company-detail", [1]),
        ("document-list", []),
        ("document-upload", []),
    ],
)
def test_pages_require_login(client, name, args):
    response = client.get(reverse(name, args=args))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_healthcheck_is_public(client):
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.content == b"ok"


def test_login_flow(client, user):
    response = client.post(
        reverse("login"), {"username": "me", "password": "correct horse 42"}, follow=True
    )
    assert response.redirect_chain[-1][0] == reverse("application-list")
    assert "No applications yet" in response.content.decode()


def test_list_shows_applications(auth_client, application):
    page = auth_client.get(reverse("application-list")).content.decode()
    assert "Backend Engineer" in page
    assert "Acme" in page


def test_list_filters_by_status(auth_client, application, company):
    Application.objects.create(
        owner=company.owner, company=company, role_title="Rejected role", status=Status.REJECTED
    )

    page = auth_client.get(reverse("application-list"), {"status": "rejected"}).content.decode()
    assert "Rejected role" in page
    assert "Backend Engineer" not in page

    page = auth_client.get(reverse("application-list"), {"status": "open"}).content.decode()
    assert "Backend Engineer" in page
    assert "Rejected role" not in page


def test_list_search(auth_client, application):
    other = Company.objects.create(owner=application.owner, name="Globex")
    Application.objects.create(
        owner=other.owner, company=other, role_title="Designer", notes="Met at meetup"
    )

    page = auth_client.get(reverse("application-list"), {"q": "globex"}).content.decode()
    assert "Designer" in page
    assert "Backend Engineer" not in page

    page = auth_client.get(reverse("application-list"), {"q": "meetup"}).content.decode()
    assert "Designer" in page


@pytest.mark.parametrize("sort", ["-date_applied", "company", "status", "next_follow_up", "bogus"])
def test_list_sorting(auth_client, application, sort):
    response = auth_client.get(reverse("application-list"), {"sort": sort})
    assert response.status_code == 200


def test_list_sorts_by_company(auth_client, company):
    zed = Company.objects.create(owner=company.owner, name="Zed")
    Application.objects.create(owner=zed.owner, company=zed, role_title="Role at Zed")
    Application.objects.create(owner=company.owner, company=company, role_title="Role at Acme")
    page = auth_client.get(reverse("application-list"), {"sort": "company"}).content.decode()
    assert page.index("Role at Acme") < page.index("Role at Zed")


def test_list_shows_follow_up_reminders(auth_client, company, settings):
    settings.STALE_AFTER_DAYS = 14
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Overdue role",
        next_follow_up=timezone.localdate() - timedelta(days=2),
    )
    quiet = Application.objects.create(
        owner=company.owner, company=company, role_title="Quiet role"
    )
    quiet.events.create(
        kind=ApplicationEvent.Kind.CREATED, created_at=timezone.now() - timedelta(days=30)
    )

    page = auth_client.get(reverse("application-list")).content.decode()
    assert "Needs attention (2)" in page
    assert "Follow-up overdue by 2 d" in page
    assert "No activity for 30 days" in page


def test_create_application_with_new_company_and_documents(auth_client, private_media):
    response = auth_client.post(
        reverse("application-create"),
        {
            "posting_url": "https://jobs.example/123",
            "company_name": "  New   Corp ",
            "role_title": "Staff Engineer",
            "status": "applied",
            "date_applied": "",
            "new_cv": make_pdf("cv-2026.pdf"),
            "new_cv_label": "CV 2026",
            "new_cover_letter": make_docx("letter-newcorp.docx"),
        },
    )
    application = Application.objects.get()
    assert response.status_code == 302
    assert response["Location"] == application.get_absolute_url()
    assert application.company.name == "New Corp"
    assert application.date_applied == timezone.localdate()
    assert application.cv.label == "CV 2026"
    assert application.cv.kind == Document.Kind.CV
    assert application.cover_letter.label == "letter-newcorp"
    assert application.cover_letter.kind == Document.Kind.COVER_LETTER
    assert (private_media / application.cv.file.name).exists()
    event = application.events.get()
    assert event.kind == ApplicationEvent.Kind.CREATED


def test_create_reuses_a_company_lost_in_a_concurrent_insert(auth_client, user):
    Company.objects.create(owner=user, name="Race Corp")
    real_filter = Company.objects.filter
    missed = False

    def miss_the_precheck(*args, **kwargs):
        nonlocal missed
        qs = real_filter(*args, **kwargs)
        if not missed and kwargs.get("name__iexact") == "race corp":
            missed = True
            return qs.none()
        return qs

    with patch.object(Company.objects, "filter", side_effect=miss_the_precheck):
        response = auth_client.post(
            reverse("application-create"),
            {"company_name": "race corp", "role_title": "Engineer", "status": "applied"},
        )

    assert response.status_code == 302
    assert Company.objects.count() == 1
    application = Application.objects.get()
    assert application.company.name == "Race Corp"
    assert response["Location"] == application.get_absolute_url()


def test_create_reuses_existing_company_and_cv(auth_client, company, cv):
    auth_client.post(
        reverse("application-create"),
        {"company_name": "acme", "role_title": "SRE", "status": "applied", "cv": cv.pk},
    )
    application = Application.objects.get()
    assert application.company == company
    assert application.cv == cv
    assert Company.objects.count() == 1
    assert Document.objects.count() == 1


def test_wishlist_application_has_no_applied_date(auth_client):
    auth_client.post(
        reverse("application-create"),
        {"company_name": "Later Inc", "role_title": "Dream job", "status": "wishlist"},
    )
    assert Application.objects.get().date_applied is None


def test_create_prefills_url_from_quick_add(auth_client):
    page = auth_client.get(
        reverse("application-create"), {"url": "https://jobs.example/42"}
    ).content.decode()
    assert 'value="https://jobs.example/42"' in page


def test_create_rejects_cover_letter_as_cv(auth_client, user):
    upload = make_docx()
    letter = Document.objects.create(
        owner=user,
        kind=Document.Kind.COVER_LETTER,
        label="Letter",
        file=upload,
        original_filename=upload.name,
        size=upload.size,
    )
    response = auth_client.post(
        reverse("application-create"),
        {"company_name": "X", "role_title": "Y", "status": "applied", "cv": letter.pk},
    )
    assert response.status_code == 200
    assert not Application.objects.exists()


def test_create_shows_errors(auth_client):
    response = auth_client.post(reverse("application-create"), {"status": "applied"})
    assert response.status_code == 200
    assert "This field is required." in response.content.decode()


def test_create_rejects_a_blank_company_name(auth_client):
    response = auth_client.post(
        reverse("application-create"),
        {"company_name": "   ", "role_title": "Engineer", "status": "applied"},
    )
    assert response.status_code == 200
    assert "This field is required." in response.content.decode()
    assert not Company.objects.exists()
    assert not Application.objects.exists()


def test_detail_page(auth_client, application):
    application.events.create(kind=ApplicationEvent.Kind.NOTE, message="Recruiter called")
    page = auth_client.get(application.get_absolute_url()).content.decode()
    assert "Backend Engineer" in page
    assert "CV backend" in page
    assert reverse("document-download", args=[application.cv_id]) in page
    assert "Recruiter called" in page


def test_edit_logs_status_change_and_edited_fields(auth_client, application):
    response = auth_client.post(
        reverse("application-edit", args=[application.pk]),
        {
            "company_name": "Acme",
            "role_title": "Senior Backend Engineer",
            "posting_url": application.posting_url,
            "status": "interviewing",
            "date_applied": application.date_applied or "",
            "cv": application.cv_id,
            "location": "Copenhagen",
        },
    )
    assert response.status_code == 302
    kinds = {event.kind: event for event in application.events.all()}
    assert kinds[ApplicationEvent.Kind.STATUS].to_status == Status.INTERVIEWING
    message = kinds[ApplicationEvent.Kind.EDITED].message
    assert "role" in message
    assert "location" in message
    assert "status" not in message


def test_edit_keeps_previously_sent_cv_when_new_version_is_uploaded(
    auth_client, application, cv, company
):
    other = Application.objects.create(
        owner=company.owner, company=company, role_title="Other", cv=cv
    )
    auth_client.post(
        reverse("application-edit", args=[other.pk]),
        {
            "company_name": "Acme",
            "role_title": "Other",
            "status": "applied",
            "cv": cv.pk,
            "new_cv": make_pdf("cv-v2.pdf"),
        },
    )
    other.refresh_from_db()
    application.refresh_from_db()
    assert other.cv != cv
    assert application.cv == cv
    assert Document.objects.filter(kind=Document.Kind.CV).count() == 2


def test_quick_status_change(auth_client, application):
    response = auth_client.post(
        reverse("application-status", args=[application.pk]),
        {"status": "offer", "note": "Verbal offer"},
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.status == Status.OFFER
    assert application.events.get().message == "Verbal offer"


@pytest.mark.parametrize(
    "status",
    [
        Status.APPLIED,
        Status.INTERVIEWING,
        Status.OFFER,
        Status.REJECTED,
        Status.WITHDRAWN,
        Status.NO_RESPONSE,
    ],
)
def test_status_button_stamps_a_blank_applied_date(auth_client, company, status):
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Dream job", status=Status.WISHLIST
    )
    response = auth_client.post(
        reverse("application-status", args=[application.pk]),
        {"status": status},
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.status == status
    assert application.date_applied == timezone.localdate()
    page = auth_client.get(application.get_absolute_url()).content.decode()
    assert date_format(timezone.localdate(), "j M Y") in page
    assert "Not yet" not in page


def test_status_button_keeps_an_existing_applied_date(auth_client, company):
    applied_on = timezone.localdate() - timedelta(days=10)
    application = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Dream job",
        status=Status.WISHLIST,
        date_applied=applied_on,
    )
    response = auth_client.post(
        reverse("application-status", args=[application.pk]),
        {"status": Status.INTERVIEWING},
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.date_applied == applied_on
    page = auth_client.get(application.get_absolute_url()).content.decode()
    assert date_format(applied_on, "j M Y") in page


def test_status_button_leaves_wishlist_undated(auth_client, company):
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Someday", status=Status.APPLIED
    )
    response = auth_client.post(
        reverse("application-status", args=[application.pk]),
        {"status": Status.WISHLIST},
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.status == Status.WISHLIST
    assert application.date_applied is None
    page = auth_client.get(application.get_absolute_url()).content.decode()
    assert "Not yet" in page


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_editing_to_a_closed_status_clears_follow_up(auth_client, application, status):
    follow_up = timezone.localdate()
    application.next_follow_up = follow_up
    application.save()
    response = auth_client.post(
        reverse("application-edit", args=[application.pk]),
        {
            "company_name": "Acme",
            "role_title": application.role_title,
            "status": status,
            "date_applied": follow_up.isoformat(),
            "next_follow_up": follow_up.isoformat(),
        },
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.status == status
    assert application.next_follow_up is None
    assert not application.events.filter(message__icontains="follow-up").exists()


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_creating_a_closed_application_drops_follow_up(auth_client, status):
    follow_up = timezone.localdate()
    response = auth_client.post(
        reverse("application-create"),
        {
            "company_name": "Nope",
            "role_title": "No",
            "status": status,
            "next_follow_up": follow_up.isoformat(),
        },
    )
    application = Application.objects.get()
    assert response.status_code == 302
    assert application.status == status
    assert application.next_follow_up is None


def test_edit_sets_follow_up_on_an_open_application(auth_client, application):
    follow_up = timezone.localdate() + timedelta(days=5)
    response = auth_client.post(
        reverse("application-edit", args=[application.pk]),
        {
            "company_name": "Acme",
            "role_title": application.role_title,
            "status": Status.INTERVIEWING,
            "next_follow_up": follow_up.isoformat(),
        },
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.next_follow_up == follow_up


def test_reopening_does_not_restore_a_cleared_follow_up(auth_client, application):
    follow_up = timezone.localdate() - timedelta(days=3)
    application.next_follow_up = follow_up
    application.save()
    edit_url = reverse("application-edit", args=[application.pk])
    auth_client.post(
        edit_url,
        {
            "company_name": "Acme",
            "role_title": application.role_title,
            "status": Status.REJECTED,
            "next_follow_up": follow_up.isoformat(),
        },
    )
    auth_client.post(
        edit_url,
        {
            "company_name": "Acme",
            "role_title": application.role_title,
            "status": Status.INTERVIEWING,
        },
    )
    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING
    assert application.next_follow_up is None
    page = auth_client.get(application.get_absolute_url()).content.decode()
    assert "(due)" not in page


def test_closed_application_sorts_after_open_follow_ups(auth_client, company):
    today = timezone.localdate()
    closing = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Closing",
        next_follow_up=today,
        date_applied=today,
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Still open",
        next_follow_up=today + timedelta(days=10),
        date_applied=today,
    )
    auth_client.post(
        reverse("application-edit", args=[closing.pk]),
        {
            "company_name": "Acme",
            "role_title": "Closing",
            "status": Status.REJECTED,
            "date_applied": today.isoformat(),
            "next_follow_up": today.isoformat(),
        },
    )
    page = auth_client.get(reverse("application-list"), {"sort": "next_follow_up"}).content.decode()
    assert page.index("Still open") < page.index("Closing")


def test_closed_detail_does_not_offer_a_follow_up_date(auth_client, company, application):
    closed = Application.objects.create(
        owner=company.owner, company=company, role_title="Closed role", status=Status.NO_RESPONSE
    )
    closed_page = auth_client.get(closed.get_absolute_url()).content.decode()
    open_page = auth_client.get(application.get_absolute_url()).content.decode()
    assert 'id="id_next_follow_up"' not in closed_page
    assert 'id="id_next_follow_up"' in open_page


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_note_on_a_closed_application_does_not_set_follow_up(auth_client, company, status):
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Closed role", status=status
    )
    follow_up = timezone.localdate() + timedelta(days=3)
    response = auth_client.post(
        reverse("application-note", args=[application.pk]),
        {"message": "Heard back", "next_follow_up": follow_up.isoformat()},
    )
    assert response.status_code == 302
    application.refresh_from_db()
    assert application.next_follow_up is None
    event = application.events.get()
    assert event.kind == ApplicationEvent.Kind.NOTE
    assert event.message == "Heard back"


def test_add_note_and_follow_up(auth_client, application):
    follow_up = timezone.localdate() + timedelta(days=7)
    auth_client.post(
        reverse("application-note", args=[application.pk]),
        {"message": "Emailed the recruiter", "next_follow_up": follow_up.isoformat()},
    )
    application.refresh_from_db()
    assert application.next_follow_up == follow_up
    event = application.events.get()
    assert event.kind == ApplicationEvent.Kind.NOTE
    assert event.message.startswith("Emailed the recruiter")


def test_status_and_note_endpoints_reject_get(auth_client, application):
    assert auth_client.get(reverse("application-status", args=[application.pk])).status_code == 405
    assert auth_client.get(reverse("application-note", args=[application.pk])).status_code == 405


def test_delete_application_keeps_documents(auth_client, application, cv):
    response = auth_client.post(reverse("application-delete", args=[application.pk]))
    assert response.status_code == 302
    assert not Application.objects.exists()
    assert Document.objects.filter(pk=cv.pk).exists()


def test_company_edit_collapses_whitespace(auth_client, company):
    response = auth_client.post(
        reverse("company-edit", args=[company.pk]),
        {"name": "  Acme   Corp  ", "website": company.website, "notes": company.notes},
    )
    assert response.status_code == 302
    company.refresh_from_db()
    assert company.name == "Acme Corp"


def test_company_edit_rejects_a_blank_name(auth_client, company):
    response = auth_client.post(
        reverse("company-edit", args=[company.pk]),
        {"name": "   ", "website": company.website, "notes": company.notes},
    )
    assert response.status_code == 200
    assert "This field is required." in response.content.decode()
    company.refresh_from_db()
    assert company.name == "Acme"


def test_company_pages(auth_client, application, company):
    page = auth_client.get(reverse("company-list")).content.decode()
    assert "Acme" in page
    page = auth_client.get(company.get_absolute_url()).content.decode()
    assert "Backend Engineer" in page

    response = auth_client.post(
        reverse("company-edit", args=[company.pk]),
        {"name": "Acme Corp", "website": "https://acme.example", "notes": "Nice office"},
    )
    assert response.status_code == 302
    company.refresh_from_db()
    assert company.name == "Acme Corp"


def test_company_with_applications_cannot_be_deleted(auth_client, application, company):
    auth_client.post(reverse("company-delete", args=[company.pk]))
    assert Company.objects.filter(pk=company.pk).exists()


def test_company_without_applications_can_be_deleted(auth_client, company):
    response = auth_client.post(reverse("company-delete", args=[company.pk]))
    assert response.status_code == 302
    assert not Company.objects.exists()


def test_company_list_is_alphabetical(auth_client, user):
    for name in ["Zed", "acme", "Mid"]:
        Company.objects.create(owner=user, name=name)
    page = auth_client.get(reverse("company-list")).content.decode()
    assert page.index("acme") < page.index("Mid") < page.index("Zed")


def test_not_found_page_uses_site_layout(auth_client):
    response = auth_client.get(reverse("application-detail", args=[999999]))
    assert response.status_code == 404
    assert "This page does not exist" in response.content.decode()


def test_company_reference_added_after_unused_check_blocks_deletion(
    auth_client, company, monkeypatch
):
    original_delete = Company.delete

    def delete_with_new_reference(instance, *args, **kwargs):
        Application.objects.create(owner=instance.owner, company=instance, role_title="Engineer")
        return original_delete(instance, *args, **kwargs)

    monkeypatch.setattr(Company, "delete", delete_with_new_reference)
    response = auth_client.post(reverse("company-delete", args=[company.pk]), follow=True)
    assert response.status_code == 200
    assert any(
        str(message) == "Delete or move this company's applications first."
        for message in response.context["messages"]
    )
    assert Company.objects.filter(pk=company.pk).exists()
    assert Application.objects.filter(company=company).exists()
