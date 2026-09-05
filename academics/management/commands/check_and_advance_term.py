"""
FR-CAL-004: Check if the current term has ended and auto-advance.

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
        "FR-CAL-004: Detect when a term's end_date has passed, lock it, "
        "and notify Super Admin / Admin Officer about the transition."
    )

    def handle(self, *args, **options):
        today = timezone.now().date()

        # Find the currently-active term (today is between start_date and
        # end_date, and it is not yet locked).
        from academics.models import Term

        active_term = Term.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
            is_locked=False,
        ).order_by("start_date").first()

        if not active_term:
            self.stdout.write("No active (unlocked, in-range) term found. Nothing to advance.")
            return

        # The term is still in its date range — nothing to do.
        if active_term.end_date >= today:
            self.stdout.write(
                f"Term '{active_term.name}' is still active (ends {active_term.end_date}). "
                "No advance needed."
            )
            return

        # ── Term end-date has passed — advance ──────────────────────────
        self.stdout.write(
            self.style.WARNING(
                f"Term '{active_term.name}' ended on {active_term.end_date}. "
                "Locking and searching for the next term..."
            )
        )

        # Lock the finished term
        active_term.is_locked = True
        active_term.save(update_fields=["is_locked", "updated_at"])

        # Look for the next unlocked term in the same academic year (or
        # any year) whose start_date is the closest upcoming date.
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

        if next_term:
            msg = (
                f"Academic term '{active_term.name}' ({active_term.start_date} → "
                f"{active_term.end_date}) has been automatically locked.\n\n"
                f"The next term is '{next_term.name}' "
                f"({next_term.start_date} → {next_term.end_date})."
            )
            link = "/settings/?tab=academic_year"
        else:
            msg = (
                f"Academic term '{active_term.name}' ({active_term.start_date} → "
                f"{active_term.end_date}) has been automatically locked.\n\n"
                "No upcoming term was found. Please create the next term "
                "in System Settings → Academic Years."
            )
            link = "/settings/?tab=academic_year"

        for admin in admins:
            dispatch_notification(
                user=admin,
                title="Term Transition — New Term Started",
                message=msg,
                link=link,
                actor=None,
            )

        # Also log to audit trail
        try:
            from audit.models import log_event
            log_event(
                actor=None,
                action_type="TERM_TRANSITION",
                model_name="Term",
                object_id=active_term.pk,
                description=(
                    f"Term '{active_term.name}' automatically locked (ended "
                    f"{active_term.end_date})."
                    + (f" Next term: '{next_term.name}'." if next_term else " No next term found.")
                ),
            )
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(msg))
