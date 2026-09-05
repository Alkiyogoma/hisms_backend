"""
Finance → Tasks integration hooks.

These are called from finance/views.py to resolve or create tasks
when payments are recorded, invoices are matched, etc.
Kept separate from the 160K+ finance/views.py for maintainability.
"""

from django.utils import timezone


def resolve_overdue_tasks_for_invoice(invoice):
    """When an invoice is fully paid, mark any overdue_collection tasks as completed."""
    try:
        from tasks.models import Task
        from django.contrib.contenttypes.models import ContentType

        inv_ct = ContentType.objects.get_for_model(invoice.__class__)
        updated = Task.objects.filter(
            task_type='overdue_collection',
            content_type=inv_ct,
            object_id=invoice.pk,
            status__in=['pending', 'in_progress'],
        ).update(status='completed', completed_at=timezone.now())
        return updated
    except Exception:
        return 0


def generate_tasks_for_unmatched_payments(payments):
    """Create payment_matching tasks for newly created unmatched payments."""
    try:
        from tasks.services import generate_payment_matching_task
        created = []
        for payment in payments:
            tasks = generate_payment_matching_task(payment)
            if tasks:
                created.extend(tasks)
        return created
    except Exception:
        return []


def resolve_tasks_for_invoice(invoice):
    """Generic: resolve all pending tasks linked to an invoice when it's fully paid."""
    try:
        from tasks.models import Task
        from django.contrib.contenttypes.models import ContentType

        inv_ct = ContentType.objects.get_for_model(invoice.__class__)
        Task.objects.filter(
            content_type=inv_ct,
            object_id=invoice.pk,
            status__in=['pending', 'in_progress'],
        ).update(status='completed', completed_at=timezone.now())
    except Exception:
        pass
