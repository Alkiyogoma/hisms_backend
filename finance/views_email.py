"""
FR-FIN-014b: Send invoice as PDF email attachment to parent/guardian.

This view generates the invoice PDF via WeasyPrint and attaches it to an
email sent to the primary guardian's email address.
"""
import logging
from django.db.models import Sum

from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core.permissions import RoleRequiredMixin
from users.models import UserRole

logger = logging.getLogger(__name__)


class SendInvoiceEmailView(RoleRequiredMixin, View):
    """
    POST-only: generate PDF invoice and email it to the primary guardian.

    URL: /finance/invoices/<int:pk>/email/
    """
    allowed_roles = [
        UserRole.FINANCE_OFFICER,
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN_OFFICER,
    ]
    required_permission = "finance.change_invoice"

    def post(self, request, pk):
        from finance.models import Invoice
        from students.models import ParentGuardian, StudentGuardian
        from finance.views import _invoice_money_context

        invoice = get_object_or_404(
            Invoice.objects.select_related("student", "term"),
            pk=pk,
        )

        # Find primary guardian
        if not invoice.student_id:
            messages.error(request, "Invoice is not linked to a student.")
            return redirect("finance:invoice_detail", pk=pk)

        sg = (
            StudentGuardian.objects.filter(
                student=invoice.student, is_primary=True
            )
            .select_related("guardian")
            .first()
        )
        if not sg or not sg.guardian.email:
            messages.error(
                request,
                "Primary guardian has no email address on file.",
            )
            return redirect("finance:invoice_detail", pk=pk)

        guardian = sg.guardian

        # ── Generate PDF ──────────────────────────────────────────────
        try:
            from weasyprint import HTML
        except ImportError:
            messages.error(
                request,
                "WeasyPrint is not installed. PDF generation is unavailable.",
            )
            return redirect("finance:invoice_detail", pk=pk)

        ctx = _invoice_money_context(invoice)
        ctx["invoice"] = invoice
        ctx["pdf_mode"] = True

        html_string = render_to_string(
            "finance/invoice_print.html", ctx, request=request
        )
        pdf_bytes = HTML(string=html_string).write_pdf()

        filename = f"invoice-{invoice.invoice_number or invoice.id}.pdf"

        # ── Build and send email ──────────────────────────────────────
        try:
            from_email = getattr(
                settings, "DEFAULT_FROM_EMAIL", "noreply@hodari.ac.tz"
            )

            total_paid = (
                invoice.payments.filter(is_reversal=False)
                .aggregate(t=Sum("amount"))["t"]
                or 0
            )
            balance = invoice.total_due - total_paid

            due_date_str = invoice.due_date.strftime('%d %B %Y') if invoice.due_date else "N/A"

            site_url = getattr(settings, "SITE_URL", "https://hodari.elimcoregroup.com:8443")
            portal_url = site_url + "/parent/"

            line_items = []
            for li in invoice.line_items.all():
                line_items.append({
                    "description": li.description,
                    "amount": f"{li.amount:,.0f}",
                    "is_discount": li.is_discount,
                })

            tpl_context = {
                "guardian_name": guardian.full_name,
                "student_name": f"{invoice.student.first_name} {invoice.student.last_name}",
                "student_class": str(invoice.student.class_name),
                "invoice_number": invoice.invoice_number,
                "currency": "TZS",
                "amount_due": f"{invoice.total_due:,.0f}",
                "amount_paid": f"{total_paid:,.0f}",
                "balance": f"{balance:,.0f}",
                "due_date": due_date_str,
                "school_name": SchoolSettings.get_settings().school_name or "Hodari Christian School",
                "site_url": site_url,
                "static_url": getattr(settings, "STATIC_URL", "/static/"),
                "portal_url": portal_url,
                "line_items": line_items,
            }

            # Try dynamic DB template first
            from core.email_templates import send_dynamic_email
            db_sent = send_dynamic_email(
                template_type="fee_invoice",
                to_email=guardian.email,
                context=tpl_context,
                actor=request.user,
            )

            if not db_sent:
                subject = f"Hodari Christian School — Invoice {invoice.invoice_number}"
                body = (
                    f"Dear {guardian.full_name},\n\n"
                    f"Please find attached the fee invoice for "
                    f"{invoice.student.first_name} {invoice.student.last_name} "
                    f"({invoice.student.class_name}).\n\n"
                    f"Invoice Number: {invoice.invoice_number}\n"
                    f"Amount Due: TZS {invoice.total_due:,.0f}\n"
                    f"Amount Paid: TZS {total_paid:,.0f}\n"
                    f"Outstanding Balance: TZS {balance:,.0f}\n"
                )
                if invoice.due_date:
                    body += f"Due Date: {due_date_str}\n"
                body += (
                    f"\nPayment can be made via bank transfer or mobile money. "
                    f"Please use reference: {invoice.invoice_number}\n\n"
                    f"Thank you,\nHodari Christian School Finance Office"
                )

                email = EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=from_email,
                    to=[guardian.email],
                )
                email.attach(filename, pdf_bytes, "application/pdf")
                email.send(fail_silently=False)

            # ── Log the event ─────────────────────────────────────────
            from audit.models import log_event

            log_event(
                actor=request.user,
                action_type="INVOICE_EMAILED",
                model_name="Invoice",
                object_id=invoice.pk,
                description=(
                    f"Invoice {invoice.invoice_number} emailed to "
                    f"{guardian.full_name} ({guardian.email})"
                ),
                request=request,
            )

            # ── Update last_reminder tracking ─────────────────────────
            invoice.last_reminder_at = timezone.now()
            invoice.last_reminder_type = "email"
            invoice.save(update_fields=["last_reminder_at", "last_reminder_type", "updated_at"])

            messages.success(
                request,
                f"Invoice emailed successfully to {guardian.full_name} "
                f"({guardian.email}).",
            )

        except Exception as e:
            logger.exception("Failed to email invoice %s", invoice.pk)
            messages.error(
                request, f"Failed to send email: {e}"
            )

        return redirect("finance:invoice_detail", pk=pk)



