from __future__ import annotations

from datetime import date as date_type, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView
from django.views import View
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import json
import hmac

from core.permissions import RoleRequiredMixin
from datetime import datetime
from attendance.forms import AttendanceCorrectionForm, AttendanceFilterForm, AttendanceMarkForm
from attendance.models import AttendanceEntry, AttendanceStatus
from attendance.services import (
    AttendanceService,
    calculate_attendance_rate,
    correct_attendance,
    mark_attendance,
)
from attendance.policy import AttendanceWindowError, check_can_modify, is_register_locked, is_super_admin, lock_hour
from core.teacher_context import get_teacher_assigned_classes
from django.http import HttpResponse
from students.models import ParentGuardian, Student
from users.models import User, UserRole

ATTENDANCE_MARK_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN_OFFICER,
}


def _batch_attendance_rates(students, term):
    """Compute attendance rates for many students in 1 query instead of N*2.

    Returns dict: {student_id: float|None}
    """
    if not students or not term:
        return {}
    from academics.models import Term
    if isinstance(term, Term):
        start_date, end_date = term.start_date, term.end_date
    else:
        return {}
    if not start_date or not end_date:
        return {}

    ids = [s.id for s in students]
    entries = AttendanceEntry.objects.filter(
        student_id__in=ids,
        date__range=[start_date, end_date],
    ).exclude(status=AttendanceStatus.UNCONFIRMED).values("student_id", "status")

    from collections import defaultdict
    totals = defaultdict(int)
    presents = defaultdict(int)
    for row in entries:
        sid = row["student_id"]
        totals[sid] += 1
        if row["status"] in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            presents[sid] += 1

    rates = {}
    for sid in ids:
        t = totals.get(sid, 0)
        if t == 0:
            rates[sid] = 0
        else:
            rates[sid] = round((presents.get(sid, 0) / t) * 100, 1)
    return rates



def was_edited(entry) -> bool:
    """Re-marked after it was first recorded (the register stays open to
    correction until the cut-off)."""
    return bool(entry and entry.marked_at and entry.created_at
                and (entry.marked_at - entry.created_at).total_seconds() > 60)


def render_register_row(request, student, entry, d):
    """One <tr> of the Today register, as rendered on the page."""
    from academics.utils import get_current_term
    from attendance import analytics
    from attendance.policy import can_view_excuse_reason
    today = timezone.localdate()
    rate = analytics.term_rates([student.pk], get_current_term(), today).get(student.pk)
    weeks, week = analytics.week_codes([student.pk], d)
    code = analytics.CODES.get(entry.status, "U") if entry else "U"
    row = {
        "student": student, "entry": entry, "code": code, "attendance_rate": rate,
        "is_below_threshold": (rate or 100) < analytics.FLAG,
        "week": list(zip(week, weeks[student.pk])),
        "can_view_reason": can_view_excuse_reason(request.user, entry),
        "edited": was_edited(entry),
    }
    can_change = request.user.has_perm("attendance.change_attendanceentry")
    return render(request, "attendance/_row.html", {
        "row": row, "date": d, "can_mark": can_change, "can_correct": can_change,
        "is_locked": is_register_locked(d), "is_super_admin": is_super_admin(request.user),
        "show_checkout_gaps": d < today or timezone.localtime().hour >= 16,
    }).content.decode("utf-8")


