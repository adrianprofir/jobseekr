"""Uploaded CVs and cover letters must only ever reach the logged-in user."""

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse

from tracker.models import Application, Document

from .conftest import PDF_BYTES, make_docx, make_pdf

pytestmark = pytest.mark.django_db


def test_download_requires_login(client, cv):
    response = client.get(reverse("document-download", args=[cv.pk]))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("login"))


def test_anonymous_response_does_not_leak_file_contents(client, cv):
    response = client.get(reverse("document-download", args=[cv.pk]), follow=True)
    assert PDF_BYTES not in response.content


def test_logged_in_user_can_view_pdf_inline(auth_client, cv):
    response = auth_client.get(reverse("document-download", args=[cv.pk]))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF_BYTES
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert "Jane Doe CV.pdf" in response["Content-Disposition"]
    assert response["Cache-Control"] == "private, no-store"
    response.close()


def test_download_param_forces_attachment(auth_client, cv):
    response = auth_client.get(reverse("document-download", args=[cv.pk]) + "?download=1")
    assert response["Content-Disposition"].startswith("attachment")
    response.close()


def test_docx_is_always_an_attachment(auth_client, user):
    upload = make_docx()
    document = Document.objects.create(
        owner=user,
        kind=Document.Kind.COVER_LETTER,
        label="Letter",
        file=upload,
        original_filename=upload.name,
        size=upload.size,
    )
    response = auth_client.get(reverse("document-download", args=[document.pk]))
    assert response["Content-Disposition"].startswith("attachment")
    response.close()


def test_no_public_media_url(client, cv):
    """There is no MEDIA_URL route: guessing the stored path finds nothing."""
    assert not settings.MEDIA_URL or settings.MEDIA_URL == "/"
    for prefix in ("/media/", "/static/", "/"):
        response = client.get(prefix + cv.file.name)
        assert response.status_code in (302, 404)
        if response.status_code == 302:
            assert response["Location"].startswith(reverse("login"))


def test_missing_file_returns_404(auth_client, cv):
    cv.file.storage.delete(cv.file.name)
    response = auth_client.get(reverse("document-download", args=[cv.pk]))
    assert response.status_code == 404


def test_upload_rejects_disallowed_extension(auth_client):
    response = auth_client.post(
        reverse("document-upload"),
        {"kind": "cv", "file": SimpleUploadedFile("cv.exe", b"MZ...")},
    )
    assert response.status_code == 200
    assert "Upload a PDF or DOCX file." in response.content.decode()
    assert not Document.objects.exists()


def test_upload_rejects_fake_pdf(auth_client):
    response = auth_client.post(
        reverse("document-upload"),
        {"kind": "cv", "file": SimpleUploadedFile("cv.pdf", b"<html>not a pdf</html>")},
    )
    assert "does not look like a valid PDF" in response.content.decode()
    assert not Document.objects.exists()


def test_upload_rejects_oversized_files(auth_client, settings):
    settings.DOCUMENT_MAX_UPLOAD_MB = 0
    response = auth_client.post(reverse("document-upload"), {"kind": "cv", "file": make_pdf()})
    assert "larger than 0 MB" in response.content.decode()


def test_upload_and_list(auth_client, private_media):
    response = auth_client.post(
        reverse("document-upload"), {"kind": "cv", "label": "", "file": make_pdf("Main CV.pdf")}
    )
    assert response.status_code == 302
    document = Document.objects.get()
    assert document.label == "Main CV"
    assert (private_media / document.file.name).read_bytes() == PDF_BYTES

    page = auth_client.get(reverse("document-list")).content.decode()
    assert "Main CV" in page
    assert "Not sent yet" in page


def test_document_sent_with_an_application_cannot_be_deleted(auth_client, application, cv):
    response = auth_client.post(reverse("document-delete", args=[cv.pk]))
    assert response.status_code == 302
    assert Document.objects.filter(pk=cv.pk).exists()


