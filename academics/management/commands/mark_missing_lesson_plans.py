"""
FR-LP-004: Auto-mark lesson plans as MISSING after the weekly deadline.

After the Monday 8:00 AM deadline passes, this command:
1. Finds all draft/no-status plans for the current week and marks them MISSING
2. Creates MISSING placeholder records for teachers who have no plan at all

Run via:
  python manage.py mark_missing_lesson_plans

Scheduled as Celery beat task (every hour during school days).
"""
import logging
from datetime import timedelta, time as time_type

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Mark non-submitted lesson plans as MISSING after the weekly deadline."

    def handle(self, *args, **options):
        from academics.models import LessonPlan, LessonPlanStatus, Term
        from academics.utils import get_current_term
        from users.models import User, UserRole
        from core.teacher_context import is_ecd_teacher
        from audit.models import log_event

        now = timezone.now()
        today = now.date()

        # Determine current week's Monday
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)

        # Calculate deadline
        deadline_day = getattr(settings, "LESSON_PLAN_SUBMISSION_DEADLINE_DAY", 0)
        deadline_time_str = getattr(settings, "LESSON_PLAN_SUBMISSION_DEADLINE_TIME", "08:00")
        h, m = map(int, deadline_time_str.split(":"))
        deadline_date = this_monday - timedelta(days=(7 - deadline_day) % 7)
        deadline_dt = timezone.make_aware(
            timezone.datetime.combine(deadline_date, time_type(h, m))
        )

        # Only run after the deadline has passed
        if now <= deadline_dt:
            self.stdout.write(f"Deadline has not yet passed (deadline: {deadline_dt.isoformat()}). Skipping.")
            return

        # Only mark missing for the current week (not future weeks)
        current_term = get_current_term()
        if not current_term:
            self.stdout.write("No current term. Skipping.")
            return

        # 1. Mark existing DRAFT/REVISION_REQUESTED plans for this week as MISSING
        draft_plans = LessonPlan.objects.filter(
            week_start_date=this_monday,
            status__in=[LessonPlanStatus.DRAFT, LessonPlanStatus.REVISION_REQUESTED],
        )
        marked_count = draft_plans.update(status=LessonPlanStatus.MISSING)
        for plan in draft_plans:
            log_event(
                actor=None,
                action_type="LESSON_PLAN_MARKED_MISSING",
                model_name="LessonPlan",
                object_id=plan.pk,
                description=f"Lesson plan {plan.class_name} — {plan.subject_name} ({plan.week_start_date}) auto-marked MISSING after deadline",
            )

        # 2. Create MISSING records for teachers with no plan at all for this week
        # Only teachers with timetable slots this term (no slots = no LP expected)
        from timetable.models import TimetableSlot
        slot_teacher_ids = set(
            TimetableSlot.objects.filter(term=current_term).values_list("teacher_id", flat=True)
        )
        all_teachers = User.objects.filter(
            role=UserRole.TEACHER, is_active=True,
            pk__in=slot_teacher_ids,
        ).select_related("staff_profile") if slot_teacher_ids else User.objects.none()
        all_teachers = [t for t in all_teachers if not is_ecd_teacher(t)]

        existing_teacher_ids = set(
            LessonPlan.objects.filter(
                week_start_date=this_monday,
            ).values_list("teacher_id", flat=True)
        )

        created_count = 0
        for teacher in all_teachers:
            if teacher.pk not in existing_teacher_ids:
                dept = ""
                if hasattr(teacher, "staff_profile") and teacher.staff_profile:
                    dept = teacher.staff_profile.department or ""
                LessonPlan.objects.create(
                    teacher=teacher,
                    term=current_term,
                    class_name="",
                    subject_name="",
                    week_start_date=this_monday,
                    status=LessonPlanStatus.MISSING,
                )
                created_count += 1

        self.stdout.write(
            f"Marked {marked_count} existing plans as MISSING, "
            f"created {created_count} MISSING placeholders for teachers with no plan."
        )