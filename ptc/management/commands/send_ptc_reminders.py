# PTC Notification Management Command
# Sends scheduled PTC notifications per FR-PTC-004, FR-PTC-005.
# Run via cron or Celery beat: e.g., daily at 8am.
#
# Adjustment note: The existing codebase uses dispatch_notification() from
# communications.email_service for in-app + email delivery. This command
# reuses that mechanism rather than building a parallel one.

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ptc.models import PTCWindow
from ptc.services import _get_ecd_class_names

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send PTC notification and reminder notifications (FR-PTC-003 to FR-PTC-005)"

    def handle(self, *args, **options):
        today = timezone.now().date()
        active_windows = PTCWindow.objects.filter(is_published=True).select_related("academic_year")

        for window in active_windows:
            self._check_notification_window(window, today)
            self._check_comment_window_open(window, today)
            self._check_reminders(window, today)

    def _check_notification_window(self, window: PTCWindow, today):
        """
        FR-PTC-003, FR-PTC-004: When the 30-day notification window opens,
        notify all subject teachers and class teachers.
        """
        if (
            window.notification_window_opens_at
            and today == window.notification_window_opens_at
        ):
            days_until = window.days_until_ptc
            self.stdout.write(
                f"PTC notification window opened for {window}. "
                f"PTC is in {days_until} days."
            )
            self._notify_subject_teachers_window_open(window, days_until)
            self._notify_class_teachers_window_open(window, days_until)

    def _check_comment_window_open(self, window: PTCWindow, today):
        """
        FR-PTC-014: When the 14-day comment entry window opens,
        notify subject teachers and class teachers that they can
        begin entering comments and attribute ratings.
        """
        if (
            window.comment_entry_window_opens_at
            and today == window.comment_entry_window_opens_at
        ):
            self.stdout.write(
                f"PTC comment entry window opened for {window}. "
                f"PTC is in {window.days_until_ptc} days."
            )
            self._notify_teachers_comment_window_open(window)

    def _check_reminders(self, window: PTCWindow, today):
        """
        FR-PTC-005: 3 days before PTC date, remind teachers with incomplete data.
        """
        if window.days_until_ptc == 3:
            self.stdout.write(f"PTC reminders triggered for {window} (3 days out)")
            self._remind_incomplete_subject_teachers(window)
            self._remind_incomplete_class_teachers(window)

    def _get_actor_user(self):
        """Return a system/superuser actor for audit purposes."""
        from users.models import User
        return User.objects.filter(is_superuser=True).first()

    def _notify_subject_teachers_window_open(self, window, days_until):
        """Notify all subject teachers when PTC notification window opens."""
        try:
            from communications.email_service import dispatch_notification
            from users.models import User, UserRole
            from hr.models import TeacherClassAssignment

            teachers = User.objects.filter(
                role=UserRole.TEACHER, is_active=True
            ).distinct()
            actor = self._get_actor_user()

            for teacher in teachers:
                assignments = TeacherClassAssignment.objects.filter(
                    teacher__user=teacher,
                    term=window.academic_year.terms.first(),
                )
                has_subjects = any(
                    a.subjects_taught for a in assignments
                )
                if not has_subjects:
                    continue

                dispatch_notification(
                    user=teacher,
                    title="PTC Comment Window Open",
                    message=(
                        f"PTC is scheduled for {window.ptc_date}. "
                        f"You have {days_until} days to add your subject comments "
                        f"for each student."
                    ),
                    link="/ptc/comments/",
                    actor=actor,
                )
                logger.info(
                    f"Notified subject teacher {teacher} - PTC window open"
                )
        except ImportError:
            logger.warning("dispatch_notification not available; skipping notifications")

    def _notify_class_teachers_window_open(self, window, days_until):
        """Notify all class teachers when learner attributes window opens."""
        try:
            from communications.email_service import dispatch_notification
            from users.models import User, UserRole
            from hr.models import TeacherClassAssignment

            teachers = User.objects.filter(
                role=UserRole.TEACHER, is_active=True
            ).distinct()
            actor = self._get_actor_user()

            for teacher in teachers:
                is_class_teacher = TeacherClassAssignment.objects.filter(
                    teacher__user=teacher,
                    term=window.academic_year.terms.first(),
                    is_class_teacher=True,
                ).exists()

                if not is_class_teacher:
                    continue

                dispatch_notification(
                    user=teacher,
                    title="PTC Learner Attributes",
                    message=(
                        f"PTC is in {days_until} days. "
                        f"Please complete learner attribute ratings for your class."
                    ),
                    link="/ptc/attributes/",
                    actor=actor,
                )
                logger.info(
                    f"Notified class teacher {teacher} - attributes window open"
                )
        except ImportError:
            logger.warning("dispatch_notification not available; skipping notifications")

    def _notify_teachers_comment_window_open(self, window):
        """
        FR-PTC-014: Notify subject teachers and class teachers when the
        14-day comment/attribute entry window opens.
        """
        try:
            from communications.email_service import dispatch_notification
            from users.models import User, UserRole
            from hr.models import TeacherClassAssignment

            teachers = User.objects.filter(
                role=UserRole.TEACHER, is_active=True
            ).distinct()
            actor = self._get_actor_user()

            for teacher in teachers:
                assignments = TeacherClassAssignment.objects.filter(
                    teacher__user=teacher,
                    term=window.academic_year.terms.first(),
                )

                is_ct = any(a.is_class_teacher for a in assignments)
                has_subjects = any(a.subjects_taught for a in assignments)

                if not is_ct and not has_subjects:
                    continue

                parts = []
                if has_subjects:
                    parts.append("subject comments")
                if is_ct:
                    parts.append("learner attribute ratings")

                dispatch_notification(
                    user=teacher,
                    title="PTC Comment Entry Window Open",
                    message=(
                        f"The comment entry window for PTC ({window.ptc_date}) "
                        f"is now open. You can enter {' and '.join(parts)} "
                        f"for your students. The window closes on {window.comment_entry_window_closes_at}."
                    ),
                    link="/ptc/comments/",
                    actor=actor,
                )
                logger.info(
                    f"Notified {teacher} - comment entry window open"
                )
        except ImportError:
            logger.warning("dispatch_notification not available; skipping notifications")

    def _remind_incomplete_subject_teachers(self, window):
        """Remind subject teachers who haven't entered all comments (FR-PTC-005)."""
        try:
            from communications.email_service import dispatch_notification
            from ptc.models import PTCSubjectComment
            from students.models import Student
            from academics.models import GradeClass, Department
            from hr.models import TeacherClassAssignment

            ecd_names = _get_ecd_class_names()
            actor = self._get_actor_user()

            teachers_with_incomplete = {}

            assignments = TeacherClassAssignment.objects.filter(
                term=window.academic_year.terms.first(),
            ).select_related("teacher__user", "grade_class")

            for ta in assignments:
                subjects = ta.subjects_taught or []
                if not subjects:
                    continue

                students = Student.objects.filter(
                    is_archived=False, status="active",
                    class_name=ta.grade_class.name,
                ).exclude(class_name__in=ecd_names)

                for student in students:
                    for subj in subjects:
                        exists = PTCSubjectComment.objects.filter(
                            student=student,
                            ptc_window=window,
                            subject_name=subj,
                        ).exists()
                        if not exists:
                            teacher = ta.teacher.user
                            if teacher.pk not in teachers_with_incomplete:
                                teachers_with_incomplete[teacher.pk] = {
                                    "teacher": teacher,
                                    "subjects": {},
                                }
                            teachers_with_incomplete[teacher.pk]["subjects"].setdefault(subj, 0)
                            teachers_with_incomplete[teacher.pk]["subjects"][subj] += 1

            for data in teachers_with_incomplete.values():
                parts = []
                for subj, count in data["subjects"].items():
                    parts.append(f"{count} students in {subj}")
                message = (
                    "Reminder: PTC is in 3 days. You have not yet entered comments for "
                    + "; ".join(parts) + "."
                )
                dispatch_notification(
                    user=data["teacher"],
                    title="PTC Comment Reminder",
                    message=message,
                    link="/ptc/comments/",
                    actor=actor,
                )
                logger.info(f"Reminder sent to {data['teacher']} - incomplete comments")

        except ImportError:
            logger.warning("dispatch_notification not available; skipping reminders")

    def _remind_incomplete_class_teachers(self, window):
        """Remind class teachers who haven't completed all learner attributes (FR-PTC-005)."""
        try:
            from communications.email_service import dispatch_notification
            from ptc.models import LearnerAttributeRatingEntry
            from students.models import Student
            from academics.models import GradeClass, Department
            from hr.models import TeacherClassAssignment

            ecd_names = _get_ecd_class_names()
            actor = self._get_actor_user()

            class_teachers = TeacherClassAssignment.objects.filter(
                term=window.academic_year.terms.first(),
                is_class_teacher=True,
            ).select_related("teacher__user", "grade_class")

            for ta in class_teachers:
                teacher = ta.teacher.user
                students = Student.objects.filter(
                    is_archived=False, status="active",
                    class_name=ta.grade_class.name,
                ).exclude(class_name__in=ecd_names)

                incomplete_count = 0
                for student in students:
                    rated = LearnerAttributeRatingEntry.objects.filter(
                        student=student, ptc_window=window,
                    ).exclude(rating="").count()
                    if rated < 12:
                        incomplete_count += 1

                if incomplete_count > 0:
                    dispatch_notification(
                        user=teacher,
                        title="PTC Learner Attributes Reminder",
                        message=(
                            f"Reminder: PTC is in 3 days. Learner attribute ratings "
                            f"are incomplete for {incomplete_count} students."
                        ),
                        link="/ptc/attributes/",
                        actor=actor,
                    )
                    logger.info(
                        f"Reminder sent to {teacher} - {incomplete_count} incomplete attributes"
                    )

        except ImportError:
            logger.warning("dispatch_notification not available; skipping reminders")
