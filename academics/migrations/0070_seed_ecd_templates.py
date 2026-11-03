from django.db import migrations


def seed(apps, schema_editor):
    from academics.ecd_seed import seed_ecd_templates
    seed_ecd_templates(
        apps.get_model("academics", "ECDReportLayout"),
        apps.get_model("academics", "ECDSection"),
        apps.get_model("academics", "ECDItem"),
    )


def unseed(apps, schema_editor):
    apps.get_model("academics", "ECDSection").objects.all().delete()
    apps.get_model("academics", "ECDReportLayout").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0069_ecd_report_config'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
