import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, TemplateView, View
import json

from academics.ecd_utils import build_ecd_report_context, ecd_template_type_from_class_name, grade_class_names_for_department
from academics.forms import LessonPlanForm, LessonPlanReviewForm, ExamScoreFilterForm, SubjectForm, TermForm
from academics.models import (
    AcademicYear,
    Department,
    ECDEvaluation,
    ExamScore,
    ExamType,
    LessonPlan,
    LessonPlanAttachment,
    LessonPlanStatus,
    GradeClass,
    ProgressionConfig,
    ProgressionCase,
    ProgressionStatus,
    ProgressionOutcome,
    PromotionRun,
    ReportCard,
    ReportCardStatus,
    Term,
    GradeClass,
    get_exam_weights,
    get_active_exam_types,
    get_active_ecd_templates,
    ABCPaceProgress,
    ABCScripture,
    ABCReadingProgramme,
    ABCGeneralAssignment,
    ABCInternalExam,
    Subject,
)
from academics.services import generate_class_reports, sign_off_report, calculate_progression_cases
from audit.models import log_event
from students.models import Student, StudentStatus, EnrollmentHistory
from users.models import UserRole
from core.permissions import RoleRequiredMixin
from core.teacher_context import get_teacher_assigned_classes, get_teacher_assigned_classes_from_tca, is_ecd_teacher
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


class StudentAcademicRecordView(RoleRequiredMixin, TemplateView):
    """Full grade history across all terms for a single student.
    Permission-driven via academics.view_reportcard; the teacher-scoping
    check below still applies regardless of role.
    """
    template_name = "academics/student_academic_record.html"
    login_url = "/accounts/login/"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.TEACHER, UserRole.ADMIN_OFFICER,
    ]
    required_permission = "academics.view_reportcard"

    def dispatch(self, request, *args, **kwargs):
        if request.user.role == UserRole.TEACHER:
            from hr.models import TeacherClassAssignment
            from academics.utils import get_current_term
            pk = kwargs.get("pk")
            student = get_object_or_404(Student, pk=pk)
            current_term = get_current_term()
            if current_term:
                assigned_names = set(
                    TeacherClassAssignment.objects.filter(
                        teacher__user=request.user,
                        term=current_term,
                    ).values_list("grade_class__name", flat=True)
                )
                if student.class_name not in assigned_names:
                    messages.error(request, "You can only view records for students in your assigned classes.")
                    return redirect("academics:lesson_plans")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        student = get_object_or_404(Student, pk=pk)
        ctx["student"] = student

        import json
        from collections import OrderedDict, defaultdict
        from academics.models import ScoreStatus, ExamTypeConfiguration

        # All report cards for this student, oldest first (for trends)
        reports = list(ReportCard.objects.filter(student=student).select_related(
            "term__academic_year", "signed_off_by"
        ).order_by("term__start_date"))

        ctx["report_count"] = len(reports)

        # Build per-term score breakdowns
        term_data = OrderedDict()
        prev_overall = None

        for rc in reports:
            ecd_rating_map = {"E": 4, "G": 3, "S": 2, "N": 1}
            ecd_rating_rev = {4: "E", 3: "G", 2: "S", 1: "N"}

            if rc.is_ecd_report:
                evaluations = rc.ecd_evaluations.all()
                if not evaluations.exists():
                    continue

                subject_rows = []
                overall_wsum = 0
                overall_tw = 0
                for ev in evaluations:
                    num = ecd_rating_map.get(ev.rating, 0)
                    if num > 0:
                        overall_wsum += num
                        overall_tw += 1
                    subject_rows.append({
                        "name": ev.domain,
                        "weighted_avg": num if num else None,
                        "exam_details": [{"code": "rating", "exam_type": "Rating", "score": ev.rating}],
                    })

                overall_avg = round(overall_wsum / overall_tw, 1) if overall_tw else None
                exam_columns = [("rating", "Rating")]
            else:
                scores_qs = ExamScore.objects.filter(
                    student=student, term=rc.term, status=ScoreStatus.APPROVED
                ).select_related("exam_type_config")

                if not scores_qs.exists():
                    continue

                subjects = OrderedDict()
                for s in scores_qs:
                    key = s.subject_name
                    if key not in subjects:
                        subjects[key] = {}
                    subjects[key][s.exam_type] = s.score

                exam_configs = {
                    et.code: et for et in ExamTypeConfiguration.objects.filter(is_active=True)
                }
                subject_rows = []
                overall_wsum = 0
                overall_tw = 0
                for subj_name, exam_scores in subjects.items():
                    sw = 0
                    stw = 0
                    exam_details = []
                    for et_code, et_obj in exam_configs.items():
                        score_val = exam_scores.get(et_code)
                        if score_val is not None:
                            w = float(et_obj.weight_percentage)
                            sw += float(score_val) * w
                            stw += w
                            exam_details.append({
                                "exam_type": et_obj.name,
                                "code": et_code,
                                "score": score_val,
                            })
                    subj_avg = round(sw / stw, 1) if stw > 0 else None
                    subject_rows.append({
                        "name": subj_name,
                        "weighted_avg": subj_avg,
                        "exam_details": exam_details,
                    })
                    if subj_avg:
                        overall_wsum += subj_avg
                        overall_tw += 1

                overall_avg = round(overall_wsum / overall_tw, 1) if overall_tw > 0 else None

                exam_columns_set = set()
                for srow in subject_rows:
                    for d in srow["exam_details"]:
                        exam_columns_set.add((d["code"], d["exam_type"]))
                exam_columns = sorted(exam_columns_set, key=lambda x: x[0])

            comparison = None
            if prev_overall is not None and overall_avg is not None:
                diff = round(overall_avg - prev_overall, 1)
                comparison = {
                    "diff": diff,
                    "direction": "up" if diff > 1 else ("down" if diff < -1 else "flat"),
                }

            grade = _grade_from_pct(overall_avg) if overall_avg and not rc.is_ecd_report else (ecd_rating_rev.get(round(overall_avg)) if overall_avg else "--")

            term_data[rc.term] = {
                "report": rc,
                "subjects": subject_rows,
                "overall_average": overall_avg,
                "grade": grade,
                "exam_columns": exam_columns,
                "comparison": comparison,
                "is_ecd": rc.is_ecd_report,
            }
            if overall_avg is not None:
                prev_overall = overall_avg

        ctx["term_data"] = term_data

        # --- Analysis & Insights ---
        term_list = list(term_data.values())
        ctx["has_data"] = bool(term_list)

        if term_list:
            first = term_list[0]
            last = term_list[-1]

            first_avg = first["overall_average"]
            last_avg = last["overall_average"]
            trajectory = "stable"
            trajectory_diff = 0
            if first_avg is not None and last_avg is not None:
                trajectory_diff = round(last_avg - first_avg, 1)
                if trajectory_diff > 0.3:
                    trajectory = "improving"
                elif trajectory_diff < -0.3:
                    trajectory = "declining"

            ctx["trajectory"] = trajectory
            ctx["trajectory_diff"] = trajectory_diff
            ctx["first_term_name"] = first["report"].term.name if term_list else ""
            ctx["last_term_name"] = last["report"].term.name if term_list else ""

            current = last
            ctx["current_avg"] = current["overall_average"]
            ctx["current_grade"] = current["grade"]
            ctx["is_ecd"] = current["is_ecd"]

            if current["subjects"]:
                sorted_subjects = sorted(
                    [s for s in current["subjects"] if s["weighted_avg"] is not None],
                    key=lambda x: x["weighted_avg"], reverse=True
                )
                if sorted_subjects:
                    ctx["best_subject"] = sorted_subjects[0]
                    ctx["worst_subject"] = sorted_subjects[-1]

            if len(term_list) >= 2:
                subject_trends = {}
                for td in term_list:
                    for s in td["subjects"]:
                        if s["name"] not in subject_trends:
                            subject_trends[s["name"]] = []
                        subject_trends[s["name"]].append({
                            "term": td["report"].term,
                            "avg": s["weighted_avg"],
                        })

                improving_subjects = []
                declining_subjects = []
                for subj_name, points in subject_trends.items():
                    if len(points) >= 2:
                        first_s = points[0]["avg"]
                        last_s = points[-1]["avg"]
                        if first_s is not None and last_s is not None:
                            sd = round(last_s - first_s, 1)
                            if sd > 0.5:
                                improving_subjects.append({"name": subj_name, "diff": sd})
                            elif sd < -0.5:
                                declining_subjects.append({"name": subj_name, "diff": sd})

                ctx["improving_subjects"] = sorted(improving_subjects, key=lambda x: -x["diff"])
                ctx["declining_subjects"] = sorted(declining_subjects, key=lambda x: x["diff"])

            total_present = sum(
                td["report"].attendance_days_present for td in term_list if td["report"].attendance_days_present
            )
            total_absent = sum(
                td["report"].attendance_days_absent for td in term_list if td["report"].attendance_days_absent
            )
            total_late = sum(
                td["report"].attendance_days_late for td in term_list if td["report"].attendance_days_late
            )
            att_total = total_present + total_absent + total_late
            ctx["att_summary"] = {
                "present": total_present,
                "absent": total_absent,
                "late": total_late,
                "rate": round((total_present + total_late) / att_total * 100, 1) if att_total else None,
            }

            grade_counts = {}
            for td in term_list:
                g = td["grade"]
                grade_counts[g] = grade_counts.get(g, 0) + 1
            ctx["grade_distribution"] = grade_counts

            all_avgs = [td["overall_average"] for td in term_list if td["overall_average"] is not None]
            ctx["lifetime_avg"] = round(sum(all_avgs) / len(all_avgs), 1) if all_avgs else None

            # Standard deviation (grade stability)
            if len(all_avgs) >= 2:
                mean = sum(all_avgs) / len(all_avgs)
                variance = sum((x - mean) ** 2 for x in all_avgs) / len(all_avgs)
                ctx["std_dev"] = round(variance ** 0.5, 1)
            else:
                ctx["std_dev"] = None

            # Subject count
            ctx["subject_count"] = len(current["subjects"]) if term_list else 0

        # Group by academic year for tabs
        year_groups = defaultdict(list)
        for term_obj, td in term_data.items():
            year_groups[term_obj.academic_year].append({
                "data": td,
                "term": term_obj,
            })
        ctx["year_groups"] = [
            {
                "year": year,
                "terms": sorted(terms, key=lambda x: x["term"].start_date or ""),
            }
            for year, terms in sorted(year_groups.items(), key=lambda x: x[0].name)
        ]

        # Chart data as JSON
        chart_points = []
        for term_obj, td in term_data.items():
            if td["overall_average"] is not None:
                chart_points.append({
                    "label": f"{term_obj.academic_year.name[:4]} {term_obj.name}",
                    "value": td["overall_average"],
                    "year": term_obj.academic_year.name,
                })
        ctx["chart_data_json"] = json.dumps(chart_points)

        return ctx


