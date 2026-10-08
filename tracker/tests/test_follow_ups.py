from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from tracker.models import Application, ApplicationEvent, Status

pytestmark = pytest.mark.django_db


def make(company, role, status=Status.APPLIED, follow_up_in=None, quiet_days=None):
    today = timezone.localdate()
    application = Application.objects.create(
        owner=company.owner,
        company=company,
        role_title=role,
        status=status,
        next_follow_up=None if follow_up_in is None else today + timedelta(days=follow_up_in),
    )
    if quiet_days is not None:
        Application.objects.filter(pk=application.pk).update(
            created_at=timezone.now() - timedelta(days=quiet_days)
        )
    return application


def test_follow_up_page_groups_reminders(auth_client, company):
    make(company, "Overdue role", follow_up_in=-3)
    make(company, "Today role", follow_up_in=0)
    make(company, "Later role", follow_up_in=5)
    make(company, "Quiet role", quiet_days=30)
    make(company, "Closed role", status=Status.REJECTED, quiet_days=30)

    page = auth_client.get(reverse("follow-up-list")).content.decode()

    overdue = page.index("Overdue role")
    today = page.index("Today role")
    quiet = page.index("Quiet role")
    later = page.index("Later role")
    assert overdue < today < quiet < later
    assert "Follow-up overdue by 3 days" in page
    assert "Follow-up due today" in page
    assert "No activity for 30 days" in page
    assert "Follow-up in 5 days" in page
    assert "Closed role" not in page


def test_follow_up_page_empty_state(auth_client):
    page = auth_client.get(reverse("follow-up-list")).content.decode()
    assert "Nothing to follow up." in page


def test_nav_shows_how_many_reminders_need_attention(auth_client, company):
    make(company, "Overdue role", follow_up_in=-1)
    make(company, "Quiet role", quiet_days=30)
    make(company, "Later role", follow_up_in=4)

    page = auth_client.get(reverse("company-list")).content.decode()

    assert '<span class="nav-count" aria-label="2 need attention">2</span>' in page


def test_mark_done_clears_the_date_and_logs_it(auth_client, company):
    application = make(company, "Overdue role", follow_up_in=-2)
    due = application.next_follow_up

    response = auth_client.post(
        reverse("follow-up-done", args=[application.pk]), {"next": reverse("follow-up-list")}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("follow-up-list")
    application.refresh_from_db()
    assert application.next_follow_up is None
    event = application.events.get()
    assert event.kind == ApplicationEvent.Kind.FOLLOW_UP
    assert event.message == f"Followed up (was due {date_format(due)})."
    page = auth_client.get(reverse("follow-up-list")).content.decode()
    assert "Logged a follow-up on Overdue role at Acme." in page
    assert reverse("follow-up-done", args=[application.pk]) not in page


def test_following_up_on_a_quiet_application_resets_it(auth_client, company):
    application = make(company, "Quiet role", quiet_days=30)

    auth_client.post(reverse("follow-up-done", args=[application.pk]))

    assert application.events.get().message == "Followed up."
    assert not Application.objects.gone_quiet().exists()


@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_snooze_moves_the_follow_up(auth_client, company, days):
    application = make(company, "Overdue role", follow_up_in=-2)

    auth_client.post(reverse("follow-up-snooze", args=[application.pk]), {"days": days})

    application.refresh_from_db()
    until = timezone.localdate() + timedelta(days=days)
    assert application.next_follow_up == until
    assert application.events.get().message == f"Follow-up snoozed until {date_format(until)}."
    assert not Application.objects.follow_up_due().exists()


def test_snoozing_a_quiet_application_hides_it_until_the_date(auth_client, company):
    application = make(company, "Quiet role", quiet_days=30)

    auth_client.post(reverse("follow-up-snooze", args=[application.pk]), {"days": 7})

    page = auth_client.get(reverse("follow-up-list")).content.decode()
    assert "Coming up" in page
    assert "No activity for" not in page
    application.refresh_from_db()
    assert application.next_follow_up == timezone.localdate() + timedelta(days=7)


def test_snooze_rejects_other_durations(auth_client, company):
    application = make(company, "Overdue role", follow_up_in=-2)

    response = auth_client.post(
        reverse("follow-up-snooze", args=[application.pk]), {"days": 365}, follow=True
    )

    assert "Pick how long to snooze for." in response.content.decode()
    application.refresh_from_db()
    assert application.next_follow_up < timezone.localdate()


@pytest.mark.parametrize("action", ["follow-up-done", "follow-up-snooze"])
def test_closed_applications_have_no_follow_ups(auth_client, company, action):
    application = make(company, "Closed role", status=Status.WITHDRAWN)

    response = auth_client.post(reverse(action, args=[application.pk]), {"days": 3}, follow=True)

    assert "Closed applications have no follow-ups." in response.content.decode()
    assert not application.events.exists()


@pytest.mark.parametrize("action", ["follow-up-done", "follow-up-snooze"])
def test_follow_up_actions_reject_get(auth_client, company, action):
    application = make(company, "Overdue role", follow_up_in=-2)
    assert auth_client.get(reverse(action, args=[application.pk])).status_code == 405


@pytest.mark.parametrize("action", ["follow-up-done", "follow-up-snooze"])
def test_follow_up_actions_require_login(client, company, action):
    application = make(company, "Overdue role", follow_up_in=-2)
    response = client.post(reverse(action, args=[application.pk]), {"days": 3})
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))
    application.refresh_from_db()
    assert application.next_follow_up < timezone.localdate()


def test_actions_do_not_redirect_off_site(auth_client, company):
    application = make(company, "Overdue role", follow_up_in=-2)

    response = auth_client.post(
        reverse("follow-up-done", args=[application.pk]), {"next": "https://evil.example/"}
    )

    assert response["Location"] == reverse("follow-up-list")


def test_overview_reminders_have_actions_that_return_to_the_overview(auth_client, company):
    application = make(company, "Overdue role", follow_up_in=-2)

    page = auth_client.get(reverse("application-list")).content.decode()

    assert reverse("follow-up-done", args=[application.pk]) in page
    assert reverse("follow-up-snooze", args=[application.pk]) in page
    assert '<input type="hidden" name="next" value="/">' in page


def test_history_shows_follow_up_events(auth_client, company):
    application = make(company, "Overdue role", follow_up_in=-2)
    application.complete_follow_up()

    page = auth_client.get(application.get_absolute_url()).content.decode()

    assert "<strong>Follow-up</strong>" in page
    assert "Followed up (was due" in page


@pytest.mark.parametrize("due_in", [None, -2, 0, 6])
def test_snooze_uses_the_later_of_today_and_locked_due_date(auth_client, company, due_in):
    application = make(company, "Upcoming role", follow_up_in=due_in)
    until = timezone.localdate() + timedelta(days=max(due_in or 0, 0) + 3)

    response = auth_client.post(
        reverse("follow-up-snooze", args=[application.pk]), {"days": 3}, follow=True
    )

    application.refresh_from_db()
    assert application.next_follow_up == until
    assert application.events.get().message == f"Follow-up snoozed until {date_format(until)}."
    assert f"Snoozed {application} until {date_format(until)}." in response.content.decode()


def test_snooze_uses_the_locked_date_instead_of_stale_instance(company):
    application = make(company, "Upcoming role", follow_up_in=0)
    today = timezone.localdate()
    Application.objects.filter(pk=application.pk).update(next_follow_up=today + timedelta(days=6))

    application.snooze_follow_up(3)

    application.refresh_from_db()
    assert application.next_follow_up == today + timedelta(days=9)
