import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from tracker.capture.fetch import FetchedPage, FetchError
from tracker.capture.samples import POSTINGS
from tracker.models import Application, Company, Document

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 64


@pytest.fixture(autouse=True)
def private_media(settings, tmp_path):
    """Store uploads in a per-test directory instead of the real media root.

    Static files use the plain storage so tests do not need collectstatic.
    """
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": tmp_path / "media", "base_url": None},
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    return tmp_path / "media"


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="me", password="correct horse 42")


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


def make_pdf(name="cv.pdf"):
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


def make_docx(name="letter.docx"):
    return SimpleUploadedFile(
        name,
        DOCX_BYTES,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Acme", website="https://acme.example")


@pytest.fixture
def cv(user):
    upload = make_pdf("Jane Doe CV.pdf")
    return Document.objects.create(
        owner=user,
        kind=Document.Kind.CV,
        label="CV backend",
        file=upload,
        original_filename=upload.name,
        size=upload.size,
    )


@pytest.fixture
def application(company, cv):
    return Application.objects.create(
        owner=company.owner,
        company=company,
        role_title="Backend Engineer",
        posting_url="https://acme.example/jobs/1",
        status="applied",
        cv=cv,
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never fetch real pages. Use `serve_posting` to hand the capture a page."""

    def refuse(url):
        raise FetchError("Network access is disabled in tests.")

    monkeypatch.setattr("tracker.capture.fetch_page", refuse)


@pytest.fixture
def serve_posting(monkeypatch):
    """Make the capture receive a synthetic page from tracker/capture/postings."""
    requested = []

    def serve(fixture, final_url=None):
        def fetch(url):
            requested.append(url)
            content = (POSTINGS / fixture).read_bytes()
            return FetchedPage(url=final_url or url, content=content, charset=None)

        monkeypatch.setattr("tracker.capture.fetch_page", fetch)
        return requested

    return serve


@pytest.fixture
def demo_mode(settings):
    """Settings as config/settings.py derives them from DEMO_MODE=true."""
    settings.DEMO_MODE = True
    settings.CAPTURE_MODE = "samples"
    settings.DOCUMENT_UPLOADS_ENABLED = False
    settings.FILE_UPLOAD_HANDLERS = ["tracker.uploads.SkipFilesUploadHandler"]
    settings.LOGIN_URL = "demo-try"
    settings.MAIN_SITE_URL = ""
    return settings
