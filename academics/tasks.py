"""
Celery tasks for the Academics app.

FR-CAL-004: Auto-advance terms when their end_date passes.
FR-LP-004: Auto-mark lesson plans as MISSING after deadline.
Scheduled via Celery Beat — see config/celery.py.
"""

from celery import shared_task
from django.core.management import call_command
import io
import logging

logger = logging.getLogger(__name__)


@shared_task
def check_and_advance_term_task():
    """
    FR-CAL-004 / UAT ACD-003: Check if the current term has ended and
    auto-advance to the next term.  Locks the finished term, notifies
    Super Admin / Admin Officer, and logs to the audit trail.

    Runs daily at 06:00 EAT (03:00 UTC).
    """
    out = io.StringIO()
    try:
        call_command("check_and_advance_term", stdout=out)
        output = out.getvalue().strip()
        logger.info("check_and_advance_term_task: %s", output)
        return output
    except Exception as e:
        logger.error("check_and_advance_term_task error: %s", e)
        raise


@shared_task
def mark_missing_lesson_plans_task():
    """
    FR-LP-004: After the weekly deadline (Monday 8:00 AM), mark all
    non-submitted lesson plans as MISSING and create placeholder
    MISSING records for teachers who never created a plan.

    Runs hourly during school hours (Mon-Fri 08:00-17:00 EAT).
    The command itself is idempotent — it checks if the deadline has
    passed before taking action.
    """
    out = io.StringIO()
    try:
        call_command("mark_missing_lesson_plans", stdout=out)
        output = out.getvalue().strip()
        logger.info("mark_missing_lesson_plans_task: %s", output)
        return output
    except Exception as e:
        logger.error("mark_missing_lesson_plans_task error: %s", e)
        raise


@shared_task(bind=True, max_retries=1)
def calculate_progression_cases_task(self, config_id, triggered_by_id, recalculate=False):
    """
    Item 9: Async calculation of progression cases with persisted status.

    Updates config.recalc_status at each phase (queued → running → completed/failed)
    so the config list page can display progress to the end user.
    On failure, records per-student failure details in recalc_failed_details.
    """
    from academics.models import ProgressionConfig
    from academics.models import RecalcStatus as RS
    from academics.services import calculate_progression_cases, ProgressionConfigBlockedError
    from users.models import User
    from audit.models import log_event

    try:
        config = ProgressionConfig.objects.get(pk=config_id)
        user = User.objects.get(pk=triggered_by_id)

        config.recalc_status = RS.RUNNING
        config.save(update_fields=["recalc_status"])

        result = calculate_progression_cases(config, triggered_by=user, recalculate=recalculate)

        config.recalc_status = RS.COMPLETED
        config.recalc_completed_count = result["created"]
        config.recalc_failed_details = result.get("failed_details", [])
        config.save(update_fields=["recalc_status", "recalc_completed_count", "recalc_failed_details"])

        log_event(
            actor=user,
            action_type="PROGRESSION_CASES_CALCULATED",
            model_name="ProgressionConfig",
            object_id=config_id,
            description=f"Async calculation complete: {result['created']} cases created/updated, "
                        f"{result['skipped']} skipped for {config.academic_year_from} → {config.academic_year_to}",
        )
        logger.info("calculate_progression_cases_task completed: %s", result)
        return result

    except ProgressionConfigBlockedError as e:
        logger.error("calculate_progression_cases_task config blocked for %s: %s", config_id, e)
        try:
            config = ProgressionConfig.objects.get(pk=config_id)
            config.recalc_status = RS.FAILED
            config.recalc_failed_details = [{
                "error": str(e),
                "type": "configuration_blocker",
                "academic_year": str(config.academic_year_from),
            }]
            config.save(update_fields=["recalc_status", "recalc_failed_details"])
        except Exception:
            pass
        try:
            log_event(
                actor=user if 'user' in dir() else None,
                action_type="PROGRESSION_CALCULATION_FAILED",
                model_name="ProgressionConfig",
                object_id=config_id,
                description=f"Async calculation blocked: {e}",
            )
        except Exception:
            pass
        raise

    except Exception as e:
        logger.error("calculate_progression_cases_task failed for config %s: %s", config_id, e)
        try:
            config = ProgressionConfig.objects.get(pk=config_id)
            config.recalc_status = RS.FAILED
            config.save(update_fields=["recalc_status"])
        except Exception:
            pass
        try:
            log_event(
                actor=user if 'user' in dir() else None,
                action_type="PROGRESSION_CALCULATION_FAILED",
                model_name="ProgressionConfig",
                object_id=config_id,
                description=f"Async calculation failed: {e}",
            )
        except Exception:
            pass
        raise
