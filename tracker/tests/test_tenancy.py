"""Each account sees and changes only its own companies, documents and applications.

The 404 test walks every tracker URL that takes an object id, so a new URL
fails here until it says which kind of object its id is.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from tracker import urls
from tracker.models import Application, ApplicationEvent, Company, Document, Status

from .conftest import make_docx, make_pdf

pytestmark = pytest.mark.django_db

# The kind of object each object URL's `pk` refers to.
OBJECT_KINDS = {
    "application-detail": "application",
    "application-edit": "application",
    "application-status": "application",
    "application-note": "application",
    "application-delete": "application",
    "follow-up-done": "application",
    "follow-up-snooze": "application",
    "company-detail": "company",
    "company-edit": "company",
    "company-delete": "company",
    "document-download": "document",
    "document-delete": "document",
}

# A body every form behind those URLs would accept, so a missing owner check
# would show up as a change to the other account's data.
VALID_POST = {
    "company_name": "Initech",
    "role_title": "Changed role",
    "status": Status.OFFER,
    "note": "Changed",
    "message": "Changed",
    "days": "7",
    "name": "Changed name",
}


def object_routes():
    return [p.name for p in urls.urlpatterns if "pk" in p.pattern.converters]


def make_account_data(owner, name):
    """A company, a CV and cover letter, and an application due a follow-up."""
    company = Company.objects.create(owner=owner, name=f"{name} Industries")
    upload = make_pdf(f"{name} CV.pdf")
    cv = Document.objects.create(
        owner=owner,
        kind=Document.Kind.CV,
        label=f"{name} CV",
        file=upload,
        original_filename=upload.name,
        size=upload.size,
    )
    upload = make_docx(f"{name} letter.docx")
    letter = Document.objects.create(
        owner=owner,
        kind=Document.Kind.COVER_LETTER,
        label=f"{name} letter",
        file=upload,
        original_filename=upload.name,
        size=upload.size,
    )
    yesterday = timezone.localdate() - timedelta(days=1)
    application = Application.objects.create(
        owner=owner,
        company=company,
        role_title=f"{name} Engineer",
        posting_url=f"https://jobs.example/{name.lower()}",
        status=Status.APPLIED,
        date_applied=yesterday,
        next_follow_up=yesterday,
        source=f"{name} referral",
        cv=cv,
        cover_letter=letter,
    )
    application.events.create(kind=ApplicationEvent.Kind.NOTE, message=f"{name} note")
    # An application no document points at, so the document delete URL would succeed.
    spare_upload = make_pdf(f"{name} spare.pdf")
    spare = Document.objects.create(
        owner=owner,
        kind=Document.Kind.CV,
        label=f"{name} spare CV",
        file=spare_upload,
        original_filename=spare_upload.name,
        size=spare_upload.size,
    )
    # A company with no applications, so the company delete URL would succeed.
    empty = Company.objects.create(owner=owner, name=f"{name} Empty Co")
    return {"application": application, "company": empty, "document": spare, "letter": letter}


@pytest.fixture
def mine(user):
    return make_account_data(user, "Mine")


@pytest.fixture
def other(django_user_model):
    return django_user_model.objects.create_user(username="sam", password="another horse 42")


@pytest.fixture
def theirs(other):
    return make_account_data(other, "Theirs")


def snapshot(owner):
    """Everything about an account's data that a request could change."""
    return {
        "companies": sorted(Company.objects.filter(owner=owner).values_list("name", "notes")),
        "documents": sorted(Document.objects.filter(owner=owner).values_list("label", "file")),
        "applications": sorted(
            Application.objects.filter(owner=owner).values_list(
                "role_title", "status", "next_follow_up", "updated_at"
            )
        ),
        "events": sorted(
            ApplicationEvent.objects.filter(application__owner=owner).values_list("kind", "message")
        ),
    }


def test_every_object_url_is_covered():
    assert sorted(object_routes()) == sorted(OBJECT_KINDS)


@pytest.mark.parametrize("route", sorted(OBJECT_KINDS))
def test_object_urls_answer_404_for_another_accounts_objects(
    auth_client, mine, theirs, other, route
):
    before = snapshot(other)
    kind = OBJECT_KINDS[route]
    url = reverse(route, args=[theirs[kind].pk])
    missing = reverse(route, args=[999_999])

    statuses = []
    for method in (auth_client.get, auth_client.post):
        response = method(url, VALID_POST)
        if response.streaming:
            response.close()
        # Another account's object looks exactly like one that does not exist:
        # 404, or 405 for a method the URL does not take.
        assert response.status_code == method(missing, VALID_POST).status_code
        statuses.append(response.status_code)
    assert 404 in statuses
    assert set(statuses) <= {404, 405}

    assert snapshot(other) == before
    for document in Document.objects.filter(owner=other):
        assert document.file.storage.exists(document.file.name)


