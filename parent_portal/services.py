"""
Data for the parent portal (v1): home, learner progress and school calendar.

Every academic figure comes from academics.score_progress.exam_summary (the
same calculation as the printed report card) and every attendance figure from
attendance.analytics (the approved attendance rules). Nothing here re-derives
marks or rates on its own.

Ownership: a parent only ever reaches learners returned by children_for().
"""
import calendar as _calendar
from datetime import date, timedelta

from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from academics.models import ReportCard, ReportCardStatus, Term
from students.models import Student, StudentStatus

RUNNING, FINAL, UPCOMING = "running", "final", "upcoming"

# Day codes shown on the parent attendance calendar.
DAY_LABELS = {
    "P": "Present", "L": "Late", "A": "Absent", "E": "Excused",
    "U": "Not yet marked", "H": "No school", "F": "", "": "",
}


# -- ownership -----------------------------------------------------------------

def children_for(user):
    """Active learners linked to this parent through any of their guardian records."""
    if not user.is_authenticated:
        return Student.objects.none()
    return (Student.objects
            .filter(guardians__user=user, status=StudentStatus.ACTIVE, is_archived=False)
            .distinct().order_by("first_name", "last_name"))


def child_or_404(user, student_id):
    """The learner if it is this parent's child; 404 otherwise (never 403, so
    changing the id in the URL can't tell anyone whether a learner exists)."""
    child = children_for(user).filter(pk=student_id).first()
    if child is None:
        raise Http404("No such learner.")
    return child


def published_report_or_404(user, student_id, report_id):
    """A report card of the parent's own child, published by the Head of School."""
    child = child_or_404(user, student_id)
    report = (ReportCard.objects.select_related("student", "term", "term__academic_year")
              .filter(pk=report_id, student=child, status=ReportCardStatus.PUBLISHED).first())
    if report is None:
        raise Http404("No such report.")
    return report


# -- term context --------------------------------------------------------------

def current_term():
    from academics.utils import get_current_term
    return get_current_term()


def term_state(term, current, today):
    if current and term.pk == current.pk:
        return RUNNING
    if term.start_date and term.start_date > today:
        return UPCOMING
    return FINAL


def year_terms(term):
    """The terms of the term's academic year, in order."""
    if term is None:
        return []
    qs = Term.objects.filter(academic_year_id=term.academic_year_id) if term.academic_year_id else \
        Term.objects.filter(pk=term.pk)
    return list(qs.select_related("academic_year").order_by("start_date", "name"))


def term_context(term, today):
    """Greeting-line term facts for the home page (no marks)."""
    if term is None:
        return None
    ctx = {"term": term, "year": term.academic_year.name if term.academic_year_id else "",
           "status": "", "week": None, "weeks": None}
    if term.start_date and term.end_date:
        if today < term.start_date:
            ctx["status"] = f"Starts {term.start_date:%-d %B %Y}"
        elif today > term.end_date:
            ctx["status"] = f"Ended {term.end_date:%-d %B %Y}"
        else:
            ctx["week"] = (today - term.start_date).days // 7 + 1
            ctx["weeks"] = (term.end_date - term.start_date).days // 7 + 1
            ctx["status"] = f"Week {ctx['week']} of {ctx['weeks']} · ends {term.end_date:%-d %B %Y}"
    return ctx


def greeting(now=None):
    hour = timezone.localtime(now).hour if now else timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


# -- today ---------------------------------------------------------------------

def today_statuses(students, today):
    """{student_id: today's attendance} for several learners in two queries."""
    from attendance import analytics
    from attendance.models import AttendanceEntry, has_real_check_in

    school_day = bool(analytics.build_period(today, today, today).days)
    entries = {e.student_id: e for e in AttendanceEntry.objects.filter(
        student__in=list(students), date=today).only(
        "student_id", "status", "check_in_time", "check_out_time", "date")}
    out = {}
    for s in students:
        entry = entries.get(s.pk)
        code = analytics.CODES.get(entry.status) if entry else None
        check_in = entry.check_in_time if entry and has_real_check_in(entry) else None
        check_out = entry.check_out_time if entry else None
        if code:
            status, label = code, DAY_LABELS[code]
        elif not school_day:
            status, label = "H", "No school today"
        else:
            status, label = "U", "Not yet marked"
        out[s.pk] = {
            "school_day": school_day, "code": status, "label": label,
            "check_in": check_in, "check_out": check_out,
            "not_checked_out": bool(check_in and not check_out),
        }
    return out


def today_status(student, today):
    """Attendance status, check-in and check-out for today."""
    return today_statuses([student], today)[student.pk]


# -- academics -----------------------------------------------------------------

def _summary(student, term, expected):
    from academics.score_progress import exam_summary
    return exam_summary(student, term, expected_subjects=expected)