def _grade_from_pct(pct):
    if pct is None:
        return "--"
    if pct >= 80:
        return "A"
    if pct >= 70:
        return "B"
    if pct >= 60:
        return "C"
    if pct >= 50:
        return "D"
    return "E"


class ProgressionConfigListView(RoleRequiredMixin, View):
    """List/create progression configs for a year-end cycle."""
    template_name = "academics/progression_config_list.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "academics.view_progressionconfig"

    def get(self, request, *args, **kwargs):
        configs = ProgressionConfig.objects.select_related(
            "academic_year_from", "academic_year_to", "created_by"
        ).all().order_by("-created_at")
        years = AcademicYear.objects.all().order_by("name")

        # Detect cases stuck because no HOD is assigned for their department
        from users.models import User
        dept_role_map = {
            "PRIMARY": UserRole.PRIMARY_HOD,
            "ECD": UserRole.ECD_HOD,
            "LOWER_SECONDARY": UserRole.LOWER_SECONDARY_HOD,
        }
        unreachable_warnings = []
        for dept_name, hod_role in dept_role_map.items():
            has_hod = User.objects.filter(role=hod_role, is_active=True).exists()
            if not has_hod:
                grade_names = GradeClass.objects.filter(
                    department=dept_name,
                ).values_list("name", flat=True)
                stuck_count = ProgressionCase.objects.filter(
                    student__class_name__in=list(grade_names),
                    status__in=[ProgressionStatus.CALCULATED, ProgressionStatus.PENDING_HOD_REVIEW],
                ).count()
                if stuck_count > 0:
                    unreachable_warnings.append({
                        "department_label": Department(dept_name).label,
                        "department": dept_name,
                        "count": stuck_count,
                    })

        return render(request, self.template_name, {
            "configs": configs,
            "years": years,
            "unreachable_warnings": unreachable_warnings,
            "academics_tab": "progression",
        })

    def post(self, request, *args, **kwargs):
        year_from_id = request.POST.get("academic_year_from")
        year_to_id = request.POST.get("academic_year_to")
        if not year_from_id or not year_to_id:
            messages.error(request, "Both source and target academic years are required.")
            return redirect("academics:progression_config_list")
        try:
            min_avg = float(request.POST.get("minimum_average", 50))
            min_att = float(request.POST.get("minimum_attendance", 80))
            ret_thresh = float(request.POST.get("retention_threshold", 50))
        except (TypeError, ValueError):
            messages.error(request, "Numeric values are required for thresholds.")
            return redirect("academics:progression_config_list")
        year_from = get_object_or_404(AcademicYear, pk=year_from_id)
        year_to = get_object_or_404(AcademicYear, pk=year_to_id)
        if ProgressionConfig.objects.filter(
            academic_year_from=year_from, academic_year_to=year_to
        ).exists():
            messages.error(request, "A configuration for this year pair already exists.")
            return redirect("academics:progression_config_list")
        config = ProgressionConfig.objects.create(
            academic_year_from=year_from,
            academic_year_to=year_to,
            minimum_average=min_avg,
            minimum_attendance=min_att,
            retention_threshold=ret_thresh,
            created_by=request.user,
        )
        log_event(
            actor=request.user,
            action_type="PROGRESSION_CONFIG_CREATED",
            model_name="ProgressionConfig",
            object_id=config.pk,
            description=f"Created progression config: {year_from} \u2192 {year_to} (avg\u2265{min_avg}%, att\u2265{min_att}%, ret<{ret_thresh}%)",
            request=request,
        )
        messages.success(request, f"Progression config created: {year_from} \u2192 {year_to}")
        return redirect("academics:progression_config_list")


