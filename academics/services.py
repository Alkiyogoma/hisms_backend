from decimal import Decimal
from django.db import transaction, connection
from django.db.models import Count
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse

from academics.ecd_utils import ecd_template_type_from_class_name
from attendance.models import AttendanceEntry, AttendanceStatus


class ProgressionConfigBlockedError(Exception):
    """Raised when a school-wide configuration gap prevents progression calculation."""
from academics.grading_utils import compute_grade_with_gaps
from academics.models import (
    Term, ExamScore, ReportCard, ReportCardStatus, ScoreStatus, ECDEvaluation, ExamType, get_exam_weights,
    ProgressionConfig, ProgressionCase, ProgressionStatus, ProgressionOutcome, CalculationBasis,
    GradeClass,
)
from students.models import Student, StudentStatus
from users.models import UserRole

@transaction.atomic
def generate_class_reports(term_id, class_name, user):
    term = Term.objects.get(pk=term_id)
    students = Student.objects.filter(class_name=class_name, is_archived=False)
    
    generated_count = 0
    for student in students:
        report, created = ReportCard.objects.get_or_create(
            student=student,
            term=term,
            defaults={"generated_by": user, "status": ReportCardStatus.DRAFT}
        )
        
        # detect ECD class type using the shared utility
        ecd_tpl = ecd_template_type_from_class_name(class_name)
        report.is_ecd_report = ecd_tpl is not None

        if ecd_tpl:
            report.ecd_template_type = ecd_tpl
        else:
            # FR-ACAD-014: Check that all submitted exam scores are approved before generating
            from academics.models import ScoreStatus
            pending_scores = ExamScore.objects.filter(
                student=student, term=term,
                status__in=[ScoreStatus.SUBMITTED, ScoreStatus.RETURNED]
            )
            if pending_scores.exists():
                # Skip this student — scores not yet approved by HOD
                continue

            # FR-ACAD-014: Check that teacher comment is complete (min 50 chars)
            if len((report.teacher_comments or "").strip()) < 50:
                # Skip this student — comment not yet entered
                continue

            # calculate overall average using dynamic exam weights with gap detection
            scores = ExamScore.objects.filter(student=student, term=term, status=ScoreStatus.APPROVED)
            # Group by subject
            subjects = {}
            for s in scores:
                if s.subject_name not in subjects:
                    subjects[s.subject_name] = {}
                subjects[s.subject_name][s.exam_type] = float(s.score)
            
            exam_weights = get_exam_weights()
            total_weighted_sum = Decimal(0)
            valid_subject_count = 0
            
            for subj, exams in subjects.items():
                grade_result = compute_grade_with_gaps(
                    scores=exams,
                    weights=exam_weights,
                )
                if grade_result["average"] is not None:
                    total_weighted_sum += Decimal(str(grade_result["average"]))
                    valid_subject_count += 1
            
            if valid_subject_count > 0:
                report.overall_average = total_weighted_sum / valid_subject_count
        
        report.save()

        # FR-ATT-013: Populate attendance summary for this report card
        report.populate_attendance_summary()

        generated_count += 1
        
    if generated_count > 0:
        from audit.models import log_event
        log_event(
            actor=user,
            action_type="REPORTS_GENERATED",
            model_name="ReportCard",
            object_id=term.pk,
            description=f"Generated {generated_count} term report(s) for {class_name} in {term.name}",
        )
    
    return generated_count