@pytest.mark.parametrize("route", sorted(OBJECT_KINDS))
def test_object_urls_work_for_your_own_objects(auth_client, mine, route):
    """The positive control: the same requests do reach your own objects."""
    url = reverse(route, args=[mine[OBJECT_KINDS[route]].pk])
    response = auth_client.post(url, VALID_POST)
    if response.status_code == 405:
        response = auth_client.get(url)
    if response.streaming:
        response.close()
    assert response.status_code in (200, 302)


@pytest.mark.parametrize(
    "route",
    ["application-list", "follow-up-list", "weekly-summary", "company-list", "document-list"],
)
def test_list_pages_show_only_your_own_data(auth_client, mine, theirs, route):
    page = auth_client.get(reverse(route)).content.decode()
    assert "Mine" in page
    assert "Theirs" not in page


def test_navigation_counts_only_your_own_reminders(auth_client, mine, theirs):
    response = auth_client.get(reverse("company-list"))
    assert response.context["follow_up_count"]() == 1


def test_overview_search_and_status_counts_ignore_other_accounts(auth_client, mine, theirs):
    response = auth_client.get(reverse("application-list"), {"q": "Engineer"})
    assert [a.role_title for a in response.context["applications"]] == ["Mine Engineer"]
    counts = {value: count for value, _, count in response.context["status_filters"]}
    assert counts[""] == 1
    assert counts[Status.APPLIED] == 1


def test_new_application_form_offers_only_your_own_data(auth_client, mine, theirs):
    page = auth_client.get(
        reverse("application-create"), {"company": theirs["application"].company.pk}
    ).content.decode()
    assert "Mine Industries" in page
    assert "Mine referral" in page
    assert "Mine CV" in page
    assert "Theirs" not in page


def test_cannot_attach_another_accounts_document(auth_client, mine, theirs):
    their_cv = theirs["application"].cv
    response = auth_client.post(
        reverse("application-create"),
        {"company_name": "Acme", "role_title": "Y", "status": "applied", "cv": their_cv.pk},
    )
    assert response.status_code == 200
    assert "cv" in response.context["form"].errors
    assert not Application.objects.filter(role_title="Y").exists()


def test_same_company_name_creates_your_own_company(auth_client, user, mine, theirs, other):
    response = auth_client.post(
        reverse("application-create"),
        {"company_name": "theirs industries", "role_title": "Copycat", "status": "applied"},
    )
    assert response.status_code == 302
    application = Application.objects.get(role_title="Copycat")
    assert application.owner == user
    assert application.company.owner == user
    assert application.company != theirs["application"].company
    assert Company.objects.filter(owner=other).count() == 2


def test_company_rename_may_reuse_another_accounts_name(auth_client, mine, theirs):
    company = mine["company"]
    response = auth_client.post(
        reverse("company-edit", args=[company.pk]), {"name": "Theirs Industries"}
    )
    assert response.status_code == 302
    company.refresh_from_db()
    assert company.name == "Theirs Industries"


def test_company_rename_rejects_your_own_duplicate(auth_client, mine):
    company = mine["company"]
    response = auth_client.post(
        reverse("company-edit", args=[company.pk]), {"name": "mine industries"}
    )
    assert response.status_code == 200
    assert response.context["form"].errors["name"] == ["A company with this name already exists."]


def test_upload_belongs_to_the_uploader(auth_client, user):
    auth_client.post(
        reverse("document-upload"), {"kind": "cv", "label": "Fresh", "file": make_pdf()}
    )
    assert Document.objects.get(label="Fresh").owner == user


def test_capture_warns_only_about_your_own_duplicates(auth_client, mine, theirs, serve_posting):
    serve_posting("not-a-posting.html")
    response = auth_client.get(
        reverse("application-create"), {"url": theirs["application"].posting_url}
    )
    assert response.context["capture"].duplicates == []

    response = auth_client.get(
        reverse("application-create"), {"url": mine["application"].posting_url}
    )
    assert response.context["capture"].duplicates == [mine["application"]]


def test_weekly_summary_email_covers_only_the_recipients_data(settings, user, other, mine, theirs):
    settings.WEEKLY_SUMMARY_EMAIL_ENABLED = True
    user.email = "me@example.com"
    user.save()
    other.email = "sam@example.com"
    other.save()

    call_command("send_weekly_summary")

    bodies = {email.to[0]: email.body for email in mail.outbox}
    assert "Mine Engineer" in bodies["me@example.com"]
    assert "Theirs" not in bodies["me@example.com"]
    assert "Theirs Engineer" in bodies["sam@example.com"]
    assert "Mine" not in bodies["sam@example.com"]


def test_deleting_an_account_deletes_only_its_data(user, other, mine, theirs):
    other.delete()

    assert not Company.objects.filter(owner_id=other.pk).exists()
    assert not Document.objects.filter(owner_id=other.pk).exists()
    assert not Application.objects.filter(owner_id=other.pk).exists()
    assert Application.objects.filter(owner=user).count() == 1
    assert Document.objects.filter(owner=user).count() == 3
