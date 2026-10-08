"""A demo sandbox: one visitor's private account, deleted after a day.

"Try the demo" makes a sandbox (demo/sandboxes.py): an ordinary account with a
random username and no usable password, filled with sample data. Per-account
scoping does the isolation, so two visitors never see each other's data.

The row outlives its account when the visitor leaves early, so the rate limit
still counts it; it is deleted when it expires.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Sandbox(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sandbox",
        help_text="Empty once the visitor left and the account was deleted.",
    )
    name = models.CharField(
        max_length=40, unique=True, help_text="The account's username, kept for the logs."
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(
        db_index=True, help_text="The sandbox and everything in it are deleted then."
    )
    client_ip = models.CharField(
        max_length=45,
        blank=True,
        help_text="The visitor's address, for the rate limit; deleted with the sandbox.",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "demo sandbox"
        verbose_name_plural = "demo sandboxes"

    def __str__(self):
        return self.name

    def is_expired(self, now=None):
        return self.expires_at <= (now or timezone.now())

    def remaining(self, now=None):
        """Time left before deletion, never negative."""
        return max(self.expires_at - (now or timezone.now()), timedelta(0))
