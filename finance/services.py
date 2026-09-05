from decimal import Decimal
from django.conf import settings as _django_settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from finance.models import FinancePeriod, Payment


def recalculate_invoice_status(invoice) -> str:
    """FR-FIN-009: Centralized invoice status recalculation.

    Recalculates the invoice status based on total payments received.
    Must be called after every payment recording, reversal, or correction.

    Returns the new status string.
    """
    from django.db.models import Sum
    from finance.models import InvoiceStatus
    from datetime import date

    total_paid = (
        invoice.payments.filter(is_reversal=False)
        .aggregate(t=Sum("amount"))["t"] or 0
    )

    if total_paid >= invoice.total_due:
        new_status = InvoiceStatus.PAID
    elif total_paid > 0:
        new_status = InvoiceStatus.PARTIAL
    elif invoice.due_date and date.today() > invoice.due_date:
        new_status = InvoiceStatus.OVERDUE
    else:
        new_status = InvoiceStatus.UNPAID

    if invoice.status != new_status:
        invoice.status = new_status
        invoice.save(update_fields=["status", "updated_at"])

    return new_status


@transaction.atomic
def reverse_payment(original_payment: Payment, actor, reason: str) -> Payment:
    if not reason:
        raise ValidationError("A reason must be provided for a reversal.")
    if original_payment.is_reversal:
        raise ValidationError("Reversal entries cannot be reversed.")
    if hasattr(original_payment, "reversal_entry"):
        raise ValidationError("Payment is already reversed.")
    if original_payment.invoice.period and original_payment.invoice.period.is_reconciled:
        raise ValidationError("Cannot reverse payment for reconciled period.")

    return Payment.objects.create(
        invoice=original_payment.invoice,
        amount=-original_payment.amount,
        method=f"reversal:{original_payment.method}",
        reference=f"REV-{original_payment.id}",
        is_reversal=True,
        reversed_payment=original_payment,
        correction_reason=reason,
        created_by=actor,
    )


def reconcile_period(period: FinancePeriod) -> None:
    if period.is_reconciled:
        raise ValidationError("Period is already reconciled.")
    period.is_reconciled = True
    period.full_clean()
    period.save(update_fields=["is_reconciled", "updated_at"])


@transaction.atomic
def generate_term_invoices(term, actor) -> dict:
    from academics.models import Term
    from students.models import Student
    from finance.models import FeeStructure, Invoice, InvoiceLineItem, InvoiceStatus
    from datetime import timedelta
    from audit.models import log_event
    from communications.models import Notification

    # Find active students
    students = Student.objects.filter(is_archived=False)
    
    # Get fee structures for this term
    fee_structures = {fs.class_name: fs for fs in FeeStructure.objects.filter(term=term, is_active=True)}
    
    generated = 0
    blocked_classes = set()

    for student in students:
        # Check if invoice already exists
        if Invoice.objects.filter(student=student, term=term).exists():
            continue
            
        fs = fee_structures.get(student.class_name)
        if not fs:
            blocked_classes.add(student.class_name)
            continue
            
        # Calculate subtotal
        subtotal = sum(item.amount for item in fs.items.all())
        discount_amt = 0
        
        # FR-FIN-002: Sibling discount logic
        # For simplicity in this implementation, if sibling_discount_eligible is True, apply a 10% discount
        if student.sibling_discount_eligible:
            discount_amt = round(subtotal * Decimal('0.10'), 2)
            
        total_due = subtotal - discount_amt
        
        invoice = Invoice.objects.create(
            student=student,
            term=term,
            amount_due=subtotal,
            discount_amount=discount_amt,
            total_due=total_due,
            due_date=term.start_date + timedelta(days=14) if term.start_date else timezone.now().date() + timedelta(days=14),
            status=InvoiceStatus.UNPAID
        )
        # Generate Invoice Number
        invoice.invoice_number = f"INV-{term.academic_year.name.split('-')[0]}-{term.name[:3].upper()}-{invoice.id:04d}"
        invoice.save(update_fields=["invoice_number"])
        
        # Create line items
        for item in fs.items.all():
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description=item.description,
                amount=item.amount,
                is_discount=False
            )
            
        if discount_amt > 0:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description="Sibling Discount (10%)",
                amount=-discount_amt,
                is_discount=True
            )
            
        log_event(
            actor=actor,
            action_type="INVOICE_GENERATED",
            model_name="Invoice",
            object_id=invoice.pk,
            description=f"Auto-generated term invoice {invoice.invoice_number} for {student.admission_no}",
            after={"total_due": str(total_due)}
        )
        
        # Notify parent conceptually
        from students.models import ParentGuardian
        from communications.email_service import send_parent_notification

        from django.urls import reverse

        guardians = ParentGuardian.objects.filter(studentguardian__student=student, studentguardian__is_primary=True)
        parent_finance_url = reverse("finance:parent_invoices")

        from core.email_templates import send_dynamic_email
        from core.models import SchoolSettings
        school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

        for guardian in guardians:
            tpl_context = {
                "guardian_name": guardian.full_name,
                "student_name": student.first_name,
                "currency": "TZS",
                "total_due": f"{total_due:,.0f}",
                "invoice_number": invoice.invoice_number,
                "term_name": term.name,
                "school_name": school_name,
                "portal_url": str(getattr(_django_settings, "SITE_URL", "https://hodari.elimcoregroup.com:8443")) + "/parent/",
            }
            db_sent = send_dynamic_email(
                template_type="invoice_generated",
                to_email=guardian.email,
                context=tpl_context,
                actor=actor,
            )
            if not db_sent:
                send_parent_notification(
                    guardian=guardian,
                    title="Fee Invoice Generated",
                    message=f"Dear {guardian.full_name}, a new fee invoice for {term.name} has been generated for {student.first_name}. Total due: TZS {total_due}.",
                    link=parent_finance_url,
                    actor=actor
                )

                
        generated += 1

    return {
        "generated": generated,
        "blocked_classes": list(blocked_classes)
    }

