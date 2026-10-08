"""Per-visitor demo sandboxes: make one, delete it when the visitor leaves or it expires.

A sandbox is an account with a random username and an unusable password,
filled with the seed bundle (demo/seed.py), so only the browser that made it
can ever use it. Deleting the account deletes its companies, documents and
applications, and the stored document files with them.

Creation runs inside the request: seeding is a few dozen inserts. It is bounded
two ways: a cap on active sandboxes and a rate limit per visitor address. A
short PostgreSQL advisory lock makes both exact under concurrent requests.
"""

import ipaddress
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.utils import timezone

from tracker.models import Application

from . import seed
from .models import Sandbox

logger = logging.getLogger("demo.sandboxes")

USERNAME_PREFIX = "demo-"
# The advisory lock that serialises sandbox creation; any constant will do.
CREATE_LOCK = 20261008


class SandboxRefused(Exception):
    """Creation was refused. The message says why; `retry_after` is seconds to wait."""

    def __init__(self, message, *, status, retry_after):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def lifetime():
    return timedelta(hours=settings.SANDBOX_LIFETIME_HOURS)


def active(now=None):
    """Sandboxes still in use: not expired, and not left by their visitor."""
    return Sandbox.objects.filter(expires_at__gt=now or timezone.now(), user__isnull=False)


def client_ip(request):
    """The visitor's address: from the trusted proxy header when one is configured, else the peer.

    An address that does not parse counts as unknown, so a malformed header can
    only make the rate limit stricter, never looser.
    """
    value = ""
    if settings.CLIENT_IP_HEADER:
        value = request.headers.get(settings.CLIENT_IP_HEADER, "")
    if not value:
        value = request.META.get("REMOTE_ADDR", "")
    value = value.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def new_username():
    User = get_user_model()
    while True:
        username = f"{USERNAME_PREFIX}{secrets.token_hex(4)}"
        if not (
            User.objects.filter(username=username).exists()
            or Sandbox.objects.filter(name=username).exists()
        ):
            return username


def _seconds_until(moment, now):
    return max(60, int((moment - now).total_seconds()) + 1)


def _check_room(address, now):
    """Raise SandboxRefused when the demo is full or this address started too many."""
    count = active(now).count()
    if count >= settings.SANDBOX_MAX_ACTIVE:
        logger.warning(
            "Sandbox refused for %s: %d active, the cap", address or "an unknown address", count
        )
        soonest = active(now).order_by("expires_at").values_list("expires_at", flat=True).first()
        raise SandboxRefused(
            "The demo is full right now: every demo workspace is in use. "
            "Try again in a little while.",
            status=503,
            retry_after=_seconds_until(soonest, now) if soonest else 600,
        )
    window = timedelta(seconds=settings.SANDBOX_RATE_WINDOW)
    recent = Sandbox.objects.filter(client_ip=address, created_at__gt=now - window)
    count = recent.count()
    if count >= settings.SANDBOX_RATE_LIMIT:
        logger.warning(
            "Sandbox refused for %s: %d started in the last %s, the limit",
            address or "an unknown address",
            count,
            duration_text(window),
        )
        oldest = recent.order_by("created_at").values_list("created_at", flat=True).first()
        raise SandboxRefused(
            f"You started {count} demo workspace{'s' if count != 1 else ''} "
            f"in the last {duration_text(window)}, the most one address may. "
            "Try again later.",
            status=429,
            retry_after=_seconds_until(oldest + window, now),
        )


def create(address="", now=None):
    """Make a sandbox account for a visitor, fill it with the seed bundle and return it."""
    now = now or timezone.now()
    stored = []
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CREATE_LOCK])
            _check_room(address, now)
            username = new_username()
            user = get_user_model()(username=username)
            user.set_unusable_password()
            user.save()
            sandbox = Sandbox.objects.create(
                user=user,
                name=username,
                created_at=now,
                expires_at=now + lifetime(),
                client_ip=address,
            )
            seed.seed(user, stored_files=stored)
    except BaseException:
        # The rows rolled back, but files are not part of the transaction.
        for name in stored:
            default_storage.delete(name)
        raise
    logger.info(
        "Sandbox %s created for %s (%d active)",
        sandbox.name,
        address or "an unknown address",
        active().count(),
    )
    return sandbox


def delete(sandbox, reason):
    """Delete the sandbox's account with everything it owns; the row goes too once expired.

    `reason` is "left" when the visitor leaves (the row stays until it expires,
    so the rate limit still counts it), else "expired" or "deleted", for the log.
    """
    applications = 0
    with transaction.atomic():
        if sandbox.user_id is not None:
            applications = Application.objects.filter(owner_id=sandbox.user_id).count()
            # Deleting the account cascades to its data; the files go after commit.
            get_user_model().objects.filter(pk=sandbox.user_id).delete()
            sandbox.user = None
        if reason == "left":
            sandbox.save(update_fields=["user"])
        else:
            sandbox.delete()
    logger.info(
        "Sandbox %s %s (made for %s): %d applications deleted",
        sandbox.name,
        reason,
        sandbox.client_ip or "an unknown address",
        applications,
    )


def expire_due(now=None):
    """Delete every sandbox past its expiry; returns how many went."""
    now = now or timezone.now()
    gone = 0
    for sandbox in Sandbox.objects.filter(expires_at__lte=now).order_by("pk"):
        delete(sandbox, reason="expired")
        gone += 1
    return gone


def duration_text(delta):
    """A duration in words after "the last": "hour", "2 hours", "30 minutes"."""
    seconds = int(delta.total_seconds())
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return "hour" if hours == 1 else f"{hours} hours"
    minutes = max(seconds // 60, 1)
    return "minute" if minutes == 1 else f"{minutes} minutes"


def remaining_text(sandbox, now=None):
    """How long the sandbox has left, in words: "in 23 hours", "in 40 minutes"."""
    seconds = sandbox.remaining(now).total_seconds()
    hours = round(seconds / 3600)
    if hours >= 1:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    minutes = round(seconds / 60)
    if minutes >= 1:
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    return "any moment now"
