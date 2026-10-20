"""French is remark-only: one remark per term, no marks.

It was set up as mark-bearing, so teachers entered quiz marks against it and
those marks counted in averages and the at-risk and performance lists. Set it
to remark-only and remove the French marks already entered (their amendments
go with them). Term remarks are kept.
"""
from django.db import migrations


def make_french_remark_only(apps, schema_editor):
    Subject = apps.get_model("academics", "Subject")
    ExamScore = apps.get_model("academics", "ExamScore")
    names = list(Subject.objects.filter(name__iexact="French").values_list("name", flat=True))
    Subject.objects.filter(name__in=names).update(assessment_mode="remark")
    ExamScore.objects.filter(subject_name__in=names + ["French"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0066_lesson_plan_comment"),
    ]

    operations = [
        migrations.RunPython(make_french_remark_only, migrations.RunPython.noop),
    ]
