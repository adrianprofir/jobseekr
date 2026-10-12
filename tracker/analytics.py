"""Optional PostHog analytics (enabled only when POSTHOG_PROJECT_KEY is set)."""

from django.conf import settings


def analytics_enabled_for_request(request) -> bool:
    """True when the public analytics bundle should be rendered for this request."""
    if not settings.POSTHOG_PROJECT_KEY:
        return False
    return not request.path.startswith("/admin")


def analytics_context(request) -> dict:
    """Template context for the shared consent banner and PostHog loader."""
    if not analytics_enabled_for_request(request):
        return {}
    return {
        "analytics": {
            "project_key": settings.POSTHOG_PROJECT_KEY,
            "api_host": settings.POSTHOG_HOST,
            "ui_host": settings.POSTHOG_UI_HOST,
            "app_name": settings.ANALYTICS_APP_NAME,
            "cookie_domain": settings.ANALYTICS_COOKIE_DOMAIN,
            "secure_cookies": request.is_secure(),
        }
    }
