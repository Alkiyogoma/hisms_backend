"""
Remark-only subjects.

Each Subject is either mark-bearing (Quiz, Mid Term, End Term) or remark-only
(one remark per term, no marks), set in the Subjects module. Remark-only
subjects never carry marks, so they are left out of averages, positions and
the marks table, and appear in a separate remarks table on reports.

A remark can be changed until the learner's report goes for sign-off; after
that the report is reviewed and published as a whole.
"""
from academics.models import (
    AssessmentMode, ReportCard, ReportCardStatus, Subject, SubjectRemark, SubjectTermRemark,
)

REMARK_CHOICES = [value for value, _ in SubjectRemark.choices]


def remark_subject_names(names=None) -> set:
    """Names of remark-only subjects (optionally limited to ``names``)."""
    qs = Subject.objects.filter(assessment_mode=AssessmentMode.REMARK)
    if names is not None:
        qs = qs.filter(name__in=list(names))
    return set(qs.values_list("name", flat=True))


def remarks_for(student, term) -> dict:
    """{subject_name: remark} for one learner and term."""
    return dict(
        SubjectTermRemark.objects.filter(student=student, term=term).values_list("subject_name", "remark")
    )


def remarks_locked(student, term) -> bool:
    """Remarks are locked once the report has gone for sign-off or is published."""
    return ReportCard.objects.filter(
        student=student, term=term,
        status__in=[ReportCardStatus.PENDING_SIGN_OFF, ReportCardStatus.PUBLISHED],
    ).exists()


def save_remarks(student, term, remarks, user, allowed_subjects=None):
    """Save ``{subject: remark}`` for remark-only subjects.

    An empty remark clears it. Returns ``(updated, errors)``; nothing is saved
    when there are errors.
    """
    from audit.models import log_event

    remarks = {k: (v or "").strip() for k, v in (remarks or {}).items()}
    if not remarks:
        return 0, []
    errors = []
    remark_subjects = remark_subject_names(remarks)
    for subject, value in remarks.items():
        if subject not in remark_subjects:
            errors.append(f"{subject} is a mark-bearing subject and does not take a remark.")
        elif allowed_subjects is not None and subject not in allowed_subjects:
            errors.append(f"You are not assigned to teach {subject} in this class.")
        elif value and value not in REMARK_CHOICES:
            errors.append(f"{subject}: choose one of {', '.join(REMARK_CHOICES)}.")
    if not errors and remarks_locked(student, term):
        errors.append("The report has gone for sign-off, so remarks can no longer be changed.")
    if errors:
        return 0, errors

    existing = {r.subject_name: r for r in SubjectTermRemark.objects.filter(
        student=student, term=term, subject_name__in=list(remarks))}
    updated = 0
    for subject, value in remarks.items():
        row = existing.get(subject)
        old = row.remark if row else ""
        if value == old:
            continue
        if not value:
            row.delete()
        elif row:
            row.remark, row.entered_by = value, user
            row.save(update_fields=["remark", "entered_by", "updated_at"])
        else:
            row = SubjectTermRemark.objects.create(
                student=student, term=term, subject_name=subject, remark=value, entered_by=user,
            )
        updated += 1
        log_event(
            actor=user,
            action_type="SUBJECT_REMARK_SAVED",
            model_name="SubjectTermRemark",
            object_id=row.pk or 0,
            description=f"{subject} remark for {student.admission_no}: {old or '—'} → {value or '—'}",
            before_value=old,
            after_value=value,
        )
    return updated, []