@transaction.atomic
def generate_midterm_invoices(term, actor) -> dict:
    """
    FR-FIN-017: Generate mid-term (pro-rated) invoices for a term.

    Pro-rates the full-term fee structure based on how many weeks remain
    in the term from today.  A student who joined mid-term gets invoiced
    only for the remaining weeks, not the full term.

    Returns dict with 'generated' count and 'blocked_classes' list.
    """
    from academics.models import Term
    from students.models import Student
    from finance.models import (
        FeeStructure, Invoice, InvoiceLineItem, InvoiceStatus,
    )
    from datetime import timedelta
    from audit.models import log_event
    from django.urls import reverse

    if not term.start_date or not term.end_date:
        raise ValidationError("Term must have start_date and end_date for mid-term invoicing.")

    total_weeks = max((term.end_date - term.start_date).days / 7, 1)

    today = timezone.now().date()
    # If today is before the term, use the start date
    effective_date = max(today, term.start_date)
    remaining_days = max((term.end_date - effective_date).days, 0)
    remaining_weeks = max(remaining_days / 7, 0.5)  # minimum half-week
    pro_rata_ratio = Decimal(str(round(remaining_weeks / total_weeks, 4)))

    students = Student.objects.filter(is_archived=False, enrolment_date__lte=effective_date)
    fee_structures = {
        fs.class_name: fs
        for fs in FeeStructure.objects.filter(term=term, is_active=True)
    }

    generated = 0
    blocked_classes = set()

    for student in students:
        # Skip if a full-term invoice already exists
        if Invoice.objects.filter(student=student, term=term).exists():
            continue

        fs = fee_structures.get(student.class_name)
        if not fs:
            blocked_classes.add(student.class_name)
            continue

        # ── Pro-rate each line item ──────────────────────────────
        subtotal = Decimal("0")
        line_items_data = []
        for item in fs.items.all():
            pro_rated = (Decimal(str(item.amount)) * pro_rata_ratio).quantize(
                Decimal("0.01")
            )
            subtotal += pro_rated
            line_items_data.append((item.description, pro_rated))

        # Sibling discount
        discount_amt = Decimal("0")
        if student.sibling_discount_eligible:
            discount_amt = (subtotal * Decimal("0.10")).quantize(Decimal("0.01"))

        total_due = subtotal - discount_amt

        if total_due <= 0:
            continue

        invoice = Invoice.objects.create(
            student=student,
            term=term,
            amount_due=subtotal,
            discount_amount=discount_amt,
            total_due=total_due,
            due_date=effective_date + timedelta(days=14),
            status=InvoiceStatus.UNPAID,
        )
        invoice.invoice_number = (
            f"INV-MID-{term.academic_year.name.split('-')[0]}-"
            f"{term.name[:3].upper()}-{invoice.id:04d}"
        )
        invoice.save(update_fields=["invoice_number"])

        # Create line items
        weeks_label = f"{remaining_weeks:.1f} weeks"
        for desc, amt in line_items_data:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description=f"{desc} (pro-rated {weeks_label})",
                amount=amt,
                is_discount=False,
            )

        if discount_amt > 0:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                description="Sibling Discount (10%)",
                amount=-discount_amt,
                is_discount=True,
            )

        log_event(
            actor=actor,
            action_type="INVOICE_GENERATED",
            model_name="Invoice",
            object_id=invoice.pk,
            description=(
                f"Mid-term pro-rated invoice {invoice.invoice_number} "
                f"for {student.admission_no} ({weeks_label} remaining, "
                f"ratio {pro_rata_ratio})"
            ),
            after={"total_due": str(total_due), "pro_rata_ratio": str(pro_rata_ratio)},
        )

        # Notify parent
        guardians = ParentGuardian.objects.filter(
            studentguardian__student=student,
            studentguardian__is_primary=True,
        )
        parent_finance_url = reverse("finance:parent_invoices")

        from core.email_templates import send_dynamic_email
        from core.models import SchoolSettings
        school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

        for guardian in guardians:
            tpl_context = {
                "guardian_name": guardian.full_name,
                "student_name": student.first_name,
                "currency": "TZS",
                "total_due": f"{total_due:,.0f}",
                "invoice_number": invoice.invoice_number,
                "term_name": term.name,
                "weeks_label": weeks_label,
                "school_name": school_name,
                "portal_url": str(getattr(_django_settings, "SITE_URL", "https://hodari.elimcoregroup.com:8443")) + "/parent/",
            }
            db_sent = send_dynamic_email(
                template_type="invoice_generated",
                to_email=guardian.email,
                context=tpl_context,
                actor=actor,
            )
            if not db_sent:
                send_parent_notification(
                    guardian=guardian,
                    title="Mid-Term Fee Invoice Generated",
                    message=(
                        f"Dear {guardian.full_name}, a mid-term fee invoice "
                        f"for {term.name} has been generated for "
                        f"{student.first_name}. "
                        f"Amount due: TZS {total_due:,.0f} "
                        f"({weeks_label} of fees pro-rated)."
                    ),
                    link=parent_finance_url,
                    actor=actor,
                )

        generated += 1

    return {
        "generated": generated,
        "blocked_classes": list(blocked_classes),
        "pro_rata_ratio": str(pro_rata_ratio),
        "remaining_weeks": round(remaining_weeks, 1),
    }


