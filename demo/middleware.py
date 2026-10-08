"""Set request.sandbox, and send a visitor whose sandbox is gone to the Try page.

Only does anything with DEMO_MODE on. A sandbox past its expiry that the
scheduler has not deleted yet is treated as gone: the visitor is signed out and
told. Once it is deleted its account is too, so the session's user no longer
exists; the session still carries the sandbox id, which is how the visitor is
told their workspace expired rather than shown a page they cannot use.
"""

import logging

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from .models import Sandbox

logger = logging.getLogger("demo.sandboxes")

SESSION_KEY = "demo_sandbox_id"
# Pages a visitor without a workspace may still open.
PUBLIC_PREFIXES = ("/try/", "/login/", "/admin/", "/healthz", "/static/")


def expired_url():
    return reverse("demo-try") + "?expired=1"


class SandboxMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.sandbox = None
        if not settings.DEMO_MODE:
            return self.get_response(request)
        if request.user.is_authenticated:
            sandbox = Sandbox.objects.filter(user=request.user).first()
            if sandbox is not None and sandbox.is_expired():
                logger.info("Sandbox %s expired; signing its visitor out", sandbox.name)
                logout(request)
                return redirect(expired_url())
            request.sandbox = sandbox
        elif request.session.get(SESSION_KEY):
            del request.session[SESSION_KEY]
            if not request.path.startswith(PUBLIC_PREFIXES):
                return redirect(expired_url())
        return self.get_response(request)
