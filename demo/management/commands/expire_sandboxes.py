"""Delete demo sandboxes past their expiry, or every one of them.

The scheduler runs the first every few minutes; the second clears the demo:

    python manage.py expire_sandboxes
    python manage.py expire_sandboxes --all
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from demo import sandboxes
from demo.models import Sandbox


class Command(BaseCommand):
    help = "Delete expired demo sandboxes with their accounts, data and documents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Delete every sandbox, expired or not."
        )

    def handle(self, *args, all, **options):
        if all:
            doomed = list(Sandbox.objects.order_by("pk"))
            for sandbox in doomed:
                sandboxes.delete(sandbox, reason="deleted")
            gone = len(doomed)
        else:
            gone = sandboxes.expire_due(timezone.now())
        left = sandboxes.active().count()
        self.stdout.write(f"Deleted {gone} sandbox(es); {left} active.")