class AttendanceTodayView(RoleRequiredMixin, TemplateView):
    """Today tab: the day's register for every class, marked inline.

    Every figure is calculated on learners actually marked; unmarked learners
    are shown but counted as neither present nor absent. All staff see every
    class; only excusal reasons are restricted (see policy)."""
    template_name = "attendance/today.html"
    login_url = "/accounts/login/"
    required_permission = "attendance.view_attendanceentry"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == UserRole.PARENT:
            return redirect("attendance:parent")
        return super().dispatch(request, *args, **kwargs)

    def _default_class(self, d):
        """A teacher opening the register lands on their class: the one they
        teach now (timetable), else their class-teacher class."""
        from timetable.models import TimetableSlot
        user = self.request.user
        day = d.strftime("%a").lower()[:3]
        slots = TimetableSlot.objects.filter(teacher=user, term__is_locked=False)
        slot = None
        if d == timezone.localdate():
            now_time = timezone.localtime().time()
            slot = slots.filter(day_of_week=day, start_time__lte=now_time, end_time__gte=now_time).first()
        slot = slot or slots.filter(day_of_week=day).first()
        if slot:
            return slot.class_name, slot.subject_name
        assigned = get_teacher_assigned_classes(user)
        if not assigned:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            assigned = set(get_teacher_assigned_classes_from_tca(user).keys())
        if assigned:
            return sorted(assigned)[0], None
        slot = slots.first()
        return (slot.class_name, slot.subject_name) if slot else ("", None)

    def get_context_data(self, **kwargs):
        from academics.utils import get_current_term
        from attendance import analytics
        from attendance.policy import can_view_excuse_reasons

        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        user = self.request.user
        today = timezone.localdate()
        d = analytics.parse_date(params.get("date"), today)
        if d > today:
            d = today

        classes = analytics.class_names()
        class_name = (params.get("class_name") or "").strip()
        auto_selected_subject = None
        if "class_name" not in params and user.role == UserRole.TEACHER:
            class_name, auto_selected_subject = self._default_class(d)
        if class_name not in classes:
            class_name, auto_selected_subject = "", None
        form = AttendanceFilterForm({"date": d.isoformat(), "class_name": class_name})

        # One school-day period for the chosen date, whatever the calendar says,
        # so a register can still be opened on a non-teaching day.
        period = analytics.Period(start=d, end=d, days=[d], today=today)
        hols = analytics.holidays_between(d, d)
        non_school_reason = ("a weekend" if d.weekday() >= 5 else (f"a holiday ({hols[d]})" if d in hols else ""))

        everyone = [r for r in analytics.build_learners(period) if r["codes"][0] != analytics.NOT_ON_ROLL]
        class_status = analytics.class_day_status(d, everyone, classes)
        rank = {n: i for i, n in enumerate(classes)}
        learners = sorted((r for r in everyone if not class_name or r["class_name"] == class_name),
                          key=lambda r: (rank.get(r["class_name"], len(rank)), r["name"]))

        ids = [r["id"] for r in learners]
        entries = {e.student_id: e for e in AttendanceEntry.objects.filter(date=d, student_id__in=ids)}
        current_term = get_current_term()
        rates = analytics.term_rates(ids, current_term, today)
        weeks, week = analytics.week_codes(ids, d)
        can_change = user.has_perm("attendance.change_attendanceentry")
        show_reasons = can_view_excuse_reasons(user)
        rows = []
        for r in learners:
            entry = entries.get(r["id"])
            code = r["codes"][0]
            rows.append({
                "student": r["student"],
                "entry": entry,
                "code": code,
                "attendance_rate": rates.get(r["id"]),
                "is_below_threshold": (rates.get(r["id"]) or 100) < analytics.FLAG,
                "week": list(zip(week, weeks[r["id"]])),
                "can_view_reason": show_reasons or bool(entry and entry.marked_by_id == user.pk),
                "edited": was_edited(entry),
            })

        def count(code):
            return sum(1 for r in learners if r["codes"][0] == code)
        late = count("L")
        present = count("P") + late
        absent, excused, unconfirmed = count("A"), count("E"), count("U")
        marked = present + absent + excused
        ctx["summary"] = {
            "total": len(learners),
            "marked": marked,
            "present": present,
            "late": late,
            "absent": absent,
            "excused": excused,
            "unconfirmed": unconfirmed,
            # Measured on learners actually marked, never on the whole roll.
            "present_pct": analytics.rate(present, marked) or 0,
        }
        unmarked_classes = [c for c in class_status if c["unmarked"]]
        ctx.update({
            "filter_form": form,
            "date": d,
            "today": today,
            "max_date": today,
            "class_name": class_name,
            "class_options": classes,
            "rows": rows,
            "class_status": class_status,
            "unmarked_classes": unmarked_classes,
            "unmarked_class_learners": sum(c["unmarked"] for c in unmarked_classes),
            "non_school_reason": non_school_reason,
            "statuses": AttendanceStatus.choices,
            "current_term": current_term,
            "auto_selected_subject": auto_selected_subject,
            "is_locked": is_register_locked(d),
            "is_future": False,
            "is_super_admin": is_super_admin(user),
            "lock_hour": f"{lock_hour():02d}",
            "can_correct": can_change,
            "can_mark": can_change,
            "can_excuse": can_change,
            "show_checkout_gaps": d < today or timezone.localtime().hour >= 16,
            "flag": analytics.FLAG,
            "attendance_tab": "today",
        })
        if ctx["is_super_admin"]:
            from attendance.models import AttendanceCorrectionRequest
            ctx["pending_correction_count"] = AttendanceCorrectionRequest.objects.filter(status="pending").count()
        return ctx


