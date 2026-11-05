from django.db import migrations, models


def set_initial_period_status(apps, schema_editor):
    Semester = apps.get_model('catalog', 'Semester')
    Semester.objects.filter(academic__is_current=True).update(status='ACTIVE')
    Semester.objects.filter(academic__is_current=False).update(status='ARCHIVED')


class Migration(migrations.Migration):
    dependencies = [('catalog', '0006_require_module_definition')]

    operations = [
        migrations.AddField(
            model_name='semester',
            name='status',
            field=models.CharField(
                choices=[
                    ('DRAFT', 'Draft'), ('ACTIVE', 'Active'),
                    ('CLOSED', 'Closed'), ('ARCHIVED', 'Archived'),
                ],
                default='DRAFT', max_length=20,
            ),
        ),
        migrations.RunPython(set_initial_period_status, migrations.RunPython.noop),
    ]
