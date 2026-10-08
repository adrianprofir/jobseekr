"""DOCUMENT_UPLOADS_ENABLED=false: no file reaches the server, stored documents still open."""

import pytest
from django.urls import reverse

from tracker.models import Application, Document

from .conftest import make_pdf

pytestmark = pytest.mark.django_db


def stored_files(media):
    return [path for path in media.rglob("*") if path.is_file()] if media.exists() else []


def test_upload_page_explains_why_in_the_demo(auth_client, demo_mode):
    response = auth_client.get(reverse("document-upload"))
    page = response.content.decode()

    assert response.status_code == 200
    assert "Uploads are switched off in this demo." in page
    assert "a CV is personal data" in page
    assert 'type="file"' not in page


def test_upload_page_says_so_on_a_server_without_uploads(auth_client, settings):
    settings.DOCUMENT_UPLOADS_ENABLED = False

    page = auth_client.get(reverse("document-upload")).content.decode()

    assert "Uploads are switched off on this server." in page
    assert "a CV is personal data" not in page


def test_upload_post_is_refused_and_stores_nothing(auth_client, demo_mode, private_media):
    response = auth_client.post(
        reverse("document-upload"), {"kind": "cv", "label": "Mine", "file": make_pdf()}
    )

    assert response.status_code == 403
    assert not Document.objects.exists()
    assert stored_files(private_media) == []


def test_application_form_has_no_upload_fields(auth_client, demo_mode):
    page = auth_client.get(reverse("application-create")).content.decode()

    assert 'type="file"' not in page
    assert "Uploads are switched off in this demo." in page


def test_files_posted_with_an_application_are_dropped(auth_client, demo_mode, private_media):
    response = auth_client.post(
        reverse("application-create"),
        {
            "company_name": "Initech",
            "role_title": "Developer",
            "status": "applied",
            "new_cv": make_pdf("Real CV.pdf"),
            "new_cover_letter": make_pdf("Real letter.pdf"),
        },
    )

    assert response.status_code == 302
    application = Application.objects.get()
    assert application.cv is None
    assert application.cover_letter is None
    assert not Document.objects.exists()
    assert stored_files(private_media) == []


def test_document_list_hides_uploading_and_keeps_downloads(auth_client, demo_mode, cv):
    page = auth_client.get(reverse("document-list")).content.decode()

    assert "Uploads are off in this demo." in page
    assert ">Upload document<" not in page
    assert cv.get_absolute_url() in page
    response = auth_client.get(cv.get_absolute_url(), {"download": "1"})
    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


def test_existing_documents_can_still_be_picked(auth_client, demo_mode, cv):
    response = auth_client.post(
        reverse("application-create"),
        {"company_name": "Initech", "role_title": "Dev", "status": "applied", "cv": cv.pk},
    )

    assert response.status_code == 302
    assert Application.objects.get().cv == cv
