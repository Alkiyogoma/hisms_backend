"""Attendance Reports, the learner pattern view, the Printable Register and
the print documents for every attendance tab. All figures come from
attendance/analytics.py."""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.generic import TemplateView

from attendance import analytics
from attendance.policy import can_view_excuse_reasons, lock_hour
from core.permissions import RoleRequiredMixin
from users.models import UserRole

SHOW = ("all", "flag", "pers", "late", "unm", "cls")
PAGE_SIZES = (25, 50, 100)


class AttendanceAccessMixin(RoleRequiredMixin):
    """Every member of staff who can see attendance sees all of it; parents
    have their own page."""
    login_url = "/accounts/login/"
    required_permission = "attendance.view_attendanceentry"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == UserRole.PARENT:
            return redirect("attendance:parent")
        return super().dispatch(request, *args, **kwargs)


def _term_for(day):
    from academics.models import Term
    return (Term.objects.filter(start_date__lte=day, end_date__gte=day).select_related("academic_year").first()
            or Term.objects.filter(start_date__lte=day).select_related("academic_year").order_by("-start_date").first())


def default_range(today):
    """The current term so far; 30 days when no term is in progress."""
    term = _term_for(today)
    if term and term.start_date and term.start_date <= today and (not term.end_date or term.end_date >= today):
        return term.start_date, today
    return today - timedelta(days=30), today


def quick_ranges(today):
    """Shortcuts that fill the start and end dates: today, this week, this
    month, this term and each academic year."""
    from academics.models import AcademicYear, Term
    monday = today - timedelta(days=today.weekday())
    out = [("Today", today, today), ("This week", monday, monday + timedelta(days=4)),
           ("This month", today.replace(day=1), (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))]
    term = _term_for(today)
    if term and term.start_date and term.end_date:
        out.append((term.name, term.start_date, term.end_date))
    years = []
    for y in AcademicYear.objects.order_by("-name")[:4]:
        spans = Term.objects.filter(academic_year=y, start_date__isnull=False, end_date__isnull=False)
        first = spans.order_by("start_date").first()
        last = spans.order_by("-end_date").first()
        if first and last:
            years.append((f"Year {y.name}", first.start_date, last.end_date))
    return out + years


def resolve_filters(request, *, need_class=False):
    params = request.GET
    today = timezone.localdate()
    d_start, d_end = default_range(today)
    start = analytics.parse_date(params.get("start"), d_start)
    end = analytics.parse_date(params.get("end"), d_end)
    if end < start:
        start, end = end, start
    if (end - start).days > 400:  # keep a single report to about a school year
        start = end - timedelta(days=400)
    classes = analytics.class_names()
    class_name = (params.get("class_name") or "").strip()
    if class_name not in classes:
        class_name = ""
    if need_class and not class_name:
        class_name = _default_class(request.user, classes)
    return today, start, end, classes, class_name


def _default_class(user, classes):
    if user.role == UserRole.TEACHER:
        from core.teacher_context import get_teacher_assigned_classes_from_tca
        mine = sorted(c for c in get_teacher_assigned_classes_from_tca(user) if c in classes)
        if mine:
            return mine[0]
    return classes[0] if classes else ""


def period_notes(period):
    notes = []
    if period.holidays:
        notes.append("Holidays omitted: " + "; ".join(f"{d:%-d %b} ({t})" for d, t in period.holidays) + ".")
    if period.out_of_term:
        notes.append(f"{period.out_of_term} weekday{'s' if period.out_of_term != 1 else ''} outside term dates omitted.")
    return notes


