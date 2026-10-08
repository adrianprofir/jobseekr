from urllib.parse import urlsplit

from django.conf import settings

from .capture.samples import SAMPLES
from .reminders import due_reminders


def follow_ups(request):
    """The number of reminders needing attention, for the navigation badge."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    return {"follow_up_count": lambda: len(due_reminders(user))}


def main_site(request):
    """The link back to the site this app is shown on (MAIN_SITE_URL), for the strip on top."""
    url = settings.MAIN_SITE_URL
    if not url:
        return {}
    return {"main_site": {"url": url, "name": urlsplit(url).hostname or url}}


def features(request):
    """Switches the templates follow: uploads, and the sample links of CAPTURE_MODE=samples."""
    return {
        "uploads_enabled": settings.DOCUMENT_UPLOADS_ENABLED,
        "capture_samples": SAMPLES if settings.CAPTURE_MODE == "samples" else [],
    }
