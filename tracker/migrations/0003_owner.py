from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the owning account to companies, documents and applications.

    The column starts out nullable so 0004 can assign the rows that already
    exist; 0005 then makes it required. They are separate migrations because
    PostgreSQL refuses to alter a table in the same transaction that updated
    rows with a deferred foreign key.
    """

    dependencies = [
        ('tracker', '0002_posting_text_and_follow_up_events'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=models.CASCADE, related_name='companies', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='document',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=models.CASCADE, related_name='documents', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='application',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=models.CASCADE, related_name='applications', to=settings.AUTH_USER_MODEL),
        ),
    ]