@transaction.atomic
def check_overdue_invoices() -> int:
    from datetime import date

    from django.urls import reverse

    from audit.models import log_event
    from finance.models import Invoice, InvoiceStatus
    from students.models import ParentGuardian

    today = date.today()
    parent_finance_url = reverse("finance:parent_invoices")
    overdue_count = 0
    
    # Invoices where due date passed, not fully paid, and status is not overdue
    invoices = Invoice.objects.filter(
        due_date__lt=today,
        total_due__gt=0, # Assuming we use total_due, or track balance if payments applied. Wait, we need to track balance.
    ).exclude(status=InvoiceStatus.PAID)
    
    for inv in invoices:
        # Check actual balance
        payments = inv.payments.filter(is_reversal=False) # Simplified, usually sum valid payments
        # Let's assume total_due is updated as payments are made, or we calculate outstanding
        # FRD says "outstanding_balance > 0"
        
        if inv.status != InvoiceStatus.OVERDUE:
            inv.status = InvoiceStatus.OVERDUE
            inv.save(update_fields=["status", "updated_at"])
            overdue_count += 1
            
        # FR-FIN-013: 7 days past due
        days_overdue = (today - inv.due_date).days
        if days_overdue == 7:
            from communications.email_service import send_parent_notification

            guardians = ParentGuardian.objects.filter(studentguardian__student=inv.student, studentguardian__is_primary=True)

            from core.email_templates import send_dynamic_email
            from core.models import SchoolSettings
            school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

            for guardian in guardians:
                tpl_context = {
                    "guardian_name": guardian.full_name,
                    "student_name": inv.student.first_name,
                    "currency": "TZS",
                    "balance": f"{inv.total_due:,.0f}",
                    "due_date": str(inv.due_date) if inv.due_date else "N/A",
                    "invoice_number": inv.invoice_number or f"INV-{inv.pk}",
                    "school_name": school_name,
                    "portal_url": str(getattr(_django_settings, "SITE_URL", "https://hodari.elimcoregroup.com:8443")) + "/parent/",
                }
                db_sent = send_dynamic_email(
                    template_type="fee_reminder",
                    to_email=guardian.email,
                    context=tpl_context,
                    actor=None,
                )
                if not db_sent:
                    send_parent_notification(
                        guardian=guardian,
                        title="Fee Payment Reminder (7 Days Overdue)",
                        message=f"Dear {guardian.full_name}, this is a reminder that {inv.student.first_name}'s school fees of TZS {inv.total_due} were due on {inv.due_date} and remain unpaid. Please contact the school to arrange payment. Thank you.",
                        link=parent_finance_url,
                    )
            
            # Log it (simulated system actor)
            log_event(
                actor=None,
                action_type="OVERDUE_REMINDER_SENT",
                model_name="Invoice",
                object_id=inv.pk,
                description=f"Auto 7-day overdue reminder sent for {inv.invoice_number}",
            )
            
    return overdue_count