def report_context(request, *, for_print=False):
    """Everything the Attendance Reports tab (and its print document) shows."""
    today, start, end, classes, class_name = resolve_filters(request)
    params = request.GET
    period = analytics.build_period(start, end, today)
    learners = analytics.build_learners(period, class_name or None)
    learners = [r for r in learners if r["on_roll_days"] or r["marked"] or any(c == analytics.FUTURE for c in r["codes"])]
    roster = learners
    student = None
    if params.get("student"):
        student = next((r for r in learners if str(r["id"]) == params.get("student")), None)
        if student:
            learners = [student]
    show = params.get("show") if params.get("show") in SHOW else "all"
    query = (params.get("q") or "").strip()
    summary = analytics.summarise(learners)

    if show == "flag":
        rows = [r for r in learners if analytics.is_flagged(r)]
    elif show == "pers":
        rows = [r for r in learners if analytics.is_persistent(r)]
    elif show == "late":
        rows = sorted((r for r in learners if analytics.is_habitually_late(r)), key=lambda r: (-r["late"], r["name"]))
    else:
        rows = list(learners)
    if show != "late":
        rows.sort(key=lambda r: (r["rate"] is None, r["rate"] if r["rate"] is not None else 0, r["name"]))
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r["name"].lower() or q in (r["admission_no"] or "").lower()]

    try:
        page_size = int(params.get("page_size", PAGE_SIZES[0]))
    except ValueError:
        page_size = PAGE_SIZES[0]
    if page_size not in PAGE_SIZES:
        page_size = PAGE_SIZES[0]
    page_obj = Paginator(rows, page_size).get_page(params.get("page"))

    classes_summary = analytics.by_class(learners, classes)
    issues = analytics.register_issues(period, learners)
    chart = analytics.series(period, learners)
    pattern = analytics.learner_pattern(student) if student else None
    ctx = {
        "attendance_tab": "reports",
        "today": today, "start": start, "end": end, "period": period,
        "class_options": classes, "class_name": class_name,
        "quick": [{"label": l, "start": s, "end": e, "on": s == start and e == end} for l, s, e in quick_ranges(today)],
        "show": show, "query": query,
        "summary": summary,
        "rows": rows, "page_obj": page_obj, "page_size": page_size, "page_sizes": PAGE_SIZES,
        "classes_summary": classes_summary,
        "class_count": len(classes_summary),
        "issues": issues,
        "issue_learner_days": sum(i["unmarked"] for i in issues),
        "chart": chart,
        "roster": roster,
        "student_row": student, "pattern": pattern,
        "notes": period_notes(period),
        "target": analytics.TARGET, "flag": analytics.FLAG, "persistent": analytics.PERSISTENT,
        "habitual_late": analytics.HABITUAL_LATE,
        "show_reasons": can_view_excuse_reasons(request.user) and not for_print,
        "generated_at": timezone.localtime(),
    }
    return ctx


class AttendanceReportsView(AttendanceAccessMixin, TemplateView):
    template_name = "attendance/reports.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(report_context(self.request))
        return ctx


class AttendanceLearnerView(AttendanceAccessMixin, TemplateView):
    """One learner's day-by-day pattern over the selected range (modal body)."""
    template_name = "attendance/_learner_detail.html"

    def get_context_data(self, **kwargs):
        from students.models import Student
        ctx = super().get_context_data(**kwargs)
        student = get_object_or_404(Student, pk=kwargs["pk"])
        today, start, end, classes, class_name = resolve_filters(self.request)
        ctx.update(learner_context(self.request, student, start, end, today))
        return ctx


def learner_context(request, student, start, end, today, for_print=False):
    period = analytics.build_period(start, end, today)
    rows = analytics.build_learners(period, None, student_ids=[student.pk], with_reasons=True)
    row = rows[0] if rows else None
    show_reasons = can_view_excuse_reasons(request.user) and not for_print
    reasons = []
    if row and not for_print:
        from attendance.models import AttendanceEntry
        mine = {e.date: e for e in AttendanceEntry.objects.filter(student=student, date__in=[d for d, _ in row["reasons"]])}
        for d, text in row["reasons"]:
            e = mine.get(d)
            if show_reasons or (e and e.marked_by_id == request.user.pk):
                reasons.append((d, text or "No reason recorded"))
    return {
        "student": student, "row": row, "period": period,
        "pattern": analytics.learner_pattern(row) if row else None,
        "start": start, "end": end, "reasons": reasons,
        "reasons_hidden": bool(row and row["excused"] and not reasons and not for_print),
        "notes": period_notes(period),
        "flag": analytics.FLAG, "persistent": analytics.PERSISTENT,
    }


