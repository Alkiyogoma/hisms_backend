"""
Attendance over a date range: the single source of every figure on the
Today, Attendance Reports and Printable Register tabs.

Rules (the same everywhere):

* Only school days count: weekends, holidays on the school calendar and days
  outside the term dates are omitted.
* A learner is on roll from their enrolment date. Days before it are not
  counted at all.
* Unmarked learner-days (no entry, or an "unconfirmed" entry) are counted as
  neither present nor absent: they are left out of every percentage.
* Rate = in school / marked, where in school = present + late and marked =
  present + late + absent + excused. Late is in school but is always also
  reported as its own figure. Excused is not in school and is shown as its own
  count; the reason is never part of these figures.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from attendance.models import AttendanceEntry, AttendanceStatus

TARGET = 96
FLAG = 90
PERSISTENT = 80
HABITUAL_LATE = 5
# A register confirmed after this time is reported as confirmed late.
REGISTER_DUE = time(9, 0)

CODES = {
    AttendanceStatus.PRESENT: "P",
    AttendanceStatus.LATE: "L",
    AttendanceStatus.ABSENT: "A",
    AttendanceStatus.EXCUSED: "E",
}
STATUS_FOR_CODE = {v: k for k, v in CODES.items()}
CODE_LABELS = {"P": "Present", "L": "Late", "A": "Absent", "E": "Excused", "U": "Not marked"}
# Code of a cell that is not counted: not on roll yet, or a day still ahead.
NOT_ON_ROLL = ""
FUTURE = "F"


def rate(in_school: int, marked: int):
    return round(in_school / marked * 100, 1) if marked else None


def band(pct) -> str:
    """Colour band used across the module: green at 90%+, amber 80-89, red below."""
    if pct is None:
        return "none"
    if pct < PERSISTENT:
        return "red"
    if pct < FLAG:
        return "amber"
    return "green"


def status_tag(pct):
    if pct is None:
        return ("grey", "No marks")
    if pct < PERSISTENT:
        return ("red", "Persistent")
    if pct < FLAG:
        return ("amber", "Flagged")
    return ("green", "On track")


# -- calendar ---------------------------------------------------------------

@dataclass
class Period:
    start: date
    end: date
    days: list = field(default_factory=list)          # school days, in order
    holidays: list = field(default_factory=list)      # [(date, title)] weekdays omitted as holidays
    out_of_term: int = 0                              # weekdays omitted as outside term dates
    today: date | None = None

    @property
    def past_days(self):
        """School days up to and including today (the ones that can be marked)."""
        return [d for d in self.days if d <= self.today]

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%-d %B %Y")
        if self.start.year != self.end.year:
            return f"{self.start:%-d %B %Y} – {self.end:%-d %B %Y}"
        if self.start.month != self.end.month:
            return f"{self.start:%-d %B} – {self.end:%-d %B %Y}"
        return f"{self.start:%-d} – {self.end:%-d %B %Y}"


def holidays_between(start: date, end: date) -> dict:
    from events.models import CalendarEvent, EventCategory
    events = CalendarEvent.objects.filter(
        Q(category=EventCategory.HOLIDAY) | Q(is_public_holiday=True),
        is_archived=False, start_date__lte=end,
    ).filter(Q(end_date__gte=start) | Q(end_date__isnull=True, start_date__gte=start))
    out = {}
    for ev in events:
        d, last = ev.start_date, ev.end_date or ev.start_date
        while d <= last:
            if start <= d <= end:
                out.setdefault(d, ev.title)
            d += timedelta(days=1)
    return out


def term_spans(start: date, end: date) -> list:
    """Dated terms overlapping the range, as (start, end) pairs."""
    from academics.models import Term
    return list(Term.objects.filter(start_date__isnull=False, end_date__isnull=False,
                                    start_date__lte=end, end_date__gte=start)
                .values_list("start_date", "end_date"))


def build_period(start: date, end: date, today: date | None = None) -> Period:
    today = today or timezone.localdate()
    if end < start:
        start, end = end, start
    hols = holidays_between(start, end)
    spans = term_spans(start, end)
    period = Period(start=start, end=end, today=today)
    d = start
    while d <= end:
        if d.weekday() < 5:
            if d in hols:
                period.holidays.append((d, hols[d]))
            elif spans and not any(s <= d <= e for s, e in spans):
                period.out_of_term += 1
            else:
                period.days.append(d)
        d += timedelta(days=1)
    return period


# -- classes ----------------------------------------------------------------

def class_names() -> list:
    from academics.models import GradeClass
    from students.models import Student
    names = list(GradeClass.objects.order_by("sort_order", "name").values_list("name", flat=True))
    extra = sorted(set(Student.objects.filter(is_archived=False).exclude(class_name="")
                       .values_list("class_name", flat=True)) - set(names))
    return names + extra


def class_teachers() -> dict:
    """Class name -> class teacher's name, from the latest term with an assignment."""
    from hr.models import TeacherClassAssignment
    out = {}
    rows = (TeacherClassAssignment.objects.filter(is_class_teacher=True)
            .select_related("teacher", "grade_class", "term")
            .order_by("term__start_date", "pk"))
    for a in rows:
        out[a.grade_class.name] = a.teacher.full_name or str(a.teacher)
    return out


