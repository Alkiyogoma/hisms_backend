# PTC Views  Learner Progress Report module.
#
# Adjustment note: All teachers share the UserRole.TEACHER role.
# Access differentiation is done via TeacherClassAssignment checks
# (is_class_teacher, subjects_taught) rather than role-based gating alone.

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from core.permissions import RoleRequiredMixin
from ptc.models import (
    LEARNER_ATTRIBUTES,
    LearnerAttributeRating,
    LearnerAttributeRatingEntry,
    PTCDateChangeLog,
    PTCGenerationLog,
    PTCSubjectComment,
    PTCWindow,
    TermSlot,
)
from ptc.services import (
    _get_class_teacher_for_class,
    _get_ecd_class_names,
    _get_teacher_class_names,
    _is_class_teacher,
    _is_ecd_student,
    active_students_in_class,
    attribute_completion,
    build_ptc_report,
    can_open_ptc,
    can_rate_attributes,
    comment_save_subjects_for,
    comment_subjects_for,
    current_academic_year,
    get_class_teacher_classes,
    get_current_ptc_window,
    get_marks_subjects_for_class,
    get_ptc_classes,
    get_teacher_subjects_by_class,
    is_ptc_admin,
    request_cache,
    year_ptc_windows,
)
from users.models import UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _active_academic_year():
    return current_academic_year()


class PTCCacheMixin:
    """Reads the current term, the user's class assignments and class
    subjects once per request (see ptc.services.request_cache)."""

    def dispatch(self, request, *args, **kwargs):
        with request_cache():
            return super().dispatch(request, *args, **kwargs)


def _current_ptc_window():
    """The PTC window the entry pages and the report's "before the PTC"
    check work on (open entry window, else next upcoming, else latest)."""
    return get_current_ptc_window(_active_academic_year())


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _real_user(request):
    """The signed-in person, even while they use "View as"."""
    return getattr(request, "real_user", None) or request.user


def _is_ecd_teacher(user):
    """Check if a teacher is assigned only to ECD classes."""
    names = _get_teacher_class_names(user)
    return bool(names) and names.issubset(_get_ecd_class_names())


def _get_academic_years():
    from academics.models import AcademicYear
    return AcademicYear.objects.all().order_by("-is_current", "-name")


def _class_choice(request, options: list[str], defaults: list[str]) -> str | None:
    """The class picked in the ?class= filter, else the user's first default
    class, else the first option."""
    wanted = (request.GET.get("class") or request.POST.get("class") or "").strip()
    if wanted in options:
        return wanted
    for d in defaults:
        if d in options:
            return d
    if options:
        # Otherwise the first class that has students, not an empty one.
        from students.models import Student
        filled = set(
            Student.objects.filter(is_archived=False, status="active", class_name__in=options)
            .values_list("class_name", flat=True).distinct()
        )
        return next((c for c in options if c in filled), options[0])
    return None


def _window_choice(request):
    """(selected PTC window, windows to choose from). ``?ptc=<id>`` picks a
    window of the current year; default is the current window."""
    year = _active_academic_year()
    windows = list(year_ptc_windows(year))
    current = _current_ptc_window()
    if current and current not in windows:
        windows.append(current)
    wanted = str(request.GET.get("ptc") or request.POST.get("ptc") or "")
    selected = next((w for w in windows if str(w.pk) == wanted), current)
    return selected, windows


def _entry_pages(user):
    """The PTC pages listed in the Entry filter: (url name, label)."""
    pages = [("ptc:student_picker", "Students overview")]
    if user.has_perm("ptc.change_ptcsubjectcomment"):
        pages.append(("ptc:comment_entry", "Subject comments"))
    if user.role in (UserRole.TEACHER, UserRole.SUPER_ADMIN) and user.has_perm("ptc.change_learnerattributeratingentry"):
        pages.append(("ptc:attribute_entry", "Student attributes"))
    return pages


