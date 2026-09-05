from datetime import date


def check_assignment_conflicts(teacher_profile, term, grade_class, subject_names, exclude_assignment_pk=None):
    """
    Check for conflicts when assigning a teacher to a class+subjects for a term.
    Returns a list of warning dicts: {type, message, severity}
    """
    from hr.models import TeacherClassAssignment

    warnings = []

    # --- Contract / employment date checks ---
    if teacher_profile.contract_end_date and term.end_date and term.end_date > teacher_profile.contract_end_date:
        warnings.append({
            "type": "contract",
            "message": (
                f"Term '{term.name}' ends on {term.end_date.strftime('%b %d, %Y')}, "
                f"but {teacher_profile.full_name}'s contract expires on "
                f"{teacher_profile.contract_end_date.strftime('%b %d, %Y')}."
            ),
            "severity": "warning",
        })
    if teacher_profile.employment_start_date and term.start_date and term.start_date < teacher_profile.employment_start_date:
        warnings.append({
            "type": "contract",
            "message": (
                f"Term '{term.name}' starts on {term.start_date.strftime('%b %d, %Y')}, "
                f"but {teacher_profile.full_name}'s employment started on "
                f"{teacher_profile.employment_start_date.strftime('%b %d, %Y')}."
            ),
            "severity": "warning",
        })

    # --- Subject-slot conflict: another teacher already teaches this subject in this class+term ---
    for subject_name in (subject_names or []):
        conflicting = TeacherClassAssignment.objects.filter(
            term=term,
            grade_class=grade_class,
            subjects_taught__contains=subject_name,
        ).exclude(teacher=teacher_profile)
        if exclude_assignment_pk:
            conflicting = conflicting.exclude(pk=exclude_assignment_pk)
        if conflicting.exists():
            other = conflicting.select_related("teacher").first()
            warnings.append({
                "type": "subject_conflict",
                "message": (
                    f"{other.teacher.full_name} is already assigned to teach "
                    f"{subject_name} in {grade_class.name} for {term.name}."
                ),
                "severity": "warning",
            })

    return warnings


def check_assignment_conflicts_bulk(teacher_profile, term_ids, class_ids, per_class_subjects, exclude_assignment_pks=None):
    """
    Check conflicts across multiple terms and classes.
    Returns a list of warning dicts with an added 'term_name' key.
    """
    from academics.models import Term, GradeClass

    all_warnings = []
    exclude_pks = set(exclude_assignment_pks or [])

    for term_id in term_ids:
        try:
            term = Term.objects.get(pk=term_id)
        except Term.DoesNotExist:
            continue
        for class_id in class_ids:
            try:
                gc = GradeClass.objects.get(pk=class_id)
            except GradeClass.DoesNotExist:
                continue
            subjects = per_class_subjects.get(str(class_id), [])
            warnings = check_assignment_conflicts(
                teacher_profile, term, gc, subjects,
                exclude_assignment_pk=None,
            )
            for w in warnings:
                w["term_name"] = term.name
                w["term_id"] = term.pk
                w["class_name"] = gc.name
            all_warnings.extend(warnings)

    return all_warnings
