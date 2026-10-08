from django.conf import settings

from . import sandboxes


def demo(request):
    """Whether this is the demo, and the visitor's sandbox with the time it has left."""
    if not settings.DEMO_MODE:
        return {}
    sandbox = getattr(request, "sandbox", None)
    return {
        "demo_mode": True,
        "sandbox": sandbox,
        "sandbox_remaining": sandboxes.remaining_text(sandbox) if sandbox else "",
    }
