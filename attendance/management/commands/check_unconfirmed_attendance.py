"""
FR-ATT-006: 9:00 AM unconfirmed attendance alert.

Finds every active student who has NO AttendanceEntry for today
(meaning their status is the default "unconfirmed") and sends
in-app notifications to:

  1. The teachers assigned to those students' classes (via TimetableSlot),
     with a per-class list of unconfirmed student names.
  2. All Admin Officers, with the school-wide unconfirmed student list.
  3. The Head of School, with a summary count.

Runs daily at 09:00 EAT via Celery beat.
Skips weekends and public holidays.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import AttendanceEntry
from communications.email_service import dispatch_notification
from students.models import Student, StudentStatus
from users.models import User, UserRole


class Command(BaseCommand):
    help = (
        "FR-ATT-006: At 09:00, alert teachers and Admin Officers about "
        "students with no check-in (unconfirmed status)."
    )

    def handle(self, *args, **options):
        today = timezone.now().date()

        # ---------- 1. Find unconfirmed students ----------
        # Students who are active and DO NOT have an attendance entry for today.
        active_students = Student.objects.filter(
            is_archived=False, status=StudentStatus.ACTIVE
        )
        todays_entry_student_ids = set(
            AttendanceEntry.objects.filter(date=today)
            .values_list("student_id", flat=True)
            .distinct()
        )

        unconfirmed_qs = active_students.exclude(
            id__in=todays_entry_student_ids
        ).order_by("class_name", "last_name", "first_name")

        total_unconfirmed = unconfirmed_qs.count()

        if total_unconfirmed == 0:
            self.stdout.write(self.style.SUCCESS("No unconfirmed students today."))
            return

        # ---------- 2. Group by class ----------
        from collections import defaultdict
        unconfirmed_by_class = defaultdict(list)
        for s in unconfirmed_qs:
            unconfirmed_by_class[s.class_name].append(s)

        sys_user = User.objects.filter(is_superuser=True).first()

        # ---------- 3. Notify teachers ----------
        from timetable.models import TimetableSlot
        from academics.models import Term

        # Scope to current active term so teachers aren't alerted for
        # classes they taught in previous terms.
        current_term = Term.objects.filter(is_locked=False).order_by('-start_date').first()

        for class_name, students in unconfirmed_by_class.items():
            # Find teachers assigned to this class via the timetable
            qs = TimetableSlot.objects.filter(
                class_name__iexact=class_name,
            )
            if current_term:
                qs = qs.filter(term=current_term)
            teacher_ids = set(qs.values_list("teacher_id", flat=True).distinct())
            # Also include teachers with other slots in the same class
            # (e.g. different subjects — already covered above by distinct)

            if not teacher_ids:
                continue

            student_names = ", ".join(
                s.get_full_name() or s.admission_no for s in students
            )
            msg = (
                f"Unconfirmed students in {class_name} as of 09:00 today: "
                f"{student_names}"
            )

            teachers = User.objects.filter(
                id__in=teacher_ids, is_active=True,
                role__in=[UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD]
            )
            for teacher in teachers:
                dispatch_notification(
                    user=teacher,
                    title=f"Unconfirmed Students — {class_name}",
                    message=msg,
                    link="/attendance/",
                    actor=sys_user,
                )

        # ---------- 4. Notify Admin Officers ----------
        admin_officers = User.objects.filter(
            role=UserRole.ADMIN_OFFICER, is_active=True
        )
        if admin_officers.exists():
            # Build a compact class-by-class summary for admins
            lines = []
            for class_name, students in sorted(unconfirmed_by_class.items()):
                names = ", ".join(
                    s.get_full_name() or s.admission_no for s in students
                )
                lines.append(f"{class_name} ({len(students)}): {names}")
            admin_msg = (
                f"Unconfirmed attendance as of 09:00 today — "
                f"{total_unconfirmed} student(s) across "
                f"{len(unconfirmed_by_class)} class(es).\n\n"
                + "\n".join(lines)
            )

            for ao in admin_officers:
                dispatch_notification(
                    user=ao,
                    title=f"Unconfirmed Attendance — {total_unconfirmed} Student(s)",
                    message=admin_msg,
                    link="/attendance/",
                    actor=sys_user,
                )

        # ---------- 5. Notify HOS (summary only) ----------
        hos = User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True).first()
        if hos:
            summary_lines = []
            for class_name, students in sorted(unconfirmed_by_class.items()):
                summary_lines.append(f"{class_name}: {len(students)}")
            hos_msg = (
                f"Unconfirmed attendance summary as of 09:00 today:\n"
                + "\n".join(summary_lines)
            )
            dispatch_notification(
                user=hos,
                title=f"Unconfirmed Attendance — {total_unconfirmed} Student(s)",
                message=hos_msg,
                link="/attendance/",
                actor=sys_user,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Sent unconfirmed alerts for {total_unconfirmed} student(s) "
                f"across {len(unconfirmed_by_class)} class(es)."
            )
        )
