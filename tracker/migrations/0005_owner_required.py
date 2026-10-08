import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Make the owner required and company names unique per owner instead of globally.

    Applications now RESTRICT rather than PROTECT their company and documents,
    so deleting an account can delete everything it owns. That is enforced by
    Django, not the database, so it changes no SQL.
    """

    dependencies = [
        ('tracker', '0004_assign_existing_data_owner'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='owner',
            field=models.ForeignKey(on_delete=models.CASCADE, related_name='companies', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='document',
            name='owner',
            field=models.ForeignKey(on_delete=models.CASCADE, related_name='documents', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='application',
            name='owner',
            field=models.ForeignKey(on_delete=models.CASCADE, related_name='applications', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='application',
            name='company',
            field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='applications', to='tracker.company'),
        ),
        migrations.AlterField(
            model_name='application',
            name='cv',
            field=models.ForeignKey(blank=True, limit_choices_to={'kind': 'cv'}, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='cv_applications', to='tracker.document', verbose_name='CV sent'),
        ),
        migrations.AlterField(
            model_name='application',
            name='cover_letter',
            field=models.ForeignKey(blank=True, limit_choices_to={'kind': 'cover_letter'}, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='cover_letter_applications', to='tracker.document', verbose_name='cover letter sent'),
        ),
        migrations.RemoveConstraint(
            model_name='company',
            name='unique_company_name_ci',
        ),
        migrations.AddConstraint(
            model_name='company',
            constraint=models.UniqueConstraint(models.F('owner'), django.db.models.functions.text.Lower('name'), name='unique_company_name_per_owner_ci', violation_error_message='A company with this name already exists.'),
        ),
    ]
