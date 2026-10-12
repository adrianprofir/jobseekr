import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.fixture
def posthog_settings(settings):
    settings.POSTHOG_PROJECT_KEY = "phc_test"
    settings.POSTHOG_HOST = "https://eu.i.posthog.com"
    settings.ANALYTICS_COOKIE_DOMAIN = ""
    return settings


def test_no_analytics_markup_when_key_unset(client, db):
    page = client.get(reverse("login")).content.decode()
    assert "analytics-banner" not in page
    assert "analytics-config" not in page
    assert "analytics.js" not in page


def test_banner_and_loader_when_key_set(client, db, posthog_settings):
    page = client.get(reverse("login")).content.decode()
    assert 'id="analytics-banner"' in page
    assert 'id="analytics-config"' in page
    assert "analytics.js" in page
    assert "phc_test" in page
    assert "eu.i.posthog.com" in page
    assert "Analytics preferences" in page


def test_no_analytics_on_admin_login(client, db, posthog_settings):
    page = client.get("/admin/login/").content.decode()
    assert "analytics-banner" not in page
    assert "analytics-config" not in page
    assert "analytics.js" not in page


def test_no_analytics_on_admin_index(client, db, posthog_settings):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    client.force_login(user)
    page = client.get("/admin/").content.decode()
    assert "analytics-banner" not in page
    assert "analytics-config" not in page
