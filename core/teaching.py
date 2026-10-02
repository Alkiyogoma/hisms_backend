"""Personal teaching timetable for any user who is assigned classes to teach."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from timetable.models import TimetableSlot, Weekday

_DAYS = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]


def get_my_timetable(user, today: date | None = None, now_time=None):
    """Return the user's lessons for today (or their next teaching day), or None
    if they have no teaching assignments this term (so no card is shown)."""
    from academics.utils import get_current_term
    from hr.models import TeacherClassAssignment

    term = get_current_term()
    if term is None:
        return None
    slots = TimetableSlot.objects.filter(teacher=user, term=term)
    assigned = (
        slots.exists()
        or TeacherClassAssignment.objects.filter(teacher__user=user, term=term).exists()
    )
    if not assigned:
        return None

    today = today or date.today()
    now_time = now_time or datetime.now().time()

    # One query for the whole week; grouped by weekday for the week drawer.
    by_day = {d: [] for d in _DAYS}
    for s in slots.select_related("room").order_by("start_time"):
        if s.day_of_week in by_day:
            by_day[s.day_of_week].append(s)
    week = [{"name": Weekday(d).label, "is_today": i == today.weekday(), "slots": by_day[d]}
            for i, d in enumerate(_DAYS)]

    # Today's lessons; on a non-teaching day (weekend / no lessons) the next one.
    day, day_slots, label = today, [], "Today"
    for offset in range(8):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        found = by_day[_DAYS[candidate.weekday()]]
        if found:
            day, day_slots = candidate, list(found)
            label = "Today" if offset == 0 else ("Tomorrow" if offset == 1 else candidate.strftime("%A"))
            break

    is_today = day == today
    next_marked = False
    for s in day_slots:
        if not is_today:
            s.state = "later"
        elif s.end_time <= now_time:
            s.state = "done"
        elif s.start_time <= now_time:
            s.state = "now"
        elif not next_marked:
            s.state = "next"
            next_marked = True
        else:
            s.state = "later"
    return {"slots": day_slots, "label": label, "date": day, "is_today": is_today,
            "week": week, "lessons_this_week": sum(len(v) for v in by_day.values())}