class ProgressionConfigEditView(RoleRequiredMixin, View):
    """Edit an existing progression config. Warns if cases exist under old thresholds."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "academics.change_progressionconfig"

    def post(self, request, pk):
        config = get_object_or_404(ProgressionConfig, pk=pk)
        old_avg = config.minimum_average
        old_att = config.minimum_attendance
        old_ret = config.retention_threshold
        try:
            config.minimum_average = float(request.POST.get("minimum_average", config.minimum_average))
            config.minimum_attendance = float(request.POST.get("minimum_attendance", config.minimum_attendance))
            config.retention_threshold = float(request.POST.get("retention_threshold", config.retention_threshold))
        except (TypeError, ValueError):
            messages.error(request, "Numeric values are required for thresholds.")
            return redirect("academics:progression_config_list")
        config.save()

        thresholds_changed = (
            config.minimum_average != old_avg
            or config.minimum_attendance != old_att
            or config.retention_threshold != old_ret
        )

        if thresholds_changed and config.cases.exists():
            calculated_count = config.cases.filter(status=ProgressionStatus.CALCULATED).count()
            if calculated_count:
                messages.warning(
                    request,
                    f"Thresholds updated. {calculated_count} case(s) still in 'calculated' status "
                    f"were calculated under the old thresholds. Click 'Recalculate' to re-run "
                    f"only those cases. Cases past calculated status are not affected.",
                )
            else:
                messages.info(request, "Thresholds updated. All cases are already past calculated status.")
        else:
            messages.success(request, "Progression config updated.")
        return redirect("academics:progression_config_list")


class ProgressionConfigDeleteView(RoleRequiredMixin, View):
    """Delete a progression config (only if no cases exist)."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "academics.delete_progressionconfig"

    def post(self, request, pk):
        config = get_object_or_404(ProgressionConfig, pk=pk)
        if config.cases.exists():
            messages.error(request, "Cannot delete \u2014 cases have already been calculated. Archive the config instead.")
            return redirect("academics:progression_config_list")
        log_event(
            actor=request.user,
            action_type="PROGRESSION_CONFIG_DELETED",
            model_name="ProgressionConfig",
            object_id=pk,
            description=f"Deleted progression config: {config.academic_year_from} \u2192 {config.academic_year_to}",
            request=request,
        )
        config.delete()
        messages.success(request, "Progression config deleted.")
        return redirect("academics:progression_config_list")


class ProgressionCaseCalculateView(RoleRequiredMixin, View):
    """Triggers Phase Two recalculation for a given config asynchronously.

    Pre-computes scope counts before queuing so the admin sees how many cases
    will be affected. Actual calculation runs via Celery to prevent HTTP timeouts
    at scale (see Item 9). Only cases still in 'calculated' status are touched.
    """
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "academics.change_progressionconfig"

    def post(self, request, config_id):
        config = get_object_or_404(ProgressionConfig, pk=config_id)
        from academics.models import RecalcStatus
        config.recalc_status = RecalcStatus.QUEUED
        config.recalc_completed_count = None
        config.recalc_failed_details = []
        config.save(update_fields=["recalc_status", "recalc_completed_count", "recalc_failed_details"])

        existing_calculated = config.cases.filter(status=ProgressionStatus.CALCULATED).count()
        existing_non_calc = config.cases.exclude(status=ProgressionStatus.CALCULATED).count()

        from academics.tasks import calculate_progression_cases_task
        task = calculate_progression_cases_task.delay(
            config_id=config.pk,
            triggered_by_id=request.user.pk,
            recalculate=True,
        )

        log_event(
            actor=request.user,
            action_type="PROGRESSION_CALCULATION_QUEUED",
            model_name="ProgressionConfig",
            object_id=config_id,
            description=f"Async recalculation queued (task {task.id}) \u2014 "
                        f"{existing_calculated} calculated case(s) will be updated, "
                        f"{existing_non_calc} case(s) at other statuses left untouched "
                        f"({config.academic_year_from} \u2192 {config.academic_year_to})",
            request=request,
        )

        if existing_non_calc:
            messages.success(
                request,
                f"Recalculation queued (task {task.id}). When complete, "
                f"{existing_calculated} calculated case(s) will be updated; "
                f"{existing_non_calc} case(s) at other statuses left untouched.",
            )
        else:
            messages.success(
                request,
                f"Recalculation queued (task {task.id}). "
                f"All {existing_calculated} calculated case(s) will be updated.",
            )

        return redirect("academics:progression_config_list")


# ---------------------------------------------------------------------------
# Phase Three \u2014 HOD Review & HOS Decision screens
# ---------------------------------------------------------------------------