class AttendanceMarkView(RoleRequiredMixin, TemplateView):
    template_name = "attendance/_row.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "attendance.change_attendanceentry"

    def get(self, request, *args, **kwargs):
        return redirect("attendance:today")

    def post(self, request, *args, **kwargs):
        from django.http import HttpResponse
        from django.utils import timezone as tz
        if not request.user.has_perm("attendance.change_attendanceentry"):
            return HttpResponse("Permission denied", status=403)
        form = AttendanceMarkForm(request.POST)
        if not form.is_valid():
            return HttpResponse("Invalid form data", status=400)
        reason = (form.cleaned_data.get("reason") or "").strip()
        status = form.cleaned_data["status"]
        if status == "excused" and not reason:
            return HttpResponse("Reason required for excused", status=400)
        student = Student.objects.get(pk=form.cleaned_data["student_id"])
        d = form.cleaned_data["date"]
        try:
            entry = mark_attendance(actor=request.user, student=student, date=d, status=status)
        except AttendanceWindowError as exc:
            return HttpResponse(exc.message, status=403)
        # The reason belongs to the excusal: it is replaced on a new excusal and
        # cleared when the learner is marked anything else.
        new_reason = reason if status == AttendanceStatus.EXCUSED else ""
        if entry and entry.reason != new_reason:
            entry.reason = new_reason
            entry.save(update_fields=["reason"])
        resp = HttpResponse(render_register_row(request, student, entry, d))
        resp["HX-Trigger"] = "attendance-updated"
        return resp


class AttendanceMarkAllView(RoleRequiredMixin, View):
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "attendance.change_attendanceentry"

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("attendance.change_attendanceentry"):
            raise PermissionDenied()
        status = request.POST.get("status", "").strip()
        date_str = request.POST.get("date", "").strip()
        if status not in ("present", "absent"):
            return JsonResponse({"success": False, "message": "Invalid status"}, status=400)
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(date_str)
        except (ValueError, TypeError):
            d = None
        if d is None:
            d = timezone.localdate()
        try:
            check_can_modify(request.user, d)
        except AttendanceWindowError as exc:
            return JsonResponse({"success": False, "message": exc.message}, status=403)
        class_name = request.GET.get("class_name") or request.POST.get("class_name", "").strip()
        if not class_name:
            return JsonResponse({"success": False, "message": "Class is required"}, status=400)
        students = Student.objects.filter(class_name=class_name, is_archived=False, status="active")
        marked = 0
        for student in students:
            existing = AttendanceEntry.objects.filter(student=student, date=d).first()
            if existing and existing.status != "unconfirmed":
                continue
            mark_attendance(actor=request.user, student=student, date=d, status=status)
            marked += 1
        return JsonResponse({"success": True, "marked": marked, "status": status, "date": str(d)})


class AttendanceCorrectionView(RoleRequiredMixin, TemplateView):
    template_name = "attendance/_correction_modal.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL]
    required_permission = "attendance.change_attendanceentry"

    def get(self, request, *args, **kwargs):
        if not request.user.has_perm("attendance.change_attendanceentry"):
            raise PermissionDenied()
        # Render the modal with entry context (called via HTMX from today.html)
        entry_id = request.GET.get("entry_id")
        if entry_id:
            entry = get_object_or_404(
                AttendanceEntry.objects.select_related("student"), pk=entry_id
            )
            return self.render_to_response({
                "entry": entry,
                "statuses": AttendanceStatus.choices,
            })
        return redirect("attendance:today")

    def post(self, request, *args, **kwargs):
        if request.user.role not in {
            UserRole.SUPER_ADMIN,
            UserRole.ADMIN_OFFICER,
        }:
            raise PermissionDenied()

        form = AttendanceCorrectionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Fix correction form errors.")
            return redirect("attendance:today")

        entry = AttendanceEntry.objects.select_related("student").get(pk=form.cleaned_data["entry_id"])
        try:
            result = correct_attendance(
                actor=request.user,
                entry=entry,
                status=form.cleaned_data["status"],
                reason=form.cleaned_data["reason"],
            )
            if result.get("success"):
                messages.success(request, "Attendance corrected.")
            else:
                messages.error(request, result.get("message", "Correction failed."))
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("attendance:today")