def test_unused_document_can_be_deleted_with_its_file(
    auth_client, cv, private_media, django_capture_on_commit_callbacks
):
    path = private_media / cv.file.name
    assert path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(reverse("document-delete", args=[cv.pk]))
        assert response.status_code == 302
        assert not Document.objects.exists()
        assert path.exists()
    assert not path.exists()


@pytest.mark.parametrize("reference", ["cv", "cover_letter"])
def test_document_reference_added_after_unused_check_keeps_file(
    auth_client,
    cv,
    company,
    private_media,
    monkeypatch,
    reference,
    django_capture_on_commit_callbacks,
):
    if reference == "cover_letter":
        cv.kind = Document.Kind.COVER_LETTER
        cv.save()
    pk = cv.pk
    path = private_media / cv.file.name
    original_delete = Document.delete

    def delete_with_new_reference(document, *args, **kwargs):
        Application.objects.create(
            owner=company.owner, company=company, role_title="Engineer", **{reference: document}
        )
        return original_delete(document, *args, **kwargs)

    monkeypatch.setattr(Document, "delete", delete_with_new_reference)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = auth_client.post(reverse("document-delete", args=[pk]), follow=True)
    assert response.status_code == 200
    assert "cannot be deleted" in response.content.decode()
    assert Document.objects.filter(pk=pk).exists()
    assert path.read_bytes() == PDF_BYTES
    assert not callbacks


def test_document_delete_rollback_keeps_row_and_file(
    auth_client, cv, private_media, django_capture_on_commit_callbacks
):
    pk = cv.pk
    path = private_media / cv.file.name
    with django_capture_on_commit_callbacks(execute=True) as callbacks, transaction.atomic():
        response = auth_client.post(reverse("document-delete", args=[pk]))
        assert response.status_code == 302
        assert not Document.objects.filter(pk=pk).exists()
        assert path.exists()
        transaction.set_rollback(True)
    assert Document.objects.filter(pk=pk).exists()
    assert path.read_bytes() == PDF_BYTES
    assert not callbacks


@pytest.mark.parametrize("entry", ["library", "create", "edit"])
@pytest.mark.parametrize("kind", [Document.Kind.CV, Document.Kind.COVER_LETTER])
def test_long_filename_uses_bounded_default_label(auth_client, application, entry, kind):
    filename = "x" * 201 + ".pdf"
    if entry == "library":
        url = reverse("document-upload")
        data = {"kind": kind, "file": make_pdf(filename)}
    else:
        url = (
            reverse("application-create")
            if entry == "create"
            else reverse("application-edit", args=[application.pk])
        )
        data = {
            "company_name": application.company.name,
            "role_title": "Engineer",
            "status": "applied",
            f"new_{kind}": make_pdf(filename),
        }
    response = auth_client.post(url, data)
    assert response.status_code == 302
    document = Document.objects.get(original_filename=filename)
    assert document.label == "x" * 200
    assert document.kind == kind
    response = auth_client.get(reverse("document-download", args=[document.pk]))
    assert b"".join(response.streaming_content) == PDF_BYTES
    response.close()


def test_create_document_bounds_original_filename(private_media, user):
    from tracker.forms import create_document

    upload = make_pdf()
    upload._name = "x" * 300 + ".pdf"
    document = create_document(user, Document.Kind.CV, upload)
    document.refresh_from_db()
    assert document.label == "x" * 200
    assert document.original_filename == "x" * 251 + ".pdf"
    assert document.extension == "pdf"
    assert (private_media / document.file.name).read_bytes() == PDF_BYTES


def test_deleting_an_account_removes_its_document_files(
    user, cv, application, private_media, django_capture_on_commit_callbacks
):
    path = private_media / cv.file.name
    with django_capture_on_commit_callbacks(execute=True):
        user.delete()
    assert not Document.objects.exists()
    assert not path.exists()
