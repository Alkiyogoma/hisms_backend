from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0014_alter_onboardingchecklistitem_step_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacherclassassignment",
            name="rollover_ignored",
            field=models.BooleanField(
                default=False,
                help_text="If True, this assignment will NOT be rolled over to the next term.",
            ),
        ),
    ]
