import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker.summary import build_weekly_summary, parse_week, render_summary_email

logger = logging.getLogger("tracker.weekly_summary")


class Command(BaseCommand):
    help = (
        "E-mail each account its weekly job search summary, to the e-mail address on the "
        "account. By default it covers the week (Monday to Sunday) that contains yesterday, "
        "so run it on Sunday evening or Monday morning. "
        "Does nothing, successfully, when EMAIL_HOST is not set."
    )

    def add_arguments(self, parser):
        parser.add_argument("--week", help="ISO week to summarise, for example 2026-W40.")
        parser.add_argument("--user", help="Only this account's summary (a username).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the e-mails instead of sending them.",
        )

    def handle(self, *args, week=None, user=None, dry_run=False, **options):
        today = timezone.localdate()
        if week:
            start = parse_week(week)
            if start is None:
                raise CommandError(f"--week must look like 2026-W40, not {week!r}.")
        else:
            start = today - timedelta(days=1)
        accounts = get_user_model().objects.filter(is_active=True).order_by("username")
        if user:
            accounts = accounts.filter(username=user)
            if not accounts:
                raise CommandError(f"There is no active account with the username {user!r}.")

        if dry_run:
            for account in accounts:
                summary = build_weekly_summary(account, start, today)
                subject, text, _ = render_summary_email(summary, settings.SITE_URL)
                to = account.email or "nobody: the account has no e-mail address"
                self.stdout.write(f"To: {to}\nSubject: {subject}\n\n{text}\n")
            return

        if not settings.WEEKLY_SUMMARY_EMAIL_ENABLED:
            logger.info("EMAIL_HOST is not set, so no weekly summary e-mail was sent.")
            return
        recipients = [account for account in accounts if account.email]
        for account in accounts:
            if not account.email:
                logger.info("Account %s has no e-mail address, so it gets no summary", account)
        if not recipients:
            raise CommandError(
                "EMAIL_HOST is set but no account has an e-mail address: there is nobody to "
                "send to. Add an address to your account in the admin."
            )

        failed = [account for account in recipients if not self.send(account, start, today)]
        if failed:
            names = ", ".join(str(account) for account in failed)
            raise CommandError(f"Sending the weekly summary failed for: {names}.")

    def send(self, account, start, today):
        """Send one account its summary. Returns whether it was sent."""
        summary = build_weekly_summary(account, start, today)
        subject, text, html = render_summary_email(summary, settings.SITE_URL)
        email = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [account.email])
        email.attach_alternative(html, "text/html")
        try:
            email.send()
        except Exception:
            host = settings.MAILERS["default"].get("OPTIONS", {}).get("host", "the mail server")
            logger.exception(
                "Sending the weekly summary for %s to account %s (%s) via %s failed",
                summary.week,
                account,
                account.email,
                host,
            )
            return False
        logger.info(
            "Weekly summary for %s sent to account %s (%s)", summary.week, account, account.email
        )
        return True
