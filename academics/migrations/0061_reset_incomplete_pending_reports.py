"""
Data fix: subject-level submission used to flip a learner's whole report to
"pending sign-off" even when other subjects were still empty. Move such
non-ECD, unpublished reports back to draft unless every class subject has
scores that are all submitted or approved.
"""
from django.db import migrations

READY = {"submitted", "approved"}


def reset_incomplete(apps, schema_editor):
    ReportCard = apps.get_model("academics", "ReportCard")
    ExamScore = apps.get_model("academics", "ExamScore")
    GradeClass = apps.get_model("academics", "GradeClass")

    expected_cache = {}

    def expected(class_name):
        if class_name not in expected_cache:
            gc = GradeClass.objects.filter(name=class_name).first()
            expected_cache[class_name] = (
                set(gc.subjects.filter(is_active=True).values_list("name", flat=True)) if gc else set()
            )
        return expected_cache[class_name]

    pending = ReportCard.objects.filter(
        status="pending_sign_off", is_ecd_report=False,
    ).select_related("student")
    to_reset = []
    for rc in pending:
        statuses = {}
        for subject, status in ExamScore.objects.filter(
            student_id=rc.student_id, term_id=rc.term_id,
        ).values_list("subject_name", "status"):
            statuses.setdefault(subject, set()).add(status)
        subjects = expected(rc.student.class_name) | set(statuses)
        ready = bool(subjects) and all(
            statuses.get(name) and statuses[name] <= READY for name in subjects
        )
        if not ready:
            to_reset.append(rc.pk)
    if to_reset:
        ReportCard.objects.filter(pk__in=to_reset).update(status="draft")


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0060_remove_lessonplan_idx_lp_teacher_and_more"),
    ]

    operations = [
        migrations.RunPython(reset_incomplete, migrations.RunPython.noop),
    ]