def recalculate_report_card_average(report_card):
    """FR-ACAD-003: Recalculate weighted average on any underlying score correction.
    Called automatically after a score is corrected or approved so the ReportCard
    always reflects current scores.
    """
    if report_card.is_ecd_report:
        return

    scores = ExamScore.objects.filter(
        student=report_card.student,
        term=report_card.term,
        status=ScoreStatus.APPROVED,
    )
    subjects = {}
    for s in scores:
        if s.subject_name not in subjects:
            subjects[s.subject_name] = {}
        subjects[s.subject_name][s.exam_type] = float(s.score)

    exam_weights = get_exam_weights()
    total_weighted_sum = Decimal(0)
    valid_subject_count = 0
    for subj, exams in subjects.items():
        grade_result = compute_grade_with_gaps(
            scores=exams,
            weights=exam_weights,
        )
        if grade_result["average"] is not None:
            total_weighted_sum += Decimal(str(grade_result["average"]))
            valid_subject_count += 1

    if valid_subject_count > 0:
        report_card.overall_average = total_weighted_sum / valid_subject_count
    else:
        report_card.overall_average = None
    report_card.save(update_fields=["overall_average", "updated_at"])


@transaction.atomic
def sign_off_report(report_card, user):
    # GRD-015: Only HOS or Super Admin may sign off reports.
    if getattr(user, "role", None) not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
        raise ValidationError("Only HOS or Super Admin may sign off and publish reports.")

    if report_card.status != ReportCardStatus.PENDING_SIGN_OFF and report_card.status != ReportCardStatus.DRAFT:
        raise ValidationError("Report is already published or not ready for sign-off.")

    # Term date gate: block sign-off before grading deadline
    from django.utils import timezone as _tz
    today = _tz.localdate()
    term = report_card.term
    if term:
        grading_dl = getattr(term, "grading_deadline", None)
        endterm_end = getattr(term, "endterm_exam_end_date", None)
        if grading_dl and today < grading_dl:
            raise ValidationError(
                f"Reports cannot be published before the grading deadline ({grading_dl.strftime('%d %b %Y')}). "
                f"Contact your admin to adjust the term dates."
            )
        if endterm_end and today < endterm_end:
            raise ValidationError(
                f"Reports cannot be published before endterm exams end ({endterm_end.strftime('%d %b %Y')}). "
                f"Please wait until all exam scores are finalised."
            )

    # FR-ACAD-006: Two-gate enforcement -- HOD must approve all submitted
    # exam scores before HOS can sign off the report card.
    # Only check scores that have been submitted for review (SUBMITTED or RETURNED),
    # not DRAFT scores that haven't been submitted yet.
    if report_card.student and not report_card.is_ecd_report:
        from academics.models import ScoreStatus
        pending_review = ExamScore.objects.filter(
            student=report_card.student,
            term=report_card.term,
            status__in=[ScoreStatus.SUBMITTED, ScoreStatus.RETURNED],
        )
        if pending_review.exists():
            pending_subjects = pending_review.values_list("subject_name", flat=True).distinct()
            raise ValidationError(
                f"Cannot sign off: {report_card.student.first_name}'s exam scores "
                f"are pending HOD review for: {', '.join(pending_subjects)}. "
                f"All submitted scores must be approved before sign-off."
            )

    # Validation gate: ECD mandatory comments
    if report_card.is_ecd_report and report_card.ecd_template_type in ["kindergarten", "pre_school", "abc"]:
        if len((report_card.teacher_comments or "").strip()) < 50:
            raise ValidationError("Mandatory teacher comment (min 50 chars) is missing.")

    # Validation gate: Primary/Secondary mandatory comments
    if not report_card.is_ecd_report:
        if len((report_card.teacher_comments or "").strip()) < 50:
            raise ValidationError(f"Mandatory teacher comment (min 50 chars) is missing for {report_card.student.first_name} {report_card.student.last_name}.")
            
    report_card.populate_attendance_summary()

    # Recompute overall_average if missing (was skipped at generation time when scores/comments were incomplete)
    if report_card.overall_average is None and not report_card.is_ecd_report:
        scores = ExamScore.objects.filter(student=report_card.student, term=report_card.term, status=ScoreStatus.APPROVED)
        subjects = {}
        for s in scores:
            if s.subject_name not in subjects:
                subjects[s.subject_name] = {}
            subjects[s.subject_name][s.exam_type] = float(s.score)
        exam_weights = get_exam_weights()
        total_weighted_sum = Decimal(0)
        valid_subject_count = 0
        for subj, exams in subjects.items():
            grade_result = compute_grade_with_gaps(
                scores=exams,
                weights=exam_weights,
            )
            if grade_result["average"] is not None:
                total_weighted_sum += Decimal(str(grade_result["average"]))
                valid_subject_count += 1
        if valid_subject_count > 0:
            report_card.overall_average = total_weighted_sum / valid_subject_count

    report_card.status = ReportCardStatus.PUBLISHED
    report_card.signed_off_by = user
    report_card.signed_off_at = timezone.now()
    report_card.published_at = timezone.now()
    report_card.save()
    
    from audit.models import log_event
    log_event(
        actor=user,
        action_type="REPORT_SIGNOFF",
        model_name="ReportCard",
        object_id=report_card.pk,
        description=f"Academic sign-off for term report: {report_card.student.admission_no}",
    )
    
    # NOTIF-07: Fire parent notification (in-app, email, and SMS fallback)
    from communications.email_service import send_parent_notification, dispatch_notification
    from students.models import ParentGuardian
    guardians = ParentGuardian.objects.filter(studentguardian__student=report_card.student, studentguardian__is_primary=True)
    parent_reports_url = reverse("academics:parent_reports")
    for guardian in guardians:
        send_parent_notification(
            guardian=guardian,
            title="Term Report Published",
            message=f"The term report for {report_card.student.first_name} is now available.",
            link=parent_reports_url,
            actor=user
        )

    # Notify the teacher who generated the report
    teacher = report_card.generated_by
    if teacher:
        dispatch_notification(
            user=teacher,
            title="Report Signed Off",
            message=f"The term report for {report_card.student.first_name} has been signed off and published by {user.get_full_name()}.",
            link="/academics/reports/",
            actor=user,
        )

