"""Try the demo, and leave it."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from . import sandboxes, seed
from .middleware import SESSION_KEY


def _require_demo():
    if not settings.DEMO_MODE:
        raise Http404("The demo is switched off on this server.")


def _try_page(request, status=200, **context):
    return render(
        request,
        "demo/try.html",
        {
            "lifetime_hours": settings.SANDBOX_LIFETIME_HOURS,
            "sample_count": len(seed.APPLICATIONS),
            "log_retention_days": settings.DEMO_LOG_RETENTION_DAYS,
            **context,
        },
        status=status,
    )


@login_not_required
@require_http_methods(["GET", "POST"])
def try_demo(request):
    _require_demo()
    if request.sandbox is not None:
        # Already in a workspace: go back to it rather than make another.
        return redirect("application-list")
    if request.method == "GET":
        return _try_page(
            request,
            expired=request.GET.get("expired") == "1",
            left=request.GET.get("left") == "1",
        )
    try:
        sandbox = sandboxes.create(sandboxes.client_ip(request))
    except sandboxes.SandboxRefused as refused:
        response = _try_page(request, status=refused.status, refused=str(refused))
        response["Retry-After"] = str(refused.retry_after)
        return response
    login(request, sandbox.user, backend="django.contrib.auth.backends.ModelBackend")
    request.session[SESSION_KEY] = sandbox.pk
    messages.success(
        request,
        "Your demo workspace is ready. Everything in it is made up, so change anything you like.",
    )
    return redirect("application-list")


@login_not_required
@require_POST
def leave(request):
    _require_demo()
    sandbox = request.sandbox
    logout(request)
    if sandbox is None:
        return redirect("demo-try")
    sandboxes.delete(sandbox, reason="left")
    return redirect(reverse("demo-try") + "?left=1")