# -- learners ---------------------------------------------------------------

def _students(period: Period, class_name: str | None, student_ids=None):
    """Learners on roll at any point in the period: active learners, plus
    anyone with a register entry in it (so learners who have since left still
    appear for the days they were marked)."""
    from students.models import Student, StudentStatus
    in_range = AttendanceEntry.objects.filter(date__range=(period.start, period.end))
    if class_name:
        in_range = in_range.filter(class_name=class_name)
    current = Q(is_archived=False, status=StudentStatus.ACTIVE)
    if class_name:
        current &= Q(class_name=class_name)
    qs = Student.objects.filter(current | Q(pk__in=in_range.values("student_id")))
    if student_ids is not None:
        qs = qs.filter(pk__in=student_ids)
    return list(qs.distinct().order_by("first_name", "last_name"))


def build_learners(period: Period, class_name: str | None = None, student_ids=None,
                   with_reasons: bool = False) -> list:
    """One dict per learner with a code per school day in ``period.days``:
    P/L/A/E, U (on roll, not marked), "" (not on roll) or F (still ahead)."""
    from students.models import StudentStatus
    students = _students(period, class_name, student_ids)
    if not students:
        return []
    ids = [s.pk for s in students]
    entries = defaultdict(dict)
    last_class = {}
    for e in (AttendanceEntry.objects.filter(student_id__in=ids, date__range=(period.start, period.end))
              .order_by("date")
              .values("student_id", "date", "status", "marked_at", "class_name", "reason",
                      "check_in_time", "check_out_time")):
        entries[e["student_id"]][e["date"]] = e
        last_class[e["student_id"]] = e["class_name"]

    learners = []
    for s in students:
        mine = entries.get(s.pk, {})
        first_day = s.enrolment_date
        # A learner who has left is on roll only up to their last register entry.
        last_day = None
        if s.is_archived or s.status != StudentStatus.ACTIVE:
            last_day = max(mine) if mine else period.start - timedelta(days=1)
        codes, cells = [], []
        for d in period.days:
            e = mine.get(d)
            if e and e["status"] in CODES:
                code = CODES[e["status"]]
            elif d > period.today:
                code = FUTURE
            elif e is None and ((first_day and d < first_day) or (last_day and d > last_day)):
                code = NOT_ON_ROLL
            else:
                code = "U"
            codes.append(code)
            cells.append((d, code, e))
        counts = {k: codes.count(k) for k in ("P", "L", "A", "E", "U")}
        marked = counts["P"] + counts["L"] + counts["A"] + counts["E"]
        in_school = counts["P"] + counts["L"]
        pct = rate(in_school, marked)
        row = {
            "student": s,
            "id": s.pk,
            "name": f"{s.first_name} {s.last_name}".strip(),
            "admission_no": s.admission_no,
            "class_name": class_name or last_class.get(s.pk) or s.class_name,
            "codes": codes,
            "cells": cells,
            "present": counts["P"], "late": counts["L"], "absent": counts["A"],
            "excused": counts["E"], "unmarked": counts["U"],
            "in_school": in_school, "marked": marked, "rate": pct,
            "band": band(pct), "tag": status_tag(pct),
            "on_roll_days": sum(1 for c in codes if c not in (NOT_ON_ROLL, FUTURE)),
            "admitted_in_period": bool(first_day and period.start < first_day <= period.end),
            "enrolment_date": first_day,
            "late_share": round(counts["L"] / marked * 100, 1) if marked else None,
        }
        if with_reasons:
            row["reasons"] = [(d, (e or {}).get("reason") or "") for d, c, e in cells if c == "E"]
        learners.append(row)
    return learners


# -- summaries --------------------------------------------------------------

def is_flagged(r):
    return r["rate"] is not None and PERSISTENT <= r["rate"] < FLAG


def is_persistent(r):
    return r["rate"] is not None and r["rate"] < PERSISTENT


def is_habitually_late(r):
    return r["late"] >= HABITUAL_LATE


