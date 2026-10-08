"""Try the demo: a private sandbox per visitor, its limits, leaving, and expiry."""

import os
import stat
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from demo import sandboxes, seed
from demo.models import Sandbox
from tracker.models import Application, Company, Document
from tracker.tests.test_tenancy import OBJECT_KINDS, VALID_POST

pytestmark = pytest.mark.django_db

TRY = "/try/"


def start(client, address="192.0.2.10"):
    """Press "Try the demo" as a visitor from `address`."""
    return client.post(TRY, REMOTE_ADDR=address, headers={"CF-Connecting-IP": address})


def started(address="192.0.2.10"):
    """A fresh visitor's browser, signed in to a new sandbox, and that sandbox."""
    client = Client()
    response = start(client, address)
    assert response.status_code == 302, response.content.decode()
    return client, Sandbox.objects.get(user_id=client.session["_auth_user_id"])


def stored_files(media):
    return sorted(path for path in media.rglob("*") if path.is_file()) if media.exists() else []


def test_the_demo_is_off_without_demo_mode(client):
    assert client.get(TRY).status_code == 404
    assert client.post(TRY).status_code == 404
    assert client.post(reverse("demo-leave")).status_code == 404
    assert not Sandbox.objects.exists()


def test_a_visitor_without_a_session_lands_on_try(client, demo_mode):
    response = client.get("/")

    assert response.status_code == 302
    assert response["Location"] == "/try/?next=/"


def test_try_page_states_what_happens_to_the_data(client, demo_mode, settings):
    settings.DEMO_LOG_RETENTION_DAYS = 7

    page = client.get(TRY).content.decode()

    assert "Everything in it is fictional" in page
    assert "It is deleted after 24 hours, or as soon as you leave the demo." in page
    assert "record your IP address to protect the demo from abuse, and keep it for 7 days" in page
    assert f"{len(seed.APPLICATIONS)} sample applications" in page


def test_try_makes_a_private_seeded_sandbox_and_signs_in(demo_mode, caplog):
    caplog.set_level("INFO", logger="demo.sandboxes")
    client, sandbox = started()
    user = sandbox.user

    assert user.username.startswith("demo-")
    assert user.username == sandbox.name
    assert not user.has_usable_password()
    assert sandbox.client_ip == "192.0.2.10"
    assert sandbox.expires_at - sandbox.created_at == timedelta(hours=24)
    assert Application.objects.filter(owner=user).count() == len(seed.APPLICATIONS)
    page = client.get("/").content.decode()
    assert "Your demo workspace is ready." in page
    assert "Demo workspace, deleted in 24 hours" in page
    assert "Leave the demo" in page
    assert "Log out" not in page
    assert f"Sandbox {sandbox.name} created for 192.0.2.10 (1 active)" in caplog.text


def test_try_again_goes_back_to_the_same_workspace(demo_mode):
    client, sandbox = started()

    assert client.get(TRY)["Location"] == "/"
    assert start(client)["Location"] == "/"
    assert Sandbox.objects.get() == sandbox


def test_leaving_deletes_the_workspace_at_once(
    demo_mode, caplog, private_media, django_capture_on_commit_callbacks
):
    caplog.set_level("INFO", logger="demo.sandboxes")
    client, sandbox = started()
    user_id = sandbox.user_id
    assert len(stored_files(private_media)) == len(seed.DOCUMENTS)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("demo-leave"))

    assert response["Location"] == "/try/?left=1"
    assert not get_user_model().objects.filter(pk=user_id).exists()
    assert not Application.objects.exists()
    assert not Company.objects.exists()
    assert not Document.objects.exists()
    assert stored_files(private_media) == []
    sandbox.refresh_from_db()
    assert sandbox.user is None  # kept until it expires, so the rate limit still counts it
    assert f"Sandbox {sandbox.name} left (made for 192.0.2.10)" in caplog.text
    assert "You left the demo" in client.get(response["Location"]).content.decode()
    assert client.get("/")["Location"] == "/try/?next=/"