def _filter_context(user, page, window, windows, class_options, selected_class,
                    subjects=None, subject="", footnote=""):
    """Context for templates/ptc/_filters.html (same look as the progress
    marking filters)."""
    return {
        "windows": [
            {"pk": w.pk, "selected": window and w.pk == window.pk,
             "label": f"{w.get_term_slot_display()} \u00b7 PTC {w.ptc_date:%d %b %Y}"}
            for w in windows
        ],
        "classes": [{"name": c, "selected": c == selected_class} for c in class_options],
        "pages": [
            {"url": reverse(name), "label": label, "active": name == page}
            for name, label in _entry_pages(user)
        ],
        "subjects": subjects,
        "subject": subject,
        "footnote": footnote,
    }


# ===========================================================================
# Students overview: students as rows, subjects as columns
# ===========================================================================

def _initials(name: str) -> str:
    return (name[:4] if len(name) > 4 else name).upper()


class PTCStudentPickerView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
    template_name = "ptc/student_picker.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcsubjectcomment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "student_picker"
        user = self.request.user
        if user.role == UserRole.TEACHER and _is_ecd_teacher(user):
            raise PermissionDenied("ECD teachers do not access the PTC module.")

        window, windows = _window_choice(self.request)
        all_classes = get_ptc_classes()
        if is_ptc_admin(user):
            options, defaults = all_classes, []
        else:
            taught = _get_teacher_class_names(user)
            options = [c for c in all_classes if c in taught]
            defaults = get_class_teacher_classes(user)
        selected = _class_choice(self.request, options, defaults)

        students = list(active_students_in_class(selected)) if selected else []
        subjects = get_marks_subjects_for_class(selected) if selected else []
        have = set()
        rated = {}
        if window and students:
            have = set(
                PTCSubjectComment.objects.filter(ptc_window=window, student__in=students, subject_name__in=subjects)
                .exclude(comment_text="").values_list("student_id", "subject_name")
            )
            from django.db.models import Count
            rated = dict(
                LearnerAttributeRatingEntry.objects.filter(ptc_window=window, student__in=students)
                .exclude(rating="").values("student_id").annotate(n=Count("id")).values_list("student_id", "n")
            )
        n_attr = len(LEARNER_ATTRIBUTES)
        can_open = bool(students) and can_open_ptc(user, students[0])
        rows = []
        for s in students:
            ticks = [(s.id, subj) in have for subj in subjects]
            attrs = rated.get(s.id, 0)
            rows.append({
                "student": s,
                "ticks": ticks,
                "attrs": attrs,
                "attrs_done": attrs >= n_attr,
                "done": all(ticks) and bool(subjects) and attrs >= n_attr,
            })

        ctx["filters"] = _filter_context(user, "ptc:student_picker", window, windows, options, selected)
        ctx["ptc_window"] = window
        ctx["selected_class"] = selected
        ctx["subjects"] = [{"name": s, "short": _initials(s)} for s in subjects]
        ctx["rows"] = rows
        ctx["attr_total"] = n_attr
        ctx["can_open"] = can_open
        ctx["complete_count"] = sum(1 for r in rows if r["done"])
        ctx["total_students"] = len(rows)
        ctx["active_academic_year"] = _active_academic_year()
        return ctx


# ===========================================================================
# PTC report: all three terms side by side
# ===========================================================================

def _log_ptc_open(request, student, ptc_window):
    """Record every opening of a PTC report, whoever opens it."""
    real = _real_user(request)
    viewed_as = request.user if getattr(request, "is_impersonating", False) else None
    PTCGenerationLog.objects.create(
        student=student,
        class_teacher=real,
        viewed_as=viewed_as if viewed_as and viewed_as.pk != real.pk else None,
        ptc_window=ptc_window,
        class_name=student.class_name or "",
    )


