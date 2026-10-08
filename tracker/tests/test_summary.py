import smtplib
from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from tracker.models import Application, ApplicationEvent, Company, Status
from tracker.summary import build_weekly_summary, iso_week, parse_week, week_start

pytestmark = pytest.mark.django_db

MONDAY = date(2026, 9, 28)  # ISO week 2026-W40


def at(day, hour=12):
    return timezone.make_aware(datetime.combine(day, time(hour)))


def status_event(application, to_status, when, from_status=Status.APPLIED):
    return ApplicationEvent.objects.create(
        application=application,
        kind=ApplicationEvent.Kind.STATUS,
        from_status=from_status,
        to_status=to_status,
        created_at=when,
    )


@pytest.fixture
def week(company):
    globex = Company.objects.create(owner=company.owner, name="Globex")
    applied = Application.objects.create(
        owner=company.owner, company=company, role_title="Applied Monday", date_applied=MONDAY
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Applied Sunday",
        date_applied=MONDAY + timedelta(days=6),
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Applied last week",
        date_applied=MONDAY - timedelta(days=1),
    )
    interviewing = Application.objects.create(
        owner=globex.owner,
        company=globex,
        role_title="Got interview",
        status=Status.OFFER,
        date_applied=MONDAY,
    )
    status_event(interviewing, Status.INTERVIEWING, at(MONDAY + timedelta(days=1)))
    status_event(interviewing, Status.OFFER, at(MONDAY + timedelta(days=4)), Status.INTERVIEWING)
    rejected = Application.objects.create(
        owner=globex.owner, company=globex, role_title="Got rejected", status=Status.REJECTED
    )
    status_event(rejected, Status.REJECTED, at(MONDAY + timedelta(days=6), 23))
    late = Application.objects.create(
        owner=globex.owner, company=globex, role_title="Next week reply"
    )
    status_event(late, Status.REJECTED, at(MONDAY + timedelta(days=7), 0))
    withdrawn = Application.objects.create(
        owner=globex.owner, company=globex, role_title="Withdrew"
    )
    status_event(withdrawn, Status.WITHDRAWN, at(MONDAY + timedelta(days=2)))
    return applied


def test_week_helpers():
    assert week_start(date(2026, 10, 4)) == MONDAY
    assert week_start(MONDAY) == MONDAY
    assert iso_week(MONDAY) == "2026-W40"
    assert parse_week("2026-W40") == MONDAY
    assert parse_week("2026-W54") is None
    assert parse_week("last week") is None


def test_summary_counts_only_the_chosen_week(week, user):
    summary = build_weekly_summary(user, MONDAY, today=MONDAY + timedelta(days=8))

    assert [a.role_title for a in summary.applied] == [
        "Applied Monday",
        "Got interview",
        "Applied Sunday",
    ]
    responses = {r.application.role_title: [s for s, _ in r.statuses] for r in summary.responses}
    assert responses == {
        "Got interview": [Status.INTERVIEWING, Status.OFFER],
        "Got rejected": [Status.REJECTED],
    }
    assert [a.role_title for a in summary.interviews] == ["Got interview"]
    assert summary.week == "2026-W40"
    assert summary.previous_week == "2026-W39"
    assert summary.next_week == "2026-W41"


def test_summary_lists_follow_ups_as_of_today(company):
    today = timezone.localdate()
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Due",
        next_follow_up=today - timedelta(days=1),
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Soon",
        next_follow_up=today + timedelta(days=7),
    )
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Far",
        next_follow_up=today + timedelta(days=8),
    )

    summary = build_weekly_summary(company.owner, today, today)

    assert [r.application.role_title for r in summary.follow_ups_due] == ["Due"]
    assert [r.application.role_title for r in summary.upcoming] == ["Soon"]
    assert summary.next_week is None


def test_summary_page(auth_client, week):
    page = auth_client.get(reverse("weekly-summary"), {"week": "2026-W40"}).content.decode()

    assert "Week 40 · 28 Sep - 4 Oct 2026" in page
    assert '<span class="stat-value">3</span><span class="stat-label">Applied</span>' in page
    assert '<span class="stat-value">2</span><span class="stat-label">Responses</span>' in page
    assert "Got interview" in page
    assert "Applied last week" not in page
    assert "Next week reply" not in page
    assert "?week=2026-W39" in page
    assert "?week=2026-W41" in page


def test_summary_page_links_back_to_this_week(auth_client, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: date(2026, 10, 21))

    page = auth_client.get(reverse("weekly-summary"), {"week": "2026-W40"}).content.decode()

    assert '?week=2026-W43">This week</a>' in page
    assert '?week=2026-W41">Next week' in page


@pytest.mark.parametrize(
    "today,week,current",
    [
        (date(2026, 10, 1), 40, True),  # Thursday: this week so far
        (date(2026, 10, 4), 40, True),  # Sunday
        (date(2026, 10, 5), 40, False),  # Monday: the week that just ended
    ],
)
def test_summary_page_defaults_to_the_week_containing_yesterday(
    auth_client, monkeypatch, today, week, current
):
    monkeypatch.setattr(timezone, "localdate", lambda *args: today)

    page = auth_client.get(reverse("weekly-summary")).content.decode()

    assert f"Week {week} " in page
    assert ("· this week" in page) is current
    assert ("Next week &rarr;" in page) is not current