def test_an_expired_sandbox_signs_the_visitor_out(demo_mode):
    client, sandbox = started()
    Sandbox.objects.filter(pk=sandbox.pk).update(expires_at=timezone.now())

    response = client.get(reverse("follow-up-list"))

    assert response["Location"] == "/try/?expired=1"
    page = client.get(response["Location"]).content.decode()
    assert "Your demo workspace expired and was deleted." in page
    assert "_auth_user_id" not in client.session


def test_a_deleted_sandbox_sends_the_visitor_to_the_expired_page(demo_mode):
    client, _ = started()
    sandboxes.expire_due(timezone.now() + timedelta(hours=25))

    response = client.get(reverse("document-list"))

    assert response["Location"] == "/try/?expired=1"
    # Told once: the next visit is an ordinary one.
    assert client.get("/")["Location"] == "/try/?next=/"


def test_the_demo_refuses_new_sandboxes_when_full(demo_mode, caplog):
    demo_mode.SANDBOX_MAX_ACTIVE = 1
    started("192.0.2.10")

    response = start(Client(), "198.51.100.7")

    assert response.status_code == 503
    assert 60 <= int(response["Retry-After"]) <= 24 * 3600 + 1
    assert "The demo is full right now" in response.content.decode()
    assert "Sandbox refused for 198.51.100.7: 1 active, the cap" in caplog.text
    assert Sandbox.objects.count() == 1


def test_a_left_sandbox_frees_its_place(demo_mode, django_capture_on_commit_callbacks):
    demo_mode.SANDBOX_MAX_ACTIVE = 1
    client, _ = started("192.0.2.10")
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("demo-leave"))

    assert start(Client(), "198.51.100.7").status_code == 302


def test_one_address_may_start_a_few_sandboxes_an_hour(demo_mode, caplog):
    demo_mode.CLIENT_IP_HEADER = "CF-Connecting-IP"
    demo_mode.SANDBOX_RATE_LIMIT = 2
    started("192.0.2.10")
    started("192.0.2.10")

    response = start(Client(), "192.0.2.10")

    assert response.status_code == 429
    assert 3500 <= int(response["Retry-After"]) <= 3601
    page = response.content.decode()
    assert "You started 2 demo workspaces in the last hour" in page
    assert "Sandbox refused for 192.0.2.10: 2 started in the last hour, the limit" in caplog.text
    assert start(Client(), "198.51.100.7").status_code == 302


def test_leaving_does_not_reset_the_rate_limit(demo_mode, django_capture_on_commit_callbacks):
    demo_mode.SANDBOX_RATE_LIMIT = 1
    client, _ = started()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("demo-leave"))

    assert start(client).status_code == 429


@pytest.mark.parametrize(
    "header_setting,header,peer,expected",
    [
        ("", "198.51.100.7", "192.0.2.10", "192.0.2.10"),
        ("CF-Connecting-IP", "198.51.100.7", "192.0.2.10", "198.51.100.7"),
        ("CF-Connecting-IP", "198.51.100.7, 203.0.113.9", "192.0.2.10", "198.51.100.7"),
        ("CF-Connecting-IP", "", "192.0.2.10", "192.0.2.10"),
        ("CF-Connecting-IP", "not an address", "192.0.2.10", ""),
        ("CF-Connecting-IP", "2001:db8::1", "192.0.2.10", "2001:db8::1"),
    ],
)
def test_client_ip(settings, header_setting, header, peer, expected):
    settings.CLIENT_IP_HEADER = header_setting
    headers = {"CF-Connecting-IP": header} if header else {}
    request = RequestFactory().get("/", REMOTE_ADDR=peer, headers=headers)

    assert sandboxes.client_ip(request) == expected


