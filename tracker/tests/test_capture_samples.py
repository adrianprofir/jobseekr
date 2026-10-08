"""CAPTURE_MODE=samples: the demo captures its bundled sample postings and fetches nothing."""

import socket

import pytest
import urllib3
from django.urls import reverse

from tracker.capture.samples import REFUSED, SAMPLES
from tracker.models import Application

from .test_capture_views import field_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def outbound(monkeypatch):
    """Record every attempt to reach the network, at each layer a fetch would go through."""
    attempts = []

    def record(name):
        def refuse(*args, **kwargs):
            attempts.append((name, args))
            raise AssertionError(f"{name} was called in samples mode")

        return refuse

    monkeypatch.setattr("tracker.capture.fetch_page", record("fetch_page"))
    monkeypatch.setattr("tracker.capture.fetch.fetch_page", record("fetch.fetch_page"))
    monkeypatch.setattr(socket, "getaddrinfo", record("getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", record("create_connection"))
    monkeypatch.setattr(urllib3.HTTPConnectionPool, "urlopen", record("urlopen"))
    return attempts


def sample(board):
    return next(s for s in SAMPLES if s.board == board)


@pytest.mark.parametrize(
    "board,role,company",
    [
        ("LinkedIn", "Senior Python Engineer", "Lindholm Analytics"),
        ("Jobindex", "Junior Python Developer", "Tidewater Imaging ApS"),
        ("Teamtailor", "Senior Data Engineer", "Harbourline Systems"),
        ("The Hub", "Founding Backend Engineer", "Kestrel Sports"),
    ],
)
def test_sample_links_are_captured_without_a_request(
    auth_client, demo_mode, outbound, board, role, company
):
    url = sample(board).url

    page = auth_client.get(reverse("application-create"), {"url": url}).content.decode()

    assert "Filled in from the posting:" in page
    assert field_value(page, "posting_url") == url
    assert field_value(page, "role_title") == role
    assert field_value(page, "company_name") == company
    assert field_value(page, "posting_text")
    assert outbound == []


def test_indeed_sample_shows_the_blocked_path(auth_client, demo_mode, outbound):
    page = auth_client.get(
        reverse("application-create"), {"url": sample("Indeed").url}
    ).content.decode()

    assert "Could not fill in the form from the link." in page
    assert "dk.indeed.com refused to show the posting to the tracker (HTTP 403)" in page
    assert field_value(page, "source") == "Indeed"
    assert outbound == []


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example/42",
        "https://www.linkedin.com/jobs/view/9999999999/",
        "http://192.0.2.10/admin",
    ],
)
def test_any_other_link_is_refused_without_a_request(auth_client, demo_mode, outbound, url):
    page = auth_client.get(reverse("application-create"), {"url": url}).content.decode()

    assert REFUSED in page
    assert field_value(page, "posting_url") == url
    assert outbound == []


def test_fetch_details_button_uses_the_samples_too(auth_client, demo_mode, outbound):
    response = auth_client.post(
        reverse("application-create"),
        {"fetch": "1", "posting_url": "https://elsewhere.example/job", "status": "applied"},
    )

    assert REFUSED in response.content.decode()
    assert outbound == []


def test_a_refused_link_can_still_be_saved_by_hand(auth_client, demo_mode, outbound):
    response = auth_client.post(
        reverse("application-create"),
        {
            "posting_url": "https://jobs.example/42",
            "company_name": "Initech",
            "role_title": "Developer",
            "status": "applied",
        },
    )

    assert response.status_code == 302
    assert Application.objects.get().posting_url == "https://jobs.example/42"


def test_overview_offers_the_samples_only_in_samples_mode(auth_client, settings):
    url = reverse("application-list")
    assert "Try one:" not in auth_client.get(url).content.decode()

    settings.CAPTURE_MODE = "samples"
    page = auth_client.get(url).content.decode()

    assert "This demo only captures sample postings." in page
    for each in SAMPLES:
        assert f"{reverse('application-create')}?url=" in page
        assert each.board in page
    assert "Indeed (blocked)" in page


def test_detail_offers_fetching_the_ad_only_for_a_sample(auth_client, demo_mode, application):
    edit = reverse("application-edit", args=[application.pk]) + "?fetch=1"

    assert edit not in auth_client.get(application.get_absolute_url()).content.decode()

    application.posting_url = sample("The Hub").url
    application.save()
    assert edit in auth_client.get(application.get_absolute_url()).content.decode()


def test_edit_fetch_reads_the_sample(auth_client, demo_mode, outbound, application):
    application.posting_url = sample("The Hub").url
    application.save()

    page = auth_client.get(
        reverse("application-edit", args=[application.pk]), {"fetch": "1"}
    ).content.decode()

    assert "Kestrel Sports is building" in field_value(page, "posting_text")
    assert outbound == []


def test_live_mode_still_fetches(auth_client, serve_posting):
    requested = serve_posting("thehub.html")

    auth_client.get(reverse("application-create"), {"url": sample("The Hub").url})

    assert requested == [sample("The Hub").url]
