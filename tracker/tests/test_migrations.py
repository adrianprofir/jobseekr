"""The migration that gives existing data an owner (0004_assign_existing_data_owner)."""

import pytest
from django.core.management import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE_OWNERS = [("tracker", "0002_posting_text_and_follow_up_events")]


@pytest.fixture
def migrate(transactional_db):
    """Migrate the tracker app to a target and return the models as they were then."""

    def run(target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        other_apps = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "tracker"]
        return executor.loader.project_state([*target, *other_apps]).apps

    yield run

    # Leave the schema as the rest of the suite expects it, even after a failed run.
    with connection.cursor() as cursor:
        cursor.execute(
            "TRUNCATE tracker_applicationevent, tracker_application, tracker_document, "
            "tracker_company"
        )
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def latest(migrate):
    return migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


def create_old_data(apps):
    """One of everything, as the single-user tracker stored it."""
    company = apps.get_model("tracker", "Company").objects.create(name="Nordlys Energy")
    document = apps.get_model("tracker", "Document").objects.create(
        kind="cv", label="CV", file="documents/2026/10/cv.pdf", original_filename="cv.pdf"
    )
    application = apps.get_model("tracker", "Application").objects.create(
        company=company, role_title="Data Engineer", cv=document
    )
    application.events.create(kind="created")


def test_existing_data_goes_to_the_only_account(migrate, caplog):
    caplog.set_level("INFO", logger="tracker.migrations")
    old = migrate(BEFORE_OWNERS)
    owner = old.get_model("auth", "User").objects.create(username="me")
    create_old_data(old)

    new = latest(migrate)

    for name in ["Company", "Document", "Application"]:
        assert list(new.get_model("tracker", name).objects.values_list("owner", flat=True)) == [
            owner.pk
        ]
    assert "to account 'me': company 1, document 1, application 1" in caplog.text


def test_a_fresh_database_needs_no_account(migrate):
    migrate(BEFORE_OWNERS)

    new = latest(migrate)

    assert not new.get_model("auth", "User").objects.exists()


def test_existing_data_without_an_account_stops_the_migration(migrate):
    create_old_data(migrate(BEFORE_OWNERS))

    with pytest.raises(CommandError, match="has data but no account to own it"):
        latest(migrate)


def test_existing_data_with_several_accounts_stops_the_migration(migrate):
    old = migrate(BEFORE_OWNERS)
    users = old.get_model("auth", "User").objects
    users.create(username="me")
    users.create(username="sam")
    create_old_data(old)

    with pytest.raises(CommandError, match=r"there are 2 accounts \(me, sam\)\. Set EXISTING_DAT"):
        latest(migrate)


def test_existing_data_owner_picks_the_account(migrate, monkeypatch):
    monkeypatch.setenv("EXISTING_DATA_OWNER", "sam")
    old = migrate(BEFORE_OWNERS)
    users = old.get_model("auth", "User").objects
    users.create(username="me")
    sam = users.create(username="sam")
    create_old_data(old)

    new = latest(migrate)

    assert new.get_model("tracker", "Application").objects.get().owner_id == sam.pk


def test_existing_data_owner_must_name_an_account(migrate, monkeypatch):
    monkeypatch.setenv("EXISTING_DATA_OWNER", "nobody")
    old = migrate(BEFORE_OWNERS)
    old.get_model("auth", "User").objects.create(username="me")
    create_old_data(old)

    with pytest.raises(CommandError, match="EXISTING_DATA_OWNER is 'nobody', but there is no"):
        latest(migrate)