def academic_progress(student, term, today):
    """Subject table (current term), header average, and one bar chart per
    assessment type with one bar per term of the year.

    Uses only HOD-approved marks of mark-bearing subjects, converted to points
    out of each assessment's weight (Quiz /20, Mid-Term /30, End-Term /50).
    """
    from academics.score_progress import expected_subjects_for_class
    if term is None:
        return None
    expected = expected_subjects_for_class(student.class_name)
    current = _summary(student, term, expected)
    types = current["types"]

    charts = [{"type": t, "max": _weight(t), "bars": []} for t in types]
    for t in year_terms(term):
        state = term_state(t, term, today)
        values = [None] * len(types)
        if state != UPCOMING:
            summary = current if t.pk == term.pk else _summary(student, t, expected)
            values = summary["type_averages"]
        for i, chart in enumerate(charts):
            v = values[i]
            chart["bars"].append({
                "term": t, "state": state, "value": v,
                "pct": round(v / chart["max"] * 100, 1) if v is not None and chart["max"] else 0,
            })

    for chart in charts:
        chart["has_marks"] = any(b["value"] is not None for b in chart["bars"])

    rows = []
    for r in current["rows"]:
        rows.append({
            "subject": r["subject"],
            "cells": list(zip(r["cells"], [_weight(t) for t in types])),
            "running": r["running"], "grade": r["grade"] or r["running_grade"],
            "complete": r["complete"],
        })
    return {
        "term": term,
        "types": [{"type": t, "max": _weight(t)} for t in types],
        "rows": rows,
        "complete": current["complete"],
        "assessed": current["assessed"],
        "total_subjects": current["total_subjects"],
        "average": current["running_average"],
        "grade": current["running_grade"],
        "charts": charts,
    }


def _weight(exam_type):
    w = float(exam_type.weight_percentage or 0)
    return int(w) if w.is_integer() else w


# -- attendance ----------------------------------------------------------------

def term_attendance_rate(student, term, today):
    """Same figure as the report card's attendance rate (unmarked days excluded)."""
    from attendance import analytics
    start, end = analytics.term_range(term, today)
    row = analytics.learner_attendance(student, start, end, today)["row"] or {}
    return row.get("rate")


def parse_month(value, today):
    try:
        y, m = (int(x) for x in str(value).split("-", 1))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return min(date(y, m, 1), today.replace(day=1))
    except (TypeError, ValueError):
        pass
    return today.replace(day=1)


def month_attendance(student, month_start, today):
    """Mon-Fri grid for one month. Reasons for excused days are never included."""
    from attendance import analytics

    last = _calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last)
    period = analytics.build_period(month_start, month_end, today)
    rows = analytics.build_learners(period, None, student_ids=[student.pk])
    codes = dict(zip(period.days, rows[0]["codes"])) if rows else {}

    weeks, week = [], []
    d = month_start
    # Pad the first week so Monday is the first column.
    if month_start.weekday() < 5:
        week.extend([None] * month_start.weekday())
    while d <= month_end:
        if d.weekday() < 5:
            if d in codes:
                code = codes[d]
            elif d > today:
                code = "F"
            else:
                code = "H"  # weekday holiday or outside the term dates
            week.append({"date": d, "code": code or "", "label": DAY_LABELS.get(code, ""),
                         "is_today": d == today})
        if d.weekday() == 4 or d == month_end:
            if any(week):
                weeks.append(week + [None] * (5 - len(week)))
            week = []
        d += timedelta(days=1)

    counted = [c for c in codes.values()]
    counts = {k: counted.count(k) for k in "PLAEU"}
    marked = counts["P"] + counts["L"] + counts["A"] + counts["E"]
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = month_end + timedelta(days=1)
    return {
        "month": month_start,
        "weeks": weeks,
        "present": counts["P"], "late": counts["L"], "absent": counts["A"],
        "excused": counts["E"], "unmarked": counts["U"], "marked": marked,
        "rate": analytics.rate(counts["P"] + counts["L"], marked),
        "prev": prev_month,
        "next": next_month if next_month <= today.replace(day=1) else None,
    }


# -- reports and calendar ------------------------------------------------------

def published_reports(student):
    return list(ReportCard.objects.filter(student=student, status=ReportCardStatus.PUBLISHED)
                .select_related("term", "term__academic_year").order_by("-term__start_date"))


def school_events(today, months=6):
    """Published school-wide events from the start of this month on."""
    from events.models import CalendarEvent
    start = today.replace(day=1)
    end = today + timedelta(days=31 * months)
    events = (CalendarEvent.objects
              .filter(is_published=True, is_archived=False, notify_parents=True, start_date__lte=end)
              .filter(Q(start_date__gte=start) | Q(end_date__gte=start))
              .only("title", "category", "start_date", "end_date", "is_public_holiday")
              .order_by("start_date", "title"))
    groups = []
    for ev in events:
        key = ev.start_date.replace(day=1) if ev.start_date >= start else start
        if not groups or groups[-1]["month"] != key:
            groups.append({"month": key, "events": []})
        groups[-1]["events"].append({
            "title": ev.title,
            "category": ev.get_category_display() if not ev.is_public_holiday else "Holiday",
            "kind": "holiday" if ev.is_public_holiday else ev.category,
            "start": ev.start_date,
            "end": ev.end_date if ev.end_date and ev.end_date != ev.start_date else None,
            "past": (ev.end_date or ev.start_date) < today,
        })
    return groups