class PTCScreenView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
    """The learner's PTC report: marks subjects, subject teacher comments,
    remark-only subjects, student attributes, attendance and (when there are
    any) welfare and discipline incidents, for all three terms of the year."""
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
        self.student = student
        self.ptc_window, _windows = _window_choice(request)
        _log_ptc_open(request, student, self.ptc_window)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.views.reports import _photo_data_uri

        student = self.student
        user = self.request.user
        academic_year = _active_academic_year()
        ctx["ptc_tab"] = "student_picker"
        ctx["student"] = student
        ctx["photo_src"] = _photo_data_uri(student)
        ctx["photo_checked"] = True
        ctx["class_teacher"] = _get_class_teacher_for_class(student.class_name)
        ctx["academic_year"] = academic_year
        ctx["ptc_window"] = self.ptc_window
        ctx["report"] = build_ptc_report(student, academic_year, self.ptc_window)
        from urllib.parse import urlencode
        back = {"class": self.request.GET.get("class") or student.class_name}
        if self.ptc_window:
            back["ptc"] = self.ptc_window.pk
        ctx["back_url"] = f"{reverse('ptc:student_picker')}?{urlencode(back)}"
        is_ct = _is_class_teacher(user, student.class_name)
        ctx["can_edit_attributes"] = is_ct
        ctx["is_readonly"] = not is_ct and not user.has_role(
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,
        )
        return ctx


# ===========================================================================
# SUBJECT TEACHER  Comment Entry (mark-bearing subjects only)
# ===========================================================================

