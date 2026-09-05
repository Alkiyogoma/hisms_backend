from __future__ import annotations

from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect, get_object_or_404
from django.views.generic import CreateView, TemplateView, View

from audit.models import log_event

from timetable.forms import TimetableSlotForm
from timetable.models import TimetableSlot, Weekday
from users.models import User, UserRole
from core.permissions import RoleRequiredMixin


class TimetableView(RoleRequiredMixin, TemplateView):
    template_name = "timetable/index.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER, UserRole.TEACHER]
    required_permission = "timetable.view_timetableslot"

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["timetable/_timetable_content.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import timedelta, date as date_type
        view_type = self.request.GET.get("view", "grid")
        day = (self.request.GET.get("day") or "").strip() or Weekday.MON
        q = (self.request.GET.get("q") or "").strip()
        class_filter = self.request.GET.get("class_name", "").strip()
        teacher_filter = self.request.GET.get("teacher_id", "").strip()
        week_offset = int(self.request.GET.get("week", "0"))

        from academics.utils import get_current_term
        current_term = get_current_term()

        # Calculate the Monday of the target week
        today = date_type.today()
        this_monday = today - timedelta(days=today.weekday())
        target_monday = this_monday + timedelta(weeks=week_offset)
        target_sunday = target_monday + timedelta(days=6)

        # Default teacher filter: only auto-filter on initial page load (no teacher_id param at all).
        # If the user explicitly selects 'All Teachers' (empty value) or another teacher, respect
        # that choice so teachers can view their own timetable as well as other teachers' timetables.
        user = self.request.user
        if "teacher_id" not in self.request.GET and user.role == UserRole.TEACHER:
            teacher_filter = str(user.pk)

        slots_qs = TimetableSlot.objects.select_related("teacher", "room", "subject").filter(
            term=current_term
        ).order_by("start_time")

        # Non-admin users only see published timetable
        if user.role not in {UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER}:
            slots_qs = slots_qs.filter(is_published=True)

        # FR-TT-004: HODs see all timetable but their department is the default filter
        HOD_DEPT_MAP = {
            UserRole.PRIMARY_HOD: "primary",
            UserRole.ECD_HOD: "ecd",
            UserRole.LOWER_SECONDARY_HOD: "lower_secondary",
        }
        hod_default_dept = None
        if user.role in HOD_DEPT_MAP:
            hod_default_dept = HOD_DEPT_MAP[user.role]

        if view_type == "list":
            slots_qs = slots_qs.filter(day_of_week=day)
        
        if q:
            slots_qs = slots_qs.filter(
                Q(class_name__icontains=q)
                | Q(subject_name__icontains=q)
                | Q(teacher__first_name__icontains=q)
                | Q(teacher__last_name__icontains=q)
            )
        
        if class_filter:
            slots_qs = slots_qs.filter(class_name=class_filter)
        if teacher_filter:
            slots_qs = slots_qs.filter(teacher_id=teacher_filter)

        slots = list(slots_qs)

        # For Grid View: group by time slot
        grid_data = {}
        if view_type == "grid":
            weekday_map = {v: k for k, v in Weekday.choices}
            time_slots = sorted(list(set((s.start_time, s.end_time) for s in slots)))
            for ts in time_slots:
                time_key = f"{ts[0].strftime('%H:%M')} - {ts[1].strftime('%H:%M')}"
                grid_data[time_key] = { d[0]: [] for d in Weekday.choices }
                for s in slots:
                    if s.start_time == ts[0] and s.end_time == ts[1]:
                        day = weekday_map.get(s.day_of_week, s.day_of_week)
                        grid_data[time_key][day].append(s)

        # Lesson plan status for each teacher+class+day combo
        from academics.models import LessonPlan, LessonPlanStatus
        lp_status_map = {}
        if slots:
            teacher_ids = list(set(s.teacher_id for s in slots if s.teacher_id))
            class_names = list(set(s.class_name for s in slots))
            days_in_week = list(set(s.day_of_week for s in slots))
            if teacher_ids and class_names:
                plans = LessonPlan.objects.filter(
                    teacher_id__in=teacher_ids,
                    class_name__in=class_names,
                    week_start_date=target_monday,
                    day_of_week__in=days_in_week,
                ).values("teacher_id", "class_name", "subject_name", "day_of_week", "status", "pk")
                for p in plans:
                    key = f"{p['teacher_id']}:{p['class_name']}:{p['subject_name']}:{p['day_of_week']}"
                    priority = {
                        LessonPlanStatus.APPROVED: 1,
                        LessonPlanStatus.SUBMITTED: 2,
                        LessonPlanStatus.REVISION_REQUESTED: 3,
                        LessonPlanStatus.MISSING: 4,
                        LessonPlanStatus.DRAFT: 5,
                        LessonPlanStatus.REJECTED: 6,
                    }
                    new_pri = priority.get(p["status"], 99)
                    existing_pri = lp_status_map.get(key, (None, 99, None))[1]
                    if new_pri < existing_pri:
                        lp_status_map[key] = (p["status"], new_pri, p["pk"])

        # Attach LP status to each slot for easy template access
        for s in slots:
            key = f"{s.teacher_id}:{s.class_name}:{s.subject_name}:{s.day_of_week}"
            status_tuple = lp_status_map.get(key)
            s.lp_status = status_tuple[0] if status_tuple else None
            s.lp_pk = status_tuple[2] if status_tuple else None

        ctx["current_term"] = current_term
        ctx["view_type"] = view_type
        ctx["day"] = day
        ctx["days"] = Weekday.choices
        ctx["q"] = q
        ctx["class_filter"] = class_filter
        ctx["teacher_filter"] = teacher_filter
        ctx["slots"] = slots
        ctx["grid_data"] = grid_data
        ctx["week_offset"] = week_offset
        ctx["target_monday"] = target_monday
        ctx["target_sunday"] = target_sunday
        
        from hr.models import TeacherClassAssignment
        # Only show teachers with active class assignments for the current term
        assigned_user_ids = TeacherClassAssignment.objects.filter(
            term=current_term
        ).values_list("teacher__user_id", flat=True).distinct()
        ctx["teachers"] = User.objects.filter(
            id__in=assigned_user_ids,
            is_active=True,
        ).exclude(
            first_name="", last_name=""
        ).order_by("first_name", "last_name")
        from academics.models import GradeClass
        ctx["class_names"] = GradeClass.objects.values_list("name", flat=True).order_by("sort_order", "name")

        ctx["can_edit"] = user.has_perm("timetable.change_timetableslot")
        ctx["can_create_lp"] = user.has_perm("academics.add_lessonplan")
        ctx["can_publish"] = user.has_perm("timetable.change_timetableslot")
        ctx["hod_default_dept"] = hod_default_dept

        from timetable.models import EcdBreakConfig
        ctx["ecd_breaks"] = list(EcdBreakConfig.objects.all().order_by("class_name"))

        # Teachers with class assignments but no timetable slots this term
        teachers_with_slots = TimetableSlot.objects.filter(
            term=current_term
        ).values_list("teacher_id", flat=True).distinct()
        unassigned_teachers = User.objects.filter(
            id__in=assigned_user_ids,
        ).exclude(id__in=teachers_with_slots).order_by("first_name", "last_name")
        ctx["unassigned_teachers"] = unassigned_teachers

        return ctx


class TimetableSlotCreateView(RoleRequiredMixin, CreateView):
    template_name = "timetable/new_slot.html"
    form_class = TimetableSlotForm
    success_url = "/timetable/"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.add_timetableslot"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in {
            UserRole.SUPER_ADMIN,
            UserRole.PRIMARY_HOD,
            UserRole.ECD_HOD,
            UserRole.LOWER_SECONDARY_HOD,
            UserRole.ADMIN_OFFICER,
        }:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        repeat_raw = self.request.POST.get("repeat_days", "").strip()
        days = [d.strip() for d in repeat_raw.split(",") if d.strip()] if repeat_raw else []

        if len(days) <= 1:
            return super().form_valid(form)

        from django.contrib import messages as msgs
        created = 0
        for day in days:
            slot = form.instance.__class__(
                term=form.cleaned_data["term"],
                class_name=form.cleaned_data["class_name"],
                subject_name=form.cleaned_data["subject_name"],
                teacher=form.cleaned_data["teacher"],
                day_of_week=day,
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
            try:
                slot.full_clean()
                slot.save()
                created += 1
            except Exception:
                pass
        msgs.success(self.request, f"Created {created} timetable slot(s).")
        from django.shortcuts import redirect
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["dept_subject_data"] = self._get_dept_subject_data()
        return ctx

    @staticmethod
    def _get_dept_subject_data():
        import json
        from academics.models import GradeClass, Subject, Department
        from hr.models import TeacherClassAssignment
        from users.models import User, UserRole
        from timetable.models import TimetableSlot as TSlot
        from academics.utils import get_current_term

        current_term = get_current_term()

        data = {
            "classes": {},
            "subjects": {},
        }

        # Build class data: subjects from M2M (GradeClass.subjects)
        for gc in GradeClass.objects.prefetch_related("subjects").all().order_by("name"):
            subject_names = list(
                gc.subjects.filter(is_active=True).values_list("name", flat=True).order_by("name")
            )
            data["classes"][gc.name] = {
                "department": gc.department,
                "subject_names": subject_names,
            }

        # Also index subjects by department for reverse lookup
        all_subjects = list(
            Subject.objects.filter(is_active=True).values("name", "departments")
        )
        for dept_choice in Department.choices:
            dept_code = dept_choice[0]
            data["subjects"][dept_code] = sorted(
                s["name"] for s in all_subjects
                if dept_code in (s["departments"] or [])
            )

        # FR-TT-008: Teachers per class + subject-teacher mapping
        assignments_qs = TeacherClassAssignment.objects.select_related(
            "teacher", "teacher__user", "grade_class"
        )
        if current_term:
            assignments_qs = assignments_qs.filter(term=current_term)

        teacher_map = {}
        seen = set()
        for a in assignments_qs:
            key = (a.teacher_id, a.grade_class.name)
            if key in seen:
                continue
            seen.add(key)
            full_name = a.teacher.full_name or a.teacher.user.get_full_name() or a.teacher.user.username
            teacher_map.setdefault(a.grade_class.name, []).append({
                "id": a.teacher.user_id,
                "full_name": full_name,
            })

        # Only classes with actual TCAs get teachers — no fallback that
        # silently adds unassigned teachers to the dropdown.
        data["teachers_by_class"] = teacher_map

        # FR-TT-009: Subjects per teacher (per-class + cross-class aggregate)
        subjects_by_teacher_class = {}
        subjects_by_teacher = {}
        teachers_by_subject = {}
        for a in assignments_qs:
            tid = str(a.teacher.user_id)
            cname = a.grade_class.name
            full_name = a.teacher.full_name or a.teacher.user.get_full_name() or a.teacher.user.username
            # Per-class subjects
            subjects_by_teacher_class[f"{tid}:{cname}"] = list(a.subjects_taught or [])
            # Cross-class aggregate
            if tid not in subjects_by_teacher:
                subjects_by_teacher[tid] = set()
            subjects_by_teacher[tid].update(a.subjects_taught or [])
            # Teachers per subject
            for subj in (a.subjects_taught or []):
                if subj not in teachers_by_subject:
                    teachers_by_subject[subj] = []
                if not any(t["id"] == a.teacher.user_id for t in teachers_by_subject[subj]):
                    teachers_by_subject[subj].append({
                        "id": a.teacher.user_id,
                        "full_name": full_name,
                    })

        # For fallback teachers (no TCA this term), pull subjects from any
        # other-term TCA so the JS filter doesn't eliminate them.
        all_term_tcas = TeacherClassAssignment.objects.select_related(
            "teacher", "teacher__user", "grade_class"
        )
        for a in all_term_tcas:
            tid = str(a.teacher.user_id)
            cname = a.grade_class.name
            key = f"{tid}:{cname}"
            if key not in subjects_by_teacher_class and (a.subjects_taught or []):
                subjects_by_teacher_class[key] = list(a.subjects_taught)

        data["subjects_by_teacher_class"] = subjects_by_teacher_class
        data["subjects_by_teacher"] = {
            k: sorted(v) for k, v in subjects_by_teacher.items()
        }
        data["teachers_by_subject"] = teachers_by_subject

        return json.dumps(data)

    def form_valid(self, form):
        repeat_raw = self.request.POST.get("repeat_days", "").strip()
        repeat_days = [d.strip() for d in repeat_raw.split(",") if d.strip()] if repeat_raw else []
        if not repeat_days:
            form.add_error(None, "Please select at least one day.")
            return self.form_invalid(form)

        term = form.cleaned_data["term"]
        class_name = form.cleaned_data["class_name"]
        subject_name = form.cleaned_data["subject_name"]
        teacher = form.cleaned_data["teacher"]
        start_time = form.cleaned_data["start_time"]
        end_time = form.cleaned_data["end_time"]
        room = form.cleaned_data.get("room")

        created = []
        errors = []

        for day in repeat_days:
            slot = TimetableSlot(
                term=term,
                class_name=class_name,
                subject_name=subject_name,
                teacher=teacher,
                day_of_week=day,
                start_time=start_time,
                end_time=end_time,
                room=room,
            )
            try:
                slot.full_clean()
                slot.save()
                created.append(dict(Weekday.choices).get(day, day))
            except Exception as e:
                error_msg = str(e)
                if hasattr(e, "message_dict"):
                    error_msg = "; ".join(
                        f"{k}: {', '.join(v) if isinstance(v, list) else v}"
                        for k, v in e.message_dict.items()
                    )
                elif hasattr(e, "messages"):
                    error_msg = "; ".join(e.messages)
                errors.append(f"{dict(Weekday.choices).get(day, day)}: {error_msg}")

        if created:
            day_names = ", ".join(created)
            if len(created) == 1:
                messages.success(self.request, f"Timetable slot created for {day_names}.")
            else:
                messages.success(self.request, f"Created {len(created)} timetable slots ({day_names}).")
            log_event(
                actor=self.request.user,
                action_type="TIMETABLE_SLOT_CREATED",
                model_name="TimetableSlot",
                object_id=term.pk if term else "",
                description=f"Created {len(created)} slot(s) for {class_name} / {subject_name} on {day_names}",
                after={"class_name": class_name, "subject_name": subject_name, "teacher": teacher.pk, "days": created},
                request=self.request,
            )

        if errors:
            err_text = "; ".join(errors)
            if created:
                messages.warning(self.request, f"Skipped: {err_text}")
            else:
                messages.error(self.request, f"Could not create slots: {err_text}")

        return redirect(self.success_url)


from django.views.generic.edit import UpdateView


class TimetableSlotUpdateView(RoleRequiredMixin, UpdateView):
    """Edit an existing timetable slot."""
    model = TimetableSlot
    form_class = TimetableSlotForm
    template_name = "timetable/slot_form.html"
    success_url = "/timetable/"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.change_timetableslot"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in {
            UserRole.SUPER_ADMIN,
            UserRole.PRIMARY_HOD,
            UserRole.ECD_HOD,
            UserRole.LOWER_SECONDARY_HOD,
            UserRole.ADMIN_OFFICER,
        }:
            raise PermissionDenied()
        # Block editing published slots unless SA
        pk = kwargs.get("pk")
        if pk and request.user.role != UserRole.SUPER_ADMIN:
            slot = TimetableSlot.objects.filter(pk=pk).first()
            if slot and slot.is_published:
                from django.core.exceptions import PermissionDenied as PD
                raise PD("Cannot edit a published timetable slot. Unpublish it first.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_edit"] = True
        ctx["slot"] = self.object
        ctx["dept_subject_data"] = TimetableSlotCreateView._get_dept_subject_data()
        return ctx

    def form_valid(self, form):
        old = TimetableSlot.objects.get(pk=self.object.pk)
        before = {"class_name": old.class_name, "subject_name": old.subject_name, "teacher": old.teacher_id, "day_of_week": old.day_of_week, "start_time": str(old.start_time), "end_time": str(old.end_time)}
        messages.success(self.request, "Timetable slot updated.")
        response = super().form_valid(form)
        new = self.object
        after = {"class_name": new.class_name, "subject_name": new.subject_name, "teacher": new.teacher_id, "day_of_week": new.day_of_week, "start_time": str(new.start_time), "end_time": str(new.end_time)}
        log_event(
            actor=self.request.user,
            action_type="TIMETABLE_SLOT_UPDATED",
            model_name="TimetableSlot",
            object_id=new.pk,
            description=f"Updated slot: {new.class_name} {new.day_of_week} {new.start_time}-{new.end_time} {new.subject_name}",
            before=before,
            after=after,
            request=self.request,
        )
        return response


