"""Follow-up reminders: what needs a nudge now and what is coming up."""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.formats import date_format

from .models import Application


@dataclass
class Reminder:
    application: Application
    kind: str  # "overdue", "today", "upcoming" or "quiet"
    reason: str

    @property
    def needs_attention(self):
        return self.kind != "upcoming"


def _days(n):
    return "1 day" if n == 1 else f"{n} days"


def due_reminders(owner, today=None):
    """Follow-ups that are due or overdue, then applications that have gone quiet."""
    today = today or timezone.localdate()
    applications = Application.objects.filter(owner=owner)
    reminders = []
    due = (
        applications.follow_up_due(today).select_related("company").order_by("next_follow_up", "pk")
    )
    for application in due:
        days = (today - application.next_follow_up).days
        if days == 0:
            reminders.append(Reminder(application, "today", "Follow-up due today"))
        else:
            reminders.append(
                Reminder(application, "overdue", f"Follow-up overdue by {_days(days)}")
            )
    seen = {reminder.application.pk for reminder in reminders}
    now = timezone.now()
    quiet = applications.gone_quiet(now).select_related("company").order_by("last_activity_at")
    for application in quiet:
        if application.pk not in seen:
            days = (now - application.last_activity_at).days
            reminders.append(Reminder(application, "quiet", f"No activity for {_days(days)}"))
    return reminders


def upcoming_reminders(owner, today=None, within_days=None):
    """Open applications with a follow-up date after today, soonest first."""
    today = today or timezone.localdate()
    applications = (
        Application.objects.filter(owner=owner)
        .open()
        .filter(next_follow_up__gt=today)
        .select_related("company")
        .order_by("next_follow_up", "pk")
    )
    if within_days is not None:
        applications = applications.filter(next_follow_up__lte=today + timedelta(within_days))
    reminders = []
    for application in applications:
        days = (application.next_follow_up - today).days
        when = "tomorrow" if days == 1 else f"in {_days(days)}"
        label = date_format(application.next_follow_up, "D j M")
        reminders.append(Reminder(application, "upcoming", f"Follow-up {when}, {label}"))
    return reminders