class PTCCommentEntryView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
    template_name = "ptc/comment_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcsubjectcomment"

    def _class_options(self, user):
        """Classes the user can pick, and those to default to."""
        all_classes = get_ptc_classes()
        if is_ptc_admin(user):
            return all_classes, []
        by_class = get_teacher_subjects_by_class(user)
        mine = [c for c in all_classes if c in by_class and comment_subjects_for(user, c)]
        return mine, mine

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "comment_entry"
        user = self.request.user
        ptc_window, windows = _window_choice(self.request)
        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = _active_academic_year()
        ctx["students_data"] = []
        if not ptc_window:
            return ctx

        ctx["is_window_open"] = ptc_window.is_comment_entry_window_open
        ctx["term_label"] = ptc_window.get_term_slot_display()

        options, defaults = self._class_options(user)
        selected = _class_choice(self.request, options, defaults)
        subjects = comment_subjects_for(user, selected) if selected else []
        writable = comment_save_subjects_for(user, selected) if selected else []
        ctx["may_write"] = bool(writable)
        ctx["can_save"] = bool(writable) and (
            ptc_window.is_comment_entry_window_open or user.role == UserRole.SUPER_ADMIN
        )
        students = list(active_students_in_class(selected)) if selected else []

        comment_map = {
            (c.student_id, c.subject_name): c.comment_text
            for c in PTCSubjectComment.objects.filter(
                ptc_window=ptc_window, student__in=students, subject_name__in=subjects,
            )
        }
        compact = []
        done = 0
        for s in students:
            comments = {subj: comment_map.get((s.id, subj), "") for subj in subjects}
            ok = bool(subjects) and all(v.strip() for v in comments.values())
            done += ok
            compact.append({
                "id": s.id, "first": s.first_name, "last": s.last_name,
                "class": s.class_name, "adm": s.admission_no, "comments": comments, "ok": ok,
            })

        subject = self.request.GET.get("subject", "")
        shown = [subject] if subject in subjects else subjects
        ctx["filters"] = _filter_context(
            user, "ptc:comment_entry", ptc_window, windows, options, selected,
            subjects=subjects, subject=subject if subject in subjects else "",
            footnote="Only mark-bearing subjects take a PTC comment. Write a full paragraph for each.",
        )
        ctx["class_options"] = options
        ctx["selected_class"] = selected
        ctx["subjects"] = subjects
        ctx["shown_subjects"] = shown
        ctx["students_data"] = compact
        ctx["total_students"] = len(students)
        ctx["complete_count"] = done
        ctx["comments_data"] = compact
        ctx["subjects_data"] = [{"name": s, "slug": _slug(s)} for s in shown]
        ctx["start_student"] = self.request.GET.get("student", "")
        return ctx

    def post(self, request, *args, **kwargs):
        """Save one learner's comments. Answers JSON for the page's fetch
        call; a plain form post is redirected back to the same learner."""
        from students.models import Student

        user = request.user
        ptc_window, _windows = _window_choice(request)

        def fail(msg, status=400):
            if _is_ajax(request):
                return JsonResponse({"ok": False, "error": msg}, status=status)
            messages.error(request, msg)
            return redirect(f"{request.path}?class={request.POST.get('class', '')}&ptc={request.POST.get('ptc', '')}")

        if not ptc_window:
            return fail("No PTC date is set up yet, so comments cannot be saved.")
        if not ptc_window.is_comment_entry_window_open and user.role != UserRole.SUPER_ADMIN:
            return fail("The comment entry window is closed, so comments cannot be changed.", 403)

        student_id = request.POST.get("student_id", "")
        if not student_id.isdigit():
            return fail("No student was chosen.")
        student = get_object_or_404(Student, pk=int(student_id))
        allowed = comment_save_subjects_for(user, student.class_name)
        if not allowed:
            if is_ptc_admin(user):
                return fail("Subject comments are written by the subject teacher. You can read them here but not change them.", 403)
            return fail(f"You do not teach a mark-bearing subject in {student.class_name}, so you cannot save comments there.", 403)

        by_slug = {_slug(s): s for s in allowed}
        saved = []
        with transaction.atomic():
            for key, value in request.POST.items():
                if not key.startswith("comment_"):
                    continue
                subject = by_slug.get(key[len("comment_"):].lower())
                if not subject:
                    continue
                PTCSubjectComment.objects.update_or_create(
                    student=student, ptc_window=ptc_window, subject_name=subject,
                    defaults={"comment_text": (value or "").strip(), "entered_by": user},
                )
                saved.append(subject)

        if saved:
            from audit.models import log_event
            log_event(
                actor=user, action_type="PTC_COMMENT_SAVED", model_name="PTCSubjectComment",
                object_id=student.pk,
                description=f"PTC comments saved for {student.admission_no}: {', '.join(saved)}",
                request=request,
            )

        stored = dict(
            PTCSubjectComment.objects.filter(student=student, ptc_window=ptc_window, subject_name__in=allowed)
            .values_list("subject_name", "comment_text")
        )
        comments = {s: stored.get(s, "") for s in allowed}
        done = all((v or "").strip() for v in comments.values())

        if _is_ajax(request):
            return JsonResponse({"ok": True, "saved": len(saved), "done": done, "comments": comments})
        messages.success(request, f"Saved comments for {student.get_full_name()}.")
        return redirect(f"{request.path}?class={student.class_name}&ptc={ptc_window.pk}&student={student.pk}")


# ===========================================================================
# CLASS TEACHER  Student Attributes (E / G / S / N)
# ===========================================================================

