"""Give the companies, documents and applications that already exist an owner.

See "Upgrading from a version without separate accounts" in README.md.

Before accounts were separated the tracker had one user, so existing rows go
to the only account. If there is no account, or more than one, the migration
stops with a message saying what to do: create the account, or set
EXISTING_DATA_OWNER to the username that should own the data. A fresh
database with no data needs neither.
"""

import logging
import os

from django.conf import settings
from django.core.management.base import CommandError
from django.db import migrations

logger = logging.getLogger("tracker.migrations")

OWNER_VARIABLE = "EXISTING_DATA_OWNER"
MODELS = ["Company", "Document", "Application"]


def existing_data_owner(User):
    username = os.environ.get(OWNER_VARIABLE, "").strip()
    if username:
        owner = User.objects.filter(username=username).first()
        if owner is None:
            raise CommandError(
                f"{OWNER_VARIABLE} is {username!r}, but there is no account with that "
                "username. Fix it and run `manage.py migrate` again."
            )
        return owner
    accounts = list(User.objects.order_by("pk"))
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise CommandError(
            "The tracker has data but no account to own it. Create your account with "
            "`manage.py createsuperuser`, then run `manage.py migrate` again."
        )
    names = ", ".join(account.username for account in accounts)
    raise CommandError(
        f"The existing data needs one owner, but there are {len(accounts)} accounts "
        f"({names}). Set {OWNER_VARIABLE} to the username that should own it, then "
        "run `manage.py migrate` again."
    )


def assign_existing_rows(apps, schema_editor):
    unowned = {
        name: apps.get_model("tracker", name).objects.filter(owner__isnull=True)
        for name in MODELS
    }
    if not any(rows.exists() for rows in unowned.values()):
        return
    owner = existing_data_owner(apps.get_model(settings.AUTH_USER_MODEL))
    counts = {name: rows.update(owner=owner) for name, rows in unowned.items()}
    logger.info(
        "Assigned existing data to account %r: %s",
        owner.username,
        ", ".join(f"{name.lower()} {count}" for name, count in counts.items()),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0003_owner'),
    ]

    operations = [
        migrations.RunPython(assign_existing_rows, migrations.RunPython.noop, elidable=True),
    ]