def summarise(learners: list) -> dict:
    marked = sum(r["marked"] for r in learners)
    in_school = sum(r["in_school"] for r in learners)
    return {
        "learners": len(learners),
        "marked": marked,
        "in_school": in_school,
        "present": sum(r["present"] for r in learners),
        "late": sum(r["late"] for r in learners),
        "absent": sum(r["absent"] for r in learners),
        "excused": sum(r["excused"] for r in learners),
        "unmarked": sum(r["unmarked"] for r in learners),
        "average": rate(in_school, marked),
        "flagged": sum(1 for r in learners if is_flagged(r)),
        "persistent": sum(1 for r in learners if is_persistent(r)),
        "habitually_late": sum(1 for r in learners if is_habitually_late(r)),
    }


def by_class(learners: list, order: list) -> list:
    groups = defaultdict(list)
    for r in learners:
        groups[r["class_name"]].append(r)
    rank = {n: i for i, n in enumerate(order)}
    out = []
    for name in sorted(groups, key=lambda n: (rank.get(n, len(rank)), n)):
        s = summarise(groups[name])
        s["name"] = name
        s["band"] = band(s["average"])
        out.append(s)
    return out


def series(period: Period, learners: list) -> dict:
    """Learner-day counts per status, one point per school day for short
    ranges and one per week otherwise. Points still ahead are null."""
    per_day = len(period.days) <= 15
    buckets = []
    for i, d in enumerate(period.days):
        key = d if per_day else d - timedelta(days=d.weekday())
        if not buckets or buckets[-1]["key"] != key:
            buckets.append({"key": key, "idx": [], "days": []})
        buckets[-1]["idx"].append(i)
        buckets[-1]["days"].append(d)
    points = []
    for n, b in enumerate(buckets):
        first, last = b["days"][0], b["days"][-1]
        future = first > period.today
        counts = {k: 0 for k in "PLAEU"}
        if not future:
            for r in learners:
                for i in b["idx"]:
                    c = r["codes"][i]
                    if c in counts:
                        counts[c] += 1
        marked = counts["P"] + counts["L"] + counts["A"] + counts["E"]
        if per_day:
            label, title = first.strftime("%a %-d"), first.strftime("%A %-d %B %Y")
        else:
            label = f"Wk {n + 1}"
            title = f"{first:%-d %b} – {last:%-d %b %Y}"
        # A week is plotted as learners per school day, so a short week (a
        # holiday, or the week still in progress) does not look like a drop.
        days = len([d for d in b["days"] if d <= period.today]) or 1

        def value(k):
            if future:
                return None
            return counts[k] if per_day else round(counts[k] / days, 1)
        points.append({
            "label": label, "title": title,
            "start": first.isoformat(), "end": last.isoformat(),
            "future": future, "days": len(b["days"]),
            "present": value("P"), "late": value("L"), "excused": value("E"),
            "absent": value("A"), "unmarked": value("U"),
            "totals": None if future else counts,
            "rate": None if future else rate(counts["P"] + counts["L"], marked),
        })
    return {"unit": "day" if per_day else "week", "points": points}


def register_issues(period: Period, learners: list, now=None) -> list:
    """Class-days whose register was never or only partly confirmed, or was
    confirmed after REGISTER_DUE. Most recent first."""
    now = timezone.localtime(now) if now else timezone.localtime()
    from attendance.policy import lock_hour
    teachers = class_teachers()
    groups = defaultdict(lambda: {"on_roll": 0, "unmarked": 0, "last": None})
    for r in learners:
        for i, (d, code, e) in enumerate(r["cells"]):
            if code in (NOT_ON_ROLL, FUTURE):
                continue
            g = groups[(d, r["class_name"])]
            g["on_roll"] += 1
            if code == "U":
                g["unmarked"] += 1
            elif e and e["marked_at"]:
                t = timezone.localtime(e["marked_at"])
                if t.date() == d and (g["last"] is None or t > g["last"]):
                    g["last"] = t
    out = []
    for (d, cls), g in groups.items():
        open_today = d == now.date() and now.hour < lock_hour()
        if g["unmarked"] == g["on_roll"]:
            state, label = "red", ("Not yet marked — open" if open_today else "Never confirmed")
        elif g["unmarked"]:
            state, label = ("amber" if open_today else "red"), f"Partly confirmed — {g['on_roll'] - g['unmarked']} of {g['on_roll']}"
        elif g["last"] and g["last"].time() > REGISTER_DUE:
            state, label = "amber", f"Confirmed {g['last']:%H:%M} — late"
        else:
            continue
        out.append({"date": d, "class_name": cls, "teacher": teachers.get(cls, ""),
                    "unmarked": g["unmarked"], "on_roll": g["on_roll"], "state": state, "label": label})
    out.sort(key=lambda x: (-x["date"].toordinal(), x["class_name"]))
    return out