class PTCAttributeEntryView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
    template_name = "ptc/attribute_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN]
    required_permission = "ptc.view_learnerattributeratingentry"

    def get(self, request, *args, **kwargs):
        if _is_ajax(request):
            return self._ajax_completion_count(request)
        return super().get(request, *args, **kwargs)

    def _class_options(self, user):
        all_classes = get_ptc_classes()
        if user.role == UserRole.SUPER_ADMIN:
            return all_classes, []
        mine = get_class_teacher_classes(user)
        taught = _get_teacher_class_names(user)
        return [c for c in all_classes if c in taught], mine

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "attribute_entry"
        user = self.request.user
        ptc_window, windows = _window_choice(self.request)
        ctx["ptc_window"] = ptc_window
        ctx["academic_year"] = _active_academic_year()
        ctx["students_data"] = []
        if not ptc_window:
            return ctx

        ctx["is_window_open"] = ptc_window.is_comment_entry_window_open
        options, defaults = self._class_options(user)
        selected = _class_choice(self.request, options, defaults)
        students = list(active_students_in_class(selected)) if selected else []
        may_rate = bool(selected) and can_rate_attributes(user, selected)
        ctx["can_rate"] = may_rate
        ctx["can_save"] = may_rate and (ptc_window.is_comment_entry_window_open or user.role == UserRole.SUPER_ADMIN)
        ctx["class_teacher"] = _get_class_teacher_for_class(selected) if selected else None

        ratings = {}
        for r in LearnerAttributeRatingEntry.objects.filter(ptc_window=ptc_window, student__in=students).exclude(rating=""):
            ratings.setdefault(r.student_id, {})[r.attribute_number] = r.rating
        compact = []
        done = 0
        for s in students:
            r = ratings.get(s.id, {})
            ok = len(r) >= len(LEARNER_ATTRIBUTES)
            done += ok
            compact.append({
                "id": s.id, "first": s.first_name, "last": s.last_name,
                "class": s.class_name, "adm": s.admission_no, "r": r, "ok": ok,
            })

        ctx["filters"] = _filter_context(
            user, "ptc:attribute_entry", ptc_window, windows, options, selected,
            footnote="Rated by the class teacher: E Excellent, G Good, S Satisfactory, N Needs improvement.",
        )
        ctx["class_options"] = options
        ctx["selected_class"] = selected
        ctx["students_data"] = compact
        ctx["total_students"] = len(students)
        ctx["complete_count"] = done
        ctx["learner_attributes"] = LEARNER_ATTRIBUTES
        ctx["attributes_data"] = LEARNER_ATTRIBUTES
        ctx["ratings_data"] = compact
        ctx["start_student"] = self.request.GET.get("student", "")
        return ctx

    def post(self, request, *args, **kwargs):
        """Save one learner's ratings. JSON for the page's fetch call; a plain
        form post is redirected back to the same learner."""
        from students.models import Student

        user = request.user
        ptc_window, _windows = _window_choice(request)

        def fail(msg, status=400):
            if _is_ajax(request):
                return JsonResponse({"ok": False, "error": msg}, status=status)
            messages.error(request, msg)
            return redirect(f"{request.path}?class={request.POST.get('class', '')}&ptc={request.POST.get('ptc', '')}")

        if not ptc_window:
            return fail("No PTC date is set up yet, so ratings cannot be saved.")
        if not ptc_window.is_comment_entry_window_open and user.role != UserRole.SUPER_ADMIN:
            return fail("The attribute entry window is closed, so ratings cannot be changed.", 403)

        student_id = request.POST.get("student_id", "")
        if not student_id.isdigit():
            return fail("No student was chosen.")
        student = get_object_or_404(Student, pk=int(student_id))
        if not can_rate_attributes(user, student.class_name):
            return fail(
                f"Only the class teacher of {student.class_name} can save student attributes. "
                "Your ratings were not saved.", 403,
            )

        valid = dict(LearnerAttributeRating.choices)
        changed = 0
        with transaction.atomic():
            for num, _label in LEARNER_ATTRIBUTES:
                key = f"attr_{num}"
                if key not in request.POST:
                    continue
                rating = request.POST.get(key, "").strip().upper()
                if rating and rating not in valid:
                    transaction.set_rollback(True)
                    return fail(f"'{rating}' is not a valid rating for attribute {num}.")
                LearnerAttributeRatingEntry.objects.update_or_create(
                    student=student, ptc_window=ptc_window, attribute_number=num,
                    defaults={"rating": rating, "entered_by": user},
                )
                changed += 1

        if changed:
            from audit.models import log_event
            log_event(
                actor=user, action_type="PTC_ATTRIBUTES_SAVED", model_name="LearnerAttributeRatingEntry",
                object_id=student.pk,
                description=f"Attribute ratings saved for {student.admission_no} in window {ptc_window}",
                request=request,
            )

        stored = dict(
            LearnerAttributeRatingEntry.objects.filter(student=student, ptc_window=ptc_window)
            .exclude(rating="").values_list("attribute_number", "rating")
        )
        done = len(stored) >= len(LEARNER_ATTRIBUTES)
        if _is_ajax(request):
            return JsonResponse({"ok": True, "saved": changed, "done": done, "ratings": stored})
        messages.success(request, f"Saved ratings for {student.get_full_name()}.")
        return redirect(f"{request.path}?class={student.class_name}&ptc={ptc_window.pk}&student={student.pk}")

    def _ajax_completion_count(self, request):
        ptc_window = _current_ptc_window()
        if not ptc_window:
            return JsonResponse({"complete": 0, "total": 0})
        options, defaults = self._class_options(request.user)
        selected = _class_choice(request, options, defaults)
        students = list(active_students_in_class(selected)) if selected else []
        done = attribute_completion(students, ptc_window)
        return JsonResponse({"class": selected, "complete": sum(done.values()), "total": len(students)})