class AttendanceCorrectionRequestCreateView(RoleRequiredMixin, View):
    """Teachers (anyone who can view the register) ask the super admin to fix a locked record."""
    login_url = "/accounts/login/"
    required_permission = "attendance.view_attendanceentry"

    def post(self, request, *args, **kwargs):
        from django.http import HttpResponse
        from attendance.models import AttendanceCorrectionRequest
        form = AttendanceMarkForm(request.POST)
        reason = (request.POST.get("reason") or "").strip()
        if not form.is_valid() or not reason:
            return HttpResponse("A status and a reason are required.", status=400)
        d = form.cleaned_data["date"]
        if d > timezone.localdate():
            return HttpResponse("Attendance cannot be marked for a future date.", status=400)
        if not is_register_locked(d):
            return HttpResponse("The register is still open; mark it directly.", status=400)
        student = get_object_or_404(Student, pk=form.cleaned_data["student_id"])
        # One open request per learner and day: a second request replaces the first.
        req, _ = AttendanceCorrectionRequest.objects.update_or_create(
            student=student, date=d, status="pending",
            defaults={"requested_status": form.cleaned_data["status"], "reason": reason,
                      "requested_by": request.user},
        )
        try:
            from communications.email_service import dispatch_notification
            for admin in User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True):
                dispatch_notification(
                    user=admin,
                    title="Attendance correction requested",
                    message=(f"{request.user.get_full_name() or request.user.username} asked to change "
                             f"{student.get_full_name()} on {d} to {req.get_requested_status_display()}: {reason}"),
                    link=reverse("attendance:today") + "?corrections=1",
                )
        except Exception:
            pass  # the request is saved; notification is best-effort
        return JsonResponse({"success": True, "id": req.pk})


class AttendanceCorrectionRequestListView(RoleRequiredMixin, TemplateView):
    """Super admin inbox, shown as a drawer on the register: approve or reject
    correction requests. Plain (non-htmx) visits land on the register with the
    drawer open."""
    template_name = "attendance/_correction_requests_drawer.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_super_admin(request.user):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if not request.headers.get("HX-Request"):
            return redirect(reverse("attendance:today") + "?corrections=1")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from attendance.models import AttendanceCorrectionRequest
        ctx = super().get_context_data(**kwargs)
        qs = AttendanceCorrectionRequest.objects.select_related("student", "requested_by", "resolved_by")
        ctx["pending"] = qs.filter(status="pending")
        ctx["resolved"] = qs.exclude(status="pending")[:50]
        return ctx

    def post(self, request, *args, **kwargs):
        from attendance.models import AttendanceCorrectionRequest
        req = get_object_or_404(AttendanceCorrectionRequest, pk=request.POST.get("request_id"), status="pending")
        decision = request.POST.get("decision")
        note = (request.POST.get("note") or "").strip()
        if decision == "approve":
            entry = AttendanceEntry.objects.filter(student=req.student, date=req.date).first()
            if entry:
                result = correct_attendance(actor=request.user, entry=entry,
                                            status=req.requested_status, reason=req.reason)
            else:
                mark_attendance(actor=request.user, student=req.student, date=req.date, status=req.requested_status)
                result = {"success": True}
            if not result.get("success"):
                return self._list(error=result.get("message", "Could not apply the correction."))
            req.status = "approved"
        elif decision == "reject":
            req.status = "rejected"
        else:
            return self._list(error="Choose approve or reject.")
        req.resolved_by = request.user
        req.resolved_at = timezone.now()
        req.resolution_note = note
        req.save()
        try:
            from communications.email_service import dispatch_notification
            dispatch_notification(
                user=req.requested_by,
                title=f"Attendance correction {req.status}",
                message=(f"Your request to change {req.student.get_full_name()} on {req.date} to "
                         f"{req.get_requested_status_display()} was {req.status}."
                         + (f" Note: {note}" if note else "")),
                link=reverse("attendance:today"),
            )
        except Exception:
            pass
        resp = self._list(notice=f"Request {req.status}.")
        resp["HX-Trigger"] = "att-corrections-changed"
        return resp

    def _list(self, notice="", error=""):
        ctx = self.get_context_data()
        ctx.update(notice=notice, error=error)
        return render(self.request, "attendance/_correction_requests_list.html", ctx)


