"""The tracker's data.

Every company, document and application belongs to one account (`owner`), and
events belong to their application. Views only ever query the signed-in
account's rows, so another account's objects answer 404.
"""

import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Coalesce, Lower
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format


def normalize_company_name(name):
    return " ".join(name.split())


class Company(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="companies"
    )
    name = models.CharField(max_length=200)
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [Lower("name")]
        verbose_name_plural = "companies"
        constraints = [
            models.UniqueConstraint(
                models.F("owner"),
                Lower("name"),
                name="unique_company_name_per_owner_ci",
                violation_error_message="A company with this name already exists.",
            ),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("company-detail", args=[self.pk])

    def clean(self):
        super().clean()
        normalized = normalize_company_name(self.name)
        if not normalized:
            if self.name:
                raise ValidationError(
                    {"name": self._meta.get_field("name").error_messages["blank"]}
                )
            return
        self.name = normalized


def document_upload_to(instance, filename):
    # Store under a random name so file paths never leak the original name and
    # never collide; the original name is kept on the model for downloads.
    suffix = Path(filename).suffix.lower()
    return f"documents/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{suffix}"


class Document(models.Model):
    """An exact file sent to an employer.

    Documents are immutable: a new version of a CV is a new Document, so every
    application keeps pointing at the file that was actually sent.
    """

    class Kind(models.TextChoices):
        CV = "cv", "CV"
        COVER_LETTER = "cover_letter", "Cover letter"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    label = models.CharField(
        max_length=200,
        help_text="A name you will recognise later, e.g. 'CV - backend focus, Oct 2026'.",
    )
    file = models.FileField(upload_to=document_upload_to)
    original_filename = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.label

    def get_absolute_url(self):
        return reverse("document-download", args=[self.pk])

    @property
    def extension(self):
        return Path(self.original_filename).suffix.lower().lstrip(".")

    def applications(self):
        return Application.objects.filter(
            models.Q(cv=self) | models.Q(cover_letter=self)
        ).select_related("company")


@receiver(models.signals.post_delete, sender=Document)
def delete_document_file(sender, instance, **kwargs):
    """Remove the stored file once a deletion commits, also when its account is deleted.

    A rolled-back deletion keeps the file.
    """
    if name := instance.file.name:
        storage = instance.file.storage
        transaction.on_commit(lambda: storage.delete(name))


class Status(models.TextChoices):
    WISHLIST = "wishlist", "Wishlist"
    APPLIED = "applied", "Applied"
    INTERVIEWING = "interviewing", "Interviewing"
    OFFER = "offer", "Offer"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"
    NO_RESPONSE = "no_response", "No response"


# Statuses where the process is over; these never need a follow-up.
CLOSED_STATUSES = {Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE}
# Statuses where silence from the employer is worth noticing.
WAITING_STATUSES = {Status.APPLIED, Status.INTERVIEWING, Status.OFFER}


def fill_blank_applied_date(status, date_applied):
    if status and status != Status.WISHLIST and not date_applied:
        return timezone.localdate()
    return date_applied


class WorkMode(models.TextChoices):
    ONSITE = "onsite", "On-site"
    HYBRID = "hybrid", "Hybrid"
    REMOTE = "remote", "Remote"


class ApplicationQuerySet(models.QuerySet):
    def with_last_activity(self):
        return self.annotate(
            last_activity_at=Coalesce(models.Max("events__created_at"), "created_at")
        )

    def open(self):
        return self.exclude(status__in=CLOSED_STATUSES)

    def follow_up_due(self, today=None):
        today = today or timezone.localdate()
        return self.open().filter(next_follow_up__lte=today)

    def gone_quiet(self, now=None):
        """Waiting on the employer with no logged activity for STALE_AFTER_DAYS."""
        now = now or timezone.now()
        cutoff = now - timedelta(days=settings.STALE_AFTER_DAYS)
        return (
            self.with_last_activity()
            .filter(status__in=WAITING_STATUSES, last_activity_at__lt=cutoff)
            .exclude(next_follow_up__gt=timezone.localdate(now))
        )


class Application(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="applications"
    )
    # RESTRICT rather than PROTECT: a company or document an application uses
    # cannot be deleted on its own, but deleting the account deletes all three.
    company = models.ForeignKey(Company, on_delete=models.RESTRICT, related_name="applications")
    role_title = models.CharField("role", max_length=200)
    posting_url = models.URLField("job posting URL", max_length=1000, blank=True)
    location = models.CharField(max_length=200, blank=True)
    work_mode = models.CharField(max_length=10, choices=WorkMode.choices, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    date_applied = models.DateField(null=True, blank=True)
    salary = models.CharField(
        max_length=200, blank=True, help_text="Free text, e.g. '650-700k DKK + pension'."
    )
    contact_name = models.CharField("contact person", max_length=200, blank=True)
    contact_email = models.EmailField("contact email", blank=True)
    source = models.CharField(
        max_length=100, blank=True, help_text="Where you found it, e.g. LinkedIn or a referral."
    )
    next_follow_up = models.DateField("next follow-up", null=True, blank=True)
    cv = models.ForeignKey(
        Document,
        verbose_name="CV sent",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="cv_applications",
        limit_choices_to={"kind": Document.Kind.CV},
    )
    cover_letter = models.ForeignKey(
        Document,
        verbose_name="cover letter sent",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="cover_letter_applications",
        limit_choices_to={"kind": Document.Kind.COVER_LETTER},
    )
    notes = models.TextField(blank=True)
    posting_text = models.TextField(
        "copy of the job ad",
        blank=True,
        help_text="The ad text, kept so you can still read it after the posting is taken down.",
    )
    posting_text_saved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ApplicationQuerySet.as_manager()

    class Meta:
        ordering = [models.F("date_applied").desc(nulls_last=True), "-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["next_follow_up"]),
        ]

    def __str__(self):
        return f"{self.role_title} at {self.company}"

    def save(self, *args, **kwargs):
        if self.is_closed:
            self.next_follow_up = None
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "next_follow_up"}
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("application-detail", args=[self.pk])

    @property
    def is_closed(self):
        return self.status in CLOSED_STATUSES

    @property
    def follow_up_overdue(self):
        return (
            not self.is_closed
            and self.next_follow_up is not None
            and self.next_follow_up <= timezone.localdate()
        )

    @transaction.atomic
    def change_status(self, new_status, note=""):
        """Move the application to a new status and record it in the history."""
        self.refresh_from_db(from_queryset=type(self).objects.select_for_update())
        old_status = self.status
        if new_status == old_status:
            return None
        self.status = new_status
        update_fields = ["status", "updated_at"]
        date_applied = fill_blank_applied_date(new_status, self.date_applied)
        if date_applied != self.date_applied:
            self.date_applied = date_applied
            update_fields.append("date_applied")
        self.save(update_fields=update_fields)
        return self.events.create(
            kind=ApplicationEvent.Kind.STATUS,
            from_status=old_status,
            to_status=new_status,
            message=note,
        )

    @transaction.atomic
    def complete_follow_up(self):
        """Record that you followed up, and clear the follow-up date."""
        self.refresh_from_db(from_queryset=type(self).objects.select_for_update())
        if self.is_closed:
            raise ValidationError("Closed applications have no follow-ups.")
        message = "Followed up."
        if self.next_follow_up:
            message = f"Followed up (was due {date_format(self.next_follow_up)})."
            self.next_follow_up = None
        self.save(update_fields=["next_follow_up", "updated_at"])
        return self.events.create(kind=ApplicationEvent.Kind.FOLLOW_UP, message=message)

    @transaction.atomic
    def snooze_follow_up(self, days):
        """Move the follow-up to a later date."""
        self.refresh_from_db(from_queryset=type(self).objects.select_for_update())
        if self.is_closed:
            raise ValidationError("Closed applications have no follow-ups.")
        if days not in (1, 3, 7, 14):
            raise ValidationError("Pick how long to snooze for.")
        today = timezone.localdate()
        until = max(self.next_follow_up or today, today) + timedelta(days=days)
        self.next_follow_up = until
        self.save(update_fields=["next_follow_up", "updated_at"])
        return self.events.create(
            kind=ApplicationEvent.Kind.FOLLOW_UP,
            message=f"Follow-up snoozed until {date_format(until)}.",
        )


class ApplicationEvent(models.Model):
    """Append-only activity log for an application."""

    class Kind(models.TextChoices):
        CREATED = "created", "Created"
        STATUS = "status", "Status changed"
        EDITED = "edited", "Edited"
        NOTE = "note", "Note"
        FOLLOW_UP = "follow_up", "Follow-up"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    from_status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_kind_display()} - {self.application}"