@transaction.atomic
def reject_report_for_edit(report_card, user, reason=""):
    """
    Returns a report to DRAFT status and unlocks individual scores 
    so the teacher can re-edit.
    """
    if getattr(user, "role", None) not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD}:
        raise ValidationError("Only HOS, Super Admin, or HOD may reject reports.")

    if report_card.status != ReportCardStatus.PENDING_SIGN_OFF:
        raise ValidationError("Only reports pending sign-off can be returned for edit.")

    report_card.status = ReportCardStatus.DRAFT
    report_card.signed_off_by = None
    report_card.signed_off_at = None
    # Preserve existing comments but allow teacher to edit
    report_card.save()

    # Unlock all individual scores for this student/term
    from academics.models import ScoreStatus
    ExamScore.objects.filter(student=report_card.student, term=report_card.term).update(
        status=ScoreStatus.DRAFT,
        is_locked=False,
        updated_at=timezone.now()
    )

    from audit.models import log_event
    log_event(
        actor=user,
        action_type="REPORT_REJECTED",
        model_name="ReportCard",
        object_id=report_card.pk,
        description=f"Report returned for edit: {report_card.student.admission_no}. Reason: {reason}",
    )

    # Notify teacher
    from communications.email_service import dispatch_notification
    # Find the teacher who generated it or is assigned to the class
    teacher = report_card.generated_by
    if teacher:
        dispatch_notification(
            user=teacher,
            title="Report Returned for Edit",
            message=f"The term report for {report_card.student.first_name} has been returned for correction by {user.get_full_name()}.\n\nReason: {reason}",
            link="/academics/primary-assessment/", # or dynamic based on type
            actor=user
        )


