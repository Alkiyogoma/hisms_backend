from __future__ import annotations

from datetime import date as date_type, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
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
    get_teacher_assigned_classes,
    mark_attendance,
)
from django.http import HttpResponse
from students.models import ParentGuardian, Student
from users.models import UserRole

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
    ).values("student_id", "status")

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


class AttendanceTodayView(RoleRequiredMixin, TemplateView):
    template_name = "attendance/today.html"
    login_url = "/accounts/login/"
    required_permission = "attendance.view_attendanceentry"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if not self.request.user.has_perm("attendance.view_attendanceentry"):
            raise PermissionDenied()
        from academics.models import GradeClass, Department
        
        role = self.request.user.role
        is_teacher = role == UserRole.TEACHER
        teacher_assigned_classes = get_teacher_assigned_classes(self.request.user) if is_teacher else set()
        
        # TCA fallback: if teacher has no TimetableSlot assignments but has
        # TeacherClassAssignment entries, use those for class scoping.
        if is_teacher and not teacher_assigned_classes:
            from core.teacher_context import get_teacher_assigned_classes_from_tca
            tca_map = get_teacher_assigned_classes_from_tca(self.request.user)
            teacher_assigned_classes = set(tca_map.keys())
        
        # FR-ATT-008: HOD Scoping — determine which classes each role can see
        # for BOTH the student query and the filter form dropdown.
        if role == UserRole.PRIMARY_HOD:
            scoped_classes = set(
                GradeClass.objects.filter(department=Department.PRIMARY)
                .values_list('name', flat=True)
            )
        elif role == UserRole.ECD_HOD:
            scoped_classes = set(
                GradeClass.objects.filter(department=Department.ECD)
                .values_list('name', flat=True)
            )
        elif is_teacher:
            scoped_classes = teacher_assigned_classes
        else:
            scoped_classes = None  # show all
        
        # Debug: Log GET parameters to help identify the issue
        import logging
        logger = logging.getLogger(__name__)
        if self.request.GET:
            logger.info(f"AttendanceTodayView GET parameters: {dict(self.request.GET)}")
        
        try:
            form = AttendanceFilterForm(
                self.request.GET or None,
                allowed_classes=scoped_classes if scoped_classes is not None else None,
                include_all_option=scoped_classes is None,
            )
        except Exception as e:
            logger.error(f"Error creating AttendanceFilterForm: {e}")
            logger.error(f"GET data: {self.request.GET}")
            # Create form without GET data as fallback
            form = AttendanceFilterForm(
                None,
                allowed_classes=scoped_classes if scoped_classes is not None else None,
                include_all_option=scoped_classes is None,
            )
        
        # Handle form validation with better error handling
        if form.is_valid():
            d = form.cleaned_data.get("date") or timezone.localdate()
            class_name = (form.cleaned_data.get("class_name") or "").strip()
        else:
            # If form is invalid, use defaults and log the errors for debugging
            d = timezone.localdate()
            class_name = ""
            # Log form errors for debugging
            if form.errors:
                logger.warning(f"AttendanceFilterForm validation errors: {form.errors}")
                logger.warning(f"Form data: {self.request.GET}")
        
        # Ensure d is always a date object
        if not isinstance(d, (date_type, timezone.datetime)):
            d = timezone.localdate()

        # FR-TT-006: Auto-select class based on current timetable slot.
        # On weekends (Sat/Sun) fall back to the most recent school day (Fri > Thu > ...).
        # Configurable via FRD_TT006_ATTENDANCE_AUTO_CLASS in settings.py.
        from django.conf import settings as dj_settings
        auto_class_enabled = getattr(dj_settings, "FRD_TT006_ATTENDANCE_AUTO_CLASS", True)
        auto_selected_subject = None
        if auto_class_enabled and not class_name and is_teacher:
            from timetable.models import TimetableSlot
            now_time = timezone.now().time()
            day_of_week = d.strftime('%a').lower()[:3]  # 'mon', 'tue', etc.
            school_days = ['mon', 'tue', 'wed', 'thu', 'fri']

            slot = None
            # 1) Try current period first (only if viewing today)
            if d == timezone.localdate():
                slot = TimetableSlot.objects.filter(
                    teacher=self.request.user,
                    day_of_week=day_of_week,
                    start_time__lte=now_time,
                    end_time__gte=now_time,
                    term__is_locked=False
                ).first()

            # 2) Try current day (any time slot)
            if not slot:
                slot = TimetableSlot.objects.filter(
                    teacher=self.request.user,
                    day_of_week=day_of_week,
                    term__is_locked=False
                ).first()

            # 3) Weekend fallback: try most recent school day (Fri → Thu → ...)
            if not slot and day_of_week not in school_days:
                for fallback_day in reversed(school_days):
                    slot = TimetableSlot.objects.filter(
                        teacher=self.request.user,
                        day_of_week=fallback_day,
                        term__is_locked=False
                    ).first()
                    if slot:
                        break

            # 4) Last resort: any slot for this teacher
            if not slot:
                slot = TimetableSlot.objects.filter(
                    teacher=self.request.user,
                    term__is_locked=False
                ).first()

            # 5) TCA fallback: if teacher has TeacherClassAssignment but no
            #    timetable slots, use the first assigned class.
            if not slot and teacher_assigned_classes:
                class_name = next(iter(sorted(teacher_assigned_classes)))
                data = self.request.GET.copy()
                data["class_name"] = class_name
                if not data.get("date"):
                    data["date"] = d.strftime("%Y-%m-%d")
                form = AttendanceFilterForm(
                    data,
                    allowed_classes=teacher_assigned_classes,
                    include_all_option=False,
                )

            if slot:
                class_name = slot.class_name
                auto_selected_subject = slot.subject_name
                # Re-bind form with auto-selected data to reflect in UI
                data = self.request.GET.copy()
                data["class_name"] = class_name
                if not data.get("date"):
                    data["date"] = d.strftime("%Y-%m-%d")
                form = AttendanceFilterForm(
                    data,
                    allowed_classes=teacher_assigned_classes,
                    include_all_option=False,
                )

        if is_teacher and class_name and class_name not in teacher_assigned_classes:
            class_name = ""
            auto_selected_subject = None

        # Teachers should normally mark for a class; until timetable/class assignment exists
        # we require a class filter for Teachers to prevent “mark everyone”.
        students = Student.objects.filter(is_archived=False).order_by("last_name", "first_name")
        if is_teacher:
            students = students.filter(class_name__in=teacher_assigned_classes)
            if class_name:
                students = students.filter(class_name__iexact=class_name)
            else:
                students = Student.objects.none()
        elif class_name:
            students = students.filter(class_name__icontains=class_name)

        from academics.models import Term
        from academics.utils import get_current_term
        
        # FR-ATT-008: HOD Scoping — use pre-computed scoped_classes (set at top of method)
        if scoped_classes is not None:
            students = students.filter(class_name__in=scoped_classes)


        entries = AttendanceEntry.objects.filter(date=d, student__in=students)
        if class_name:
            entries = entries.filter(class_name__icontains=class_name)
        by_student = {e.student_id: e for e in entries.select_related("student")}

        week_start = d - timedelta(days=d.weekday())
        week_days = [week_start + timedelta(days=i) for i in range(5)]
        week_entries = AttendanceEntry.objects.filter(date__in=week_days, student__in=students)
        if class_name:
            week_entries = week_entries.filter(class_name__icontains=class_name)
        weekly_by_student = {}
        for e in week_entries:
            weekly_by_student.setdefault(e.student_id, {})[e.date] = e

        status_counts_by_day = {day: {AttendanceStatus.PRESENT: 0, AttendanceStatus.LATE: 0, AttendanceStatus.EXCUSED: 0, AttendanceStatus.ABSENT: 0} for day in week_days}
        for e in week_entries:
            if e.status in status_counts_by_day[e.date]:
                status_counts_by_day[e.date][e.status] += 1

        status_chart = [
            {
                'label': week_day.strftime('%a'),
                'present': status_counts_by_day[week_day][AttendanceStatus.PRESENT],
                'late': status_counts_by_day[week_day][AttendanceStatus.LATE],
                'excused': status_counts_by_day[week_day][AttendanceStatus.EXCUSED],
                'absent': status_counts_by_day[week_day][AttendanceStatus.ABSENT],
            }
            for week_day in week_days
        ]

        status_totals = {
            'present': sum(item['present'] for item in status_chart),
            'late': sum(item['late'] for item in status_chart),
            'excused': sum(item['excused'] for item in status_chart),
            'absent': sum(item['absent'] for item in status_chart),
        }

        # FR-ATT-012: Threshold flagging
        current_term = get_current_term()
        
        threshold = 85.0 # FRD FR-ATT-012: default 85%
        
        # FR-ATT-012b: Previous term comparison data
        # Guard: current_term.start_date can be None (Terms 1 & 2 have no dates set)
        # Using None as a filter value crashes Django ORM ("Cannot use None as a query value").
        prev_term = None
        prev_term_rates = {}
        if current_term and current_term.start_date:
            prev_term = Term.objects.filter(
                academic_year=current_term.academic_year,
                start_date__lt=current_term.start_date,
                is_locked=True,
            ).order_by('-start_date').first()
            ctx["prev_term"] = prev_term


        rows = []
        # ── Batch attendance rates to avoid N+1 (2 queries per student) ──
        term_rates = _batch_attendance_rates(students[:200], current_term)
        prev_term_rates = _batch_attendance_rates(students[:200], prev_term) if prev_term else {}

        ctx["prev_term_rates"] = prev_term_rates

        for s in students[:200]:
            entry = by_student.get(s.id)
            rate = term_rates.get(s.id)
            is_below = rate < threshold if rate is not None else False

            attendance_week = []
            student_week_entries = weekly_by_student.get(s.id, {})
            for week_day in week_days:
                day_entry = student_week_entries.get(week_day)
                if day_entry:
                    if day_entry.status == AttendanceStatus.PRESENT:
                        state = 'present'
                    elif day_entry.status == AttendanceStatus.LATE:
                        state = 'late'
                    elif day_entry.status == AttendanceStatus.ABSENT:
                        state = 'absent'
                    elif day_entry.status == AttendanceStatus.EXCUSED:
                        state = 'excused'
                    else:
                        state = 'other'
                else:
                    state = 'missing'
                attendance_week.append({
                    'date': week_day,
                    'state': state,
                })
            
            rows.append({
                "student": s,
                "entry": entry,
                "attendance_rate": rate,
                "is_below_threshold": is_below,
                "weekly_bars": attendance_week,
            })


        # Summary counts (FR-ATT-003)
        total_expected = students.count()
        present = entries.filter(status=AttendanceStatus.PRESENT).count()
        absent = entries.filter(status=AttendanceStatus.ABSENT).count()
        late = entries.filter(status=AttendanceStatus.LATE).count()
        excused = entries.filter(status=AttendanceStatus.EXCUSED).count()
        unconfirmed = total_expected - len(entries)
        
        ctx["summary"] = {
            "total": total_expected,
            "present": present,
            "absent": absent,
            "late": late,
            "excused": excused,
            "unconfirmed": unconfirmed,
            "present_pct": round((present / total_expected * 100), 1) if total_expected > 0 else 0
        }

        ctx["filter_form"] = form
        ctx["date"] = d
        ctx["class_name"] = class_name
        ctx["rows"] = rows
        ctx["statuses"] = AttendanceStatus.choices
        ctx["status_chart"] = status_chart
        ctx["status_totals"] = status_totals
        ctx["current_term"] = current_term
        ctx["auto_selected_subject"] = auto_selected_subject
        can_change = self.request.user.has_perm("attendance.change_attendanceentry")
        ctx["can_correct"] = can_change
        ctx["can_mark"] = can_change
        ctx["can_excuse"] = can_change
        return ctx


