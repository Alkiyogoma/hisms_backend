import json

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, reverse
from django.views.generic import TemplateView, View

from academics import ecd_report
from academics.ecd_utils import ecd_template_type_from_class_name, grade_class_names_for_department
from academics.models import Department, ECDEvaluation, GradeClass, ReportCard, ReportCardStatus, Term
from academics.utils import get_current_term
from audit.models import log_event
from core.permissions import RoleRequiredMixin
from core.teacher_context import get_teacher_assigned_classes_from_tca
from students.models import EnrollmentHistory, Student
from users.models import UserRole


class ECDEvaluationEntryView(RoleRequiredMixin, View):
    """Old ECD evaluation URL; the entry page is ECDAssessmentView."""
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ECD_HOD]
    required_permission = "academics.view_reportcard"

    def get(self, request, *args, **kwargs):
        url = reverse("academics:ecd_assessment")
        query = request.GET.urlencode()
        return redirect(f"{url}?{query}" if query else url)


def _export_students_for_user(user):
    """Scope academic CSV export by role (Primary HOD / ECD HOD cannot export other departments)."""
    from students.models import Student

    qs = Student.objects.filter(is_archived=False).order_by("class_name", "last_name")
    if user.is_school_wide:
        return qs
    # Union of every section role held (e.g. Primary and ECD head).
    names = set()
    if user.has_role(UserRole.PRIMARY_HOD):
        names |= set(grade_class_names_for_department(Department.PRIMARY))
    if user.has_role(UserRole.ECD_HOD):
        names |= set(grade_class_names_for_department(Department.ECD))
    return qs.filter(class_name__in=names) if names else Student.objects.none()


def _requested_term(request):
    """The term picked in the page's filter bar (``?term=``), else the current term."""
    term_id = request.GET.get("term", "")
    if term_id.isdigit():
        term = Term.objects.filter(pk=term_id).first()
        if term:
            return term
    return get_current_term()


def _ecd_api_class_allowed(request, class_name: str) -> bool:
    if not (class_name or "").strip():
        return False
    role = request.user.role
    if request.user.is_school_wide:
        return True
    if request.user.has_role(UserRole.ECD_HOD) and class_name in grade_class_names_for_department(Department.ECD):
        return True
    if role == UserRole.TEACHER:
        from timetable.models import TimetableSlot

        if TimetableSlot.objects.filter(teacher=request.user, class_name=class_name).exists():
            return True
        from core.teacher_context import get_teacher_assigned_classes_from_tca
        return class_name in get_teacher_assigned_classes_from_tca(request.user)
    if role == UserRole.ECD_HOD:
        return class_name in grade_class_names_for_department(Department.ECD)
    return False


def _ecd_api_student_allowed(request, student: Student) -> bool:
    # Reading an earlier term, the page names the class the learner was in
    # then (they may have moved up since).
    class_name = request.GET.get("class_name", "").strip()
    if class_name and class_name != student.class_name:
        term = _requested_term(request)
        return (
            _ecd_api_class_allowed(request, class_name)
            and term is not None
            and ecd_class_roster(class_name, term).filter(pk=student.pk).exists()
        )
    return _ecd_api_class_allowed(request, student.class_name)


def _ecd_class_of(request, student: Student) -> str:
    """The class the learner is evaluated under: the page's class, else their own."""
    return request.GET.get("class_name", "").strip() or student.class_name


def ecd_term_editable(term) -> bool:
    """Only the current term is evaluated; earlier (and later) terms are read-only."""
    return term is not None and term == get_current_term()


def ecd_class_roster(class_name, term):
    """Learners in an ECD class during ``term``.

    For the current term that is the class as it is now (learners who left or
    moved class are not counted), so the list, the progress count and a class
    print all match the number of children in the class.

    For an earlier term the enrolment history places learners in a class for a
    term or a year, so a past class is found even after they move up or leave.
    In the current academic year, anyone in the class now is included as well.
    """
    current = get_current_term()
    if current is not None and term == current:
        return Student.objects.filter(class_name=class_name, is_archived=False)
    ids = set(
        EnrollmentHistory.objects.filter(class_name=class_name)
        .filter(Q(term=term) | Q(term__isnull=True, academic_year=term.academic_year))
        .values_list("student_id", flat=True)
    )
    ids |= set(
        ReportCard.objects.filter(term=term, is_ecd_report=True, student__class_name=class_name)
        .values_list("student_id", flat=True)
    )
    if current is None or term.academic_year_id == current.academic_year_id:
        ids |= set(
            Student.objects.filter(class_name=class_name, is_archived=False).values_list("id", flat=True)
        )
    return Student.objects.filter(pk__in=ids)


