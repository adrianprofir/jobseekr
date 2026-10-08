import pytest
from django.urls import reverse


def test_source_suggestions_come_from_settings(auth_client, settings):
    settings.SOURCE_SUGGESTIONS = ["Meetup", "Alumni network"]
    response = auth_client.get(reverse("application-create"))
    assert response.context["sources"] == ["Alumni network", "Meetup"]


def test_source_suggestions_can_be_turned_off(auth_client, settings):
    settings.SOURCE_SUGGESTIONS = []
    response = auth_client.get(reverse("application-create"))
    assert response.context["sources"] == []


def test_ssl_redirect_is_opt_in_and_spares_the_health_check(client, settings, db):
    assert settings.SECURE_SSL_REDIRECT is False
    settings.SECURE_SSL_REDIRECT = True
    assert client.get("/healthz").status_code == 200
    redirect = client.get(reverse("application-list"))
    assert redirect.status_code == 301
    assert redirect["Location"].startswith("https://")


def load_settings(**env):
    """Start Django in a fresh process with these environment variables; return the result."""
    import json
    import os
    import subprocess
    import sys

    from django.conf import settings as current

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DEMO_MODE", "EMAIL_HOST", "CAPTURE_MODE", "DOCUMENT_UPLOADS_ENABLED"}
    }
    environment.update(DJANGO_SETTINGS_MODULE="config.settings", **env)
    script = (
        "import json, django; from django.conf import settings; django.setup(); "
        "print(json.dumps({name: str(getattr(settings, name, None)) for name in ["
        "'CAPTURE_MODE', 'DOCUMENT_UPLOADS_ENABLED', 'FILE_UPLOAD_HANDLERS', 'LOGIN_URL', "
        "'DATA_UPLOAD_MAX_MEMORY_SIZE', 'SESSION_COOKIE_AGE']}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=current.BASE_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return json.loads(result.stdout.strip().splitlines()[-1]), ""
    return None, result.stderr


def test_self_hosted_defaults():
    loaded, error = load_settings()
    assert error == ""
    assert loaded["CAPTURE_MODE"] == "live"
    assert loaded["DOCUMENT_UPLOADS_ENABLED"] == "True"
    assert loaded["LOGIN_URL"] == "login"
    assert loaded["DATA_UPLOAD_MAX_MEMORY_SIZE"] == str(5 * 1024 * 1024)


def test_demo_mode_defaults():
    loaded, error = load_settings(DEMO_MODE="true")
    assert error == ""
    assert loaded["CAPTURE_MODE"] == "samples"
    assert loaded["DOCUMENT_UPLOADS_ENABLED"] == "False"
    assert loaded["FILE_UPLOAD_HANDLERS"] == "['tracker.uploads.SkipFilesUploadHandler']"
    assert loaded["LOGIN_URL"] == "demo-try"
    assert loaded["DATA_UPLOAD_MAX_MEMORY_SIZE"] == str(512 * 1024)
    assert loaded["SESSION_COOKIE_AGE"] == str(48 * 3600)


@pytest.mark.parametrize(
    "env,message",
    [
        ({"EMAIL_HOST": "smtp.example"}, "DEMO_MODE is on and EMAIL_HOST is set"),
        ({"CAPTURE_MODE": "live"}, "DEMO_MODE needs CAPTURE_MODE=samples"),
        ({"DOCUMENT_UPLOADS_ENABLED": "true"}, "DEMO_MODE needs DOCUMENT_UPLOADS_ENABLED=false"),
    ],
)
def test_demo_mode_refuses_to_start_with_unsafe_settings(env, message):
    loaded, error = load_settings(DEMO_MODE="true", **env)
    assert loaded is None
    assert "ImproperlyConfigured" in error
    assert message in error


def test_unknown_capture_mode_refuses_to_start():
    loaded, error = load_settings(CAPTURE_MODE="sometimes")
    assert loaded is None
    assert "CAPTURE_MODE must be 'live' or 'samples'" in error


def test_main_site_strip_only_when_configured(client, settings, db):
    settings.MAIN_SITE_URL = ""
    assert "main-site-link" not in client.get(reverse("login")).content.decode()

    settings.MAIN_SITE_URL = "https://portfolio.example"
    page = client.get(reverse("login")).content.decode()
    assert 'href="https://portfolio.example"' in page
    assert "portfolio.example</a>" in page