class TimetableSlotDeleteView(RoleRequiredMixin, View):
    """Delete a timetable slot (POST-only)."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.delete_timetableslot"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in {
            UserRole.SUPER_ADMIN,
            UserRole.PRIMARY_HOD,
            UserRole.ECD_HOD,
            UserRole.LOWER_SECONDARY_HOD,
            UserRole.ADMIN_OFFICER,
        }:
            raise PermissionDenied()
        # Block deleting published slots unless SA
        pk = kwargs.get("pk")
        if pk and request.user.role != UserRole.SUPER_ADMIN:
            slot = TimetableSlot.objects.filter(pk=pk).first()
            if slot and slot.is_published:
                from django.core.exceptions import PermissionDenied as PD
                raise PD("Cannot delete a published timetable slot. Unpublish it first.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, pk):
        slot = get_object_or_404(TimetableSlot, pk=pk)
        deleted = {"class_name": slot.class_name, "subject_name": slot.subject_name, "teacher": slot.teacher_id, "day_of_week": slot.day_of_week, "start_time": str(slot.start_time), "end_time": str(slot.end_time)}
        slot.delete()
        log_event(
            actor=request.user,
            action_type="TIMETABLE_SLOT_DELETED",
            model_name="TimetableSlot",
            object_id=pk,
            description=f"Deleted slot: {deleted['class_name']} {deleted['day_of_week']} {deleted['start_time']}-{deleted['end_time']} {deleted['subject_name']}",
            before=deleted,
            request=request,
        )
        messages.success(request, "Timetable slot deleted.")
        return redirect("/timetable/")


class BulkTimetableCreateView(RoleRequiredMixin, View):
    """Bulk-create multiple timetable slots at once — UI for adding many slots with
    different subjects, departments, colors, and teachers."""
    login_url = "/accounts/login/"
    template_name = "timetable/bulk_create.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.add_timetableslot"

    ALLOWED_ROLES = {
        UserRole.SUPER_ADMIN,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.LOWER_SECONDARY_HOD,
        UserRole.ADMIN_OFFICER,
    }

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in self.ALLOWED_ROLES:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render
        ctx = self._build_context()
        return render(request, self.template_name, ctx)

    def post(self, request, *args, **kwargs):
        from django.shortcuts import render
        from academics.utils import get_current_term
        import json

        current_term = get_current_term()
        dept_subject_data = json.loads(TimetableSlotCreateView._get_dept_subject_data())

        # Parse bulk rows from POST: rows[0][class_name], rows[0][subject_name], etc.
        rows = []
        idx = 0
        while f"rows[{idx}][class_name]" in request.POST:
            row_data = {
                "class_name": request.POST.get(f"rows[{idx}][class_name]", "").strip(),
                "subject_name": request.POST.get(f"rows[{idx}][subject_name]", "").strip(),
                "teacher_id": request.POST.get(f"rows[{idx}][teacher_id]", "").strip(),
                "day_of_week": request.POST.get(f"rows[{idx}][day_of_week]", "").strip(),
                "start_time": request.POST.get(f"rows[{idx}][start_time]", "").strip(),
                "end_time": request.POST.get(f"rows[{idx}][end_time]", "").strip(),
            }
            rows.append(row_data)
            idx += 1

        created = 0
        errors = []

        if rows:
            from users.models import User
            from academics.models import Subject as AcadSubject
            for i, row in enumerate(rows):
                if not all([row["class_name"], row["subject_name"], row["teacher_id"],
                           row["day_of_week"], row["start_time"], row["end_time"]]):
                    errors.append(f"Row {i+1}: All fields are required.")
                    continue

                try:
                    from datetime import time as time_type
                    teacher = User.objects.get(pk=int(row["teacher_id"]))
                    start_h, start_m = map(int, row["start_time"].split(":"))
                    end_h, end_m = map(int, row["end_time"].split(":"))

                    slot = TimetableSlot(
                        term=current_term,
                        class_name=row["class_name"],
                        subject_name=row["subject_name"],
                        teacher=teacher,
                        day_of_week=row["day_of_week"],
                        start_time=time_type(start_h, start_m),
                        end_time=time_type(end_h, end_m),
                    )
                    # Link Subject FK
                    subj = AcadSubject.objects.filter(name__iexact=row["subject_name"]).first()
                    if subj:
                        slot.subject = subj
                    slot.full_clean()
                    slot.save()
                    created += 1
                except Exception as exc:
                    errors.append(f"Row {i+1}: {exc}")

        if created and not errors:
            messages.success(request, f"{created} timetable slot(s) created successfully.")
        elif created and errors:
            messages.warning(request, f"{created} slot(s) created. {len(errors)} row(s) had errors.")
        elif errors:
            messages.error(request, f"No slots created. {len(errors)} error(s).")

        if created:
            log_event(
                actor=request.user,
                action_type="TIMETABLE_SLOT_CREATED",
                model_name="TimetableSlot",
                object_id="bulk",
                description=f"Bulk created {created} timetable slot(s)",
                after={"created_count": created, "errors": len(errors)},
                request=request,
            )

        ctx = self._build_context()
        ctx["created_count"] = created
        ctx["errors"] = errors
        ctx["rows_submitted"] = rows
        return render(request, self.template_name, ctx)

    @staticmethod
    def _build_context():
        from academics.models import GradeClass, Subject
        from academics.utils import get_current_term
        import json

        current_term = get_current_term()
        dept_subject_data = TimetableSlotCreateView._get_dept_subject_data()

        # Build subject color map for JS
        subject_colors = {}
        for s in Subject.objects.filter(is_active=True).values_list("name", "color", "code"):
            subject_colors[s[0]] = {"color": s[1], "code": s[2]}

        return {
            "current_term": current_term,
            "dept_subject_data": dept_subject_data,
            "subject_colors": json.dumps(subject_colors),
            "days": Weekday.choices,
        }


class BulkDeleteTimetableView(RoleRequiredMixin, View):
    """Delete multiple timetable slots at once (POST-only)."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.delete_timetableslot"

    ALLOWED_ROLES = {
        UserRole.SUPER_ADMIN,
        UserRole.PRIMARY_HOD,
        UserRole.ECD_HOD,
        UserRole.LOWER_SECONDARY_HOD,
        UserRole.ADMIN_OFFICER,
    }

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in self.ALLOWED_ROLES:
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        slot_ids = request.POST.getlist("slot_ids")
        if not slot_ids:
            messages.error(request, "No slots selected for deletion.")
            return redirect("timetable:index")

        if request.user.role != UserRole.SUPER_ADMIN:
            published_blocked = TimetableSlot.objects.filter(pk__in=slot_ids, is_published=True).exists()
            if published_blocked:
                from django.core.exceptions import PermissionDenied as PD
                raise PD("Cannot delete published timetable slots. Unpublish them first.")

        slots_info = list(TimetableSlot.objects.filter(pk__in=slot_ids).values("class_name", "subject_name", "day_of_week", "start_time", "end_time"))
        deleted_count, _ = TimetableSlot.objects.filter(pk__in=slot_ids).delete()
        log_event(
            actor=request.user,
            action_type="TIMETABLE_SLOTS_BULK_DELETED",
            model_name="TimetableSlot",
            object_id=",".join(str(s) for s in slot_ids),
            description=f"Bulk deleted {deleted_count} timetable slot(s)",
            before={"slots": slots_info},
            request=request,
        )
        if deleted_count == 1:
            messages.success(request, "1 timetable slot deleted.")
        else:
            messages.success(request, f"{deleted_count} timetable slots deleted.")
        return redirect("timetable:index")


