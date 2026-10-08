from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from tracker.models import Application, ApplicationEvent, Company, Status

pytestmark = pytest.mark.django_db


def test_company_names_are_unique_case_insensitively(company):
    with pytest.raises(IntegrityError):
        Company.objects.create(owner=company.owner, name="ACME")


def test_company_names_are_unique_per_account(company, django_user_model):
    other = django_user_model.objects.create_user(username="other")
    assert Company.objects.create(owner=other, name="ACME").name == "ACME"


def test_company_name_collapses_whitespace(user):
    company = Company(owner=user, name="  Acme   Corp  ")
    company.full_clean()
    assert company.name == "Acme Corp"


def test_company_name_rejects_whitespace_only():
    company = Company(name="   ")
    with pytest.raises(ValidationError) as raised:
        company.full_clean()
    assert "name" in raised.value.error_dict


def test_change_status_records_history(application):
    event = application.change_status(Status.INTERVIEWING, "Phone screen booked")

    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING
    assert event.kind == ApplicationEvent.Kind.STATUS
    assert (event.from_status, event.to_status) == (Status.APPLIED, Status.INTERVIEWING)
    assert event.message == "Phone screen booked"


def test_change_status_to_same_status_is_a_noop(application):
    assert application.change_status(Status.APPLIED) is None
    assert not application.events.exists()


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
def test_change_status_fills_a_blank_applied_date(company, status):
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Data Engineer", status=Status.WISHLIST
    )
    application.change_status(status)
    application.refresh_from_db()
    assert application.status == status
    assert application.date_applied == timezone.localdate()


def test_change_status_keeps_an_existing_applied_date(company):
    applied_on = timezone.localdate() - timedelta(days=4)
    application = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Data Engineer",
        status=Status.WISHLIST,
        date_applied=applied_on,
    )
    application.change_status(Status.OFFER)
    application.refresh_from_db()
    assert application.date_applied == applied_on


def test_change_status_to_wishlist_leaves_the_applied_date_blank(company):
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Data Engineer", status=Status.APPLIED
    )
    application.change_status(Status.WISHLIST)
    application.refresh_from_db()
    assert application.status == Status.WISHLIST
    assert application.date_applied is None


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_closing_an_application_clears_its_follow_up(application, status):
    application.next_follow_up = timezone.localdate()
    application.save()
    application.change_status(status)
    application.refresh_from_db()
    assert application.next_follow_up is None


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_saving_a_closed_application_drops_its_follow_up(company, status):
    today = timezone.localdate()
    application = Application.objects.create(
        owner=company.owner, company=company, role_title="Done", status=status, next_follow_up=today
    )
    assert application.next_follow_up is None

    application.next_follow_up = today
    application.save(update_fields=["next_follow_up"])
    application.refresh_from_db()
    assert application.next_follow_up is None


def test_follow_up_due_includes_overdue_open_applications_only(company):
    today = timezone.localdate()
    due = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Due",
        next_follow_up=today - timedelta(days=1),
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Later",
        next_follow_up=today + timedelta(days=3),
    )
    closed = Application.objects.create(
        owner=company.owner, company=company, role_title="Closed", status=Status.REJECTED
    )
    Application.objects.filter(pk=closed.pk).update(next_follow_up=today - timedelta(days=1))
    assert list(Application.objects.follow_up_due()) == [due]


def test_gone_quiet_uses_latest_activity(settings, company):
    settings.STALE_AFTER_DAYS = 14
    old = timezone.now() - timedelta(days=20)
    quiet = Application.objects.create(owner=company.owner, company=company, role_title="Quiet")
    quiet.events.create(kind=ApplicationEvent.Kind.CREATED, created_at=old)
    active = Application.objects.create(owner=company.owner, company=company, role_title="Active")
    active.events.create(kind=ApplicationEvent.Kind.CREATED, created_at=old)
    active.events.create(kind=ApplicationEvent.Kind.NOTE, message="Chased recruiter")
    wishlist = Application.objects.create(
        owner=company.owner, company=company, role_title="Someday", status=Status.WISHLIST
    )
    wishlist.events.create(kind=ApplicationEvent.Kind.CREATED, created_at=old)

    assert list(Application.objects.gone_quiet()) == [quiet]


def test_gone_quiet_skips_applications_with_a_future_follow_up(settings, company):
    settings.STALE_AFTER_DAYS = 14
    application = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Scheduled",
        next_follow_up=timezone.localdate() + timedelta(days=2),
    )
    application.events.create(
        kind=ApplicationEvent.Kind.CREATED, created_at=timezone.now() - timedelta(days=30)
    )
    assert not Application.objects.gone_quiet().exists()


def test_document_knows_which_applications_it_was_sent_with(application, cv, company):
    other = Application.objects.create(
        owner=company.owner, company=company, role_title="Platform Engineer", cv=cv
    )
    assert set(cv.applications()) == {application, other}


def test_uploaded_files_get_random_names(cv):
    assert cv.file.name.startswith("documents/")
    assert "Jane" not in cv.file.name
    assert cv.file.name.endswith(".pdf")
    assert cv.original_filename == "Jane Doe CV.pdf"


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_stale_status_change_clears_follow_up_added_by_note(application, status):
    stale = Application.objects.get(pk=application.pk)
    application.next_follow_up = timezone.localdate() + timedelta(days=3)
    application.save(update_fields=["next_follow_up", "updated_at"])
    stale.change_status(status)
    application.refresh_from_db()
    assert application.status == status
    assert application.next_follow_up is None


def test_stale_status_change_uses_current_status_and_date(application):
    stale = Application.objects.get(pk=application.pk)
    application.change_status(Status.REJECTED)
    date_applied = application.date_applied
    event = stale.change_status(Status.INTERVIEWING)
    application.refresh_from_db()
    assert event.from_status == Status.REJECTED
    assert application.status == Status.INTERVIEWING
    assert application.next_follow_up is None
    assert application.date_applied == date_applied


@pytest.mark.parametrize("status", [Status.REJECTED, Status.WITHDRAWN, Status.NO_RESPONSE])
def test_closed_partial_save_clears_persisted_follow_up(application, status):
    application.status = status
    application.save()
    Application.objects.filter(pk=application.pk).update(next_follow_up=timezone.localdate())
    application.save(update_fields=["updated_at"])
    application.refresh_from_db()
    assert application.next_follow_up is None
