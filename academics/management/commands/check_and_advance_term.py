"""
FR-CAL-004: Lock terms whose post-term grace period is over, and auto-advance.

A term stays open for academics.models.TERM_LOCK_GRACE_DAYS (30) days after
its end_date and locks on the day after that.

Run via:
  python manage.py check_and_advance_term

Intended to be scheduled as a Celery beat task (daily at 06:00) or
via cron.  Also callable manually by Super Admin from the command line.
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "FR-CAL-004: Lock terms once the grace period after their end_date is "
        "over (end_date + TERM_LOCK_GRACE_DAYS) and notify Super Admin / Admin "
        "Officer about the transition."
    )

    def handle(self, *args, **options):
        from academics.models import TERM_LOCK_GRACE_DAYS, Term, lock_cutoff

        today = timezone.localdate()

        # Terms stay open for TERM_LOCK_GRACE_DAYS after they end; lock the
        # ones whose grace period is over (day 31 after the end date onwards).
        due = list(
            Term.objects.filter(is_locked=False, end_date__lt=lock_cutoff(today))
            .select_related("academic_year")
            .order_by("end_date")
        )
        if not due:
            self.stdout.write(
                f"No unlocked term is more than {TERM_LOCK_GRACE_DAYS} days past its end date. "
                "Nothing to lock."
            )
            return

        for term in due:
            self.stdout.write(
                self.style.WARNING(
                    f"Term '{term.name}' ended on {term.end_date}; its {TERM_LOCK_GRACE_DAYS}-day "
                    f"grace period ended on {term.editable_until}. Locking."
                )
            )
            term.is_locked = True
            term.save(update_fields=["is_locked", "updated_at"])
            try:
                from audit.models import log_event
                log_event(
                    actor=None,
                    action_type="TERM_TRANSITION",
                    model_name="Term",
                    object_id=term.pk,
                    description=(
                        f"Term '{term.name}' automatically locked {TERM_LOCK_GRACE_DAYS} days "
                        f"after it ended on {term.end_date}."
                    ),
                )
            except Exception:
                pass

        # Carry forward from the most recently ended term only; older ones in
        # the batch were simply overdue for locking.
        active_term = due[-1]

        # Look for the next unlocked term whose start_date is the closest
        # upcoming date after the locked term.
        next_term = (
            Term.objects.filter(
                is_locked=False,
                start_date__gte=active_term.end_date,
            )
            .order_by("start_date")
            .first()
        )

        # ── FR-FIN-003: Carry forward fee structures to next term ──────
        if next_term:
            try:
                from finance.models import FeeStructure, FeeStructureItem, FeeStructureStatus
                source_structures = FeeStructure.objects.filter(
                    term=active_term,
                    status__in=[FeeStructureStatus.PUBLISHED, FeeStructureStatus.LOCKED],
                ).prefetch_related("items")
                copied_count = 0
                for source in source_structures:
                    if FeeStructure.objects.filter(term=next_term, class_name=source.class_name).exists():
                        continue
                    new_fs = FeeStructure.objects.create(
                        term=next_term,
                        class_name=source.class_name,
                        is_active=source.is_active,
                        status=FeeStructureStatus.DRAFT,
                        late_pickup_charge=source.late_pickup_charge,
                        sibling_discount_mode=source.sibling_discount_mode,
                        sibling_discount_value=source.sibling_discount_value,
                        assessment_fee=source.assessment_fee,
                    )
                    for item in source.items.all():
                        FeeStructureItem.objects.create(
                            structure=new_fs,
                            category=item.category,
                            description=item.description,
                            amount=item.amount,
                        )
                    copied_count += 1
                if copied_count:
                    self.stdout.write(f"Carried forward {copied_count} fee structure(s) to '{next_term.name}'.")
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Fee structure carry forward failed: {exc}"))

        # ── Carry forward teacher-class assignments to next term ──────
        if next_term:
            try:
                from hr.models import TeacherClassAssignment
                from academics.models import GradeClass

                old_assignments = TeacherClassAssignment.objects.filter(
                    term=active_term,
                    rollover_ignored=False,
                ).select_related("teacher", "grade_class")

                rolled_count = 0
                skipped_conflict = 0
                for old in old_assignments:
                    # Skip if class no longer exists
                    if not GradeClass.objects.filter(pk=old.grade_class_id).exists():
                        continue

                    # Skip if teacher already assigned to this class in next term
                    if TeacherClassAssignment.objects.filter(
                        teacher=old.teacher,
                        term=next_term,
                        grade_class=old.grade_class,
                    ).exists():
                        skipped_conflict += 1
                        continue

                    TeacherClassAssignment.objects.create(
                        teacher=old.teacher,
                        term=next_term,
                        grade_class=old.grade_class,
                        is_class_teacher=old.is_class_teacher,
                        is_assistant_class_teacher=old.is_assistant_class_teacher,
                        subjects_taught=old.subjects_taught,
                    )
                    rolled_count += 1

                self.stdout.write(
                    f"Rolled forward {rolled_count} teacher assignment(s) to '{next_term.name}'."
                    + (f" ({skipped_conflict} skipped due to conflicts)" if skipped_conflict else "")
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Teacher assignment carry forward failed: {exc}"))

        # ── Notify admins ───────────────────────────────────────────────
        from communications.email_service import dispatch_notification
        from users.models import User, UserRole

        admins = User.objects.filter(
            role__in=[UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER],
            is_active=True,
        )

        locked_list = "\n".join(
            f"- '{t.name}' ({t.start_date} → {t.end_date})" for t in due
        )
        msg = (
            f"These terms have been automatically locked, {TERM_LOCK_GRACE_DAYS} days after "
            f"they ended:\n{locked_list}\n\n"
        )
        if next_term:
            msg += (
                f"The next term is '{next_term.name}' "
                f"({next_term.start_date} → {next_term.end_date})."
            )
        else:
            msg += (
                "No upcoming term was found. Please create the next term "
                "in System Settings → Academic Years."
            )
        link = "/settings/?tab=academic_year"

        for admin in admins:
            dispatch_notification(
                user=admin,
                title="Term Locked",
                message=msg,
                link=link,
                actor=None,
            )

        self.stdout.write(self.style.SUCCESS(msg))