class ParentAttendanceView(RoleRequiredMixin, TemplateView):
    template_name = "attendance/parent.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.PARENT]
    required_permission = "attendance.view_attendanceentry"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.role != UserRole.PARENT:
            raise PermissionDenied()

        guardian = ParentGuardian.objects.filter(user=self.request.user).first()
        if not guardian:
            ctx["note"] = "No parent profile is linked to this account yet."
            return ctx

        students = Student.objects.filter(studentguardian__guardian=guardian).distinct()
        
        # FR-ATT-010: Month Grid Calendar
        import calendar
        from datetime import date
        
        year = int(self.request.GET.get("year", date.today().year))
        month = int(self.request.GET.get("month", date.today().month))
        
        cal = calendar.Calendar(firstweekday=6) # Sunday start
        month_days = cal.monthdatescalendar(year, month)
        
        # Fetch entries for all linked students in this month
        entries = AttendanceEntry.objects.filter(
            student__in=students,
            date__year=year,
            date__month=month
        ).select_related("student")
        
        # Build structured data for template
        weeks_data = []
        for week in month_days:
            week_data = []
            for day in week:
                day_entries = []
                for s in students:
                    e = next((x for x in entries if x.student_id == s.id and x.date == day), None)
                    status_class = "grey"
                    if e:
                        if e.status == AttendanceStatus.PRESENT: status_class = "green"
                        elif e.status == AttendanceStatus.ABSENT: status_class = "red"
                        elif e.status == AttendanceStatus.LATE: status_class = "amber"
                        elif e.status == AttendanceStatus.EXCUSED: status_class = "blue"
                    
                    day_entries.append({
                        "student": s,
                        "entry": e,
                        "status_class": status_class
                    })
                week_data.append({
                    "day": day,
                    "is_current_month": day.month == month,
                    "is_today": day == date.today(),
                    "entries": day_entries
                })
            weeks_data.append(week_data)
            
        ctx["guardian"] = guardian
        ctx["students"] = students
        ctx["weeks"] = weeks_data
        ctx["current_year"] = year
        ctx["current_month"] = month
        ctx["month_name"] = calendar.month_name[month]
        ctx["today"] = date.today()
        
        # Prev/Next navigation
        if month == 1:
            ctx["prev_month"] = 12
            ctx["prev_year"] = year - 1
        else:
            ctx["prev_month"] = month - 1
            ctx["prev_year"] = year
            
        if month == 12:
            ctx["next_month"] = 1
            ctx["next_year"] = year + 1
        else:
            ctx["next_month"] = month + 1
            ctx["next_year"] = year
            
        ctx["note"] = ""
        return ctx