READ_ONLY_TERM_ERROR = "Earlier terms are read-only. Only the current term can be evaluated."


class ECDStudentsAPIView(RoleRequiredMixin, View):
    """The class list for the ECD entry page, with each learner's report progress."""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "students.view_student"

    def get(self, request):
        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        term = _requested_term(request)
        roster = (
            ecd_class_roster(class_name, term) if term
            else Student.objects.filter(class_name=class_name, is_archived=False)
        )
        students = list(
            roster.order_by("last_name", "first_name")
            .values("id", "first_name", "last_name", "admission_no", "date_of_birth")
        )

        class_type = ecd_report.class_type_of(class_name)
        if term and class_type:
            structure = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
            reports = {
                rc.student_id: rc for rc in ReportCard.objects.filter(term=term, student_id__in=[s["id"] for s in students])
            }
            rated = {}
            for ev in ECDEvaluation.objects.filter(
                report_card__in=reports.values(), item_id__in=structure.items_by_id,
            ).values("report_card__student_id", "item_id", "rating", "score"):
                if ev["rating"] or ev["score"] is not None:
                    rated[ev["report_card__student_id"]] = rated.get(ev["report_card__student_id"], 0) + 1
            for s in students:
                rc = reports.get(s["id"])
                s["status"] = rc.status if rc else ""
                s["approved"] = bool(rc and rc.ecd_hod_approved_by_id)
                s["returned"] = bool(rc and rc.status == ReportCardStatus.DRAFT and rc.returned_at)
                s["rated"] = rated.get(s["id"], 0)
                s["total"] = len(structure.items)

        return JsonResponse({"students": students, "read_only": not ecd_term_editable(term)})


def _person(user):
    return (user.get_full_name() or user.username) if user else ""


def _approval(report):
    """Who approved the report so far: the ECD HOD, then the Head of School."""
    if report.status == ReportCardStatus.PUBLISHED and report.signed_off_by_id:
        return {"stage": "published", "by": _person(report.signed_off_by),
                "at": report.signed_off_at.strftime("%d %b %Y") if report.signed_off_at else ""}
    if report.ecd_hod_approved_by_id:
        return {"stage": "hod", "by": _person(report.ecd_hod_approved_by),
                "at": report.ecd_hod_approved_at.strftime("%d %b %Y") if report.ecd_hod_approved_at else ""}
    return None


def _returned(report):
    """The reviewer's note on a report sent back to the teacher, while it is still a draft."""
    if report.status != ReportCardStatus.DRAFT or not report.returned_at:
        return None
    return {"reason": report.return_reason, "by": _person(report.returned_by),
            "at": report.returned_at.strftime("%d %b %Y, %H:%M")}


def _report_payload(report, structure, *, read_only):
    values = ecd_report.values_for(report)
    info = ecd_report.summary(report, structure, values)
    return {
        "report_card_id": report.pk,
        "values": {str(k): v for k, v in values.items()},
        "teacher_comments": report.teacher_comments or "",
        "ecd_remarks": report.ecd_remarks or "",
        "status": report.status,
        "approval": _approval(report),
        "returned": _returned(report),
        "is_locked": read_only or report.status != ReportCardStatus.DRAFT,
        "read_only": read_only,
        "days_absent": report.attendance_days_absent,
        **info,
    }