@pytest.mark.parametrize("value", ["garbage", "2999-W01"])
def test_summary_page_ignores_bad_or_future_weeks(auth_client, value):
    yesterday = timezone.localdate() - timedelta(days=1)
    page = auth_client.get(reverse("weekly-summary"), {"week": value}).content.decode()
    assert f"Week {yesterday.isocalendar()[1]} " in page


def test_summary_page_requires_login(client):
    response = client.get(reverse("weekly-summary"))
    assert response["Location"].startswith(reverse("login"))


@pytest.fixture
def smtp(settings, user):
    settings.WEEKLY_SUMMARY_EMAIL_ENABLED = True
    user.email = "me@example.com"
    user.save()
    settings.DEFAULT_FROM_EMAIL = "tracker@example.com"
    settings.SITE_URL = "http://192.168.1.10:8000"


def run(*args):
    out = StringIO()
    call_command("send_weekly_summary", *args, stdout=out)
    return out.getvalue()


def test_command_sends_the_summary(smtp, week, caplog):
    caplog.set_level("INFO", logger="tracker.weekly_summary")
    run("--week", "2026-W40")

    assert "Weekly summary for 2026-W40 sent to account me (me@example.com)" in caplog.text
    [email] = mail.outbox
    assert email.to == ["me@example.com"]
    assert email.from_email == "tracker@example.com"
    assert email.subject == "Job search week 40: 3 applied, 2 responses, 0 follow-ups due"

    assert "Applied Monday at Acme (Mon 28 Sep)" in email.body
    assert "Got interview at Globex (Interviewing, then Offer)" in email.body
    assert "http://192.168.1.10:8000/summary/?week=2026-W40" in email.body
    html, mimetype = email.alternatives[0]
    assert mimetype == "text/html"
    assert f'href="http://192.168.1.10:8000{week.get_absolute_url()}"' in html


def test_subject_uses_singular_counts(smtp, company):
    yesterday = timezone.localdate() - timedelta(days=1)
    Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Due",
        date_applied=yesterday,
        next_follow_up=yesterday,
    )

    run()

    assert ": 1 applied, 0 responses, 1 follow-up due" in mail.outbox[0].subject


def test_command_defaults_to_the_week_containing_yesterday(smtp, monkeypatch):
    monday_after = MONDAY + timedelta(days=7)
    monkeypatch.setattr(timezone, "localdate", lambda *args: monday_after)

    run()

    assert mail.outbox[0].subject.startswith("Job search week 40:")


def test_command_without_smtp_does_nothing(settings, caplog):
    settings.WEEKLY_SUMMARY_EMAIL_ENABLED = False
    caplog.set_level("INFO", logger="tracker.weekly_summary")

    run()

    assert "EMAIL_HOST is not set" in caplog.text
    assert mail.outbox == []


def test_command_without_recipients_fails(smtp, user):
    user.email = ""
    user.save()
    with pytest.raises(CommandError, match="no account has an e-mail address"):
        run()


def test_command_sends_each_account_its_own_summary(smtp, week, django_user_model, caplog):
    caplog.set_level("INFO", logger="tracker.weekly_summary")
    other = django_user_model.objects.create_user(username="sam", email="sam@example.com")
    other_company = Company.objects.create(owner=other, name="Initech")
    Application.objects.create(
        owner=other, company=other_company, role_title="Sam's role", date_applied=MONDAY
    )
    django_user_model.objects.create_user(username="no-address")
    django_user_model.objects.create_user(
        username="gone", email="gone@example.com", is_active=False
    )

    run("--week", "2026-W40")

    by_recipient = {tuple(email.to): email for email in mail.outbox}
    assert set(by_recipient) == {("me@example.com",), ("sam@example.com",)}
    mine = by_recipient["me@example.com",].body
    assert "Applied Monday" in mine
    assert "Sam's role" not in mine
    sams = by_recipient["sam@example.com",]
    assert sams.subject == "Job search week 40: 1 applied, 0 responses, 0 follow-ups due"
    assert "Sam's role" in sams.body
    assert "Applied Monday" not in sams.body
    assert "Account no-address has no e-mail address" in caplog.text


def test_command_can_send_one_account(smtp, django_user_model):
    django_user_model.objects.create_user(username="sam", email="sam@example.com")

    run("--user", "sam")

    assert [email.to for email in mail.outbox] == [["sam@example.com"]]


def test_command_rejects_an_unknown_account(smtp):
    with pytest.raises(CommandError, match="no active account with the username 'nobody'"):
        run("--user", "nobody")


def test_command_reports_send_failures(smtp, monkeypatch, caplog):
    def refuse(self, fail_silently=False, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", refuse)

    with pytest.raises(CommandError, match="Sending the weekly summary failed for: me"):
        run()
    assert "Sending the weekly summary for" in caplog.text


def test_command_dry_run_prints_without_sending(smtp, week):
    output = run("--week", "2026-W40", "--dry-run")

    assert output.startswith("To: me@example.com\nSubject: Job search week 40:")
    assert "RESPONSES" in output
    assert mail.outbox == []


def test_command_rejects_a_bad_week():
    with pytest.raises(CommandError, match="--week must look like 2026-W40"):
        run("--week", "40")