class ProgressionHODReviewListView(RoleRequiredMixin, TemplateView):
    """HOD reviews retention candidates for their department."""
    template_name = "academics/progression_hod_review.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.can_review_progression"
    page_size = 50

    def _hod_department(self, user):
        if user.role == UserRole.PRIMARY_HOD:
            return "PRIMARY"
        if user.role == UserRole.ECD_HOD:
            return "ECD"
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            return "LOWER_SECONDARY"
        return None  # HOS/Super Admin see all

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        search_q = self.request.GET.get("q", "").strip()
        filter_class = self.request.GET.get("class", "").strip()
        page = int(self.request.GET.get("page", 1))
        config = None
        cases_page = None
        departments = []
        class_list = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            dept = self._hod_department(self.request.user)
            base = ProgressionCase.objects.filter(progression_config=config).select_related(
                "student", "hod_recommended_by", "hos_decided_by",
            )
            if dept:
                grade_names = GradeClass.objects.filter(
                    department__iexact=dept
                ).values_list("name", flat=True)
                class_list = sorted(grade_names)
                base = base.filter(student__class_name__in=list(grade_names))
            else:
                class_list = sorted(GradeClass.objects.values_list("name", flat=True))

            if search_q and len(search_q) >= 2:
                from django.db.models import Q
                base = base.filter(
                    Q(student__first_name__icontains=search_q) |
                    Q(student__last_name__icontains=search_q) |
                    Q(student__admission_no__icontains=search_q)
                )
            if filter_class:
                base = base.filter(student__class_name=filter_class)

            total_count = base.count()
            base = base.order_by("-created_at")

            from django.core.paginator import Paginator
            paginator = Paginator(base, self.page_size)
            cases_page = paginator.get_page(page)
        else:
            departments = [Department.ECD, Department.PRIMARY, Department.LOWER_SECONDARY]

        ctx.update({
            "config": config,
            "cases_page": cases_page,
            "total_count": cases_page.paginator.count if cases_page else 0,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "departments": departments,
            "class_list": class_list,
            "search_q": search_q,
            "filter_class": filter_class,
            "academics_tab": "progression",
            "ProgressionOutcome": ProgressionOutcome,
        })
        return ctx


class ProgressionHODReviewActionView(RoleRequiredMixin, View):
    """POST endpoint for HOD to recommend an outcome."""
    required_permission = "academics.can_review_progression"

    def post(self, request, case_id):
        case = get_object_or_404(ProgressionCase, pk=case_id)
        hod_dept = None
        if request.user.role == UserRole.PRIMARY_HOD:
            hod_dept = "PRIMARY"
        elif request.user.role == UserRole.ECD_HOD:
            hod_dept = "ECD"
        elif request.user.role == UserRole.LOWER_SECONDARY_HOD:
            hod_dept = "LOWER_SECONDARY"
        if hod_dept:
            grade_names = GradeClass.objects.filter(
                department=hod_dept
            ).values_list("name", flat=True)
            if case.student.class_name not in list(grade_names):
                log_event(
                    actor=request.user,
                    action_type="HOD_CROSS_DEPT_DENIED",
                    model_name="ProgressionCase",
                    object_id=case.pk,
                    description=(
                        f"HOD {request.user} (role={request.user.role}) denied access to "
                        f"case {case.pk} for {case.student} \u2014 student class "
                        f"'{case.student.class_name}' is not in department {hod_dept}"
                    ),
                    request=request,
                )
                raise PermissionDenied("You do not have access to cases outside your department.")

        if case.status not in (ProgressionStatus.CALCULATED, ProgressionStatus.PENDING_HOD_REVIEW):
            messages.error(request, "This case is not awaiting HOD review.")
            return redirect(self._redirect_url(case))

        recommendation = request.POST.get("recommendation")
        if recommendation not in ProgressionOutcome.values:
            messages.error(request, "Invalid recommendation.")
            return redirect(self._redirect_url(case))

        override_reason = request.POST.get("override_reason", "")
        if recommendation != case.system_suggested_outcome and not override_reason.strip():
            messages.error(request, "Override reason is required when outcome differs from system suggestion.")
            return redirect(self._redirect_url(case))

        old_recommendation = case.hod_recommendation
        old_recommended_by = case.hod_recommended_by

        case.hod_recommendation = recommendation
        case.hod_recommended_by = request.user
        case.hod_recommended_at = timezone.now()

        # Item 6: When HOD resubmits after a return, preserve previous recommendation history
        if case.hos_return_comment and old_recommendation and old_recommendation != recommendation:
            prev_line = f"Previous: {ProgressionOutcome(old_recommendation).label}"
            if old_recommended_by:
                prev_line += f" (by {old_recommended_by.get_full_name()})"
            now_line = f"Now: {ProgressionOutcome(recommendation).label}"
            if override_reason.strip():
                case.override_reason = f"{prev_line}\n{now_line}\nReason: {override_reason.strip()}"
            else:
                case.override_reason = f"{prev_line}\n{now_line}"
        else:
            case.override_reason = override_reason.strip()

        case.status = ProgressionStatus.PENDING_HOS_DECISION
        case.save()

        log_event(
            actor=request.user,
            action_type="HOD_RECOMMENDATION",
            model_name="ProgressionCase",
            object_id=case.pk,
            description=f"HOD recommended {ProgressionOutcome(recommendation).label} for {case.student} "
                        f"(system suggested: {case.get_system_suggested_outcome_display() or 'none'})",
            request=request,
        )
        messages.success(request, f"HOD recommendation recorded: {case.student} \u2192 {ProgressionOutcome(recommendation).label}")
        return redirect(self._redirect_url(case))

    def _redirect_url(self, case):
        return reverse("academics:progression_hod_review") + f"?config_id={case.progression_config_id}"


