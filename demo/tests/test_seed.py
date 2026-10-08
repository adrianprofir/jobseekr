"""The seed bundle gives every page something to show, with only fictional data."""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from demo import pdf, seed
from tracker.forms import validate_document_file
from tracker.models import Application, ApplicationEvent, Company, Document, Status
from tracker.reminders import due_reminders, upcoming_reminders

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded(user):
    seed.seed(user)
    return user


def test_every_status_and_every_kind_of_reminder(seeded):
    applications = Application.objects.filter(owner=seeded)

    assert applications.count() == 15
    assert set(applications.values_list("status", flat=True)) == set(Status.values)
    kinds = {reminder.kind for reminder in due_reminders(seeded)}
    assert kinds == {"overdue", "today", "quiet"}
    assert len(upcoming_reminders(seeded)) >= 3


def test_every_application_has_a_history_in_the_past(seeded):
    now = timezone.now()
    for application in Application.objects.filter(owner=seeded):
        events = list(application.events.all())
        assert events, application
        assert events[-1].kind == ApplicationEvent.Kind.CREATED
        assert all(event.created_at < now for event in events)
        assert application.created_at == events[-1].created_at
    assert ApplicationEvent.objects.filter(kind=ApplicationEvent.Kind.STATUS).count() >= 8
    assert ApplicationEvent.objects.filter(kind=ApplicationEvent.Kind.NOTE).exists()


def test_only_reserved_domains(seeded):
    addresses = Application.objects.exclude(contact_email="").values_list(
        "contact_email", flat=True
    )
    assert addresses
    assert all(address.endswith(".example") for address in addresses)
    for url in [
        *Company.objects.values_list("website", flat=True),
        *Application.objects.values_list("posting_url", flat=True),
    ]:
        assert re.match(r"https://[a-z0-9.-]+\.example/", url + "/"), url


def test_three_sample_pdfs_that_open(auth_client, seeded):
    documents = Document.objects.filter(owner=seeded)

    assert documents.count() == 3
    assert set(documents.values_list("kind", flat=True)) == {"cv", "cover_letter"}
    for document in documents:
        response = auth_client.get(document.get_absolute_url())
        content = b"".join(response.streaming_content)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        validate_document_file(SimpleUploadedFile(document.original_filename, content))
        assert b"Kim Sandvik" in content


def test_pages_show_the_seed(auth_client, seeded):
    overview = auth_client.get(reverse("application-list")).content.decode()
    assert "Needs attention" in overview
    assert "Backend Developer (Python)" in overview

    follow_ups = auth_client.get(reverse("follow-up-list")).content.decode()
    for heading in ("Overdue", "Due today", "Gone quiet", "Coming up"):
        assert heading in follow_ups

    # Applied yesterday, so it is in the week the summary opens on.
    summary = auth_client.get(reverse("weekly-summary")).content.decode()
    assert "Site Reliability Engineer" in summary


def test_pdf_cross_reference_table_points_at_each_object():
    content = pdf.render([("title", "Résumé (draft)"), ("body", "Line \\ with ) brackets")])

    assert content.startswith(b"%PDF-1.4")
    assert content.rstrip().endswith(b"%%EOF")
    xref = int(re.search(rb"startxref\n(\d+)", content)[1])
    assert content[xref:].startswith(b"xref")
    offsets = re.findall(rb"(\d{10}) 00000 n ", content[xref:])
    for number, offset in enumerate(offsets, start=1):
        assert content[int(offset) :].startswith(b"%d 0 obj" % number)
    assert b"(R\xe9sum\xe9 \\(draft\\)) Tj" in content
    assert b"(Line \\\\ with \\) brackets) Tj" in content
