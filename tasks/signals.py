"""
Django signals for the Tasks app.

Automatically resolves or creates tasks when module events occur,
without requiring inline hooks in the original views.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='finance.Payment')
def resolve_tasks_on_payment(sender, instance, created, **kwargs):
    """
    When a Payment is saved (created or updated), check if the parent
    invoice is now fully paid. If so, resolve any overdue_collection tasks.
    """
    if not created:
        return

    invoice = instance.invoice
    if not invoice:
        return

    try:
        from django.db.models import Sum
        total_paid = invoice.payments.filter(is_reversal=False).aggregate(
            t=Sum("amount")
        )["t"] or 0

        if total_paid >= invoice.total_due:
            from tasks.models import Task
            from django.contrib.contenttypes.models import ContentType

            inv_ct = ContentType.objects.get_for_model(invoice.__class__)
            from django.utils import timezone
            Task.objects.filter(
                task_type='overdue_collection',
                content_type=inv_ct,
                object_id=invoice.pk,
                status__in=['pending', 'in_progress'],
            ).update(status='completed', completed_at=timezone.now())
    except Exception:
        # Never break the payment flow due to task resolution
        pass


@receiver(post_save, sender='finance.UnmatchedPayment')
def create_task_on_unmatched_payment(sender, instance, created, **kwargs):
    """
    When a new UnmatchedPayment is created (e.g. from bank statement CSV upload),
    generate a payment_matching task for finance officers.
    """
    if not created:
        return

    try:
        from tasks.services import generate_payment_matching_task
        generate_payment_matching_task(instance)
    except Exception:
        pass


@receiver(post_save, sender='discipline.DisciplineIncident')
def create_parent_contact_task_on_discipline(sender, instance, created, **kwargs):
    """When a discipline incident is created, generate a parent contact task if needed."""
    if not created:
        return
    try:
        from tasks.services import generate_discipline_contact_task
        generate_discipline_contact_task(instance)
    except Exception:
        pass


@receiver(post_save, sender='academics.ReportCard')
def create_parent_task_on_report_card_publish(sender, instance, **kwargs):
    """When a report card is published, generate a parent notification task."""
    try:
        from tasks.services import generate_report_card_ready_task
        generate_report_card_ready_task(instance)
    except Exception:
        pass


@receiver(post_save, sender='finance.Invoice')
def create_parent_task_on_invoice(sender, instance, created, **kwargs):
    """When an invoice is created or status changes to unpaid/partial/overdue, create parent task."""
    if instance.status in ('unpaid', 'partial', 'overdue'):
        try:
            from tasks.services import generate_open_invoice_task
            generate_open_invoice_task(instance)
        except Exception:
            pass