class ProgressionHODBulkApproveView(RoleRequiredMixin, View):
    """Bulk approve promote for selected eligible cases (system_suggested_outcome is promote)."""
    required_permission = "academics.can_review_progression"

    def post(self, request):
        config_id = request.POST.get("config_id")
        if not config_id:
            messages.error(request, "No configuration selected.")
            return redirect("academics:progression_hod_review")
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        case_ids = request.POST.getlist("case_ids")
        if not case_ids:
            messages.warning(request, "No cases selected for bulk approval.")
            return redirect(reverse("academics:progression_hod_review") + f"?config_id={config_id}")

        dept = None
        if request.user.role == UserRole.PRIMARY_HOD:
            dept = "PRIMARY"
        elif request.user.role == UserRole.ECD_HOD:
            dept = "ECD"
        elif request.user.role == UserRole.LOWER_SECONDARY_HOD:
            dept = "LOWER_SECONDARY"

        selected = ProgressionCase.objects.filter(pk__in=case_ids, progression_config=config)
        if dept:
            grade_names = GradeClass.objects.filter(department__iexact=dept).values_list("name", flat=True)
            selected = selected.filter(student__class_name__in=list(grade_names))

        rejected = []
        count = 0
        for case in selected.iterator():
            if case.system_suggested_outcome != ProgressionOutcome.PROMOTE or \
               case.status != ProgressionStatus.PENDING_HOD_REVIEW:
                rejected.append(str(case.pk))
                continue
            case.hod_recommendation = ProgressionOutcome.PROMOTE
            case.hod_recommended_by = request.user
            case.hod_recommended_at = timezone.now()
            case.status = ProgressionStatus.PENDING_HOS_DECISION
            case.save()
            log_event(
                actor=request.user,
                action_type="HOD_BULK_APPROVE",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOD bulk-approved promote for {case.student}",
                request=request,
            )
            count += 1

        msg = f"Bulk approved promote for {count} student(s)."
        if rejected:
            msg += f" {len(rejected)} ineligible case(s) skipped (not promote-suggested or wrong status)."
        messages.success(request, msg)
        return redirect(reverse("academics:progression_hod_review") + f"?config_id={config_id}")


class ProgressionHOSDecisionListView(RoleRequiredMixin, TemplateView):
    """HOS reviews and makes final decisions on progression cases."""
    template_name = "academics/progression_hos_decision.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.can_decide_progression"
    page_size = 50

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        search_q = self.request.GET.get("q", "").strip()
        filter_class = self.request.GET.get("class", "").strip()
        page = int(self.request.GET.get("page", 1))
        config = None
        cases_page = None
        class_list = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            base = ProgressionCase.objects.filter(
                progression_config=config,
                status=ProgressionStatus.PENDING_HOS_DECISION,
            ).select_related(
                "student", "hod_recommended_by",
            )
            class_list = sorted(
                base.values_list("student__class_name", flat=True).distinct()
            )

            if search_q and len(search_q) >= 2:
                from django.db.models import Q
                base = base.filter(
                    Q(student__first_name__icontains=search_q) |
                    Q(student__last_name__icontains=search_q) |
                    Q(student__admission_no__icontains=search_q)
                )
            if filter_class:
                base = base.filter(student__class_name=filter_class)

            total_count = base.count()
            base = base.order_by("-created_at")

            from django.core.paginator import Paginator
            paginator = Paginator(base, self.page_size)
            cases_page = paginator.get_page(page)
        else:
            configs_with_pending = ProgressionConfig.objects.filter(
                cases__status=ProgressionStatus.PENDING_HOS_DECISION
            ).distinct().order_by("-created_at")
            ctx["configs_with_pending"] = configs_with_pending

        ctx.update({
            "config": config,
            "cases_page": cases_page,
            "total_count": cases_page.paginator.count if cases_page else 0,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "class_list": class_list,
            "search_q": search_q,
            "filter_class": filter_class,
            "academics_tab": "progression",
            "ProgressionOutcome": ProgressionOutcome,
        })
        return ctx


class ProgressionHOSDecisionActionView(RoleRequiredMixin, View):
    """POST endpoint for HOS to make a final decision or return to HOD."""
    required_permission = "academics.can_decide_progression"

    def post(self, request, case_id):
        case = get_object_or_404(ProgressionCase, pk=case_id)
        if case.status != ProgressionStatus.PENDING_HOS_DECISION:
            messages.error(request, "This case is not awaiting HOS decision.")
            return redirect(self._redirect_url(case))

        action = request.POST.get("action", "decide")

        if action == "return_to_hod":
            comment = request.POST.get("return_comment", "").strip()
            if not comment:
                messages.error(request, "A written comment is required when returning a case to HOD.")
                return redirect(self._redirect_url(case))
            old_recommendation = case.hod_recommendation
            case.hos_return_comment = comment
            # Retain hod_recommendation so HOD can see their previous recommendation
            case.status = ProgressionStatus.PENDING_HOD_REVIEW
            case.save()

            log_event(
                actor=request.user,
                action_type="HOS_RETURNED_TO_HOD",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOS returned case for {case.student} to HOD. Comment: {comment}",
                request=request,
            )
            messages.success(request, f"Case returned to HOD for {case.student}.")
            return redirect(self._redirect_url(case))

        # Normal final decision path
        decision = request.POST.get("decision")
        if decision not in ProgressionOutcome.values:
            messages.error(request, "Invalid decision.")
            return redirect(self._redirect_url(case))

        override_reason = request.POST.get("override_reason", "")
        ref_field = case.hod_recommendation if case.hod_recommendation else case.system_suggested_outcome
        if decision != ref_field and not override_reason.strip():
            messages.error(request, "Override reason is required when decision differs from HOD recommendation.")
            return redirect(self._redirect_url(case))

        case.hos_decision = decision
        case.hos_decided_by = request.user
        case.hos_decided_at = timezone.now()
        if override_reason.strip():
            existing = case.override_reason or ""
            if existing.strip():
                case.override_reason = f"{existing}\nHOS override: {override_reason.strip()}"
            else:
                case.override_reason = override_reason.strip()
        case.status = ProgressionStatus.FINALIZED
        case.save()

        log_event(
            actor=request.user,
            action_type="HOS_DECISION",
            model_name="ProgressionCase",
            object_id=case.pk,
            description=f"HOS decided {ProgressionOutcome(decision).label} for {case.student} "
                        f"(HOD recommended: {case.get_hod_recommendation_display() or 'none'})",
            request=request,
        )
        messages.success(request, f"HOS decision recorded: {case.student} \u2192 {ProgressionOutcome(decision).label}")
        return redirect(self._redirect_url(case))

    def _redirect_url(self, case):
        return reverse("academics:progression_hos_decision") + f"?config_id={case.progression_config_id}"


