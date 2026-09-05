"""
Automated parent fee reminder schedules — run via management command or cron.

Schedules:
  - due_soon: 3 days before due date (unpaid/partial)
  - due_today: on due date
  - overdue_7: 7 days after due date
  - overdue_14: 14 days after due date
"""

from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone

from finance.models import Invoice, InvoiceStatus
from students.models import ParentGuardian


REMINDER_SCHEDULE = (
    ("due_soon", lambda today, due: due and (due - today).days == 3),
    ("due_today", lambda today, due: due and due == today),
    ("overdue_7", lambda today, due: due and (today - due).days == 7),
    ("overdue_14", lambda today, due: due and (today - due).days == 14),
)


def _invoice_balance(invoice):
    total_paid = invoice.payments.filter(is_reversal=False).aggregate(t=models.Sum("amount"))["t"] or 0
    return invoice.total_due - total_paid


def _message_for(reminder_type, guardian, invoice, balance):
    student = invoice.student
    name = student.first_name if student else "your child"
    inv = invoice.invoice_number or f"INV-{invoice.pk}"
    due = invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "the due date"

    templates = {
        "due_soon": (
            "Fee due soon",
            f"Dear {guardian.full_name}, school fees for {name} (invoice {inv}) "
            f"of TZS {balance:,.0f} are due on {due}. Please plan payment.",
        ),
        "due_today": (
            "Fee due today",
            f"Dear {guardian.full_name}, school fees for {name} (invoice {inv}) "
            f"of TZS {balance:,.0f} are due today ({due}).",
        ),
        "overdue_7": (
            "Fee payment reminder (7 days overdue)",
            f"Dear {guardian.full_name}, invoice {inv} for {name} is 7 days overdue. "
            f"Outstanding balance: TZS {balance:,.0f}. Please contact the school.",
        ),
        "overdue_14": (
            "Urgent: fees 14 days overdue",
            f"Dear {guardian.full_name}, invoice {inv} for {name} remains unpaid after 14 days. "
            f"Balance: TZS {balance:,.0f}. Please settle urgently.",
        ),
    }
    return templates[reminder_type]


def process_fee_reminders(*, dry_run=False, actor=None):
    """
    Evaluate all open invoices and send scheduled parent reminders.
    Returns dict with counts per reminder type.
    """
    from communications.email_service import send_parent_notification
    from audit.models import log_event

    today = timezone.now().date()
    counts = {key: 0 for key, _ in REMINDER_SCHEDULE}
    parent_link = reverse("finance:parent_invoices")

    open_invoices = Invoice.objects.filter(
        status__in=[InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE],
        is_finalized=True,
        student__isnull=False,
        due_date__isnull=False,
    ).select_related("student")

    for invoice in open_invoices:
        balance = _invoice_balance(invoice)
        if balance <= 0:
            continue

        if invoice.due_date < today and invoice.status != InvoiceStatus.OVERDUE:
            if not dry_run:
                invoice.status = InvoiceStatus.OVERDUE
                invoice.save(update_fields=["status", "updated_at"])

        for reminder_type, matcher in REMINDER_SCHEDULE:
            if not matcher(today, invoice.due_date):
                continue
            if invoice.last_reminder_type == reminder_type:
                continue

            guardians = ParentGuardian.objects.filter(
                studentguardian__student=invoice.student,
                studentguardian__is_primary=True,
            )
            if not guardians.exists():
                continue

            title, message = _message_for(reminder_type, guardians.first(), invoice, balance)
            sent_for_invoice = 0

            for guardian in guardians:
                if not guardian.user_id and not guardian.email and not guardian.phone:
                    continue
                if dry_run:
                    sent_for_invoice += 1
                    continue
                send_parent_notification(
                    guardian=guardian,
                    title=title,
                    message=message,
                    link=parent_link,
                    actor=actor,
                )
                sent_for_invoice += 1

            counts[reminder_type] += sent_for_invoice

            if not dry_run and sent_for_invoice:
                invoice.last_reminder_type = reminder_type
                invoice.last_reminder_at = timezone.now()
                invoice.save(update_fields=["last_reminder_type", "last_reminder_at", "updated_at"])
                log_event(
                    actor=actor,
                    action_type="FEE_REMINDER_SENT",
                    model_name="Invoice",
                    object_id=invoice.pk,
                    description=f"Sent {reminder_type} reminder for {invoice.invoice_number}",
                )

    return counts