class ECDEvaluationAPIView(RoleRequiredMixin, View):
    """Read and save one learner's ECD report lines for the selected term.

    GET  -> {student, values: {item_id: {rating, score, note}}, teacher_comments, average, ...}
    POST {values: {...}, teacher_comments, overall_remarks} -> same shape after saving.
    """
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def _resolve(self, request, student_id):
        student = get_object_or_404(Student, id=student_id)
        if not _ecd_api_student_allowed(request, student):
            return None, None, None, JsonResponse({"error": "Access denied for this student"}, status=403)
        class_type = ecd_report.class_type_of(_ecd_class_of(request, student))
        if not class_type:
            return None, None, None, JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)
        term = _requested_term(request)
        if not term:
            return None, None, None, JsonResponse({"error": "No active term found"}, status=400)
        return student, class_type, term, None

    @staticmethod
    def _student_json(student):
        return {
            "id": student.id, "name": f"{student.first_name} {student.last_name}",
            "class_name": student.class_name, "admission_no": student.admission_no,
        }

    @staticmethod
    def _report(student, term, class_type, user):
        report, _ = ReportCard.objects.get_or_create(
            student=student, term=term,
            defaults={"is_ecd_report": True, "ecd_template_type": class_type,
                      "generated_by": user, "status": ReportCardStatus.DRAFT},
        )
        if not report.is_ecd_report or report.ecd_template_type != class_type:
            report.is_ecd_report = True
            report.ecd_template_type = class_type
            report.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])
        return report

    def get(self, request, student_id=None):
        student, class_type, term, error = self._resolve(request, student_id)
        if error:
            return error
        structure = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
        if not ecd_term_editable(term):
            # Reading an earlier term: show what was recorded, never start a report.
            report = ReportCard.objects.filter(student=student, term=term).first()
            if report is None:
                return JsonResponse({
                    "student": self._student_json(student), "report_card_id": None, "values": {},
                    "teacher_comments": "", "ecd_remarks": "", "status": "", "is_locked": True,
                    "read_only": True, "no_report": True, "average": None, "remarks": "",
                    "remarks_auto": "", "rated": 0, "total": len(structure.items), "missing": [],
                    "days_absent": None,
                })
            payload = _report_payload(report, structure, read_only=True)
        else:
            report = self._report(student, term, class_type, request.user)
            report.populate_attendance_summary()
            payload = _report_payload(report, structure, read_only=False)
        return JsonResponse({"student": self._student_json(student), "ecd_template_type": class_type, **payload})

    def post(self, request, student_id=None):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        student, class_type, term, error = self._resolve(request, student_id)
        if error:
            return error
        if not ecd_term_editable(term):
            return JsonResponse({"error": READ_ONLY_TERM_ERROR}, status=403)
        if term.is_locked:
            return JsonResponse({"error": "This term is locked and cannot accept score entry."}, status=403)

        report = self._report(student, term, class_type, request.user)
        # UAT ECD-009: Lock evaluations when report is not in DRAFT status
        # SA can bypass lock for corrections after HOD review
        if report.status != ReportCardStatus.DRAFT and request.user.role != UserRole.SUPER_ADMIN:
            return JsonResponse(
                {"error": "This report has already been submitted and is locked for editing."}, status=403,
            )

        structure = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
        from django.db import transaction
        try:
            with transaction.atomic():
                ecd_report.save_values(report, structure, data.get("values") or {})
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        fields = []
        if "teacher_comments" in data:
            comment = (data.get("teacher_comments") or "").strip()
            if comment != (report.teacher_comments or ""):
                report.teacher_comments = comment
                fields.append("teacher_comments")
        if "overall_remarks" in data:
            remarks = (data.get("overall_remarks") or "").strip()[:50]
            if remarks != (report.ecd_remarks or ""):
                report.ecd_remarks = remarks
                fields.append("ecd_remarks")
        if fields:
            report.save(update_fields=fields + ["updated_at"])
            if "teacher_comments" in fields:
                log_event(
                    actor=request.user, action_type="ECD_COMMENT_SAVED", model_name="ReportCard",
                    object_id=report.pk, description=f"ECD teacher comment saved for {student.admission_no}",
                    request=request,
                )

        return JsonResponse({"success": True, **_report_payload(report, structure, read_only=False)})