@transaction.atomic
def revoke_report_signoff(report_card, user, reason=""):
    """
    GRD-016: Revoke a PUBLISHED report back to PENDING_SIGN_OFF.
    Only HOS or Super Admin may revoke. Requires a reason.
    """
    if not reason or not reason.strip():
        raise ValidationError("A reason is required to revoke a report sign-off.")

    if getattr(user, "role", None) not in {UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN}:
        raise ValidationError("Only HOS or Super Admin may revoke a signed-off report.")

    if report_card.status != ReportCardStatus.PUBLISHED:
        raise ValidationError("Only published reports can have their sign-off revoked.")

    report_card.status = ReportCardStatus.PENDING_SIGN_OFF
    report_card.signed_off_by = None
    report_card.signed_off_at = None
    report_card.published_at = None
    report_card.save()

    from audit.models import log_event
    log_event(
        actor=user,
        action_type="REPORT_SIGNOFF_REVOKED",
        model_name="ReportCard",
        object_id=report_card.pk,
        description=f"Report sign-off revoked for {report_card.student.admission_no}. Reason: {reason}",
    )

    # Notify teacher
    from communications.email_service import dispatch_notification
    teacher = report_card.generated_by
    if teacher:
        dispatch_notification(
            user=teacher,
            title="Report Sign-off Revoked",
            message=(
                f"The sign-off for {report_card.student.first_name}'s term report has been revoked "
                f"by {user.get_full_name()}.\n\nReason: {reason}"
            ),
            link="/academics/primary-assessment/",
            actor=user
        )