class ProgressionHOSBulkDecideView(RoleRequiredMixin, View):
    """HOS bulk-decide promote for selected PENDING_HOS_DECISION cases with hod_recommendation=promote."""
    required_permission = "academics.can_decide_progression"

    def post(self, request):
        config_id = request.POST.get("config_id")
        if not config_id:
            messages.error(request, "No configuration selected.")
            return redirect("academics:progression_hos_decision")
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        case_ids = request.POST.getlist("case_ids")
        if not case_ids:
            messages.warning(request, "No cases selected for bulk decision.")
            return redirect(reverse("academics:progression_hos_decision") + f"?config_id={config_id}")

        selected = ProgressionCase.objects.filter(pk__in=case_ids, progression_config=config)

        rejected = []
        count = 0
        for case in selected.iterator():
            if case.status != ProgressionStatus.PENDING_HOS_DECISION or \
               case.hod_recommendation != ProgressionOutcome.PROMOTE:
                rejected.append(str(case.pk))
                continue
            case.hos_decision = ProgressionOutcome.PROMOTE
            case.hos_decided_by = request.user
            case.hos_decided_at = timezone.now()
            case.status = ProgressionStatus.FINALIZED
            case.save()
            log_event(
                actor=request.user,
                action_type="HOS_BULK_DECISION",
                model_name="ProgressionCase",
                object_id=case.pk,
                description=f"HOS bulk-decided promote for {case.student}",
                request=request,
            )
            _notify_finalized_case(case, config, request.user)
            count += 1

        msg = f"HOS bulk-decided promote for {count} student(s)."
        if rejected:
            msg += f" {len(rejected)} ineligible case(s) skipped (not awaiting HOS decision or not promote-recommended)."
        messages.success(request, msg)
        return redirect(reverse("academics:progression_hos_decision") + f"?config_id={config_id}")


# ---------------------------------------------------------------------------
# Item 2 \u2014 Teacher read-only progression view
# ---------------------------------------------------------------------------