class StaffAttendanceView(RoleRequiredMixin, View):
    template_name = "attendance/staff.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "hr.view_staffprofile"

    def get(self, request, *args, **kwargs):
        from hr.models import StaffProfile
        from attendance.models import StaffAttendanceEntry, AttendanceStatus
        today = timezone.localdate()

        staff_list = StaffProfile.objects.filter(is_active=True).select_related("user")
        entries = {
            e.staff_id: e
            for e in StaffAttendanceEntry.objects.filter(date=today, staff__in=staff_list)
        }

        rows = []
        for s in staff_list:
            entry = entries.get(s.id)
            if entry:
                status = entry.get_status_display()
                check_in = entry.check_in_time.strftime("%I:%M %p").lstrip("0") if entry.check_in_time else None
            else:
                status = "Unconfirmed"
                check_in = None
            rows.append({
                "staff": s,
                "status": status,
                "check_in": check_in,
                "department": s.get_department_display(),
                "has_entry": entry is not None,
                "entry_pk": entry.pk if entry else None,
            })

        ctx = {
            "rows": rows,
            "today": today,
            "status_choices": AttendanceStatus.choices,
        }
        return render(request, self.template_name, ctx)

    def post(self, request, *args, **kwargs):
        from hr.models import StaffProfile
        from attendance.models import StaffAttendanceEntry, AttendanceStatus
        today = timezone.localdate()
        action = request.POST.get("action", "")

        if action == "mark_all":
            status_val = request.POST.get("status", AttendanceStatus.PRESENT)
            now_time = timezone.localtime().time()
            staff_list = StaffProfile.objects.filter(is_active=True)
            for s in staff_list:
                StaffAttendanceEntry.objects.update_or_create(
                    date=today, staff=s,
                    defaults={
                        "status": status_val,
                        "check_in_time": now_time,
                        "marked_by": request.user,
                    },
                )
            messages.success(request, f"All staff marked as {status_val} for today.")

        elif action == "mark_single":
            staff_pk = request.POST.get("staff_pk")
            status_val = request.POST.get("status", AttendanceStatus.PRESENT)
            now_time = timezone.localtime().time()
            try:
                s = StaffProfile.objects.get(pk=staff_pk)
                StaffAttendanceEntry.objects.update_or_create(
                    date=today, staff=s,
                    defaults={
                        "status": status_val,
                        "check_in_time": now_time,
                        "marked_by": request.user,
                    },
                )
                messages.success(request, f"{s.full_name} marked as {status_val}.")
            except StaffProfile.DoesNotExist:
                messages.error(request, "Staff member not found.")

        elif action == "unmark":
            staff_pk = request.POST.get("staff_pk")
            deleted, _ = StaffAttendanceEntry.objects.filter(
                date=today, staff_id=staff_pk
            ).delete()
            if deleted:
                messages.info(request, "Attendance record removed.")
            else:
                messages.info(request, "No record to remove.")

        return redirect("attendance:staff")


def require_webhook_secret(view_func):
    """
    Replaces session-based auth for hardware webhook endpoints.
    Checks X-Api-Key header against ATTENDANCE_WEBHOOK_SECRET setting.
    Fails closed: empty or missing secret rejects all requests.
    """
    from functools import wraps
    from django.conf import settings

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        secret = getattr(settings, "ATTENDANCE_WEBHOOK_SECRET", "")
        if not secret:
            return JsonResponse(
                {"error": "Webhook authentication not configured."},
                status=503
            )
        provided = request.META.get("HTTP_X_API_KEY", "")
        if not hmac.compare_digest(provided, secret):
            return JsonResponse(
                {"error": "Unauthorized."},
                status=401
            )
        return view_func(request, *args, **kwargs)
    return wrapper