def test_sandboxes_cannot_see_each_other(demo_mode):
    visitor, mine = started("192.0.2.10")
    _, theirs = started("198.51.100.7")
    objects = {
        "application": Application.objects.filter(owner=theirs.user),
        "company": Company.objects.filter(owner=theirs.user),
        "document": Document.objects.filter(owner=theirs.user),
    }
    before = {
        kind: sorted(queryset.values_list("pk", "updated_at" if kind != "document" else "label"))
        for kind, queryset in objects.items()
    }

    for route, kind in OBJECT_KINDS.items():
        for pk in objects[kind].values_list("pk", flat=True):
            url = reverse(route, args=[pk])
            # 405 is a GET-only or POST-only URL, which never reaches the object.
            assert visitor.get(url).status_code in (404, 405), url
            assert visitor.post(url, VALID_POST).status_code in (404, 405), url

    page = visitor.get("/").content.decode()
    for pk in objects["application"].values_list("pk", flat=True):
        assert reverse("application-detail", args=[pk]) not in page
    after = {
        kind: sorted(queryset.values_list("pk", "updated_at" if kind != "document" else "label"))
        for kind, queryset in objects.items()
    }
    assert after == before
    assert Application.objects.filter(owner=mine.user).count() == len(seed.APPLICATIONS)


def test_a_failed_seed_leaves_nothing_behind(demo_mode, monkeypatch, private_media):
    original = seed.seed

    def seed_then_fail(user, **kwargs):
        original(user, **kwargs)
        raise RuntimeError("disk full")

    monkeypatch.setattr(seed, "seed", seed_then_fail)

    with pytest.raises(RuntimeError):
        sandboxes.create("192.0.2.10")

    assert not Sandbox.objects.exists()
    assert not get_user_model().objects.exists()
    assert stored_files(private_media) == []


def test_expire_sandboxes_deletes_only_expired_ones(
    demo_mode, caplog, capsys, private_media, django_capture_on_commit_callbacks
):
    caplog.set_level("INFO", logger="demo.sandboxes")
    _, old = started("192.0.2.10")
    _, current = started("198.51.100.7")
    Sandbox.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    with django_capture_on_commit_callbacks(execute=True):
        call_command("expire_sandboxes")

    assert "Deleted 1 sandbox(es); 1 active." in capsys.readouterr().out
    assert list(Sandbox.objects.all()) == [current]
    assert not get_user_model().objects.filter(pk=old.user_id).exists()
    assert set(Document.objects.values_list("owner", flat=True)) == {current.user_id}
    assert len(stored_files(private_media)) == len(seed.DOCUMENTS)
    assert f"Sandbox {old.name} expired (made for 192.0.2.10): 15 applications deleted" in (
        caplog.text
    )


def test_expire_sandboxes_all_clears_the_demo(
    demo_mode, capsys, private_media, django_capture_on_commit_callbacks
):
    started("192.0.2.10")
    started("198.51.100.7")

    with django_capture_on_commit_callbacks(execute=True):
        call_command("expire_sandboxes", "--all")

    assert "Deleted 2 sandbox(es); 0 active." in capsys.readouterr().out
    assert not Sandbox.objects.exists()
    assert not get_user_model().objects.exists()
    assert stored_files(private_media) == []


def test_scheduler_runs_both_housekeeping_commands(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    fake = bin_dir / "python"
    fake.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n[ "$2" != expire_sandboxes ]\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    script = Path(settings.BASE_DIR) / "docker" / "scheduler.sh"

    with pytest.raises(subprocess.TimeoutExpired) as stopped:
        subprocess.run(
            ["sh", str(script)],
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "SCHEDULER_INTERVAL_SECONDS": "1",
            },  # fmt: skip
            capture_output=True,
            text=True,
            timeout=2.5,
        )

    runs = calls.read_text().splitlines()
    assert runs[:4] == [
        "manage.py expire_sandboxes",
        "manage.py clearsessions",
        "manage.py expire_sandboxes",
        "manage.py clearsessions",
    ]
    # A failing command is reported and the loop carries on.
    assert b"expire_sandboxes failed (exit 1)" in stopped.value.stderr


def test_main_site_strip_links_back(client, demo_mode):
    demo_mode.MAIN_SITE_URL = "https://portfolio.example"

    page = client.get(TRY).content.decode()

    assert '<a class="main-site-link" href="https://portfolio.example"' in page
