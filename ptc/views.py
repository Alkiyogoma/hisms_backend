# PTC Views — Learner Progress Report module.
#
# Adjustment note: All teachers share the UserRole.TEACHER role.
# Access differentiation is done via TeacherClassAssignment checks
# (is_class_teacher, subjects_taught) rather than role-based gating alone.

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from core.permissions import RoleRequiredMixin
from ptc.models import (
    EnrichmentGrade,
    EnrichmentLetterGrade,
    EnrichmentSubject,
    LEARNER_ATTRIBUTES,
    LearnerAttributeRating,
    LearnerAttributeRatingEntry,
    PTCDateChangeLog,
    PTCGenerationLog,
    PTCProgressTrendOverride,
    PTCSubjectComment,
    PTCWindow,
    TermSlot,
)
from ptc.services import (
    _get_class_teacher_for_class,
    get_core_subjects_for_student,
    get_subject_teacher_students,
    _get_teacher_class_names,
    _get_teacher_subjects,
    _is_class_teacher,
    _is_ecd_student,
    _get_ptc_eligible_students,
    calculate_progress_trend,
    can_enter_comment,
    can_enter_enrichment_grade,
    can_open_ptc,
    get_academic_performance_grid,
    get_attendance_summary,
    get_compliance_summary,
)
from ptc.forms import EnrichmentSubjectConfigForm
from users.models import UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_ptc_window():
    """Get the most recent PTC window (by ptc_date descending)."""
    return PTCWindow.objects.order_by("-ptc_date").first()


def _active_academic_year():
    from academics.utils import get_current_academic_year
    return get_current_academic_year()


def _is_ecd_teacher(user):
    """Check if a teacher is assigned only to ECD classes."""
    from academics.models import Department, GradeClass
    from academics.utils import get_current_term
    from hr.models import TeacherClassAssignment

    current_term = get_current_term()
    if not current_term:
        return False

    assignments = TeacherClassAssignment.objects.filter(
        teacher__user=user, term=current_term,
    ).select_related("grade_class")

    if not assignments:
        return False

    ecd_class_names = set(
        GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
    )
    assigned_class_names = {a.grade_class.name for a in assignments}
    return assigned_class_names.issubset(ecd_class_names)


def _get_academic_years():
    from academics.models import AcademicYear
    return AcademicYear.objects.all().order_by("-is_current", "-name")


# ===========================================================================
# CLASS TEACHER — Student Picker
# ===========================================================================

class PTCStudentPickerView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/student_picker.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcsubjectcomment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "student_picker"
        user = self.request.user
        if user.role == UserRole.TEACHER and _is_ecd_teacher(user):
            raise PermissionDenied("ECD teachers do not access the PTC module.")

        students = _get_ptc_eligible_students(user)

        # Build class list from GradeClass model (excluding ECD) — not just from student data
        from academics.models import GradeClass, Department
        is_admin = user.role in {
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        }
        if is_admin:
            all_db_classes = GradeClass.objects.exclude(
                department=Department.ECD
            ).order_by("name").values_list("name", flat=True)
        else:
            teacher_class_names = _get_teacher_class_names(user)
            all_db_classes = GradeClass.objects.filter(
                name__in=teacher_class_names
            ).exclude(
                department=Department.ECD
            ).order_by("name").values_list("name", flat=True)

        # Group students by class_name, include classes even if empty
        student_map = {}
        for s in students:
            student_map.setdefault(s.class_name, []).append(s)
        classes = {cn: student_map.get(cn, []) for cn in all_db_classes}

        # Compute PTC completion per student and per class
        ptc_window = _current_ptc_window()
        student_ids = [s.id for s in students]

        # Bulk query attribute counts and comment counts to avoid N+1
        attr_counts = {}
        if ptc_window and student_ids:
            from django.db.models import Count
            attr_rows = (
                LearnerAttributeRatingEntry.objects
                .filter(ptc_window=ptc_window, student_id__in=student_ids)
                .exclude(rating="")
                .values("student_id")
                .annotate(cnt=Count("id"))
                .values_list("student_id", "cnt")
            )
            attr_counts = dict(attr_rows)

        comment_counts = {}
        if ptc_window and student_ids:
            comment_rows = (
                PTCSubjectComment.objects
                .filter(ptc_window=ptc_window, student_id__in=student_ids)
                .exclude(comment_text="")
                .values("student_id")
                .annotate(cnt=Count("id"))
                .values_list("student_id", "cnt")
            )
            comment_counts = dict(comment_rows)

        # Get total core subjects count per student (to verify all comments entered)
        core_subject_counts = {}
        for s in students:
            core_subject_counts[s.id] = len(get_core_subjects_for_student(s))

        # Determine completion per student
        student_completion = {}
        for s in students:
            attrs_ok = attr_counts.get(s.id, 0) >= 12
            comments_ok = comment_counts.get(s.id, 0) >= core_subject_counts.get(s.id, 1)
            is_done = attrs_ok and comments_ok
            student_completion[s.id] = is_done
            s.ptc_complete = is_done  # Attach for template access

        # Compute per-class completion stats
        class_completion = {}
        total_complete = 0
        for cn, students_list in classes.items():
            class_complete = sum(1 for s in students_list if student_completion.get(s.id, False))
            class_completion[cn] = (class_complete, len(students_list))
            total_complete += class_complete

        total_students_count = sum(len(st) for st in classes.values())

        # Sorted class names with student counts and completion for tab UI
        class_tabs = []
        for cn in classes:
            cnt = len(classes[cn])
            comp, _ = class_completion.get(cn, (0, cnt))
            class_tabs.append({"name": cn, "count": cnt, "complete": comp})

        ctx["classes"] = classes
        ctx["class_tabs"] = class_tabs
        ctx["total_students"] = total_students_count
        ctx["total_complete"] = total_complete
        ctx["is_admin_view"] = is_admin
        ctx["ptc_window"] = ptc_window
        ctx["active_academic_year"] = _active_academic_year()
        ctx["is_class_teacher"] = _is_class_teacher(user)
        return ctx