def register_context(request):
    today, start, end, classes, class_name = resolve_filters(request, need_class=True)
    if "start" not in request.GET and "end" not in request.GET:
        start = today.replace(day=1)
        end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    period = analytics.build_period(start, end, today)
    learners = analytics.build_learners(period, class_name or None) if class_name else []
    # Admitted after the period: not on this register at all.
    learners = [r for r in learners if any(c != analytics.NOT_ON_ROLL for c in r["codes"])]
    learners.sort(key=lambda r: (r["student"].last_name.lower(), r["student"].first_name.lower()))

    chunks = []
    for i, d in enumerate(period.days):
        key = (d.year, d.month)
        if not chunks or chunks[-1]["key"] != key:
            chunks.append({"key": key, "label": d.strftime("%B %Y"), "idx": [], "days": []})
        chunks[-1]["idx"].append(i)
        chunks[-1]["days"].append(d)
    for ch in chunks:
        ch["rows"] = []
        for r in learners:
            codes = [r["codes"][i] for i in ch["idx"]]
            c = {k: codes.count(k) for k in "PLAEU"}
            marked = c["P"] + c["L"] + c["A"] + c["E"]
            pct = analytics.rate(c["P"] + c["L"], marked)
            ch["rows"].append({"learner": r, "codes": codes, "in_school": c["P"] + c["L"], "absent": c["A"],
                               "late": c["L"], "excused": c["E"], "unmarked": c["U"], "rate": pct,
                               "band": analytics.band(pct)})
        ch["daily"] = []
        for i in ch["idx"]:
            on_roll = sum(1 for r in learners if r["codes"][i] not in (analytics.NOT_ON_ROLL,))
            marked = sum(1 for r in learners if r["codes"][i] in "PLAE" and r["codes"][i])
            present = sum(1 for r in learners if r["codes"][i] in ("P", "L"))
            future = period.days[i] > today
            ch["daily"].append({"present": present, "marked": marked, "on_roll": on_roll, "future": future})
    from attendance.analytics import class_teachers
    terms = _terms_between(start, end)
    return {
        "attendance_tab": "register",
        "today": today, "start": start, "end": end, "period": period,
        "class_options": classes, "class_name": class_name,
        "quick": [{"label": l, "start": s, "end": e, "on": s == start and e == end} for l, s, e in quick_ranges(today)],
        "learners": learners, "chunks": chunks, "multi": len(chunks) > 1,
        "summary": analytics.summarise(learners),
        "class_teacher": class_teachers().get(class_name, ""),
        "terms_label": ", ".join(f"{t.name}, {t.academic_year.name}" for t in terms) or "—",
        "admitted": [r for r in learners if r["admitted_in_period"]],
        "notes": period_notes(period),
        "generated_at": timezone.localtime(),
        "lock_hour": f"{lock_hour():02d}",
    }


def _terms_between(start, end):
    from academics.models import Term
    return list(Term.objects.filter(start_date__lte=end, end_date__gte=start)
                .select_related("academic_year").order_by("start_date"))


class AttendanceRegisterView(AttendanceAccessMixin, TemplateView):
    template_name = "attendance/register.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(register_context(self.request))
        return ctx


class AttendancePrintView(AttendanceAccessMixin, TemplateView):
    """Official A4 documents for every tab: ?kind=today|report|student|register.
    ``format=pdf`` downloads a PDF (WeasyPrint), otherwise the document opens
    in the browser ready to print or save as PDF. Excusal reasons are never
    printed."""
    template_name = "attendance/print.html"

    def get_context_data(self, **kwargs):
        from students.models import Student
        ctx = super().get_context_data(**kwargs)
        kind = self.request.GET.get("kind", "report")
        ctx["kind"] = kind
        ctx["is_pdf"] = self.request.GET.get("format") == "pdf"
        if kind == "register":
            ctx.update(register_context(self.request))
            ctx["doc_title"] = "Daily Attendance Register"
        elif kind == "student":
            student = get_object_or_404(Student, pk=self.request.GET.get("student"))
            today, start, end, classes, class_name = resolve_filters(self.request)
            ctx.update(learner_context(self.request, student, start, end, today, for_print=True))
            ctx["doc_title"] = "Learner Attendance Record"
        elif kind == "today":
            from attendance.views import AttendanceTodayView
            view = AttendanceTodayView()
            view.setup(self.request)
            ctx.update(view.get_context_data())
            ctx["kind"] = "today"
            ctx["doc_title"] = "Daily Attendance Roster"
        else:
            ctx.update(report_context(self.request, for_print=True))
            ctx["kind"] = "report"
            ctx["doc_title"] = "Attendance Report"
        ctx["generated_at"] = timezone.localtime()
        return ctx

    def render_to_response(self, context, **kwargs):
        if not context["is_pdf"]:
            return super().render_to_response(context, **kwargs)
        html = render_to_string(self.template_name, context, request=self.request)
        try:
            from weasyprint import HTML
            pdf = HTML(string=html, base_url=self.request.build_absolute_uri("/")).write_pdf()
        except (ImportError, OSError):
            # No PDF engine on this server: open the document with the print
            # dialog, where "Save as PDF" gives the same file.
            return super().render_to_response({**context, "is_pdf": False, "print_now": True}, **kwargs)
        name = context.get("class_name") or "school"
        if context["kind"] == "student":
            name = context["student"].admission_no
        filename = f"attendance-{context['kind']}-{name}-{context.get('start', context.get('date'))}".replace(" ", "-")
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return resp
