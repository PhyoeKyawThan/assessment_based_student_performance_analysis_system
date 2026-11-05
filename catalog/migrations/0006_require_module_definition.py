import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0005_moduledefinition_curriculumversion_cohort_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='module',
            name='definition',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='offerings',
                to='catalog.moduledefinition',
            ),
        ),
    ]
