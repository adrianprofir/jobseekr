"""In the demo each account holds a bounded amount of data; self-hosted accounts do not."""

import pytest
from django.urls import reverse

from tracker.forms import DEMO_TEXT_LIMITS
from tracker.models import Application, Company

pytestmark = pytest.mark.django_db


def new_application(company="Initech", **extra):
    return {"company_name": company, "role_title": "Developer", "status": "applied", **extra}


def add_applications(user, count):
    company, _ = Company.objects.get_or_create(owner=user, name="Filler")
    Application.objects.bulk_create(
        Application(owner=user, company=company, role_title=f"Role {i}") for i in range(count)
    )


def test_application_cap(auth_client, demo_mode, user):
    demo_mode.SANDBOX_MAX_APPLICATIONS = 3
    add_applications(user, 3)

    response = auth_client.post(reverse("application-create"), new_application("Filler"))

    assert response.status_code == 200
    assert "A demo account holds at most 3 applications." in response.content.decode()
    assert Application.objects.count() == 3


def test_application_cap_still_allows_editing(auth_client, demo_mode, user):
    demo_mode.SANDBOX_MAX_APPLICATIONS = 1
    add_applications(user, 1)
    application = Application.objects.get()

    response = auth_client.post(
        reverse("application-edit", args=[application.pk]), new_application("Filler")
    )

    assert response.status_code == 302


def test_company_cap(auth_client, demo_mode, user):
    demo_mode.SANDBOX_MAX_COMPANIES = 2
    Company.objects.create(owner=user, name="Acme")
    Company.objects.create(owner=user, name="Globex")

    response = auth_client.post(reverse("application-create"), new_application("Initech"))
    assert "A demo account holds at most 2 companies." in response.content.decode()
    assert not Application.objects.exists()

    response = auth_client.post(reverse("application-create"), new_application("acme"))
    assert response.status_code == 302


def test_caps_only_apply_in_the_demo(auth_client, settings, user):
    settings.SANDBOX_MAX_APPLICATIONS = 1
    add_applications(user, 1)

    response = auth_client.post(reverse("application-create"), new_application())

    assert response.status_code == 302


@pytest.mark.parametrize("field", ["notes", "posting_text"])
def test_long_application_text_is_refused_in_the_demo(auth_client, demo_mode, field):
    limit = DEMO_TEXT_LIMITS[field]

    response = auth_client.post(
        reverse("application-create"), new_application(**{field: "x" * (limit + 1)})
    )

    assert response.status_code == 200
    assert f"at most {limit} characters" in response.content.decode()
    assert not Application.objects.exists()


def test_long_note_is_refused_in_the_demo(auth_client, demo_mode, application):
    limit = DEMO_TEXT_LIMITS["message"]

    response = auth_client.post(
        reverse("application-note", args=[application.pk]),
        {"message": "x" * (limit + 1)},
        follow=True,
    )

    assert f"at most {limit} characters" in response.content.decode()
    assert not application.events.exists()


def test_long_company_notes_are_refused_in_the_demo(auth_client, demo_mode, company):
    limit = DEMO_TEXT_LIMITS["notes"]

    response = auth_client.post(
        reverse("company-edit", args=[company.pk]), {"name": "Acme", "notes": "x" * (limit + 1)}
    )

    assert response.status_code == 200
    assert f"at most {limit} characters" in response.content.decode()


def test_long_text_is_fine_when_self_hosted(auth_client):
    response = auth_client.post(reverse("application-create"), new_application(notes="x" * 50_000))

    assert response.status_code == 302