class ProgressionTeacherView(RoleRequiredMixin, TemplateView):
    """Read-only view for teachers to see their own class progression cases.

    Only exposes: calculated_average, system_suggested_outcome, status.
    Never exposes: hod_recommendation, override_reason, hos_decision, calculation_basis.
    """
    template_name = "academics/progression_teacher_view.html"
    allowed_roles = [UserRole.TEACHER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_progressioncase"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        config_id = self.request.GET.get("config_id")

        if user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            assigned_class_names = list(GradeClass.objects.values_list("name", flat=True))
        else:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            assigned = get_teacher_assigned_classes_from_tca(user)
            assigned_class_names = list(assigned.keys())

        config = None
        cases_data = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            cases = ProgressionCase.objects.filter(
                progression_config=config,
                student__class_name__in=assigned_class_names,
            ).select_related("student").order_by("student__last_name", "student__first_name")

            for case in cases:
                cases_data.append({
                    "student_name": case.student.get_full_name(),
                    "admission_no": case.student.admission_no,
                    "class_name": case.student.class_name,
                    "calculated_average": case.calculated_average,
                    "calculated_attendance_rate": case.calculated_attendance_rate,
                    "system_suggested_outcome": case.system_suggested_outcome,
                    "system_suggested_outcome_label": case.get_system_suggested_outcome_display(),
                    "status": case.status,
                    "status_label": case.get_status_display(),
                })

        ctx.update({
            "config": config,
            "cases_data": cases_data,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "academics_tab": "progression_teacher",
        })
        return ctx


# ---------------------------------------------------------------------------
# Item 3 \u2014 Parent finalized-outcome view
# ---------------------------------------------------------------------------

class ProgressionParentView(RoleRequiredMixin, TemplateView):
    """Parent sees finalized outcome for their children only.

    Multi-child support: ?student=N to select a specific child.
    Only exposes: outcome_label, new_class, student basics.
    Never exposes: hod_recommendation, override_reason, calculation_basis.
    """
    template_name = "academics/progression_parent_view.html"
    allowed_roles = [UserRole.PARENT, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_progressioncase"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        config_id = self.request.GET.get("config_id")
        selected_student_id = self.request.GET.get("student")

        if user.role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            children = Student.objects.filter(is_archived=False)
        else:
            from students.models import ParentGuardian, StudentGuardian
            guardian = ParentGuardian.objects.filter(email=user.email).first()
            if guardian:
                child_ids = StudentGuardian.objects.filter(
                    guardian=guardian
                ).values_list("student_id", flat=True)
                children = Student.objects.filter(pk__in=child_ids, is_archived=False)
            else:
                children = Student.objects.none()

        selected_student = None
        if selected_student_id:
            selected_student = children.filter(id=selected_student_id).first()
        if not selected_student and children:
            selected_student = children.first()

        config = None
        results = []

        if config_id and selected_student:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            cases = ProgressionCase.objects.filter(
                progression_config=config,
                student=selected_student,
                status=ProgressionStatus.FINALIZED,
            ).select_related("student")

            for case in cases:
                new_class = ""
                if case.hos_decision == ProgressionOutcome.PROMOTE:
                    grade_order = list(
                        GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
                    )
                    grade_index = {name: i for i, name in enumerate(grade_order)}
                    idx = grade_index.get(case.student.class_name)
                    if idx is not None and idx + 1 < len(grade_order):
                        new_class = grade_order[idx + 1]
                elif case.hos_decision == ProgressionOutcome.RETAIN:
                    new_class = case.student.class_name + " (repeat)"
                elif case.hos_decision == ProgressionOutcome.GRADUATE:
                    new_class = "Graduated"

                results.append({
                    "student_name": case.student.get_full_name(),
                    "admission_no": case.student.admission_no,
                    "class_name": case.student.class_name,
                    "outcome_label": case.get_hos_decision_display(),
                    "new_class": new_class,
                })

        ctx.update({
            "config": config,
            "results": results,
            "children": children,
            "selected_student": selected_student,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "config_id": config_id,
            "academics_tab": "progression_parent",
        })
        return ctx


# ---------------------------------------------------------------------------
# Item 12 \u2014 Notification helper for finalized cases
# ---------------------------------------------------------------------------

def _notify_finalized_case(case, config, actor):
    """Send parent (and HOD for retain) notifications for a single finalized case.

    Promoted/graduated \u2192 parent notification with new placement.
    Retained \u2192 parent notification + HOD notification with support conditions.
    """
    from communications.email_service import send_parent_notification, dispatch_notification
    from students.models import ParentGuardian

    student = case.student
    decision = case.hos_decision
    if not decision:
        return

    grade_order = list(
        GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
    )
    grade_index = {name: i for i, name in enumerate(grade_order)}

    new_class = ""
    idx = grade_index.get(student.class_name)
    if decision == ProgressionOutcome.PROMOTE and idx is not None and idx + 1 < len(grade_order):
        new_class = grade_order[idx + 1]
    elif decision == ProgressionOutcome.RETAIN:
        new_class = student.class_name
    elif decision == ProgressionOutcome.GRADUATE:
        new_class = "Graduated"

    outcome_label = ProgressionOutcome(decision).label if decision else "pending"
    guardians = ParentGuardian.objects.filter(
        studentguardian__student=student,
        studentguardian__is_primary=True,
    )

    for guardian in guardians:
        send_parent_notification(
            guardian=guardian,
            title=f"Student Progression: {student.first_name}",
            message=(
                f"Dear {guardian.full_name},\n\n"
                f"{student.first_name} {student.last_name} ({student.admission_no}) "
                f"has been {outcome_label} for the {config.academic_year_to} academic year.\n"
                f"New placement: {new_class}\n\n"
                f"If you have any questions, please contact the school office."
            ),
            link="/academics/progression/parent/",
            actor=actor,
        )

    if decision == ProgressionOutcome.RETAIN:
        dept = GradeClass.objects.filter(name=student.class_name).values_list("department", flat=True).first()
        if dept:
            hod_role_map = {
                "PRIMARY": UserRole.PRIMARY_HOD,
                "ECD": UserRole.ECD_HOD,
                "LOWER_SECONDARY": UserRole.LOWER_SECONDARY_HOD,
            }
            hod_role = hod_role_map.get(dept)
            if hod_role:
                from users.models import User as UserModel
                hod = UserModel.objects.filter(role=hod_role).first()
                if hod:
                    support_conditions = ""
                    if case.override_reason:
                        support_conditions = f"\nSupport conditions: {case.override_reason}"
                    dispatch_notification(
                        user=hod,
                        title=f"Student Retained: {student.first_name}",
                        message=(
                            f"{student.first_name} {student.last_name} ({student.admission_no}) "
                            f"has been retained in {student.class_name} for {config.academic_year_to}. "
                            f"Please review any support conditions needed.{support_conditions}"
                        ),
                        link="/academics/progression/hod-review/",
                        actor=actor,
                    )


# ---------------------------------------------------------------------------
# Phase Four \u2014 Bulk Promotion Execution
# ---------------------------------------------------------------------------

class ProgressionBulkPromotionView(RoleRequiredMixin, TemplateView):
    """Admin Officer or HOS reviews finalized cases and triggers promotion."""
    template_name = "academics/progression_bulk_promotion.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "academics.change_progressioncase"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config_id = self.request.GET.get("config_id")
        config = None
        promote_cases = ProgressionCase.objects.none()
        retain_cases = ProgressionCase.objects.none()
        graduate_cases = ProgressionCase.objects.none()
        resume_run = None
        failed_students = []
        successful_students = []
        not_attempted_students = []

        if config_id:
            config = get_object_or_404(ProgressionConfig, pk=config_id)
            base = ProgressionCase.objects.filter(
                progression_config=config,
                status=ProgressionStatus.FINALIZED,
            ).select_related("student").order_by("student__last_name")
            promote_cases = base.filter(hos_decision=ProgressionOutcome.PROMOTE)
            retain_cases = base.filter(hos_decision=ProgressionOutcome.RETAIN)
            graduate_cases = base.filter(hos_decision=ProgressionOutcome.GRADUATE)

            # Resume support: identify three groups
            resume_run = PromotionRun.objects.filter(
                academic_year_from=config.academic_year_from,
                academic_year_to=config.academic_year_to,
                status__in=["in_progress", "paused", "interrupted"],
            ).order_by("-created_at").first()

            if resume_run:
                processed_set = set(resume_run.processed_student_ids)
                failed_set = set(resume_run.failed_student_ids)

                for case in base:
                    student = case.student
                    if student.pk in processed_set:
                        successful_students.append(case)
                    elif student.pk in failed_set:
                        # Attach failure reason from detail_json
                        reason = ""
                        for entry in resume_run.failed_detail_json:
                            if entry.get("student_id") == student.pk:
                                reason = entry.get("reason", "")
                                break
                        failed_students.append({"case": case, "reason": reason})
                    else:
                        not_attempted_students.append(case)

        ctx.update({
            "config": config,
            "configs": ProgressionConfig.objects.all().order_by("-created_at"),
            "selected_config_id": config_id,
            "promote_cases": promote_cases,
            "retain_cases": retain_cases,
            "graduate_cases": graduate_cases,
            "academics_tab": "progression",
            "runs": PromotionRun.objects.filter(academic_year_from=config.academic_year_from if config else None).order_by("-created_at") if config else PromotionRun.objects.none(),
            "resume_run": resume_run,
            "successful_students": successful_students,
            "failed_students": failed_students,
            "not_attempted_students": not_attempted_students,
        })
        return ctx


class ProgressionExecutePromotionView(RoleRequiredMixin, View):
    """Executes the promotion for finalized cases in a config.

    Each student is processed and committed individually so that a single
    failure does not roll back prior successes, preserving resume capability.
    """
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "academics.change_progressioncase"

    def post(self, request, config_id):
        config = get_object_or_404(ProgressionConfig, pk=config_id)

        # Verify all cases are finalized
        non_finalized = ProgressionCase.objects.filter(
            progression_config=config,
        ).exclude(status=ProgressionStatus.FINALIZED)
        if non_finalized.exists():
            names = list(non_finalized.values_list("student__admission_no", flat=True)[:10])
            messages.error(
                request,
                f"Cannot execute \u2014 {non_finalized.count()} case(s) not finalized: {', '.join(names)}. "
                "All cases must be finalized before promotion can run.",
            )
            return redirect(reverse("academics:progression_bulk_promotion") + f"?config_id={config_id}")

        # FR-ACAD-006: Sign-off gate — all report cards for the source academic year's
        # terms must be published before progression can execute.
        from academics.models import ReportCard, ReportCardStatus, Term
        student_ids = ProgressionCase.objects.filter(
            progression_config=config,
        ).values_list("student_id", flat=True)
        year_terms = Term.objects.filter(academic_year=config.academic_year_from).values_list("pk", flat=True)
        unsigned_reports = ReportCard.objects.filter(
            student_id__in=student_ids,
            term_id__in=year_terms,
        ).exclude(status=ReportCardStatus.PUBLISHED)
        if unsigned_reports.exists():
            unsigned_students = unsigned_reports.values_list("student__admission_no", flat=True)[:10]
            messages.error(
                request,
                f"Cannot execute \u2014 {unsigned_reports.count()} report card(s) not yet signed off: "
                f"{', '.join(unsigned_students)}. All report cards must be signed off before promotion.",
            )
            return redirect(reverse("academics:progression_bulk_promotion") + f"?config_id={config_id}")

        # Determine which cases still need processing (resume support)
        existing_run = PromotionRun.objects.filter(
            academic_year_from=config.academic_year_from,
            academic_year_to=config.academic_year_to,
            status__in=["in_progress", "paused", "interrupted"],
        ).order_by("-created_at").first()

        already_processed = set(existing_run.processed_student_ids) if existing_run else set()
        already_failed = set(existing_run.failed_student_ids) if existing_run else set()

        finalized = ProgressionCase.objects.filter(
            progression_config=config,
            status=ProgressionStatus.FINALIZED,
        ).select_related("student")

        grade_order = list(
            GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
        )
        grade_index = {name: i for i, name in enumerate(grade_order)}

        if existing_run and existing_run.status == "in_progress":
            run = existing_run
        else:
            run = PromotionRun.objects.create(
                academic_year_from=config.academic_year_from,
                academic_year_to=config.academic_year_to,
                executed_by=request.user,
                status="in_progress",
            )

        promoted_count = run.promoted_count
        retained_count = run.retained_count
        graduated_count = run.graduated_count
        failed_count = run.failed_count
        failed_detail = list(run.failed_detail_json)
        errors = []

        for case in finalized:
            student = case.student
            if student.pk in already_processed:
                continue

            decision = case.hos_decision
            current_class = student.class_name
            current_idx = grade_index.get(current_class)

            if current_idx is None:
                if student.pk not in already_failed:
                    failed_detail.append({"student_id": student.pk, "reason": f"Class '{current_class}' not in GradeClass registry"})
                    failed_count += 1
                    run.failed_student_ids.append(student.pk)
                errors.append(f"Class '{current_class}' not in GradeClass registry")
                continue

            from django.db import transaction

            try:
                with transaction.atomic():
                    if decision == ProgressionOutcome.GRADUATE:
                        student.status = StudentStatus.GRADUATED
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["status", "academic_year", "updated_at"])
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_to,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="graduated",
                            progression_case=case,
                        )
                        graduated_count += 1

                    elif decision == ProgressionOutcome.PROMOTE:
                        if current_idx + 1 >= len(grade_order):
                            raise ValueError(f"Already in final grade '{current_class}', cannot promote")
                        next_class = grade_order[current_idx + 1]
                        next_grade = GradeClass.objects.filter(name=next_class).first()
                        if next_grade:
                            from academics.models import get_class_capacity
                            cap = get_class_capacity(next_grade)
                            if cap:
                                enrolled = Student.objects.filter(
                                    class_name=next_class, is_archived=False,
                                    status=StudentStatus.ACTIVE,
                                ).count()
                                if enrolled >= cap:
                                    raise ValueError(
                                        f"Destination '{next_class}' at capacity ({enrolled}/{cap})"
                                    )
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_from,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="promoted",
                            notes=f"Promoted from {current_class} to {next_class}",
                            progression_case=case,
                        )
                        student.class_name = next_class
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["class_name", "academic_year", "updated_at"])
                        promoted_count += 1

                    elif decision == ProgressionOutcome.RETAIN:
                        EnrollmentHistory.objects.create(
                            student=student,
                            academic_year=config.academic_year_from,
                            class_name=current_class,
                            stream_name=student.stream_name,
                            action="retained",
                            notes=f"Retained in {current_class}",
                            progression_case=case,
                        )
                        student.academic_year = config.academic_year_to
                        student.save(update_fields=["academic_year", "updated_at"])
                        retained_count += 1

                    run.processed_student_ids.append(student.pk)
                    if student.pk in already_failed:
                        run.failed_student_ids = [pk for pk in run.failed_student_ids if pk != student.pk]
                        failed_detail = [d for d in failed_detail if d.get("student_id") != student.pk]
                        run.failed_count = max(0, run.failed_count - 1)
                        failed_count = run.failed_count
                    run.promoted_count = promoted_count
                    run.retained_count = retained_count
                    run.graduated_count = graduated_count
                    run.failed_count = failed_count
                    run.failed_detail_json = failed_detail
                    run.save()

                    # Item 12: Per-student notification fires immediately after commit
                    _notify_finalized_case(case, config, request.user)

            except Exception as e:
                if student.pk not in already_failed:
                    failed_detail.append({"student_id": student.pk, "reason": str(e)})
                    failed_count += 1
                    run.failed_student_ids.append(student.pk)
                else:
                    for entry in failed_detail:
                        if entry.get("student_id") == student.pk:
                            entry["reason"] = str(e)
                            break
                run.failed_count = failed_count
                run.failed_detail_json = failed_detail
                run.processed_student_ids = [pk for pk in run.processed_student_ids if pk != student.pk]
                run.save()
                errors.append(str(e))

        had_failures = bool(errors)
        run.status = "completed" if not had_failures else "paused" if failed_count < finalized.count() else "interrupted"
        run.completed_at = timezone.now()
        run.save()

        log_event(
            actor=request.user,
            action_type="PROMOTION_EXECUTED",
            model_name="PromotionRun",
            object_id=run.pk,
            description=f"Promotion run: {promoted_count} promoted, {retained_count} retained, "
                        f"{graduated_count} graduated, {failed_count} failed "
                        f"({config.academic_year_from} \u2192 {config.academic_year_to})",
            request=request,
        )

        msg = f"Promotion run complete: {promoted_count} promoted, {retained_count} retained, {graduated_count} graduated."
        if failed_detail:
            msg += f" {failed_count} failed: {'; '.join(d['reason'] for d in failed_detail[:5])}"
        messages.success(request, msg)

        return redirect(reverse("academics:progression_bulk_promotion") + f"?config_id={config_id}")


class PrintProgressionOutcomeView(RoleRequiredMixin, TemplateView):
    """Printable progression/promotion outcome letter for a student."""
    template_name = "academics/progression_outcome_letter.html"
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD,
    ]
    required_permission = "academics.view_progressioncase"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        case_id = self.request.GET.get("case_id")
        if case_id:
            case = get_object_or_404(ProgressionCase, pk=case_id)
        else:
            student = get_object_or_404(Student, pk=kwargs.get("pk"))
            case = ProgressionCase.objects.filter(
                student=student, status=ProgressionStatus.FINALIZED
            ).order_by("-progression_config__academic_year_to").first()
            if not case:
                raise PermissionDenied("No finalized progression record found for this student.")
        ctx["case"] = case
        ctx["student"] = case.student
        ctx["config"] = case.progression_config
        return ctx