class ECDSubmissionAPIView(RoleRequiredMixin, View):
    """API endpoint for submitting ECD evaluations for HOD review"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def post(self, request, student_id=None):

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        term = _requested_term(request)
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)
        if not ecd_term_editable(term):
            return JsonResponse({"error": READ_ONLY_TERM_ERROR}, status=403)

        report_card = get_object_or_404(ReportCard, student=student, term=term)
        report_card.populate_attendance_summary()

        if report_card.status == ReportCardStatus.PENDING_SIGN_OFF:
            return JsonResponse(
                {"error": "This report has already been submitted for review."},
                status=400,
            )
        if report_card.status == ReportCardStatus.PUBLISHED:
            return JsonResponse(
                {"error": "This report has already been published and cannot be re-submitted."},
                status=400,
            )

        class_type = ecd_report.class_type_of(_ecd_class_of(request, student)) or report_card.ecd_template_type
        if not class_type:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)

        if not report_card.teacher_comments or len(report_card.teacher_comments.strip()) < 50:
            return JsonResponse({"error": "Teacher comments must be at least 50 characters long"}, status=400)

        structure = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
        missing = ecd_report.missing_lines(structure, ecd_report.values_for(report_card))
        if missing:
            names = ", ".join(m.label for m in missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
            return JsonResponse({"error": f"{len(missing)} line(s) still to rate: {names}"}, status=400)

        report_card.status = ReportCardStatus.PENDING_SIGN_OFF
        report_card.comments_submitted = True
        # The correction asked for is done: the HOD reviews it afresh.
        report_card.return_reason = ""
        report_card.returned_by = None
        report_card.returned_at = None
        report_card.save(update_fields=[
            "status", "comments_submitted", "return_reason", "returned_by", "returned_at", "updated_at",
        ])

        log_event(
            actor=request.user,
            action_type="ECD_SUBMITTED",
            model_name="ReportCard",
            object_id=report_card.pk,
            description=f"ECD evaluations submitted for {student.admission_no} ({student.class_name})",
            request=request
        )

        # FRD: Notify ECD HOD when evaluations are submitted for review
        try:
            from communications.email_service import dispatch_notification
            from users.models import User
            hod_roles = [UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL]
            hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
            for hod in hods:
                dispatch_notification(
                    user=hod,
                    title="ECD evaluations submitted for review",
                    message=(
                        f"{request.user.get_full_name()} has submitted ECD evaluations for "
                        f"{student.first_name} {student.last_name} ({student.class_name}). "
                        f"Please review and approve."
                    ),
                    link=reverse("academics:report_review_queue") + "?dept=ecd",
                    actor=request.user,
                )
        except Exception:
            pass  # Never block submission on notification failure

        return JsonResponse({"success": True, "message": "Report submitted for HOD review"})


class ECDAssessmentView(RoleRequiredMixin, TemplateView):
    """ECD report entry: the selected class's lines for the selected term, learner by learner."""

    template_name = "academics/ecd_assessment.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get_context_data(self, **kwargs):
        from academics.progress_filters import ProgressFilters
        ctx = super().get_context_data(**kwargs)
        pf = ProgressFilters(self.request)

        # FRD 2.3: ECD classes come from the GradeClass registry
        ecd_classes = [gc.name for gc in GradeClass.objects.filter(department=Department.ECD).order_by("sort_order", "name")]
        if not ecd_classes:
            ecd_classes = ["Pre-K", "KG", "Pre-School", "ABC"]
        if self.request.user.role == UserRole.TEACHER:
            assigned = set(get_teacher_assigned_classes_from_tca(self.request.user).keys())
            ecd_classes = [c for c in ecd_classes if c in assigned]
            if not ecd_classes:
                ctx["no_class_assigned"] = True

        # The shared progress filter bar: year, term and class. ECD classes are
        # evaluated by report line, so subject and assessment type do not apply.
        class_name = pf.pick_class(ecd_classes) or (ecd_classes[0] if ecd_classes else "")
        pf.pick_subject([], note="Report lines")
        pf.pick_type(note="Term evaluation")
        ctx["ecd_read_only"] = bool(pf.term) and not ecd_term_editable(pf.term)
        ctx["pf"] = pf.context(footnote=(
            "Earlier term: reports are shown as recorded and cannot be changed."
            if ctx["ecd_read_only"] else
            "Rate each student on every line, add the class teacher's comment, then submit to the HOD."
        ))

        class_type = ecd_report.class_type_of(class_name)
        term = pf.term
        ctx["current_term"] = term
        ctx["ecd_class_name"] = class_name
        ctx["ecd_class_type"] = class_type
        ctx["academics_tab"] = "ecd_evaluations"
        if class_type and term:
            ctx["structure"] = ecd_report.ReportStructure(class_type, ecd_report.term_number_of(term))
            ctx["term_number"] = ctx["structure"].term_number

        # Welfare linkage: flag students with open high/critical welfare observations
        from welfare.models import WelfareObservation, WelfareSeverity
        students_in_class = (
            ecd_class_roster(class_name, term) if term
            else Student.objects.filter(class_name=class_name, is_archived=False)
        )
        welfare = {}
        for obs in WelfareObservation.objects.filter(
            student__in=students_in_class,
            severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
        ).exclude(hod_status="resolved"):
            welfare.setdefault(obs.student_id, []).append(
                f"{obs.get_severity_display()} welfare concern ({obs.observation_date:%d %b})"
            )
        ctx["welfare_flags"] = welfare
        return ctx


class ECDProgressAPIView(RoleRequiredMixin, View):
    """API endpoint to get ECD evaluation progress for a class"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get(self, request):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        tpl = ecd_template_type_from_class_name(class_name)
        if not tpl:
            return JsonResponse({"error": "Not an ECD class"}, status=400)

        term = _requested_term(request)
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        students = ecd_class_roster(class_name, term)
        total_students = students.count()

        if total_students == 0:
            return JsonResponse({"progress": {"total": 0, "completed": 0, "percentage": 0}})

        completed_reports = ReportCard.objects.filter(
            student__in=students,
            term=term,
            is_ecd_report=True,
            ecd_template_type=tpl,
            status__in=[ReportCardStatus.PENDING_SIGN_OFF, ReportCardStatus.PUBLISHED],
        ).count()

        percentage = round((completed_reports / total_students) * 100, 1) if total_students > 0 else 0

        return JsonResponse(
            {
                "progress": {
                    "total": total_students,
                    "completed": completed_reports,
                    "percentage": percentage,
                }
            }
        )



