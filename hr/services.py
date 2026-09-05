from django.core.exceptions import ValidationError
from hr.models import PayrollRun


def lock_payroll(payroll_run: PayrollRun) -> None:
    if payroll_run.is_locked:
        raise ValidationError("Payroll is already locked.")
    payroll_run.is_locked = True
    payroll_run.full_clean()
    payroll_run.save(update_fields=["is_locked", "updated_at"])