class AttendanceMarkView(RoleRequiredMixin, TemplateView):
    template_name = "attendance/_tr_content.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "attendance.change_attendanceentry"

    def get(self, request, *args, **kwargs):
        return redirect("attendance:today")

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("attendance.change_attendanceentry"):
            raise PermissionDenied()
        form = AttendanceMarkForm(request.POST)
        if not form.is_valid():
            raise ValidationError("Invalid attendance mark request.")
        reason = (form.cleaned_data.get("reason") or "").strip()
        if not reason:
            messages.error(request, "A mandatory reason is required for manual attendance changes.")
            return redirect("attendance:today")
        student = Student.objects.get(pk=form.cleaned_data["student_id"])
        d = form.cleaned_data["date"]
        status = form.cleaned_data["status"]
        try:
            entry = mark_attendance(actor=request.user, student=student, date=d, status=status)
            messages.success(request, "Attendance saved.")
        except PermissionDenied:
            messages.error(request, "You are not allowed to mark attendance.")
            entry = AttendanceEntry.objects.filter(date=d, student=student).first()

        rate = calculate_attendance_rate(student)
        is_below = rate < 85.0 if rate is not None else False

        week_start = d - timedelta(days=d.weekday())
        week_days = [week_start + timedelta(days=i) for i in range(5)]
        week_entries = AttendanceEntry.objects.filter(date__in=week_days, student=student)
        attendance_week = []
        entries_by_day = {e.date: e for e in week_entries}
        for week_day in week_days:
            day_entry = entries_by_day.get(week_day)
            if day_entry:
                if day_entry.status == AttendanceStatus.PRESENT:
                    state = 'present'
                elif day_entry.status == AttendanceStatus.LATE:
                    state = 'late'
                elif day_entry.status == AttendanceStatus.ABSENT:
                    state = 'absent'
                elif day_entry.status == AttendanceStatus.EXCUSED:
                    state = 'excused'
                else:
                    state = 'other'
            else:
                state = 'missing'
            attendance_week.append({
                'date': week_day,
                'state': state,
            })

        response = self.render_to_response(
            {
                "student": student,
                "entry": entry,
                "date": d,
                "attendance_rate": rate,
                "is_below_threshold": is_below,
                "weekly_bars": attendance_week,
                "statuses": AttendanceStatus.choices,
                "can_correct": request.user.has_perm("attendance.change_attendanceentry"),
                "can_mark": request.user.has_perm("attendance.change_attendanceentry"),
            }
        )
        response["HX-Trigger"] = "attendance-updated"
        return response


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
            correct_attendance(
                actor=request.user,
                entry=entry,
                status=form.cleaned_data["status"],
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, "Attendance corrected.")
        except ValidationError as e:
            messages.error(request, str(e))

        return redirect("attendance:today")


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
            ctx["note"] = "No guardian profile is linked to this account yet."
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