def class_day_status(day: date, learners: list, order: list) -> list:
    """Per class register state for one day (the Today tab's chase list)."""
    groups = defaultdict(lambda: {"on_roll": 0, "unmarked": 0, "last": None})
    for r in learners:
        for d, code, e in r["cells"]:
            if d != day or code in (NOT_ON_ROLL, FUTURE):
                continue
            g = groups[r["class_name"]]
            g["on_roll"] += 1
            if code == "U":
                g["unmarked"] += 1
            elif e and e["marked_at"]:
                t = timezone.localtime(e["marked_at"])
                if g["last"] is None or t > g["last"]:
                    g["last"] = t
    rank = {n: i for i, n in enumerate(order)}
    out = []
    for cls in sorted(groups, key=lambda n: (rank.get(n, len(rank)), n)):
        g = groups[cls]
        if g["unmarked"] == g["on_roll"]:
            state, text = "red", "Not yet marked"
        elif g["unmarked"]:
            state, text = "red", f"{g['on_roll'] - g['unmarked']} of {g['on_roll']} marked"
        elif g["last"] and g["last"].time() > REGISTER_DUE:
            state, text = "amber", f"Marked {g['last']:%H:%M}"
        else:
            state, text = "green", f"Marked {g['last']:%H:%M}" if g["last"] else "Marked"
        out.append({"name": cls, "state": state, "text": text, **g})
    return out


def learner_pattern(row: dict) -> dict:
    """Extra detail for one learner's day-by-day view."""
    weekday_abs = defaultdict(int)
    run = best = 0
    for d, code, _ in row["cells"]:
        if code in (NOT_ON_ROLL, FUTURE, "U"):
            # An unmarked day neither breaks nor extends a run of absences.
            continue
        if code == "A":
            weekday_abs[d.strftime("%A")] += 1
            run += 1
            best = max(best, run)
        else:
            run = 0
    worst = max(weekday_abs.items(), key=lambda kv: kv[1]) if weekday_abs else None
    months = []
    for d, code, _ in row["cells"]:
        if code == NOT_ON_ROLL:
            continue  # before admission: not part of this learner's record
        label = d.strftime("%B %Y")
        if not months or months[-1]["label"] != label:
            months.append({"label": label, "cells": []})
        months[-1]["cells"].append({"date": d, "code": code})
    return {"longest_absent_run": best, "worst_weekday": worst, "months": months}


def week_codes(student_ids, day: date) -> dict:
    """Mon-Fri codes for the week containing ``day`` (Today tab dots)."""
    monday = day - timedelta(days=day.weekday())
    week = [monday + timedelta(days=i) for i in range(5)]
    out = defaultdict(lambda: ["U"] * 5)
    for e in AttendanceEntry.objects.filter(student_id__in=student_ids, date__in=week).values("student_id", "date", "status"):
        out[e["student_id"]][week.index(e["date"])] = CODES.get(e["status"], "U")
    return out, week


def term_rates(student_ids, term, upto: date) -> dict:
    """Attendance rate per learner over the term so far, unmarked excluded."""
    if not term or not term.start_date:
        return {}
    end = min(term.end_date or upto, upto)
    counts = defaultdict(lambda: [0, 0])
    for sid, status in AttendanceEntry.objects.filter(
            student_id__in=student_ids, date__range=(term.start_date, end),
            status__in=list(CODES)).values_list("student_id", "status"):
        counts[sid][1] += 1
        if status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            counts[sid][0] += 1
    return {sid: rate(p, m) for sid, (p, m) in counts.items()}


def parse_date(value, default: date) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else default
    except (TypeError, ValueError):
        return default


def term_range(term, today: date | None = None):
    """A term's dates up to today (the whole term once it has ended); the
    last 30 days when there is no dated term."""
    today = today or timezone.localdate()
    if term is None or not term.start_date:
        return today - timedelta(days=30), today
    end = min(term.end_date or today, today)
    return term.start_date, max(end, term.start_date)


def learner_attendance(student, start: date, end: date, today: date | None = None) -> dict:
    """One learner's attendance over a range, for the profile and reports."""
    period = build_period(start, end, today)
    rows = build_learners(period, None, student_ids=[student.pk])
    row = rows[0] if rows else None
    counted = [(d, c) for d, c, _ in (row["cells"] if row else []) if c not in (NOT_ON_ROLL, FUTURE)]
    return {"period": period, "row": row, "pattern": learner_pattern(row) if row else None,
            "recent": counted[-10:][::-1], "strip": counted[-30:]}