class CloneTimetableView(RoleRequiredMixin, View):
    """Clones timetable slots from one term to another — FR-TIME-002."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.add_timetableslot"

    def post(self, request):
        if request.user.role not in {UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER}:
            raise PermissionDenied()
            
        from academics.models import Term
        from_term_id = request.POST.get("from_term")
        to_term_id = request.POST.get("to_term")
        
        if not from_term_id or not to_term_id:
            messages.error(request, "Both source and target terms are required.")
            return redirect("timetable:index")
            
        from_term = get_object_or_404(Term, pk=from_term_id)
        to_term = get_object_or_404(Term, pk=to_term_id)

        # Source term must be earlier than target term
        if from_term.end_date >= to_term.start_date:
            messages.error(
                request,
                "Source term must end before the target term begins."
            )
            return redirect("timetable:index")
        
        existing_count = TimetableSlot.objects.filter(term=to_term).count()
        skip_existing = request.POST.get("skip_existing", "1") == "1"
        
        if existing_count > 0 and not skip_existing:
            messages.warning(
                request,
                f"Target term already has {existing_count} slot(s). "
                "Cloning will overwrite conflicting slots. Confirm by enabling 'Skip existing'."
            )
            return redirect("timetable:index")
        
        source_slots = TimetableSlot.objects.filter(term=from_term)
        count = 0
        skipped = 0
        for slot in source_slots:
            if TimetableSlot.objects.filter(
                term=to_term,
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                class_name=slot.class_name
            ).exists():
                skipped += 1
                continue
                
            TimetableSlot.objects.create(
                term=to_term,
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                end_time=slot.end_time,
                subject_name=slot.subject_name,
                class_name=slot.class_name,
                teacher=slot.teacher
            )
            count += 1
            
        log_event(
            actor=request.user,
            action_type="TIMETABLE_CLONED",
            model_name="TimetableSlot",
            object_id=f"{from_term_id}->{to_term_id}",
            description=f"Cloned {count} slot(s) from {from_term} to {to_term} ({skipped} skipped)",
            after={"from_term": from_term_id, "to_term": to_term_id, "cloned": count, "skipped": skipped},
            request=request,
        )
        
        parts = [f"cloned {count} slot(s)"]
        if skipped:
            parts.append(f"{skipped} skipped (conflicts)")
        messages.success(request, f"Successfully {', '.join(parts)} to {to_term}.")
        return redirect("timetable:index")


class PublishTimetableView(RoleRequiredMixin, View):
    """Publish all unpublished timetable slots for the current term — FR-TT-009."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "timetable.change_timetableslot"

    def post(self, request):
        if request.user.role not in {UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER}:
            raise PermissionDenied()

        from academics.utils import get_current_term
        current_term = get_current_term()
        if not current_term:
            messages.error(request, "No active term found.")
            return redirect("timetable:index")

        slots = TimetableSlot.objects.filter(term=current_term, is_published=False)
        if not slots.exists():
            messages.info(request, "All timetable slots are already published.")
            return redirect("timetable:index")

        # Warn: classes with no slots (not blocking — partial publish is allowed)
        from academics.models import GradeClass
        all_classes = set(GradeClass.objects.values_list("name", flat=True))
        published_classes = set(
            slots.values_list("class_name", flat=True)
        ) | set(
            TimetableSlot.objects.filter(term=current_term, is_published=True).values_list("class_name", flat=True)
        )
        missing = all_classes - published_classes
        if missing:
            messages.warning(
                request,
                f"The following classes have no timetable slots and will not appear: {', '.join(sorted(missing))}."
            )

        # Validate: no unresolved scheduling conflicts (teacher or class double-booking)
        all_term_slots = TimetableSlot.objects.filter(term=current_term)
        all_days = set(all_term_slots.values_list("day_of_week", flat=True).distinct())
        conflicts = []
        for day in all_days:
            day_slots = list(all_term_slots.filter(day_of_week=day).order_by("start_time"))
            for i, a in enumerate(day_slots):
                for b in day_slots[i + 1:]:
                    if a.start_time < b.end_time and a.end_time > b.start_time:
                        if a.teacher_id == b.teacher_id:
                            conflicts.append(
                                f"{a.teacher} double-booked on {day} {a.start_time}-{a.end_time} "
                                f"vs {b.start_time}-{b.end_time}"
                            )
                        if a.class_name.lower() == b.class_name.lower():
                            conflicts.append(
                                f"Class {a.class_name} double-booked on {day} {a.start_time}-{a.end_time} "
                                f"vs {b.start_time}-{b.end_time}"
                            )
        if conflicts:
            messages.error(
                request,
                f"Cannot publish: {len(conflicts)} unresolved conflict(s): " +
                "; ".join(conflicts[:5]) +
                ("..." if len(conflicts) > 5 else "")
            )
            return redirect("timetable:index")

        count = slots.update(is_published=True)
        log_event(
            actor=request.user,
            action_type="TIMETABLE_PUBLISHED",
            model_name="TimetableSlot",
            object_id=current_term.pk,
            description=f"Published {count} timetable slot(s) for {current_term}",
            after={"published_count": count, "term": current_term.pk},
            request=request,
        )
        messages.success(request, f"Published {count} timetable slot(s) for {current_term}.")
        return redirect("timetable:index")


class UnpublishTimetableView(RoleRequiredMixin, View):
    """Unpublish all timetable slots for the current term — SA override only."""
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "timetable.change_timetableslot"

    def post(self, request):
        if request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()

        from academics.utils import get_current_term
        current_term = get_current_term()
        if not current_term:
            messages.error(request, "No active term found.")
            return redirect("timetable:index")

        slots = TimetableSlot.objects.filter(term=current_term, is_published=True)
        count = slots.update(is_published=False)
        log_event(
            actor=request.user,
            action_type="TIMETABLE_UNPUBLISHED",
            model_name="TimetableSlot",
            object_id=current_term.pk,
            description=f"Unpublished {count} timetable slot(s) for {current_term}",
            before={"published_count": count},
            request=request,
        )
        messages.success(request, f"Unpublished {count} timetable slot(s) for {current_term}.")
        return redirect("timetable:index")