# ===========================================================================
# CLASS TEACHER — PTC Screen (FR-PTC-025, FR-PTC-026)
# ===========================================================================

class PTCScreenView(RoleRequiredMixin, TemplateView):
    """
    Full PTC display for a student. Sections in order per FR-PTC-026:
    1. Learner Info  2. Academic Performance Grid  3. Average % & Meets Expectations
    4. Overall Progress Trend  5. Subject Teacher Comments  6. Enrichment Grades
    7. Learner Attributes
    """
    template_name = "ptc/ptc_screen.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcsubjectcomment"

    def get(self, request, *args, **kwargs):
        from students.models import Student
        student = get_object_or_404(Student, pk=kwargs["student_pk"])
        if not can_open_ptc(request.user, student):
            raise PermissionDenied("You do not have access to this student's PTC.")
        if _is_ecd_student(student):
            raise PermissionDenied("ECD students are not eligible for PTC (FR-PTC-032).")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Handle progress trend override (FR-PTC-029)."""
        from students.models import Student
        student = get_object_or_404(Student, pk=kwargs["student_pk"])
        user = request.user

        if not _is_class_teacher(user, student.class_name):
            messages.error(request, "Only the class teacher can override the progress trend.")
            return redirect("ptc:ptc_screen", student_pk=student.pk)

        ptc_window = _current_ptc_window()
        if not ptc_window:
            messages.error(request, "No PTC window configured.")
            return redirect("ptc:ptc_screen", student_pk=student.pk)

        trend_value = request.POST.get("progress_trend_override", "").strip()
        valid_trends = ["improving", "stable", "needs_attention"]

        if trend_value not in valid_trends:
            messages.error(request, "Invalid trend value.")
            return redirect("ptc:ptc_screen", student_pk=student.pk)

        PTCProgressTrendOverride.objects.update_or_create(
            student=student, ptc_window=ptc_window,
            defaults={"overridden_trend": trend_value, "entered_by": user},
        )
        messages.success(request, f"Progress trend updated to {trend_value.replace('_', ' ').title()}.")
        return redirect("ptc:ptc_screen", student_pk=student.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "student_picker"
        from students.models import Student
        from academics.models import GradeClass

        student = get_object_or_404(Student, pk=self.kwargs["student_pk"])
        user = self.request.user

        # Log PTC generation (FR-PTC-030) — only class teachers trigger generation logs
        # Deduplicate: skip if same teacher viewed same student in same window within 5 minutes
        from datetime import timedelta
        ptc_window = _current_ptc_window()
        if _is_class_teacher(user, student.class_name):
            five_min_ago = timezone.now() - timedelta(minutes=5)
            recent_log = PTCGenerationLog.objects.filter(
                student=student, class_teacher=user, ptc_window=ptc_window, opened_at__gte=five_min_ago
            ).exists()
            if not recent_log:
                PTCGenerationLog.objects.create(student=student, class_teacher=user, ptc_window=ptc_window)
        elif user.role in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD}:
            from audit.models import log_event
            five_min_ago = timezone.now() - timedelta(minutes=5)
            recent_audit = PTCGenerationLog.objects.filter(
                student=student, class_teacher=user, ptc_window=ptc_window, opened_at__gte=five_min_ago
            ).exists()
            if not recent_audit:
                log_event(
                    actor=user,
                    action_type="PTC_SCREEN_ACCESSED",
                    model_name="Student",
                    object_id=student.pk,
                    description=f"PTC screen accessed for {student.admission_no} by {user.get_full_name()} ({user.get_role_display()})",
                    request=self.request,
                )

        academic_year = _active_academic_year()

        # 1. Learner Information
        ctx["student"] = student
        ctx["grade_class"] = GradeClass.objects.filter(name=student.class_name).first()
        ctx["class_teacher"] = _get_class_teacher_for_class(student.class_name)

        # 2. Academic Performance Grid
        perf_grid = get_academic_performance_grid(student, academic_year)
        ctx["performance_grid"] = perf_grid
        ctx["subjects"] = perf_grid["subjects"]
        ctx["terms_data"] = perf_grid["terms"]
        ctx["exam_weights"] = perf_grid.get("exam_weights", {})

        # 3. Overall Progress Trend (FR-PTC-029) with override support
        latest_avg = None
        for td in reversed(perf_grid["terms"]):
            if td["overall_average"] is not None:
                latest_avg = td["overall_average"]
                break
        ctx["latest_overall_average"] = latest_avg
        auto_trend = calculate_progress_trend(
            student, latest_avg, academic_year,
            all_terms_averages=perf_grid["all_terms_averages"],
        )
        # Check for teacher override
        trend_override = None
        if ptc_window and _is_class_teacher(user, student.class_name):
            trend_override = PTCProgressTrendOverride.objects.filter(
                student=student, ptc_window=ptc_window
            ).first()
        ctx["progress_trend"] = {
            "trend": trend_override.overridden_trend.replace("_", " ").title() if trend_override else auto_trend["trend"],
            "auto_derived": auto_trend["auto_derived"],
            "previous_average": auto_trend["previous_average"],
            "is_overridden": trend_override is not None,
            "auto_trend": auto_trend["trend"],
        }
        ctx["trend_override"] = trend_override

        # 4. Subject Teacher Comments
        ctx["ptc_comments"] = {}
        ctx["prev_ptc_comments"] = {}
        if ptc_window:
            comments = PTCSubjectComment.objects.filter(student=student, ptc_window=ptc_window)
            ctx["ptc_comments"] = {c.subject_name: c for c in comments}
            prev_window = PTCWindow.objects.filter(academic_year=academic_year).exclude(
                pk=ptc_window.pk
            ).order_by("-ptc_date").first()
            if prev_window:
                prev_comments = PTCSubjectComment.objects.filter(student=student, ptc_window=prev_window)
                ctx["prev_ptc_comments"] = {c.subject_name: c for c in prev_comments}

        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = academic_year
        ctx["core_subjects"] = get_core_subjects_for_student(student)

        # 5. Enrichment Grades
        enrichment_subjects = EnrichmentSubject.objects.filter(is_active=True)
        enrichment_grades = {}
        terms = list(academic_year.terms.all()) if academic_year else []
        for es in enrichment_subjects:
            enrichment_grades[es.subject.name] = {}
            for term in terms:
                enrichment_grades[es.subject.name][term.name] = EnrichmentGrade.objects.filter(
                    student=student, enrichment_subject=es, term=term
                ).first()
        ctx["enrichment_subjects"] = enrichment_subjects
        ctx["enrichment_grades"] = enrichment_grades
        ctx["enrichment_terms"] = terms

        # 6. Learner Attributes
        ctx["current_attribute_ratings"] = {}
        ctx["prev_attribute_ratings"] = {}
        ctx["prev_ptc_window"] = None
        if ptc_window:
            current_ratings = LearnerAttributeRatingEntry.objects.filter(student=student, ptc_window=ptc_window)
            ctx["current_attribute_ratings"] = {r.attribute_number: r.rating for r in current_ratings}
            prev_window = PTCWindow.objects.filter(academic_year=academic_year).exclude(
                pk=ptc_window.pk
            ).order_by("-ptc_date").first()
            if prev_window:
                prev_ratings = LearnerAttributeRatingEntry.objects.filter(student=student, ptc_window=prev_window)
                ctx["prev_attribute_ratings"] = {r.attribute_number: r.rating for r in prev_ratings}
                ctx["prev_ptc_window"] = prev_window

        ctx["learner_attributes"] = LEARNER_ATTRIBUTES

        # 7. Attendance summary (OI-PTC-01, gated by feature flag)
        from ptc.services import SHOW_ATTENDANCE
        ctx["show_attendance"] = SHOW_ATTENDANCE
        if SHOW_ATTENDANCE:
            ctx["attendance_summary"] = get_attendance_summary(student, academic_year)

        # 8. Edit permissions
        ctx["can_edit_attributes"] = _is_class_teacher(user, student.class_name)
        ctx["is_readonly"] = (
            not _is_class_teacher(user, student.class_name)
            and user.role not in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD}
        )
        return ctx


# ===========================================================================
# SUBJECT TEACHER — Comment Entry (FR-PTC-012 to FR-PTC-017)
# ===========================================================================

class PTCCommentEntryView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/comment_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcsubjectcomment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "comment_entry"
        user = self.request.user
        ptc_window = _current_ptc_window()
        academic_year = _active_academic_year()
        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = academic_year

        if not ptc_window:
            ctx["students_data"] = []
            ctx["message"] = "No PTC window configured."
            return ctx

        ctx["is_window_open"] = ptc_window.is_comment_entry_window_open
        teacher_subjects = _get_teacher_subjects(user)

        # Super admin / users with no assignments get all subjects from all teachers
        if not teacher_subjects:
            from hr.models import TeacherClassAssignment
            from academics.utils import get_current_term
            current_term = get_current_term()
            if current_term:
                all_subs = set()
                for a in TeacherClassAssignment.objects.filter(term=current_term).exclude(
                    subjects_taught={}
                ).exclude(subjects_taught=[]):
                    if a.subjects_taught:
                        all_subs.update(a.subjects_taught)
                teacher_subjects = all_subs

        ctx["teacher_subjects"] = teacher_subjects

        if user.role == UserRole.SUPER_ADMIN:
            students = _get_ptc_eligible_students(user)
        else:
            teacher_classes = _get_teacher_class_names(user)
            students = get_subject_teacher_students(user, "").filter(class_name__in=teacher_classes)

        students_data = []
        # Batch query all comments in one go instead of N*M queries
        all_comments = PTCSubjectComment.objects.filter(
            student__in=students, ptc_window=ptc_window, subject_name__in=teacher_subjects
        )
        comment_map = {}
        for c in all_comments:
            comment_map[(c.student_id, c.subject_name)] = c

        for student in students:
            student_comments = {}
            for subj in teacher_subjects:
                student_comments[subj] = comment_map.get((student.id, subj))
            students_data.append({"student": student, "comments": student_comments, "is_complete": False})

        ctx["students_data"] = students_data
        ctx["total_students"] = len(students_data)

        # Compact JSON for client-side rendering
        import json
        compact = []
        complete_count = 0
        for item in students_data:
            s = item["student"]
            comments = item["comments"]
            comments_dict = {}
            is_complete = True
            for subj in teacher_subjects:
                c = comments.get(subj)
                text = c.comment_text if c and c.comment_text else ""
                comments_dict[subj] = text
                if not text:
                    is_complete = False
            if is_complete:
                complete_count += 1
            item["is_complete"] = is_complete
            compact.append({
                "id": s.id,
                "first": s.first_name,
                "last": s.last_name,
                "class": s.class_name,
                "adm": s.admission_no,
                "comments": comments_dict,
                "ok": is_complete,
            })
        ctx["complete_count"] = complete_count
        ctx["comments_json"] = json.dumps(compact)
        ctx["teacher_subjects_json"] = json.dumps(list(teacher_subjects))
        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        ptc_window = _current_ptc_window()
        if not ptc_window:
            messages.error(request, "No PTC window configured.")
            return redirect("ptc:comment_entry")

        if not ptc_window.is_comment_entry_window_open and user.role != UserRole.SUPER_ADMIN:
            messages.error(request, "The comment entry window is currently closed.")
            return redirect("ptc:comment_entry")

        from students.models import Student
        teacher_subjects = _get_teacher_subjects(user)
        # Super admin / users with no assignments get all subjects
        if not teacher_subjects:
            from hr.models import TeacherClassAssignment
            from academics.utils import get_current_term
            current_term = get_current_term()
            if current_term:
                all_subs = set()
                for a in TeacherClassAssignment.objects.filter(term=current_term).exclude(
                    subjects_taught={}
                ).exclude(subjects_taught=[]):
                    if a.subjects_taught:
                        all_subs.update(a.subjects_taught)
                teacher_subjects = all_subs
        # Build slug→name lookup for case-insensitive subject matching
        subject_slug_map = {}
        for s in teacher_subjects:
            import re
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
            subject_slug_map[slug] = s
        updated = 0

        # POST field format: comment_{student_id}_{subject_slug}
        # subject_slug uses Django's slugify (lowercase, hyphens)
        for key, value in request.POST.items():
            if not key.startswith("comment_") or key == "comment_":
                continue
            parts = key.split("_", 2)
            if len(parts) < 3:
                continue
            student_id = parts[1]
            subject_slug = parts[2].lower()

            if not student_id.isdigit():
                continue

            subject_name = subject_slug_map.get(subject_slug, subject_slug.replace("-", " "))
            if subject_name not in teacher_subjects and user.role != UserRole.SUPER_ADMIN:
                continue

            student = get_object_or_404(Student, pk=int(student_id))
            if not can_enter_comment(user, student, subject_name, ptc_window):
                continue

            PTCSubjectComment.objects.update_or_create(
                student=student, ptc_window=ptc_window, subject_name=subject_name,
                defaults={"comment_text": (value or "").strip(), "entered_by": user},
            )
            updated += 1

            from audit.models import log_event
            log_event(
                actor=user,
                action_type="PTC_COMMENT_SAVED",
                model_name="PTCSubjectComment",
                object_id=student.pk,
                description=f"PTC comment saved for {student.admission_no} — {subject_name}",
                request=request
            )

        messages.success(request, f"Saved {updated} comment(s).")
        return redirect("ptc:comment_entry")


# ===========================================================================
# CLASS TEACHER — Learner Attributes Entry Grid (FR-PTC-018 to FR-PTC-022)
# ===========================================================================

class PTCAttributeEntryView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/attribute_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN]
    required_permission = "ptc.view_learnerattributeratingentry"

    def get(self, request, *args, **kwargs):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return self._ajax_completion_count(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "attribute_entry"
        user = self.request.user
        ptc_window = _current_ptc_window()
        academic_year = _active_academic_year()
        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = academic_year

        if not ptc_window:
            ctx["students_data"] = []
            ctx["message"] = "No PTC window configured."
            return ctx

        ctx["is_window_open"] = ptc_window.is_comment_entry_window_open

        from students.models import Student
        from academics.models import GradeClass, Department
        ecd_names = set(GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True))

        if user.role == UserRole.SUPER_ADMIN:
            students = Student.objects.filter(is_archived=False, status="active").exclude(
                class_name__in=ecd_names
            ).order_by("class_name", "last_name")
        else:
            teacher_classes = _get_teacher_class_names(user)
            students = Student.objects.filter(
                is_archived=False, status="active", class_name__in=teacher_classes,
            ).order_by("last_name", "first_name")

        # Batch fetch all ratings in 2 queries instead of N*12
        from collections import defaultdict
        all_ratings = LearnerAttributeRatingEntry.objects.filter(
            ptc_window=ptc_window, student__in=students,
        ).values("student_id", "attribute_number", "rating")
        ratings_by_student = defaultdict(dict)
        for r in all_ratings:
            ratings_by_student[r["student_id"]][r["attribute_number"]] = r["rating"]

        students_data = []
        complete_count = 0
        for student in students:
            student_ratings = ratings_by_student.get(student.id, {})
            ratings = {}
            is_complete = True
            for attr_num, _ in LEARNER_ATTRIBUTES:
                rating = student_ratings.get(attr_num, "")
                ratings[attr_num] = rating
                if not rating:
                    is_complete = False
            if is_complete:
                complete_count += 1
            students_data.append({"student": student, "ratings": ratings, "is_complete": is_complete})

        ctx["students_data"] = students_data
        ctx["total_students"] = len(students_data)
        ctx["complete_count"] = complete_count
        import json
        ctx["learner_attributes"] = LEARNER_ATTRIBUTES
        ctx["learner_attributes_json"] = json.dumps(LEARNER_ATTRIBUTES)

        # Compact JSON for client-side rendering (avoids rendering 40×12 rows server-side)
        compact = []
        for item in students_data:
            s = item["student"]
            compact.append({
                "id": s.id,
                "first": s.first_name,
                "last": s.last_name,
                "class": s.class_name,
                "adm": s.admission_no,
                "r": item["ratings"],
                "ok": item["is_complete"],
            })
        ctx["ratings_json"] = json.dumps(compact)
        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        ptc_window = _current_ptc_window()
        if not ptc_window:
            messages.error(request, "No PTC window configured.")
            return redirect("ptc:attribute_entry")

        if not ptc_window.is_comment_entry_window_open and user.role != UserRole.SUPER_ADMIN:
            messages.error(request, "The attribute entry window is currently closed.")
            return redirect("ptc:attribute_entry")

        from students.models import Student
        teacher_classes = _get_teacher_class_names(user)
        students = Student.objects.filter(is_archived=False, status="active", class_name__in=teacher_classes)

        updated = 0
        for student in students:
            for attr_num, _ in LEARNER_ATTRIBUTES:
                rating = request.POST.get(f"attr_{student.id}_{attr_num}", "").strip()
                if rating and rating not in dict(LearnerAttributeRating.choices):
                    continue
                if not _is_class_teacher(user, student.class_name):
                    continue
                LearnerAttributeRatingEntry.objects.update_or_create(
                    student=student, ptc_window=ptc_window, attribute_number=attr_num,
                    defaults={"rating": rating, "entered_by": user},
                )
                updated += 1

        if updated > 0:
            from audit.models import log_event
            log_event(
                actor=user,
                action_type="PTC_ATTRIBUTES_SAVED",
                model_name="LearnerAttributeRatingEntry",
                object_id=ptc_window.pk,
                description=f"Attribute ratings saved for {updated} student(s) in window {ptc_window}",
                request=request
            )

        messages.success(request, "Saved attribute ratings for your class.")
        return redirect("ptc:attribute_entry")

    def _ajax_completion_count(self, request):
        user = request.user
        ptc_window = _current_ptc_window()
        if not ptc_window:
            return JsonResponse({"complete": 0, "total": 0})

        teacher_classes = _get_teacher_class_names(user)
        from students.models import Student
        students = Student.objects.filter(is_archived=False, status="active", class_name__in=teacher_classes)
        total = students.count()
        # Batch count ratings per student in a single query instead of N queries
        from django.db.models import Count
        rating_counts = dict(
            LearnerAttributeRatingEntry.objects.filter(
                student__in=students, ptc_window=ptc_window,
            ).exclude(rating="").values("student_id").annotate(
                cnt=Count("id")
            ).values_list("student_id", "cnt")
        )
        complete = sum(1 for s in students if rating_counts.get(s.id, 0) >= 12)
        return JsonResponse({"complete": complete, "total": total})


# ===========================================================================
# SPECIALIST TEACHER — Enrichment Grade Entry (FR-PTC-006 to FR-PTC-010)
# ===========================================================================

class PTCEnrichmentGradeEntryView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/enrichment_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN]
    required_permission = "ptc.view_enrichmentgrade"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "enrichment_entry"
        user = self.request.user

        my_subjects = list(EnrichmentSubject.objects.filter(assigned_teacher=user, is_active=True))
        if user.role == UserRole.SUPER_ADMIN:
            my_subjects = list(EnrichmentSubject.objects.filter(is_active=True))

        academic_year = _active_academic_year()
        current_term = academic_year.terms.filter(is_locked=False).first() if academic_year else None

        from students.models import Student
        from academics.models import GradeClass, Department
        ecd_names = set(GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True))

        # Collect all unique students across all subjects
        student_ids = set()
        for es in my_subjects:
            qs = Student.objects.filter(is_archived=False, status="active").exclude(class_name__in=ecd_names)
            if es.assigned_class:
                qs = qs.filter(class_name=es.assigned_class.name)
            student_ids.update(qs.values_list("id", flat=True))
        all_students = Student.objects.filter(id__in=student_ids).order_by("last_name", "first_name")

        # Batch fetch all grades for current term
        from collections import defaultdict
        grades_by_student_subject = {}
        if current_term:
            all_grades = EnrichmentGrade.objects.filter(
                student__in=all_students, enrichment_subject__in=my_subjects, term=current_term,
            ).select_related("enrichment_subject")
            for g in all_grades:
                grades_by_student_subject[(g.student_id, g.enrichment_subject_id)] = g

        students_data = []
        for student in all_students:
            subject_grades = []
            for es in my_subjects:
                grade = grades_by_student_subject.get((student.id, es.id))
                is_locked = grade.is_locked if grade else False
                subject_grades.append({
                    "subject_id": es.id,
                    "subject_name": es.subject.name,
                    "grade": grade.letter_grade if grade else "",
                    "is_locked": is_locked,
                })
            students_data.append({"student": student, "subject_grades": subject_grades})

        import json
        ctx["students_data"] = students_data
        ctx["enrichment_json"] = json.dumps([{
            "id": s["student"].id,
            "first": s["student"].first_name,
            "last": s["student"].last_name,
            "class": s["student"].class_name,
            "adm": s["student"].admission_no,
            "subjects": s["subject_grades"],
        } for s in students_data])
        ctx["enrichment_subjects_json"] = json.dumps([{"id": es.id, "name": es.subject.name} for es in my_subjects])
        ctx["academic_year"] = academic_year
        ctx["current_term"] = current_term
        ctx["is_window_open"] = True  # enrichment doesn't use window gating
        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        academic_year = _active_academic_year()
        if not academic_year:
            messages.error(request, "No active academic year found.")
            return redirect("ptc:enrichment_entry")

        current_term = academic_year.terms.filter(is_locked=False).first()
        if not current_term:
            messages.error(request, "No active term found.")
            return redirect("ptc:enrichment_entry")

        from students.models import Student
        updated = 0
        for key, value in request.POST.items():
            if not key.startswith("grade_"):
                continue
            parts = key.split("_", 2)
            if len(parts) < 3:
                continue
            student = get_object_or_404(Student, pk=parts[1])
            enrichment_subject = get_object_or_404(EnrichmentSubject, pk=parts[2])

            if not can_enter_enrichment_grade(user, student, enrichment_subject.id):
                if user.role != UserRole.SUPER_ADMIN:
                    continue

            letter_grade = (value or "").strip()
            if letter_grade and letter_grade not in dict(EnrichmentLetterGrade.choices):
                continue

            existing_grade = EnrichmentGrade.objects.filter(
                student=student, enrichment_subject=enrichment_subject, term=current_term
            ).first()
            if existing_grade and existing_grade.is_locked and user.role != UserRole.SUPER_ADMIN:
                continue

            EnrichmentGrade.objects.update_or_create(
                student=student, enrichment_subject=enrichment_subject, term=current_term,
                defaults={"letter_grade": letter_grade, "entered_by": user},
            )
            updated += 1

        if updated > 0:
            from audit.models import log_event
            log_event(
                actor=user,
                action_type="ENRICHMENT_GRADE_SAVED",
                model_name="EnrichmentGrade",
                object_id=current_term.pk,
                description=f"Enrichment grades saved: {updated} grade(s) in {current_term}",
                request=request
            )

        messages.success(request, f"Saved {updated} enrichment grade(s).")
        return redirect("ptc:enrichment_entry")


# ===========================================================================
# PRIMARY HOD — Compliance Dashboard (FR-PTC-011, FR-PTC-016, FR-PTC-024)
# ===========================================================================

class PTCComplianceDashboardView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/compliance_dashboard.html"
    allowed_roles = [UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "ptc.view_ptcsubjectcomment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "compliance_dashboard"
        ptc_window = _current_ptc_window()
        academic_year = _active_academic_year()
        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = academic_year

        if ptc_window:
            ctx["comment_compliance"] = get_compliance_summary(academic_year, ptc_window, "comments")
            ctx["enrichment_compliance"] = get_compliance_summary(academic_year, ptc_window, "enrichment_grades")
            ctx["attribute_compliance"] = get_compliance_summary(academic_year, ptc_window, "attributes")
        else:
            ctx["comment_compliance"] = []
            ctx["enrichment_compliance"] = []
            ctx["attribute_compliance"] = []
        return ctx


# ===========================================================================
# HOS — PTC Generation Log (FR-PTC-030)
# ===========================================================================

class PTCGenerationLogView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/generation_log.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcgenerationlog"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "generation_log"
        logs = PTCGenerationLog.objects.select_related("student", "class_teacher", "ptc_window").all()

        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            logs = logs.filter(opened_at__date__gte=date_from)
        if date_to:
            logs = logs.filter(opened_at__date__lte=date_to)

        teacher_id = self.request.GET.get("teacher")
        if teacher_id:
            logs = logs.filter(class_teacher_id=teacher_id)

        ctx["logs"] = logs[:200]
        ctx["date_from"] = date_from or ""
        ctx["date_to"] = date_to or ""
        ctx["teacher_filter"] = teacher_id or ""

        from users.models import User
        ctx["class_teachers"] = User.objects.filter(role=UserRole.TEACHER, is_active=True).order_by("last_name")
        return ctx


# ===========================================================================
# ADMIN OFFICER — PTC Calendar Configuration (FR-PTC-001, FR-PTC-002)
# ===========================================================================

class PTCCalendarConfigView(RoleRequiredMixin, TemplateView):
    template_name = "ptc/calendar_config.html"
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcwindow"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "calendar_config"
        ctx["ptc_windows"] = PTCWindow.objects.select_related(
            "academic_year", "created_by", "last_modified_by",
        ).all()
        ctx["academic_years"] = _get_academic_years()
        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        action = request.POST.get("action", "create")

        # FR-PTC-001/002: window mutations (create / edit / publish) are
        # permission-driven via ptc.change_ptcwindow. Viewing the config page
        # only requires ptc.view_ptcwindow, so HODs/HOS can still browse.
        if not user.has_perm("ptc.change_ptcwindow"):
            from audit.models import log_event
            log_event(
                actor=user,
                action_type="PERMISSION_DENIED",
                model_name="PTCWindow",
                object_id=0,
                description=(
                    f"User {user} (role={user.role}) denied PTC window mutation ({action}) "
                    f"on {request.path} — missing perm: ptc.change_ptcwindow"
                ),
                request=request,
            )
            messages.error(request, "You do not have permission to create or modify PTC windows.")
            return redirect("ptc:calendar_config")

        if action == "create":
            return self._create_window(request, user)
        elif action == "edit_date":
            return self._edit_date(request, user)
        elif action == "publish":
            return self._publish_window(request, user)
        return redirect("ptc:calendar_config")

    def _create_window(self, request, user):
        from academics.models import AcademicYear
        ay_id = request.POST.get("academic_year_id")
        term_slot = request.POST.get("term_slot")
        ptc_date = request.POST.get("ptc_date")

        if not all([ay_id, term_slot, ptc_date]):
            messages.error(request, "All fields are required.")
            return redirect("ptc:calendar_config")

        if term_slot not in dict(TermSlot.choices):
            messages.error(request, "Invalid term slot.")
            return redirect("ptc:calendar_config")

        academic_year = get_object_or_404(AcademicYear, pk=ay_id)
        if PTCWindow.objects.filter(academic_year=academic_year, term_slot=term_slot).exists():
            messages.error(request, f"A PTC window for {dict(TermSlot.choices)[term_slot]} already exists.")
            return redirect("ptc:calendar_config")

        # FR-PTC-002: Maximum 2 PTC dates per academic year
        existing_count = PTCWindow.objects.filter(academic_year=academic_year).count()
        if existing_count >= 2:
            messages.error(request, "Maximum of 2 PTC dates per academic year reached.")
            return redirect("ptc:calendar_config")

        PTCWindow.objects.create(
            academic_year=academic_year, term_slot=term_slot, ptc_date=ptc_date,
            created_by=user, last_modified_by=user,
        )
        from audit.models import log_event
        log_event(
            actor=user, action_type="PTC_WINDOW_CREATED", model_name="PTCWindow",
            object_id=PTCWindow.objects.filter(academic_year=academic_year, term_slot=term_slot).first().pk,
            description=f"PTC window created for {academic_year.name} ({dict(TermSlot.choices)[term_slot]}) on {ptc_date}",
            request=request,
        )
        messages.success(request, f"PTC window created for {academic_year.name}.")
        return redirect("ptc:calendar_config")

    def _edit_date(self, request, user):
        window_id = request.POST.get("window_id")
        new_date = request.POST.get("new_ptc_date")
        reason = request.POST.get("reason", "")

        if not all([window_id, new_date]):
            messages.error(request, "Window ID and new date are required.")
            return redirect("ptc:calendar_config")

        window = get_object_or_404(PTCWindow, pk=window_id)
        if window.is_published and user.role != UserRole.SUPER_ADMIN:
            messages.error(request, "This window is published. Only Super Admin can change dates.")
            return redirect("ptc:calendar_config")

        if not reason.strip():
            messages.error(request, "A reason is required for date changes.")
            return redirect("ptc:calendar_config")

        old_date = window.ptc_date
        with transaction.atomic():
            window.ptc_date = new_date
            window.last_modified_by = user
            window.save()
            if window.is_published:
                PTCDateChangeLog.objects.create(
                    ptc_window=window, changed_by=user,
                    old_ptc_date=old_date, new_ptc_date=new_date,
                )
                from audit.models import log_event
                log_event(
                    actor=user, action_type="PTC_DATE_CHANGED", model_name="PTCWindow",
                    object_id=window.pk,
                    description=f"PTC date changed from {old_date} to {new_date} for {window.academic_year.name}. Reason: {reason}",
                    request=request,
                )

        messages.success(request, f"PTC date updated to {new_date}.")
        return redirect("ptc:calendar_config")

    def _publish_window(self, request, user):
        window_id = request.POST.get("window_id")
        window = get_object_or_404(PTCWindow, pk=window_id)
        window.is_published = True
        window.last_modified_by = user
        window.save()
        from audit.models import log_event
        log_event(
            actor=user, action_type="PTC_WINDOW_PUBLISHED", model_name="PTCWindow",
            object_id=window.pk,
            description=f"PTC window published for {window.academic_year.name} ({window.get_term_slot_display()})",
            request=request,
        )
        messages.success(request, f"PTC window for {window.get_term_slot_display()} published.")
        return redirect("ptc:calendar_config")


# ===========================================================================
# ENRICHMENT SUBJECT CONFIGURATION — Create / Update (OP 9.4)
# ===========================================================================

class PTCEnrichmentConfigView(RoleRequiredMixin, TemplateView):
    """
    Super Admin configuration page for enrichment subjects.
    Allows creating, updating, and managing enrichment subject assignments
    (teacher, class, weight percentage, active status).

    RBAC: Permission-gated via ptc.manage_enrichment_config (configurable
    in the Role Management UI, not hard-coded to a specific role).
    """
    template_name = "ptc/enrichment_config.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "ptc.manage_enrichment_config"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "enrichment_config"
        ctx["subjects"] = EnrichmentSubject.objects.select_related(
            "subject", "assigned_teacher", "assigned_class",
        ).all()
        from users.models import User as _U
        ctx["teachers"] = (
            _U.objects.filter(is_active=True, role=UserRole.TEACHER)
            .order_by("last_name", "first_name")
        )
        from academics.models import GradeClass, Subject
        ctx["classes"] = GradeClass.objects.all().order_by("name")
        ctx["available_subjects"] = Subject.objects.filter(
            is_enrichment=True, is_active=True
        ).exclude(
            id__in=EnrichmentSubject.objects.values_list("subject_id", flat=True)
        )
        ctx["form"] = EnrichmentSubjectConfigForm()
        ctx["editing_id"] = None
        return ctx

    def post(self, request, *args, **kwargs):
        user = request.user
        action = request.POST.get("action", "create")

        if action == "create":
            return self._create_subject(request, user)
        elif action == "update":
            return self._update_subject(request, user)
        elif action == "toggle_active":
            return self._toggle_active(request, user)
        return redirect("ptc:enrichment_config")

    def _create_subject(self, request, user):
        form = EnrichmentSubjectConfigForm(request.POST)
        if form.is_valid():
            subject = form.save()
            from audit.models import log_event
            log_event(
                actor=user,
                action_type="ENRICHMENT_SUBJECT_CREATED",
                model_name="EnrichmentSubject",
                object_id=subject.pk,
                description=(
                    f"Enrichment subject created: {subject.subject.name} "
                    f"(teacher={subject.assigned_teacher}, "
                    f"class={subject.assigned_class}, "
                    f"weight={subject.weight_percentage}%)"
                ),
                request=request,
            )
            messages.success(request, f"Enrichment subject '{subject.subject.name}' created successfully.")
        else:
            messages.error(request, "Could not create enrichment subject. Please check the form.")
        return redirect("ptc:enrichment_config")

    def _update_subject(self, request, user):
        subject = get_object_or_404(EnrichmentSubject, pk=request.POST.get("subject_id"))
        form = EnrichmentSubjectConfigForm(request.POST, instance=subject)
        if form.is_valid():
            old_name = subject.subject.name
            subject = form.save()
            from audit.models import log_event
            log_event(
                actor=user,
                action_type="ENRICHMENT_SUBJECT_UPDATED",
                model_name="EnrichmentSubject",
                object_id=subject.pk,
                description=(
                    f"Enrichment subject updated: {old_name} "
                    f"(teacher={subject.assigned_teacher}, "
                    f"class={subject.assigned_class}, "
                    f"weight={subject.weight_percentage}%, "
                    f"active={subject.is_active})"
                ),
                request=request,
            )
            messages.success(request, f"Enrichment subject '{subject.subject.name}' updated successfully.")
        else:
            messages.error(request, "Could not update enrichment subject. Please check the form.")
        return redirect("ptc:enrichment_config")

    def _toggle_active(self, request, user):
        subject = get_object_or_404(EnrichmentSubject, pk=request.POST.get("subject_id"))
        subject.is_active = not subject.is_active
        subject.save(update_fields=["is_active"])
        from audit.models import log_event
        log_event(
            actor=user,
            action_type="ENRICHMENT_SUBJECT_TOGGLED",
            model_name="EnrichmentSubject",
            object_id=subject.pk,
            description=f"Enrichment subject '{subject.subject.name}' active status set to {subject.is_active}",
            request=request,
        )
        status = "activated" if subject.is_active else "deactivated"
        messages.success(request, f"Enrichment subject '{subject.subject.name}' {status}.")
        return redirect("ptc:enrichment_config")


# ===========================================================================
# API: Attribute Entry Completion Count (FR-PTC-022)
# ===========================================================================

class PTCAttributeCompletionAPIView(RoleRequiredMixin, LoginRequiredMixin, View):
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL]
    required_permission = "ptc.view_learnerattributeratingentry"

    def get(self, request):

        ptc_window = _current_ptc_window()
        if not ptc_window:
            return JsonResponse({"complete": 0, "total": 0})

        teacher_classes = _get_teacher_class_names(request.user)
        from students.models import Student
        from django.db.models import Count, Q
        students = Student.objects.filter(is_archived=False, status="active", class_name__in=teacher_classes)
        total = students.count()
        complete = students.annotate(
            rated_count=Count(
                "ptc_attribute_ratings",
                filter=Q(ptc_attribute_ratings__ptc_window=ptc_window) & ~Q(ptc_attribute_ratings__rating=""),
            )
        ).filter(rated_count__gte=12).count()
        return JsonResponse({"complete": complete, "total": total})