@transaction.atomic
def calculate_progression_cases(config: ProgressionConfig, triggered_by, recalculate=False) -> dict:
    """
    Phase Two: For every ACTIVE student in the *from* academic year, compute
    their overall average (from the latest published report card) and attendance
    rate, compare against config thresholds, and create ProgressionCase rows.

    When recalculate=True, only students whose case is still in 'calculated'
    status are affected. Cases past that status are left untouched.

    Uses batch queries (Item 10) to avoid N+1 patterns.
    Attendance and report card data fetched via two GROUP BY aggregate queries
    and a Subquery annotation respectively — no per-student method calls.
    Academic year boundary (Item 17) derived from actual term dates, not guessed
    Jan–Dec; raises ProgressionConfigBlockedError if no term has usable dates.

    Returns a summary dict with counts.
    """
    from_year = config.academic_year_from
    students = Student.objects.filter(
        academic_year=from_year,
        is_archived=False,
        status=StudentStatus.ACTIVE,
    ).select_related("academic_year").order_by("class_name", "last_name")

    # Item 8: When recalculating, only touch cases still in 'calculated' status
    if recalculate:
        existing_calc_ids = set(
            ProgressionCase.objects.filter(
                progression_config=config,
                status=ProgressionStatus.CALCULATED,
            ).values_list("student_id", flat=True)
        )

    grade_order = list(
        GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
    )
    grade_index = {name: i for i, name in enumerate(grade_order)}
    is_final_grade = {name: (i == len(grade_order) - 1) for i, name in enumerate(grade_order)}

    # Item 10: Batch-fetch latest published report card per student in one query
    from django.db.models import OuterRef, Subquery
    latest_report_subquery = ReportCard.objects.filter(
        student=OuterRef("pk"),
        status=ReportCardStatus.PUBLISHED,
    ).order_by("-term__end_date")

    students_with_report = students.annotate(
        latest_report_avg=Subquery(latest_report_subquery.values("overall_average")[:1]),
        latest_report_id=Subquery(latest_report_subquery.values("pk")[:1]),
    )

    # Item 17: Determine academic year boundary.
    # Prefer AcademicYear-level start_date/end_date when populated.
    # Fall back to aggregating term dates (earliest start, latest end).
    # If neither source yields usable dates, raise a configuration blocker.
    year_start = from_year.start_date
    year_end = from_year.end_date

    if year_start is None or year_end is None:
        from django.db.models import Min, Max
        date_range = Term.objects.filter(
            academic_year=from_year,
            start_date__isnull=False,
            end_date__isnull=False,
        ).aggregate(
            year_start=Min("start_date"),
            year_end=Max("end_date"),
        )
        year_start = date_range["year_start"]
        year_end = date_range["year_end"]

    if year_start is None or year_end is None:
        raise ProgressionConfigBlockedError(
            f"Academic year '{from_year}' has no start/end dates configured. "
            f"Set AcademicYear dates or configure term dates before running progression calculation."
        )

    student_ids = list(students.values_list("pk", flat=True))
    total_entries = dict(
        AttendanceEntry.objects.filter(
            student_id__in=student_ids,
            date__range=[year_start, year_end],
        ).values("student_id").annotate(count=Count("pk")).values_list("student_id", "count")
    )
    present_entries = dict(
        AttendanceEntry.objects.filter(
            student_id__in=student_ids,
            date__range=[year_start, year_end],
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
        ).values("student_id").annotate(count=Count("pk")).values_list("student_id", "count")
    )

    created_count = 0
    skipped_count = 0
    failed_details = []

    # Item 10: Pre-fetch all existing cases for this config in one query so
    # the per-student loop can decide create vs. update without an extra SELECT.
    existing_case_map = {
        c.student_id: c
        for c in ProgressionCase.objects.filter(progression_config=config)
    }

    for student in students_with_report:
        current_class = student.class_name
        current_idx = grade_index.get(current_class)

        if current_idx is None:
            skipped_count += 1
            continue

        # Item 8: Skip if recalculating and case is past 'calculated'
        if recalculate and student.pk not in existing_calc_ids:
            skipped_count += 1
            continue

        # 1. Overall average from batch-annotated report card
        avg = float(student.latest_report_avg) if student.latest_report_avg is not None else None

        # 2. Attendance rate from batch-fetched counts (Item 11: direct call, no hasattr)
        total_att = total_entries.get(student.pk, 0)
        present_att = present_entries.get(student.pk, 0)
        att_rate = round((present_att / total_att) * 100, 1) if total_att > 0 else None

        # 3. Calculation basis — missing grade or attendance data = INCOMPLETE
        if avg is None or att_rate is None:
            calc_basis = CalculationBasis.INCOMPLETE
        else:
            calc_basis = CalculationBasis.COMPLETE

        # 4. System-suggested outcome
        if is_final_grade.get(current_class, False):
            suggested = ProgressionOutcome.GRADUATE
        elif avg is not None and att_rate is not None:
            meets_avg = avg >= config.minimum_average
            meets_att = att_rate >= config.minimum_attendance
            if meets_avg and meets_att:
                suggested = ProgressionOutcome.PROMOTE
            elif avg < config.retention_threshold:
                suggested = ProgressionOutcome.RETAIN
            else:
                suggested = ProgressionOutcome.PROMOTE_WITH_CONDITIONS
        else:
            suggested = None

        defaults = {
            "calculated_average": avg,
            "calculated_attendance_rate": att_rate,
            "calculation_basis": calc_basis,
            "system_suggested_outcome": suggested,
            "status": ProgressionStatus.CALCULATED,
        }

        # Item 9: Per-student failure isolation — one student's DB error doesn't
        # crash the batch; failure recorded in recalc_failed_details.
        # Item 10: Direct create/update (not update_or_create) to eliminate the
        # extra SELECT + extra SAVEPOINT that update_or_create always emits.
        try:
            with transaction.atomic():
                existing = existing_case_map.get(student.pk)
                if existing is not None:
                    for key, val in defaults.items():
                        setattr(existing, key, val)
                    existing.save(update_fields=list(defaults.keys()))
                else:
                    ProgressionCase.objects.create(
                        student=student,
                        progression_config=config,
                        **defaults,
                    )
            created_count += 1
        except Exception as e:
            failed_details.append({
                "student_id": student.pk,
                "reason": str(e),
                "student_name": str(student),
            })
            skipped_count += 1

    return {
        "created": created_count,
        "skipped": skipped_count,
        "total": created_count + skipped_count,
        "failed_details": failed_details,
    }
