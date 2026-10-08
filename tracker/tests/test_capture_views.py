"""Paste a posting link, review the pre-filled form, save."""

import pytest
from django.urls import reverse
from django.utils import timezone

from tracker.models import Application, Company

pytestmark = pytest.mark.django_db

LINKEDIN = (
    "https://dk.linkedin.com/jobs/view/senior-python-engineer-at-lindholm-analytics-1234567890"
)


def field_value(page, name):
    """The value attribute or text the form renders for a field."""
    import re

    if match := re.search(rf'<textarea name="{name}"[^>]*>\s*(.*?)</textarea>', page, re.S):
        return match[1]
    if match := re.search(rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"', page):
        return match[1]
    if match := re.search(rf'<select name="{name}".*?</select>', page, re.S):
        selected = re.search(r'<option value="([^"]*)" selected', match[0])
        return selected[1] if selected else ""
    return None


def test_quick_add_prefills_the_form_from_the_posting(auth_client, serve_posting):
    requested = serve_posting("linkedin-dk.html")

    response = auth_client.get(reverse("application-create"), {"url": LINKEDIN})
    page = response.content.decode()

    assert requested == [LINKEDIN]
    assert "Filled in from the posting:" in page
    assert "role, company, location, source, a copy of the ad" in page
    assert field_value(page, "posting_url") == LINKEDIN
    assert field_value(page, "role_title") == "Senior Python Engineer"
    assert field_value(page, "company_name") == "Lindholm Analytics"
    assert field_value(page, "location") == "Aarhus"
    assert field_value(page, "source") == "LinkedIn"
    assert "Are you a Senior Python Engineer" in field_value(page, "posting_text")
    # Nothing is saved until the form is submitted.
    assert not Application.objects.exists()
    assert not Company.objects.exists()


def test_reviewed_form_saves_the_ad_copy(auth_client, serve_posting):
    serve_posting("thehub.html")
    page = auth_client.get(
        reverse("application-create"), {"url": "https://thehub.io/jobs/0123456789abcdef01234567"}
    ).content.decode()

    response = auth_client.post(
        reverse("application-create"),
        {
            "posting_url": field_value(page, "posting_url"),
            "company_name": field_value(page, "company_name"),
            "role_title": "Founding Backend Engineer",  # the user corrected the title
            "location": field_value(page, "location"),
            "source": field_value(page, "source"),
            "status": "applied",
            "posting_text": field_value(page, "posting_text"),
        },
    )

    application = Application.objects.get()
    assert response.status_code == 302
    assert application.role_title == "Founding Backend Engineer"
    assert application.company.name == "Kestrel Sports"
    assert application.source == "The Hub"
    assert "Kestrel Sports is building the sports and community layer" in application.posting_text
    assert application.posting_text_saved_at is not None


def test_captured_company_uses_the_existing_spelling(auth_client, user, serve_posting):
    Company.objects.create(owner=user, name="LINDHOLM ANALYTICS")
    serve_posting("linkedin-dk.html")

    page = auth_client.get(reverse("application-create"), {"url": LINKEDIN}).content.decode()

    assert field_value(page, "company_name") == "LINDHOLM ANALYTICS"


def test_failed_fetch_explains_and_leaves_manual_entry(auth_client):
    # The autouse no_network fixture makes every fetch fail.
    page = auth_client.get(
        reverse("application-create"), {"url": "https://jobs.example/42"}
    ).content.decode()

    assert "Could not fill in the form from the link." in page
    assert "Network access is disabled in tests." in page
    assert "paste the ad text into" in page
    assert field_value(page, "posting_url") == "https://jobs.example/42"

    response = auth_client.post(
        reverse("application-create"),
        {
            "posting_url": "https://jobs.example/42",
            "company_name": "Initech",
            "role_title": "Developer",
            "status": "applied",
            "posting_text": "Pasted by hand.",
        },
    )
    assert response.status_code == 302
    application = Application.objects.get()
    assert application.posting_text == "Pasted by hand."


def test_page_without_job_details_says_so(auth_client, serve_posting):
    serve_posting("not-a-posting.html")

    page = auth_client.get(
        reverse("application-create"), {"url": "https://app.example/jobs/1"}
    ).content.decode()

    assert "no job details were found on it" in page


def test_invalid_url_is_not_fetched(auth_client, serve_posting):
    requested = serve_posting("linkedin-dk.html")

    page = auth_client.get(reverse("application-create"), {"url": "not a url"}).content.decode()

    assert requested == []
    assert "Enter a valid web address to fetch." in page


def test_copied_search_link_is_turned_into_the_posting_link(auth_client, serve_posting):
    requested = serve_posting("linkedin-www.html")

    page = auth_client.get(
        reverse("application-create"),
        {"url": "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=1234567890"},
    ).content.decode()

    assert requested == ["https://www.linkedin.com/jobs/view/1234567890/"]
    assert field_value(page, "posting_url") == "https://www.linkedin.com/jobs/view/1234567890/"


def test_already_tracked_posting_is_flagged(auth_client, serve_posting, application):
    serve_posting("linkedin-dk.html")

    page = auth_client.get(
        reverse("application-create"), {"url": application.posting_url + "/"}
    ).content.decode()

    assert "You already track this posting" in page
    assert application.get_absolute_url() in page


def test_fetch_button_keeps_typed_values_and_fills_the_rest(auth_client, serve_posting):
    serve_posting("linkedin-dk.html")

    response = auth_client.post(
        reverse("application-create"),
        {
            "fetch": "1",
            "posting_url": LINKEDIN,
            "company_name": "Lindholm Analytics ApS",
            "role_title": "",
            "status": "interviewing",
            "notes": "Referred by a friend",
        },
    )
    page = response.content.decode()

    assert response.status_code == 200
    assert not Application.objects.exists()
    assert field_value(page, "company_name") == "Lindholm Analytics ApS"
    assert field_value(page, "role_title") == "Senior Python Engineer"
    assert field_value(page, "status") == "interviewing"
    assert field_value(page, "notes") == "Referred by a friend"
    assert "Filled in from the posting:</strong> role, location, source, a copy of the ad" in page
    # No validation errors for the fields that are still empty.
    assert "This field is required" not in page


def test_fetch_button_asks_to_choose_files_again(auth_client, serve_posting):
    from .conftest import make_pdf

    serve_posting("thehub.html")
    page = auth_client.post(
        reverse("application-create"),
        {"fetch": "1", "posting_url": "https://thehub.io/jobs/1", "new_cv": make_pdf()},
    ).content.decode()

    assert "Choose the documents to upload again" in page


def test_edit_fetch_fills_only_empty_fields_of_an_existing_application(
    auth_client, serve_posting, application
):
    serve_posting("linkedin-dk.html")
    application.posting_url = LINKEDIN
    application.save()

    page = auth_client.get(
        reverse("application-edit", args=[application.pk]), {"fetch": "1"}
    ).content.decode()

    assert field_value(page, "role_title") == "Backend Engineer"
    assert field_value(page, "company_name") == "Acme"
    assert field_value(page, "location") == "Aarhus"
    assert "Are you a Senior Python Engineer" in field_value(page, "posting_text")
    assert "You already track this posting" not in page
    application.refresh_from_db()
    assert application.posting_text == ""


def test_editing_the_ad_copy_is_recorded(auth_client, application):
    data = {
        "posting_url": application.posting_url,
        "company_name": "Acme",
        "role_title": application.role_title,
        "status": application.status,
        "cv": application.cv.pk,
        "posting_text": "We are hiring.",
    }
    auth_client.post(reverse("application-edit", args=[application.pk]), data)

    application.refresh_from_db()
    assert application.posting_text == "We are hiring."
    assert application.posting_text_saved_at <= timezone.now()
    assert application.events.first().message == "Changed copy of the job ad."


def test_detail_shows_the_saved_ad(auth_client, application):
    application.posting_text = "Line one\nLine two"
    application.posting_text_saved_at = timezone.now()
    application.save()

    page = auth_client.get(application.get_absolute_url()).content.decode()

    assert "Show the saved copy" in page
    assert "Line one\nLine two" in page


def test_detail_offers_to_fetch_a_missing_ad_copy(auth_client, application):
    page = auth_client.get(application.get_absolute_url()).content.decode()

    assert reverse("application-edit", args=[application.pk]) + "?fetch=1" in page


def test_enter_in_a_field_saves_instead_of_fetching(auth_client):
    page = auth_client.get(reverse("application-create")).content.decode()
    form = page[page.index('<form method="post"') :]

    # The first submit button is the form's default for implicit submission.
    first_button = form[form.index("<button") : form.index("</button>")]
    assert 'name="fetch"' not in first_button


def test_blocked_job_board_still_fills_in_the_source(auth_client):
    page = auth_client.get(
        reverse("application-create"), {"url": "https://dk.indeed.com/viewjob?jk=0123456789abcdef"}
    ).content.decode()

    assert "Could not fill in the form from the link." in page
    assert field_value(page, "source") == "Indeed"


# A real transaction per request, so the test can see whether the view opened one.
@pytest.mark.django_db(transaction=True)
def test_fetching_for_an_edit_holds_no_database_transaction(auth_client, monkeypatch, application):
    from django.db import connection

    from tracker.capture import CaptureResult

    seen = []

    def capture(url):
        seen.append(connection.in_atomic_block)
        return CaptureResult(url=url, error="offline")

    monkeypatch.setattr("tracker.views.capture_posting", capture)
    auth_client.get(reverse("application-edit", args=[application.pk]), {"fetch": "1"})
    auth_client.post(
        reverse("application-edit", args=[application.pk]),
        {"fetch": "1", "posting_url": application.posting_url},
    )

    assert seen == [False, False]
