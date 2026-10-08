"""The weekly summary, shown on the Summary page and sent by e-mail."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

from .models import Application, ApplicationEvent, Status
from .reminders import due_reminders, upcoming_reminders

# Status changes that mean the employer answered.
RESPONSE_STATUSES = [Status.INTERVIEWING, Status.OFFER, Status.REJECTED]
UPCOMING_DAYS = 7


def week_start(day):
    """The Monday of the week a date falls in."""
    return day - timedelta(days=day.weekday())


def parse_week(value):
    """The Monday of an ISO week such as '2026-W40', or None if it is not one."""
    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", value or "")
    if not match:
        return None
    try:
        return date.fromisocalendar(int(match[1]), int(match[2]), 1)
    except ValueError:
        return None


def iso_week(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class Response:
    application: Application
    statuses: list = field(default_factory=list)


@dataclass
class WeeklySummary:
    start: date
    today: date
    applied: list
    responses: list
    interviews: list
    due: list
    upcoming: list

    @property
    def end(self):
        return self.start + timedelta(days=6)

    @property
    def week(self):
        return iso_week(self.start)

    @property
    def week_number(self):
        return self.start.isocalendar()[1]

    @property
    def is_current(self):
        return self.start == week_start(self.today)

    @property
    def current_week(self):
        return iso_week(week_start(self.today))

    @property
    def previous_week(self):
        return iso_week(self.start - timedelta(days=7))

    @property
    def next_week(self):
        """The following week, unless it has not started yet."""
        following = self.start + timedelta(days=7)
        return iso_week(following) if following <= self.today else None

    @property
    def follow_ups_due(self):
        return [reminder for reminder in self.due if reminder.kind != "quiet"]

    @property
    def gone_quiet(self):
        return [reminder for reminder in self.due if reminder.kind == "quiet"]


def build_weekly_summary(owner, start, today=None):
    """What happened in `owner`'s week starting on `start`, and what needs doing as of today."""
    today = today or timezone.localdate()
    start = week_start(start)
    end = start + timedelta(days=6)
    week_begins = timezone.make_aware(datetime.combine(start, time.min))
    week_ends = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))

    applied = list(
        Application.objects.filter(owner=owner, date_applied__range=(start, end))
        .select_related("company")
        .order_by("date_applied", "pk")
    )

    events = (
        ApplicationEvent.objects.filter(
            application__owner=owner,
            kind=ApplicationEvent.Kind.STATUS,
            to_status__in=RESPONSE_STATUSES,
            created_at__gte=week_begins,
            created_at__lt=week_ends,
        )
        .select_related("application__company")
        .order_by("created_at", "pk")
    )
    responses = {}
    for event in events:
        response = responses.setdefault(event.application_id, Response(event.application))
        response.statuses.append((event.to_status, event.get_to_status_display()))
    interviews = [
        response.application
        for response in responses.values()
        if any(status == Status.INTERVIEWING for status, _ in response.statuses)
    ]

    return WeeklySummary(
        start=start,
        today=today,
        applied=applied,
        responses=list(responses.values()),
        interviews=interviews,
        due=due_reminders(owner, today),
        upcoming=upcoming_reminders(owner, today, within_days=UPCOMING_DAYS),
    )


def _count(n, noun):
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


@dataclass
class EmailItem:
    application: Application
    detail: str = ""


def render_summary_email(summary, site_url=""):
    """Subject, plain text and HTML of the weekly summary e-mail."""
    sections = [
        (
            "Applied this week",
            [EmailItem(a, date_format(a.date_applied, "D j M")) for a in summary.applied],
            "No applications sent this week.",
        ),
        (
            "Responses",
            [
                EmailItem(r.application, ", then ".join(label for _, label in r.statuses))
                for r in summary.responses
            ],
            "No interview invitations, offers or rejections this week.",
        ),
        (
            "Interviews",
            [EmailItem(a) for a in summary.interviews],
            "No applications moved to interviewing this week.",
        ),
        (
            "Follow-ups due",
            [EmailItem(r.application, r.reason) for r in summary.follow_ups_due],
            "Nothing due.",
        ),
        (
            "Coming up in the next 7 days",
            [EmailItem(r.application, r.reason) for r in summary.upcoming],
            "No follow-ups scheduled this coming week.",
        ),
        (
            "Gone quiet",
            [EmailItem(r.application, r.reason) for r in summary.gone_quiet],
            "Every open application has recent activity.",
        ),
    ]
    stats = [
        (len(summary.applied), "Applied"),
        (len(summary.responses), "Responses"),
        (len(summary.interviews), "Interviews"),
        (len(summary.follow_ups_due), "Follow-ups due"),
    ]
    context = {"summary": summary, "sections": sections, "stats": stats, "site_url": site_url}
    subject = (
        f"Job search week {summary.week_number}: {len(summary.applied)} applied, "
        f"{_count(len(summary.responses), 'response')}, "
        f"{_count(len(summary.follow_ups_due), 'follow-up')} due"
    )
    text = render_to_string("tracker/email/weekly_summary.txt", context)
    html = render_to_string("tracker/email/weekly_summary.html", context)
    return subject, text, html