# ===========================================================================
# PTC Log: every opening of a PTC report
# ===========================================================================

class PTCGenerationLogView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
    template_name = "ptc/generation_log.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "ptc.view_ptcgenerationlog"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["ptc_tab"] = "generation_log"
        logs = PTCGenerationLog.objects.select_related("student", "class_teacher", "viewed_as", "ptc_window").all()

        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            logs = logs.filter(opened_at__date__gte=date_from)
        if date_to:
            logs = logs.filter(opened_at__date__lte=date_to)

        opener_id = self.request.GET.get("teacher")
        if opener_id and opener_id.isdigit():
            logs = logs.filter(class_teacher_id=opener_id)

        from django.core.paginator import Paginator
        page_obj = Paginator(logs.order_by("-opened_at", "-pk"), 50).get_page(self.request.GET.get("page"))
        ctx["page_obj"] = page_obj
        ctx["logs"] = page_obj.object_list
        ctx["date_from"] = date_from or ""
        ctx["date_to"] = date_to or ""
        ctx["teacher_filter"] = opener_id or ""

        from users.models import User
        ctx["class_teachers"] = User.objects.filter(
            pk__in=PTCGenerationLog.objects.values("class_teacher_id")
        ).order_by("first_name", "last_name")
        return ctx


# ===========================================================================
# ADMIN OFFICER  PTC Calendar Configuration (FR-PTC-001, FR-PTC-002)
# ===========================================================================

class PTCCalendarConfigView(PTCCacheMixin, RoleRequiredMixin, TemplateView):
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
                    f"on {request.path}: missing perm: ptc.change_ptcwindow"
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

        # One PTC window per term: at most three per academic year
        existing_count = PTCWindow.objects.filter(academic_year=academic_year).count()
        if existing_count >= 3:
            messages.error(request, "Maximum of 3 PTC dates per academic year (one per term) reached.")
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
# API: Attribute Entry Completion Count (FR-PTC-022)
# ===========================================================================

class PTCAttributeCompletionAPIView(PTCCacheMixin, RoleRequiredMixin, LoginRequiredMixin, View):
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL]
    required_permission = "ptc.view_learnerattributeratingentry"

    def get(self, request):
        ptc_window = _current_ptc_window()
        if not ptc_window:
            return JsonResponse({"complete": 0, "total": 0})
        from students.models import Student
        students = list(Student.objects.filter(
            is_archived=False, status="active", class_name__in=_get_teacher_class_names(request.user),
        ))
        done = attribute_completion(students, ptc_window)
        return JsonResponse({"complete": sum(done.values()), "total": len(students)})