@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(require_webhook_secret, name='dispatch')
class CheckInCheckOutAPIView(View):
    """
    FR-ATT-001: Webhook endpoint for physical attendance hardware.
    Accepts POST requests with JSON payload:
    {
        "admission_no": "12345",
        "action": "check_in" | "check_out",
        "timestamp": "2023-10-25T08:00:00Z"
    }
    """

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            admission_no = data.get("admission_no")
            action = data.get("action")
            timestamp_str = data.get("timestamp")
            
            if not all([admission_no, action, timestamp_str]):
                return JsonResponse({"error": "Missing required fields"}, status=400)
                
            student = Student.objects.filter(admission_no=admission_no, is_archived=False).first()
            if not student:
                return JsonResponse({"error": "Student not found"}, status=404)
                
            try:
                # Ensure timestamp_str is a string
                if not isinstance(timestamp_str, str):
                    if timestamp_str is None:
                        # Use current time if no timestamp provided
                        event_time = timezone.now()
                        local_time = event_time
                    else:
                        # Convert to string if it's another type
                        timestamp_str = str(timestamp_str)
                        event_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        local_time = timezone.localtime(event_time)
                else:
                    event_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    local_time = timezone.localtime(event_time)
            except (ValueError, TypeError) as e:
                return JsonResponse({"error": f"Invalid timestamp format: {str(e)}"}, status=400)
                
            # Default to a system admin user for hardware webhooks, or None if acceptable
            from users.models import User
            system_user = User.objects.filter(is_superuser=True).first()
            
            # Backdating prevention: hardware timestamps >30 min in the past are rejected
            from datetime import timedelta as _timedelta
            max_backdate = timezone.now() - _timedelta(minutes=30)
            if local_time < max_backdate:
                return JsonResponse(
                    {"error": "Check-in timestamp is too far in the past (>30 min). Only current-day check-ins are accepted."},
                    status=400,
                )
            
            # Derive status from check-in time (FR-ATT-006: Late if after 8:30 AM, ECD only per §6.1)
            derived_status = AttendanceService._derive_checkin_status(local_time, student=student) if action == "check_in" else AttendanceStatus.PRESENT

            entry, created = AttendanceEntry.objects.get_or_create(
                date=local_time.date(),
                student=student,
                defaults={
                    "status": derived_status,
                    "marked_by": system_user,
                    "marked_at": local_time,
                    "class_name": student.class_name,
                }
            )
            
            if action == "check_in":
                if not entry.check_in_time:
                    entry.check_in_time = local_time.time()
                    entry.marked_at = local_time
            elif action == "check_out":
                entry.check_out_time = local_time.time()
                
            entry.save()

            # Audit log for check-in/check-out events
            from audit.models import log_event
            log_event(
                actor=system_user,
                action_type=f"CHECKIN_{action.upper()}",
                model_name="AttendanceEntry",
                object_id=entry.pk,
                description=f"Hardware {action} for {student.get_full_name()} at {local_time.strftime('%Y-%m-%d %H:%M')}",
                after={"student": student.pk, "date": str(local_time.date()), "action": action, "time": local_time.strftime('%H:%M')},
            )
            
            return JsonResponse({"success": True, "message": f"{action} recorded successfully"})
            
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


class QRCodeImageView(RoleRequiredMixin, View):
    """Serve a PNG QR code for a given payload (used by student ID cards)."""
    from users.models import UserRole
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL,
    ]
    login_url = "/accounts/login/"
    required_permission = "attendance.view_attendanceentry"

    def get(self, request, *args, **kwargs):
        data = request.GET.get("data", "")
        size = int(request.GET.get("size", 200))
        if not data:
            return HttpResponse("Missing data parameter", status=400)
        from attendance.qr_utils import generate_qr_code_bytes
        png = generate_qr_code_bytes(data, size=size)
        return HttpResponse(png, content_type="image/png")


class StudentQRCodeView(RoleRequiredMixin, View):
    """Serve a PNG QR code for a student's encrypted ID payload."""
    from users.models import UserRole
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL,
    ]
    login_url = "/accounts/login/"
    required_permission = "students.view_student"

    def get(self, request, *args, **kwargs):
        from attendance.qr_utils import sign_student_id, generate_qr_code_bytes
        from students.models import Student
        student_id = kwargs.get("student_id")
        if not student_id:
            return HttpResponse("Missing student_id", status=400)
        student = get_object_or_404(Student, pk=student_id)
        payload = sign_student_id(student.admission_no or str(student.id))
        size = int(request.GET.get("size", 180))
        png = generate_qr_code_bytes(payload, size=size)
        return HttpResponse(png, content_type="image/png")


class BlankAttendanceRegisterView(RoleRequiredMixin, TemplateView):
    """Printable blank attendance register for a class."""
    template_name = "attendance/blank_register.html"
    from users.models import UserRole
    allowed_roles = [
        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER,
        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL,
        UserRole.TEACHER,
    ]
    login_url = "/accounts/login/"
    required_permission = "students.view_student"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = self.request.GET.get("class_name", "")
        students = Student.objects.filter(is_archived=False).order_by("class_name", "first_name")
        if class_name:
            students = students.filter(class_name__iexact=class_name)
        ctx["students"] = students[:50]
        ctx["class_name"] = class_name
        from academics.utils import get_current_term
        current_term = get_current_term()
        ctx["term_name"] = current_term.name if current_term else ""
        return ctx
