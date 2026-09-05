"""
Finance views â€” FRD Section 10.
Implements: dashboard, invoice list/detail, payment recording, fee structures.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models as db_models
from django.db import transaction
from django.db.models import Sum, Q, Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, TemplateView, View

from core.permissions import RoleRequiredMixin
from users.models import UserRole

from .models import (
    FeeStructure, FeeStructureItem, FeeStructureStatus,
    FinancePeriod, Invoice, Payment,
    InvoiceLineItem, Expense, ExpenseCategory, ExpenseStatus,
    Budget, RecurringExpense, PaymentMethod,
    ReminderConfiguration,    FinanceConfig,
    OpeningBalance, OpeningBalanceType,
    Concession, ConcessionType, ConcessionStatus,
)


# Roles that may reach finance module pages when Role Config grants them the
# matching finance.* permission. The per-view required_permission still gates
# every page, so listing a role here only widens who can be admitted — never
# what they can see. Sensitive per-student views keep narrower lists.
_FINANCE_MODULE_ROLES = [
    UserRole.FINANCE_OFFICER,
    UserRole.HEAD_OF_SCHOOL,
    UserRole.ADMIN_OFFICER,
    UserRole.PRIMARY_HOD,
    UserRole.ECD_HOD,
    UserRole.LOWER_SECONDARY_HOD,
    UserRole.TEACHER,
    UserRole.SUPER_ADMIN,
]


class PDFPrintMixin:
    """Mixin that adds WeasyPrint PDF rendering support to DetailViews.
    Includes a 10-second timeout guard and performance logging."""
    pdf_template_name = None  # Override if different from template_name
    PDF_TIMEOUT_SECONDS = 10

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "pdf":
            return self._render_pdf(context)
        return super().render_to_response(context, **response_kwargs)

    def _render_pdf(self, context):
        import logging
        import time
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        logger = logging.getLogger(__name__)
        template = self.pdf_template_name or self.template_name
        try:
            from weasyprint import HTML
            html_string = render_to_string(template, context, request=self.request)
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(HTML(string=html_string).write_pdf)
                pdf_file = future.result(timeout=self.PDF_TIMEOUT_SECONDS)
            elapsed = round(time.monotonic() - start, 2)
            if elapsed > 5:
                logger.warning("PDF generation slow: %.2fs for %s", elapsed, template)
            else:
                logger.info("PDF generated in %.2fs", elapsed)
            response = HttpResponse(pdf_file, content_type="application/pdf")
            filename = self._get_pdf_filename(context)
            response["Content-Disposition"] = f'inline; filename="{filename}"'
            response["X-PDF-Gen-Time"] = str(elapsed)
            return response
        except FuturesTimeout:
            logger.error("PDF generation timed out after %ds for %s", self.PDF_TIMEOUT_SECONDS, template)
            return super().render_to_response(context)
        except Exception:
            logger.warning("PDF generation failed, falling back to HTML")
            return super().render_to_response(context)

    def _get_pdf_filename(self, context):
        """Override in subclass to set a meaningful filename."""
        return "document.pdf"


def _invoice_money_context(invoice):
    total_paid = invoice.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
    balance = invoice.total_due - total_paid
    from students.models import ParentGuardian

    primary_guardian = None
    if invoice.student_id:
        link = invoice.student.studentguardian_set.filter(is_primary=True).select_related("guardian").first()
        if link:
            primary_guardian = link.guardian
    return {
        "line_items": invoice.line_items.all() if hasattr(invoice, "line_items") else InvoiceLineItem.objects.filter(invoice=invoice),
        "amount_paid": total_paid,
        "balance": balance,
        "primary_guardian": primary_guardian,
    }


class InvoicePrintView(PDFPrintMixin, RoleRequiredMixin, DetailView):
    """Printable fee invoice for finance staff or parents.
    Supports PDF generation via WeasyPrint when ?format=pdf is specified.
    """

    model = Invoice
    template_name = "finance/invoice_print.html"
    context_object_name = "invoice"
    allowed_roles = [*_FINANCE_MODULE_ROLES, UserRole.PARENT]
    required_permission = "finance.view_invoice"

    def get_queryset(self):
        qs = Invoice.objects.select_related("student", "term")
        if self.request.user.role == UserRole.PARENT:
            from students.models import ParentGuardian, Student

            guardian = ParentGuardian.objects.filter(user=self.request.user).first()
            if not guardian:
                return qs.none()
            student_ids = Student.objects.filter(studentguardian__guardian=guardian).values_list("id", flat=True)
            return qs.filter(student_id__in=student_ids)
        # FR-FIN-016: HOS sees summary only - blocked from individual invoice print
        if self.request.user.role == UserRole.HEAD_OF_SCHOOL:
            return qs.none()
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_invoice_money_context(self.object))
        ctx["pdf_mode"] = self.request.GET.get("format") == "pdf"
        return ctx

    def _get_pdf_filename(self, context):
        invoice = context["invoice"]
        return f"invoice-{invoice.invoice_number or invoice.id}.pdf"


class PaymentReceiptPrintView(PDFPrintMixin, RoleRequiredMixin, DetailView):
    """Printable payment receipt.
    Supports PDF generation via WeasyPrint when ?format=pdf is specified.
    """

    model = Payment
    template_name = "finance/payment_receipt_print.html"
    context_object_name = "payment"
    allowed_roles = [*_FINANCE_MODULE_ROLES, UserRole.PARENT]
    required_permission = "finance.view_payment"

    def get_queryset(self):
        qs = Payment.objects.select_related("invoice", "invoice__student")
        if self.request.user.role == UserRole.PARENT:
            from students.models import ParentGuardian, Student

            guardian = ParentGuardian.objects.filter(user=self.request.user).first()
            if not guardian:
                return qs.none()
            student_ids = Student.objects.filter(studentguardian__guardian=guardian).values_list("id", flat=True)
            return qs.filter(invoice__student_id__in=student_ids, is_reversal=False)
        return qs.filter(is_reversal=False)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        payment = self.object
        if payment.invoice_id:
            paid = payment.invoice.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
            ctx["balance_after"] = payment.invoice.total_due - paid
        else:
            ctx["balance_after"] = 0
        ctx["pdf_mode"] = self.request.GET.get("format") == "pdf"
        return ctx

    def _get_pdf_filename(self, context):
        payment = context["payment"]
        return f"receipt-PAY{payment.id}.pdf"


class FinanceDashboardView(RoleRequiredMixin, TemplateView):
    """Finance Officer / HOS / Super Admin dashboard for finance."""
    template_name = "finance/dashboard.html"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permissions_any = [
        "finance.view_invoice", "finance.view_payment",
        "finance.view_budget", "finance.view_expense",
        "finance.view_feestructure", "finance.view_financeperiod",
    ]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term, AcademicYear
        from finance.models import InvoiceStatus
        from datetime import timedelta
        
        today = timezone.now().date()
        
        # FR-CAL-003: current term determined from dates
        # Try to find term that includes today
        current_term = Term.objects.filter(
            start_date__lte=today, 
            end_date__gte=today,
            is_locked=False
        ).first()
        
        # Fallback to the first unlocked term if no date match
        if not current_term:
            from academics.utils import get_current_term
            current_term = get_current_term()
            
        ctx["current_term"] = current_term
        
        # Term Summary - include both term-specific and non-term invoices for this period
        if current_term:
            # All invoices for this term OR created during this term's window if no term assigned (like assessments)
            term_q = Q(term=current_term)
            if current_term.start_date and current_term.end_date:
                if today < current_term.start_date:
                    # Term hasn't started yet — include all termless invoices (e.g. assessments created before term)
                    term_q |= Q(term__isnull=True)
                else:
                    term_q |= Q(term__isnull=True, created_at__date__gte=current_term.start_date, created_at__date__lte=current_term.end_date)
            
            term_invoices = Invoice.objects.filter(term_q)
            total_billed = term_invoices.aggregate(t=Sum("total_due"))["t"] or 0
            
            # Sum of payments made against THESE invoices
            total_received = Payment.objects.filter(
                invoice__in=term_invoices
            ).aggregate(t=Sum("amount"))["t"] or 0
            
            outstanding = total_billed - total_received
            collection_rate = (total_received / total_billed * 100) if total_billed > 0 else 0
            
            outstand_rate = (outstanding / total_billed * 100) if total_billed > 0 else 0
            ctx.update({
                "total_billed": total_billed,
                "total_received": total_received,
                "outstanding": outstanding,
                "collection_rate": round(collection_rate, 1),
                "outstand_rate": round(outstand_rate, 1),
            })
            
            # Collection by class for bar chart — batched to avoid N+1
            class_billed = dict(
                term_invoices.values("student__class_name")
                .annotate(t=Sum("total_due"))
                .values_list("student__class_name", "t")
            )
            class_payments = dict(
                Payment.objects.filter(invoice__in=term_invoices)
                .values("invoice__student__class_name")
                .annotate(t=Sum("amount"))
                .values_list("invoice__student__class_name", "t")
            )
            class_data = []
            classes = sorted(filter(None, set(class_billed) | set(class_payments)))
            for cls in classes:
                billed = class_billed.get(cls, 0)
                received = class_payments.get(cls, 0)
                cls_rate = round((received / billed * 100), 1) if billed > 0 else 0
                class_data.append({
                    "class_name": cls,
                    "billed": billed,
                    "received": received,
                    "rate": cls_rate,
                })
            ctx["collection_by_class"] = class_data

            # Open invoices count — scoped to the same term as billed/received
            open_invoices = term_invoices.exclude(status=InvoiceStatus.PAID).count()
            ctx["open_invoices"] = open_invoices

            # Outstanding from prior terms — invoices on any other term
            prior_invoices = Invoice.objects.exclude(status=InvoiceStatus.PAID).exclude(term=current_term)
            ctx["prior_term_open_count"] = prior_invoices.count()
            ctx["prior_term_open_amount"] = prior_invoices.aggregate(t=Sum("total_due"))["t"] or 0
        else:
            # Fallback for when no term is configured - show global stats
            total_billed = Invoice.objects.aggregate(t=Sum("total_due"))["t"] or 0
            total_received = Payment.objects.aggregate(t=Sum("amount"))["t"] or 0
            outstand_rate = ((total_billed - total_received) / total_billed * 100) if total_billed > 0 else 0
            ctx.update({
                "total_billed": total_billed,
                "total_received": total_received,
                "outstanding": total_billed - total_received,
                "collection_rate": round((total_received / total_billed * 100), 1) if total_billed > 0 else 0,
                "outstand_rate": round(outstand_rate, 1),
            })
            ctx["collection_by_class"] = []
            ctx["open_invoices"] = Invoice.objects.exclude(status=InvoiceStatus.PAID).count()
            ctx["prior_term_open_count"] = 0
            ctx["prior_term_open_amount"] = 0
        
        # FR-FIN-016: HOS sees summary only — no individual student payment data
        is_hos = self.request.user.role == UserRole.HEAD_OF_SCHOOL
        
        # Alert Queues
        overdue_invoices = Invoice.objects.filter(status=InvoiceStatus.OVERDUE)
        ctx["overdue_count"] = overdue_invoices.count()
        if not is_hos:
            ctx["overdue_invoices"] = overdue_invoices.select_related("student").order_by("due_date")[:10]
            # Overdue accounts with days calculation — batched to avoid N+1
            top10_ids = list(overdue_invoices.select_related("student").order_by("due_date")[:10].values_list("id", flat=True))
            paid_by_invoice = dict(
                Payment.objects.filter(invoice_id__in=top10_ids, is_reversal=False)
                .values("invoice_id")
                .annotate(t=Sum("amount"))
                .values_list("invoice_id", "t")
            )
            overdue_list = []
            for inv in overdue_invoices.select_related("student").filter(id__in=top10_ids).order_by("due_date"):
                total_paid = paid_by_invoice.get(inv.id, 0)
                balance = inv.total_due - total_paid
                days_overdue = (today - inv.due_date).days if inv.due_date else 0
                overdue_list.append({
                    "invoice": inv,
                    "balance": balance,
                    "days_overdue": days_overdue,
                })
            ctx["overdue_accounts"] = overdue_list
            # Recent Activity
            recent_invoices = Invoice.objects.select_related("student", "period").order_by("-created_at")[:5]
            ctx["recent_invoices"] = recent_invoices
            recent_payments = Payment.objects.select_related("invoice__student", "created_by").filter(is_reversal=False).order_by("-created_at")[:5]
            ctx["recent_payments"] = recent_payments
        else:
            ctx["overdue_accounts"] = []
            ctx["recent_invoices"] = []
            ctx["recent_payments"] = []
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        
        # NFR-PDPA-006: Log access to financial summary data
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "FinanceDashboard", 0, "finance",
            description=f"Finance dashboard viewed by {self.request.user.username}"
        )

        # Reuse the is_hos variable set above
        ctx["is_hos"] = is_hos
        
        # FR-FIN-010: Unmatched Payments Queue
        from .models import UnmatchedPayment
        ctx["unmatched_payments_count"] = UnmatchedPayment.objects.filter(is_resolved=False).count()
        ctx["unmatched_payments"] = UnmatchedPayment.objects.filter(is_resolved=False).order_by("-payment_date", "-created_at")[:5]

        # Fee Structure draft warning â€” A1.2
        if current_term:
            draft_count = FeeStructure.objects.filter(
                term=current_term,
                status=FeeStructureStatus.DRAFT,
            ).count()
            ctx["fee_structure_is_draft"] = draft_count > 0
        else:
            ctx["fee_structure_is_draft"] = False

        # Assessment fees pending
        from finance.models import InvoiceStatus
        assessment_pending = Invoice.objects.filter(
            applicant__isnull=False,
            status__in=[InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL],
        ).select_related("applicant")[:10]
        ctx["assessment_fees_pending"] = assessment_pending
        ctx["assessment_fees_pending_count"] = assessment_pending.count()
        
        # Upcoming Events â€” FR-CAL-004
        from events.models import CalendarEvent
        upcoming_events = CalendarEvent.objects.filter(
            start_date__gte=today,
            is_published=True,
        ).order_by("start_date")[:5]
        ctx["upcoming_events"] = upcoming_events
        ctx["upcoming_cutoff"] = today + timedelta(days=3)
        
        return ctx


class InvoiceListView(RoleRequiredMixin, ListView):
    """
    Enhanced invoice list â€” A4.
    Search, status/term/date-range filters, metric summary cards.
    """
    template_name = "finance/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 30
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_invoice"

    def get_queryset(self):
        qs = Invoice.objects.select_related("student", "period", "term", "applicant").order_by("-created_at")

        # Search
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(student__first_name__icontains=q) |
                Q(student__last_name__icontains=q) |
                Q(student__admission_no__icontains=q) |
                Q(invoice_number__icontains=q) |
                Q(applicant__child_full_name__icontains=q) |
                Q(applicant__parent_full_name__icontains=q) |
                Q(applicant__reference_number__icontains=q)
            )

        # Status filter
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)

        # Term filter
        term_id = self.request.GET.get("term", "")
        if term_id:
            qs = qs.filter(term_id=term_id)

        # Date range
        date_from = self.request.GET.get("from", "")
        date_to = self.request.GET.get("to", "")
        if date_from:
            qs = qs.filter(due_date__gte=date_from)
        if date_to:
            qs = qs.filter(due_date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        from .models import InvoiceStatus

        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_term"] = self.request.GET.get("term", "")
        ctx["q"] = self.request.GET.get("q", "")
        ctx["date_from"] = self.request.GET.get("from", "")
        ctx["date_to"] = self.request.GET.get("to", "")

        # Terms for filter
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name")
        ctx["statuses"] = InvoiceStatus.choices

        # Metric summary cards
        base = Invoice.objects.all()
        ctx["total_billed"] = base.aggregate(t=Sum("total_due"))["t"] or 0
        ctx["total_collected"] = Payment.objects.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
        ctx["overdue_count"] = base.filter(status=InvoiceStatus.OVERDUE).count()
        ctx["open_count"] = base.exclude(status=InvoiceStatus.PAID).count()

        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["finance_tab"] = "invoices"
        return ctx


class InvoiceDetailView(RoleRequiredMixin, DetailView):
    template_name = "finance/invoice_detail.html"
    model = Invoice
    context_object_name = "invoice"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_invoice"

    def get_queryset(self):
        return super().get_queryset().select_related("student", "term")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = self.object
        
        # NFR-PDPA-006: Log access to individual invoice financial data
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "Invoice", invoice.pk, "finance",
            description=f"Invoice {invoice.invoice_number} viewed by {self.request.user.username}"
        )
        
        ctx["payments"] = invoice.payments.order_by("-created_at")

        # Payment progress
        total_paid = invoice.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
        ctx["total_paid"] = total_paid
        if invoice.total_due > 0:
            ctx["progress_pct"] = round((total_paid / invoice.total_due) * 100, 1)
        else:
            ctx["progress_pct"] = 0

        # Aging calculation
        today = timezone.now().date()
        ctx["today_date"] = today
        if invoice.due_date:
            delta = (today - invoice.due_date).days
            if delta > 0:
                ctx["days_overdue"] = delta
                ctx["days_until_due"] = 0
            else:
                ctx["days_overdue"] = 0
                ctx["days_until_due"] = abs(delta)
        else:
            ctx["days_overdue"] = 0
            ctx["days_until_due"] = 0
        return ctx


class RecordPaymentView(RoleRequiredMixin, View):
    """POST-only view to record a payment against an invoice."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_payment"

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        amount = request.POST.get("amount", "").strip()
        method = request.POST.get("method", "cash").strip()
        reference = request.POST.get("reference", "").strip()
        
        if not reference:
            messages.error(request, "Reference number is required for all payments.")
            return redirect("finance:invoice_detail", pk=pk)
        
        try:
            amt = float(amount)
            if amt <= 0:
                raise ValueError("Amount must be positive")

            # FR-FIN-009: Prevent overpayment — amount must not exceed remaining balance
            total_paid = invoice.payments.filter(is_reversal=False).aggregate(Sum("amount"))["amount__sum"] or 0
            remaining_balance = float(invoice.total_due) - float(total_paid)
            if amt > remaining_balance:
                messages.error(
                    request,
                    f"Payment of TZS {amt:,.0f} exceeds remaining balance of TZS {remaining_balance:,.0f}."
                )
                return redirect("finance:invoice_detail", pk=pk)

            payment = Payment(
                invoice=invoice,
                amount=amt,
                method=method,
                reference=reference,
                created_by=request.user,
            )
            payment.save()
            
            # FR-FIN-009: Centralized invoice status recalculation
            from finance.services import recalculate_invoice_status
            from finance.models import InvoiceStatus
            new_status = recalculate_invoice_status(invoice)
            # FR-ADM-008: Auto-update admission record if this was an assessment invoice
            if new_status == InvoiceStatus.PAID and invoice.applicant:
                from admissions.services import confirm_assessment_fee_paid
                confirm_assessment_fee_paid(applicant=invoice.applicant, actor=request.user)
                # E20-RECEIPT: Send payment receipt for admission invoices
                try:
                    from admissions.tasks import send_payment_receipt_task
                    send_payment_receipt_task.delay(invoice.pk)
                except Exception:
                    pass

            
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="PAYMENT_RECORDED",
                model_name="Payment",
                object_id=payment.pk,
                description=f"Payment of {amt} recorded for invoice {invoice.invoice_number}",
                request=request,
            )
            
            # NOTIF-12: Fee payment received
            from students.models import ParentGuardian
            from communications.email_service import send_parent_notification
            guardians = ParentGuardian.objects.filter(studentguardian__student=invoice.student, studentguardian__is_primary=True)
            total_paid = invoice.payments.aggregate(Sum("amount"))["amount__sum"] or 0
            balance = invoice.total_due - total_paid
            receipt_url = reverse("finance:payment_receipt_print", kwargs={"pk": payment.pk})

            # Try dynamic DB template first
            from core.email_templates import send_dynamic_email
            from core.models import SchoolSettings
            school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"

            _portal = request.build_absolute_uri("/parent/")
            for guardian in guardians:
                tpl_context = {
                    "guardian_name": guardian.full_name,
                    "student_name": invoice.student.first_name,
                    "currency": "TZS",
                    "amount": f"{amt:,.0f}",
                    "balance": f"{balance:,.0f}",
                    "invoice_number": invoice.invoice_number,
                    "school_name": school_name,
                    "portal_url": _portal,
                    "receipt_url": receipt_url,
                }
                db_sent = send_dynamic_email(
                    template_type="payment_received",
                    to_email=guardian.email,
                    context=tpl_context,
                    actor=request.user,
                )
                if not db_sent:
                    send_parent_notification(
                        guardian=guardian,
                        title="Fee Payment Received",
                        message=(
                            f"Dear {guardian.full_name}, we received TZS {amt:,.0f} for "
                            f"{invoice.student.first_name}'s fees. Balance: TZS {balance:,.0f}. "
                            f"View your receipt in the parent portal."
                        ),
                        link=receipt_url,
                        actor=request.user,
                    )

            messages.success(request, f"Payment of TZS {amt:,.0f} recorded successfully.")
        except Exception as e:
            messages.error(request, f"Error recording payment: {e}")
        return redirect("finance:invoice_detail", pk=pk)

class PaymentCorrectionView(RoleRequiredMixin, View):
    """Reverse a payment and optionally log a corrected one. GET redirects to invoice list.
    Only Super Admin can perform payment corrections (FR-FIN-011)."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.FINANCE_OFFICER]
    required_permission = "finance.change_payment"

    def get(self, request):
        messages.info(request, "Use the action menu on an invoice to correct a payment.")
        return redirect("finance:invoice_list")

    def post(self, request):
        pk = request.POST.get("payment_id")
        if not pk:
            messages.error(request, "Payment ID is required.")
            return redirect("finance:invoice_list")
        from finance.services import reverse_payment
        from audit.models import log_event
        from finance.models import InvoiceStatus
        
        payment = get_object_or_404(Payment, pk=pk)
        reason = request.POST.get("reason", "").strip()
        new_amount = request.POST.get("new_amount", "").strip()
        
        try:
            with transaction.atomic():
                # 1. Reverse
                reversal = reverse_payment(payment, request.user, reason)
                log_event(
                    actor=request.user,
                    action_type="PAYMENT_REVERSAL",
                    model_name="Payment",
                    object_id=reversal.pk,
                    description=f"Reversed payment {payment.pk} due to: {reason}",
                    request=request,
                )
                
                # 2. Corrected entry is mandatory — FR-FIN-011
                if not new_amount:
                    raise ValueError("A corrected amount is required. Reversal without correction is not allowed.")
                amt = float(new_amount)
                if amt <= 0:
                    raise ValueError("Corrected amount must be positive.")
                new_payment = Payment.objects.create(
                    invoice=payment.invoice,
                    amount=amt,
                    method=payment.method,
                    reference=f"CORR-{payment.reference}" if payment.reference else "CORR",
                    created_by=request.user,
                )
                log_event(
                    actor=request.user,
                    action_type="PAYMENT_CORRECTION",
                    model_name="Payment",
                    object_id=new_payment.pk,
                    description=f"Corrected payment recorded for {payment.pk} with new amount {amt}",
                    request=request,
                )
                
                # 3. FR-FIN-009: Centralized invoice status recalculation
                from finance.services import recalculate_invoice_status
                recalculate_invoice_status(payment.invoice)

                # 4. Notify parent of payment reversal
                from communications.email_service import send_parent_notification
                from students.models import ParentGuardian
                from django.urls import reverse
                from core.email_templates import send_dynamic_email
                from core.models import SchoolSettings
                guardians = ParentGuardian.objects.filter(
                    studentguardian__student=payment.invoice.student,
                    studentguardian__is_primary=True
                )
                parent_finance_url = reverse("finance:parent_invoices")
                school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
                for guardian in guardians:
                    tpl_context = {
                        "guardian_name": guardian.full_name,
                        "currency": "TZS",
                        "amount": f"{payment.amount:,.0f}",
                        "corrected_amount": f"{amt:,.0f}",
                        "invoice_number": payment.invoice.invoice_number,
                        "reason": reason,
                        "school_name": school_name,
                    }
                    db_sent = send_dynamic_email(
                        template_type="payment_reversal",
                        to_email=guardian.email,
                        context=tpl_context,
                        actor=request.user,
                    )
                    if not db_sent:
                        send_parent_notification(
                            guardian=guardian,
                            title="Payment Reversal",
                            message=f"A payment of TZS {payment.amount:,.0f} on {payment.invoice.invoice_number} has been reversed. Reason: {reason}. A corrected entry of TZS {amt:,.0f} has been recorded.",
                            link=parent_finance_url,
                            actor=request.user,
                        )

            messages.success(request, "Payment successfully corrected.")
        except Exception as e:
            messages.error(request, f"Error correcting payment: {e}")
            
        return redirect("finance:invoice_detail", pk=payment.invoice.pk)

class BatchPaymentView(RoleRequiredMixin, TemplateView):
    """
    Batch payment recording — record payments for multiple students at once,
    grouped by term and class.
    """
    template_name = "finance/batch_payment.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permission = "finance.add_payment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term, GradeClass, Department

        today = timezone.now().date()
        ctx["today"] = today

        # Available terms
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name", "name")

        # Selected term
        term_id = self.request.GET.get("term")
        if term_id:
            current_term = get_object_or_404(Term, pk=term_id)
        else:
            from academics.utils import get_current_term
            current_term = get_current_term()
        ctx["selected_term"] = current_term

        # Selected class
        selected_class = self.request.GET.get("class", "")
        ctx["selected_class"] = selected_class

        if current_term and selected_class:
            # Get all active students in this class
            from students.models import Student
            students = Student.objects.filter(
                class_name=selected_class,
                is_archived=False,
                status="active",
            ).order_by("first_name", "last_name")

            # Get their unpaid/partial invoices for this term
            from .models import InvoiceStatus
            invoices = Invoice.objects.filter(
                student__in=students,
                term=current_term,
                status__in=[InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL],
            ).select_related("student").order_by("student__first_name")

            # Group invoices by student
            student_payments = []
            for inv in invoices:
                total_paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                balance = inv.total_due - total_paid
                if balance > 0:
                    student_payments.append({
                        "student": inv.student,
                        "invoice": inv,
                        "balance": balance,
                        "total_due": inv.total_due,
                        "total_paid": total_paid,
                    })

            ctx["student_payments"] = student_payments
            ctx["student_count"] = len(student_payments)
            ctx["total_outstanding"] = sum(sp["balance"] for sp in student_payments)

        # All classes for dropdown
        ctx["classes"] = GradeClass.objects.filter(
            department__in=[Department.ECD, Department.PRIMARY, Department.LOWER_SECONDARY]
        ).values_list("name", flat=True).distinct().order_by("name")

        ctx["payment_methods"] = PaymentMethod.choices
        return ctx

    def post(self, request):
        from .models import InvoiceStatus
        from students.models import Student, ParentGuardian
        from audit.models import log_event
        from communications.email_service import send_parent_notification

        term_id = request.POST.get("term_id")
        payment_date = request.POST.get("payment_date", "")
        default_method = request.POST.get("default_method", "cash")
        default_reference = request.POST.get("default_reference", "")
        default_notes = request.POST.get("default_notes", "")

        if not term_id:
            messages.error(request, "Term is required.")
            return redirect("finance:batch_payment")

        term = get_object_or_404(Term, pk=term_id)

        # Collect payments from form
        invoice_ids = request.POST.getlist("invoice_id[]")
        amounts = request.POST.getlist("amount[]")
        methods = request.POST.getlist("method[]")
        references = request.POST.getlist("reference[]")
        notes_list = request.POST.getlist("notes[]")

        recorded = 0
        errors = []

        for i, inv_id in enumerate(invoice_ids):
            try:
                amt = float(amounts[i]) if i < len(amounts) and amounts[i] else 0
                if amt <= 0:
                    continue

                invoice = get_object_or_404(Invoice, pk=inv_id)
                method = methods[i] if i < len(methods) and methods[i] else default_method
                ref = references[i] if i < len(references) and references[i] else default_reference
                nts = notes_list[i] if i < len(notes_list) and notes_list[i] else default_notes

                if not ref:
                    ref = f"BATCH-{invoice.invoice_number}-{timezone.now().strftime('%Y%m%d%H%M%S')}"

                with transaction.atomic():
                    payment = Payment.objects.create(
                        invoice=invoice,
                        amount=amt,
                        method=method,
                        payment_date=payment_date or timezone.now().date(),
                        reference=ref,
                        notes=nts,
                        created_by=request.user,
                    )

                    # FR-FIN-009: Centralized invoice status recalculation
                    from finance.services import recalculate_invoice_status
                    new_status = recalculate_invoice_status(invoice)
                    if new_status == InvoiceStatus.PAID and invoice.applicant:
                        from admissions.services import confirm_assessment_fee_paid
                        confirm_assessment_fee_paid(applicant=invoice.applicant, actor=request.user)
                        # E20-RECEIPT: Send payment receipt for admission invoices
                        try:
                            from admissions.tasks import send_payment_receipt_task
                            send_payment_receipt_task.delay(invoice.pk)
                        except Exception:
                            pass

                    log_event(
                        actor=request.user,
                        action_type="PAYMENT_RECORDED",
                        model_name="Payment",
                        object_id=payment.pk,
                        description=f"Batch payment of {amt} for invoice {invoice.invoice_number}",
                        request=request,
                    )

                    # Notify parent
                    if invoice.student:
                        guardians = ParentGuardian.objects.filter(
                            studentguardian__student=invoice.student,
                            studentguardian__is_primary=True
                        )
                        _portal_csv = request.build_absolute_uri("/parent/")
                        for guardian in guardians:
                            from core.email_templates import send_dynamic_email
                            from core.models import SchoolSettings
                            school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
                            tpl_context = {
                                "guardian_name": guardian.full_name,
                                "student_name": invoice.student.first_name,
                                "currency": "TZS",
                                "amount": f"{amt:,.0f}",
                                "balance": f"{(invoice.total_due - (invoice.payments.aggregate(Sum('amount'))['amount__sum'] or 0)):,.0f}",
                                "invoice_number": invoice.invoice_number,
                                "school_name": school_name,
                                "portal_url": _portal_csv,
                            }
                            db_sent = send_dynamic_email(
                                template_type="payment_received",
                                to_email=guardian.email,
                                context=tpl_context,
                                actor=request.user,
                            )
                            if not db_sent:
                                send_parent_notification(
                                    guardian=guardian,
                                    title="Fee Payment Received",
                                    message=(
                                        f"Dear {guardian.full_name}, we received TZS {amt:,.0f} for "
                                        f"{invoice.student.first_name}'s fees."
                                    ),
                                    link=reverse("finance:payment_receipt_print", kwargs={"pk": payment.pk}),
                                    actor=request.user,
                                )

                recorded += 1
            except Exception as e:
                errors.append(f"Row {i+1}: {e}")

        if recorded:
            messages.success(request, f"Successfully recorded {recorded} payment(s).")
        if errors:
            messages.warning(request, f"{len(errors)} payment(s) had errors: {'; '.join(errors[:3])}")

        return redirect(f"{reverse('finance:batch_payment')}?term={term_id}&class={request.POST.get('class_name', '')}")


class AutoGenerateInvoicesView(RoleRequiredMixin, View):
    """Trigger auto-generation of term invoices. GET redirects to dashboard."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_invoice"

    def get(self, request):
        messages.info(request, "Use the invoice dashboard to trigger auto-generation via the form.")
        return redirect("finance:dashboard")

    def post(self, request):
        from academics.models import Term
        from finance.services import generate_term_invoices
        term_id = request.POST.get("term_id")
        term = get_object_or_404(Term, pk=term_id)
        try:
            res = generate_term_invoices(term, request.user)
            gen = res["generated"]
            blocked = res["blocked_classes"]
            if blocked:
                messages.warning(request, f"Generated {gen} invoices. Blocked (no fee structure) for classes: {', '.join(blocked)}")
            else:
                messages.success(request, f"Successfully auto-generated {gen} invoices for {term.name}.")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("finance:dashboard")

class AutoGenerateMidtermInvoicesView(RoleRequiredMixin, View):
    """FR-FIN-017: Trigger mid-term pro-rated invoice generation. GET redirects to dashboard."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_invoice"

    def get(self, request):
        messages.info(request, "Use the invoice dashboard to trigger mid-term generation via the form.")
        return redirect("finance:dashboard")

    def post(self, request):
        from academics.models import Term
        from finance.services import generate_midterm_invoices
        term_id = request.POST.get("term_id")
        term = get_object_or_404(Term, pk=term_id)
        try:
            res = generate_midterm_invoices(term, request.user)
            gen = res["generated"]
            blocked = res["blocked_classes"]
            ratio = res["pro_rata_ratio"]
            weeks = res["remaining_weeks"]
            msg = f"Generated {gen} mid-term invoice(s) at {ratio} pro-ratio ({weeks} weeks remaining)"
            if blocked:
                msg += f". Blocked (no fee structure): {', '.join(blocked)}"
            messages.success(request, msg)
        except Exception as e:
            messages.error(request, str(e))
        return redirect("finance:dashboard")




class PaymentReceiptListView(RoleRequiredMixin, ListView):
    """
    List all payment receipts with date range filtering.
    """
    template_name = "finance/payment_receipt_list.html"
    context_object_name = "payments"
    paginate_by = 30
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_payment"

    def get_queryset(self):
        qs = Payment.objects.filter(is_reversal=False).select_related(
            "invoice__student", "created_by"
        ).order_by("-payment_date", "-created_at")

        date_from = self.request.GET.get("from", "")
        date_to = self.request.GET.get("to", "")
        method = self.request.GET.get("method", "")
        q = self.request.GET.get("q", "").strip()

        if date_from:
            qs = qs.filter(payment_date__gte=date_from)
        if date_to:
            qs = qs.filter(payment_date__lte=date_to)
        if method:
            qs = qs.filter(method=method)
        if q:
            qs = qs.filter(
                Q(reference__icontains=q) |
                Q(notes__icontains=q) |
                Q(invoice__student__first_name__icontains=q) |
                Q(invoice__student__last_name__icontains=q) |
                Q(invoice__invoice_number__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # NFR-PDPA-006: Log access to payment receipt list
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "Payment", 0, "finance",
            description=f"Payment receipt list viewed by {self.request.user.username}"
        )
        ctx["payment_methods"] = PaymentMethod.choices
        ctx["date_from"] = self.request.GET.get("from", "")
        ctx["date_to"] = self.request.GET.get("to", "")
        ctx["selected_method"] = self.request.GET.get("method", "")
        ctx["q"] = self.request.GET.get("q", "")
        # Summary stats â€” use a lightweight aggregation to avoid double-querying
        qs = self.object_list if hasattr(self, 'object_list') else self.get_queryset()
        ctx["total_amount"] = qs.aggregate(t=Sum("amount"))["t"] or 0
        ctx["payment_count"] = qs.count()
        return ctx


class BankStatementUploadView(RoleRequiredMixin, TemplateView):
    """
    Upload a bank statement CSV â†’ parse â†’ create UnmatchedPayment entries.
    Expected CSV columns: date, reference, amount, description
    """
    template_name = "finance/bank_statement_upload.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_payment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .models import UnmatchedPayment
        ctx["unmatched_payments"] = UnmatchedPayment.objects.filter(is_resolved=False).order_by("-payment_date")
        ctx["resolved_count"] = UnmatchedPayment.objects.filter(is_resolved=True).count()
        # Get unpaid invoices for quick matching
        from .models import InvoiceStatus
        ctx["invoicelist"] = Invoice.objects.filter(
            status__in=[InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]
        ).select_related("student").order_by("student__class_name", "student__first_name")
        return ctx

    def post(self, request):
        from .models import UnmatchedPayment, InvoiceStatus
        from audit.models import log_event
        import csv, io

        action = request.POST.get("action", "")

        # Handle match action
        if action == "match":
            payment_id = request.POST.get("payment_id")
            invoice_id = request.POST.get("invoice_id")
            try:
                payment = get_object_or_404(UnmatchedPayment, pk=payment_id)
                invoice = get_object_or_404(Invoice, pk=invoice_id)

                with transaction.atomic():
                    # Create actual payment
                    new_payment = Payment.objects.create(
                        invoice=invoice,
                        amount=payment.amount,
                        method=payment.method or "other",
                        payment_date=payment.payment_date,
                        reference=payment.reference,
                        notes=f"Matched from bank statement: {payment.bank_statement_details}",
                        created_by=request.user,
                    )

                    # Update invoice status
                    total_paid = invoice.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                    if total_paid >= invoice.total_due:
                        invoice.status = InvoiceStatus.PAID
                    elif total_paid > 0:
                        invoice.status = InvoiceStatus.PARTIAL
                    invoice.save(update_fields=["status", "updated_at"])

                    # E20-RECEIPT: Send payment receipt for admission invoices on bank reconciliation
                    if invoice.status == InvoiceStatus.PAID and invoice.applicant:
                        try:
                            from admissions.tasks import send_payment_receipt_task
                            send_payment_receipt_task.delay(invoice.pk)
                        except Exception:
                            pass

                    # Resolve unmatched
                    payment.is_resolved = True
                    payment.resolved_by = request.user
                    payment.resolved_at = timezone.now()
                    payment.resolution_invoice = invoice
                    payment.save(update_fields=["is_resolved", "resolved_by_id", "resolved_at", "resolution_invoice_id"])

                    log_event(
                        actor=request.user,
                        action_type="UNMATCHED_PAYMENT_RESOLVED",
                        model_name="UnmatchedPayment",
                        object_id=payment.pk,
                        description=f"Bank statement payment {payment.reference} matched to invoice {invoice.invoice_number}",
                        request=request,
                    )
                messages.success(request, f"Payment matched to invoice {invoice.invoice_number}.")
            except Exception as e:
                messages.error(request, f"Error matching payment: {e}")
            return redirect("finance:bank_statement_upload")

        # Handle CSV upload
        csv_file = request.FILES.get("csv_file")
        if csv_file:
            from academics.validators import validate_attachment_file
            try:
                validate_attachment_file(csv_file, area="finance_imports")
            except Exception as e:
                msg = e.message if hasattr(e, "message") else str(e)
                messages.error(request, f"CSV file rejected: {msg}")
                return redirect("finance:bank_statement_upload")
        if not csv_file:
            messages.error(request, "Please select a CSV file to upload.")
            return redirect("finance:bank_statement_upload")

        try:
            decoded = csv_file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded))

            created_count = 0
            skipped = 0

            for row in reader:
                # Normalise column names (lowercase, strip)
                row = {k.strip().lower(): v.strip() for k, v in row.items()}

                date_str = row.get("date", row.get("payment_date", row.get("transaction_date", "")))
                ref = row.get("reference", row.get("ref", row.get("transaction_id", "")))
                amount_str = row.get("amount", row.get("value", ""))
                description = row.get("description", row.get("narrative", row.get("details", "")))

                if not amount_str:
                    skipped += 1
                    continue

                try:
                    amount = float(amount_str.replace(",", ""))
                except ValueError:
                    skipped += 1
                    continue

                # Only create if positive amount and not a withdrawal
                if amount <= 0:
                    skipped += 1
                    continue

                # Try parsing date
                from datetime import datetime
                parsed_date = timezone.now().date()
                if date_str:
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                        try:
                            parsed_date = datetime.strptime(date_str, fmt).date()
                            break
                        except ValueError:
                            continue

                # Check for duplicate
                if ref and UnmatchedPayment.objects.filter(reference=ref, is_resolved=False).exists():
                    skipped += 1
                    continue
                if ref and Payment.objects.filter(reference=ref).exists():
                    skipped += 1
                    continue

                UnmatchedPayment.objects.create(
                    amount=amount,
                    payment_date=parsed_date,
                    method="bank_transfer",
                    reference=ref,
                    bank_statement_details=f"{description} | From CSV import",
                )
                created_count += 1

            log_event(
                actor=request.user,
                action_type="BANK_STATEMENT_UPLOADED",
                model_name="UnmatchedPayment",
                description=f"Uploaded bank statement: {created_count} created, {skipped} skipped",
                request=request,
            )

            if created_count:
                messages.success(request, f"Bank statement processed: {created_count} new unmatched payment(s) created. {skipped} row(s) skipped.")
            else:
                messages.warning(request, f"No new payments created. {skipped} row(s) skipped (duplicates or invalid).")

        except Exception as e:
            messages.error(request, f"Error processing CSV: {e}")

        return redirect("finance:bank_statement_upload")


class UnmatchedPaymentListView(RoleRequiredMixin, ListView):
    template_name = "finance/unmatched_payments.html"
    context_object_name = "unmatched_payments"
    paginate_by = 30
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.view_payment"

    def get_queryset(self):
        from .models import UnmatchedPayment
        qs = UnmatchedPayment.objects.filter(is_resolved=False).order_by("-payment_date", "-created_at")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(reference__icontains=q) |
                Q(bank_statement_details__icontains=q) |
                Q(amount__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .models import InvoiceStatus
        # Fetch unpaid/partial invoices grouped for the match dropdown
        ctx["invoicelist"] = Invoice.objects.filter(
            status__in=[InvoiceStatus.UNPAID, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]
        ).select_related("student").order_by("student__class_name", "student__first_name")
        return ctx

    def post(self, request):
        from .models import UnmatchedPayment
        from audit.models import log_event
        
        payment_id = request.POST.get("payment_id")
        invoice_id = request.POST.get("invoice_id")
        action = request.POST.get("action")
        
        try:
            payment = get_object_or_404(UnmatchedPayment, pk=payment_id)
            if action == "match":
                invoice = get_object_or_404(Invoice, pk=invoice_id)
                with transaction.atomic():
                    # Create actual payment
                    new_payment = Payment.objects.create(
                        invoice=invoice,
                        amount=payment.amount,
                        method=payment.method,
                        payment_date=payment.payment_date or timezone.now().date(),
                        reference=payment.reference,
                        notes=f"Matched from: {payment.bank_statement_details}" if payment.bank_statement_details else "",
                        created_by=request.user,
                    )
                    
                    # Update status
                    total_paid = invoice.payments.aggregate(t=Sum("amount"))["t"] or 0
                    from finance.models import InvoiceStatus
                    if total_paid >= invoice.total_due:
                        invoice.status = InvoiceStatus.PAID
                    elif total_paid > 0:
                        invoice.status = InvoiceStatus.PARTIAL
                    invoice.save(update_fields=["status", "updated_at"])

                    # E20-RECEIPT: Send payment receipt for admission invoices
                    if invoice.status == InvoiceStatus.PAID and invoice.applicant:
                        try:
                            from admissions.tasks import send_payment_receipt_task
                            send_payment_receipt_task.delay(invoice.pk)
                        except Exception:
                            pass

                    # Resolve unmatched entry
                    payment.is_resolved = True
                    payment.resolved_by = request.user
                    payment.resolved_at = timezone.now()
                    payment.resolution_invoice = invoice
                    payment.save(update_fields=["is_resolved", "resolved_by_id", "resolved_at", "resolution_invoice_id", "updated_at"])
                    
                    log_event(
                        actor=request.user,
                        action_type="UNMATCHED_PAYMENT_RESOLVED",
                        model_name="UnmatchedPayment",
                        object_id=payment.pk,
                        description=f"Unmatched payment {payment.reference} matched to invoice {invoice.invoice_number}",
                        request=request
                    )
                messages.success(request, f"Payment successfully matched to invoice {invoice.invoice_number}.")
        except Exception as e:
            messages.error(request, f"Error resolving payment: {e}")
            
        return redirect("finance:unmatched_payments")




class FeeStructureSetupView(RoleRequiredMixin, TemplateView):
    """
    Comprehensive fee structure setup screen — A1.1.
    Shows: academic year / term selector → department tabs (ECD / Primary / Lower Secondary) →
    class-level fee cards with inline-editable fields, audit trail, and copy-from-previous.
    """
    template_name = "finance/fee_structure_setup.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL]
    required_permissions_any = ["finance.change_feestructure", "finance.view_feestructure"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term, GradeClass, Department

        # Available terms
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name", "name")

        # Selected term (GET param or first available)
        term_id = self.request.GET.get("term")
        if term_id:
            current_term = get_object_or_404(Term, pk=term_id)
        else:
            from django.utils import timezone
            today = timezone.now().date()
            from academics.utils import get_current_term
            current_term = get_current_term()
        ctx["selected_term"] = current_term
        ctx["fee_categories"] = FeeStructureItem._meta.get_field("category").choices

        # Department ordering
        dept_order = [Department.ECD, Department.PRIMARY, Department.LOWER_SECONDARY]

        # All classes grouped by department
        classes = GradeClass.objects.filter(department__in=dept_order).order_by("name")
        departments_data = []
        for dept in dept_order:
            dept_classes = [c for c in classes if c.department == dept]
            if not dept_classes:
                continue
            # Get fee structures for this term + these classes
            class_names = [c.name for c in dept_classes]
            existing = {
                fs.class_name: fs
                for fs in FeeStructure.objects.filter(term=current_term, class_name__in=class_names)
                .prefetch_related("items")
            }
            cards = []
            for gc in dept_classes:
                fs = existing.get(gc.name)
                if not fs:
                    fs = FeeStructure(term=current_term, class_name=gc.name)
                # Group items by category
                tuition_items = []
                activity_items = []
                other_items = []
                if fs.pk:
                    for item in fs.items.all():
                        if item.category == "tuition":
                            tuition_items.append(item)
                        elif item.category == "activity":
                            activity_items.append(item)
                        elif item.category == "uniform":
                            other_items.append(item)
                        else:
                            other_items.append(item)
                cards.append({
                    "class_name": gc.name,
                    "fs": fs,
                    "tuition_items": tuition_items,
                    "activity_items": activity_items,
                    "other_items": other_items,
                })
            departments_data.append({
                "department": dept,
                "department_label": dict(Department.choices).get(dept, dept),
                "cards": cards,
            })
        ctx["departments"] = departments_data

        # Available source terms for copy feature (exclude current term)
        ctx["source_terms"] = Term.objects.filter(is_locked=False).exclude(pk=current_term.pk).order_by("-academic_year__name", "name")

        # Available classes not yet configured
        all_class_names = set(c.name for c in classes)
        configured = set(
            FeeStructure.objects.filter(term=current_term).values_list("class_name", flat=True)
        )
        ctx["unconfigured_classes"] = sorted(all_class_names - configured)

        return ctx

    def post(self, request):
        from academics.models import Term, GradeClass
        from audit.models import log_event

        action = request.POST.get("action", "save_class")

        if action == "save_class":
            term_id = request.POST.get("term_id")
            class_name = request.POST.get("class_name", "").strip()

            if not term_id or not class_name:
                messages.error(request, "Missing required fields.")
                return redirect("finance:fee_structure_setup")

            term = get_object_or_404(Term, pk=term_id)

            try:
                with transaction.atomic():
                    fs, created = FeeStructure.objects.get_or_create(term=term, class_name=class_name)

                    # Capture old values for audit
                    before = {
                        "late_pickup_charge": str(fs.late_pickup_charge),
                        "sibling_discount_mode": fs.sibling_discount_mode,
                        "sibling_discount_value": str(fs.sibling_discount_value),
                        "assessment_fee": str(fs.assessment_fee),
                        "items": [
                            {"category": i.category, "description": i.description, "amount": str(i.amount)}
                            for i in fs.items.all()
                        ],
                    }

                    # Update top-level fields
                    fs.late_pickup_charge = request.POST.get("late_pickup_charge", "0") or "0"
                    fs.sibling_discount_mode = request.POST.get("sibling_discount_mode", "percentage")
                    fs.sibling_discount_value = request.POST.get("sibling_discount_value", "0") or "0"
                    fs.assessment_fee = request.POST.get("assessment_fee", "0") or "0"
                    fs.is_active = request.POST.get("is_active", "on") == "on"
                    fs.save()

                    # Rebuild items from inline form fields
                    fs.items.all().delete()

                    descriptions = request.POST.getlist("item_description[]")
                    amounts = request.POST.getlist("item_amount[]")
                    categories = request.POST.getlist("item_category[]")

                    for desc, amt, cat in zip(descriptions, amounts, categories):
                        desc = desc.strip()
                        amt = amt.strip()
                        if desc and amt:
                            FeeStructureItem.objects.create(
                                structure=fs,
                                category=cat or "other",
                                description=desc,
                                amount=float(amt),
                            )

                    after = {
                        "late_pickup_charge": str(fs.late_pickup_charge),
                        "sibling_discount_mode": fs.sibling_discount_mode,
                        "sibling_discount_value": str(fs.sibling_discount_value),
                        "assessment_fee": str(fs.assessment_fee),
                        "items": [
                            {"category": i.category, "description": i.description, "amount": str(i.amount)}
                            for i in fs.items.all()
                        ],
                    }

                    # Audit log â€” before/after snapshot
                    log_event(
                        actor=request.user,
                        action_type="FEE_STRUCTURE_SAVED",
                        model_name="FeeStructure",
                        object_id=fs.pk,
                        description=f"Saved fee structure for {class_name} â€” {term}",
                        before=before,
                        after=after,
                        request=request,
                    )

                    messages.success(request, f"Fee structure for {class_name} saved successfully.")
            except Exception as e:
                messages.error(request, f"Error saving fee structure: {e}")

            return redirect(f"{reverse('finance:fee_structure_setup')}?term={term_id}")

        elif action == "copy_from_term":
            # Delegate to existing CopyFeeStructureView logic
            from_term_id = request.POST.get("from_term")
            to_term_id = request.POST.get("to_term")
            if from_term_id:
                # Re-use the existing copy logic inline
                from_term = get_object_or_404(Term, pk=from_term_id)
                to_term = get_object_or_404(Term, pk=to_term_id)
                source_structures = FeeStructure.objects.filter(term=from_term)
                count = 0
                for ss in source_structures:
                    if FeeStructure.objects.filter(term=to_term, class_name=ss.class_name).exists():
                        continue
                    new_fs = FeeStructure.objects.create(
                        term=to_term,
                        class_name=ss.class_name,
                        is_active=ss.is_active,
                        late_pickup_charge=ss.late_pickup_charge,
                        sibling_discount_mode=ss.sibling_discount_mode,
                        sibling_discount_value=ss.sibling_discount_value,
                        assessment_fee=ss.assessment_fee,
                    )
                    for item in ss.items.all():
                        FeeStructureItem.objects.create(
                            structure=new_fs,
                            category=item.category,
                            description=item.description,
                            amount=item.amount,
                        )
                    count += 1

                log_event(
                    actor=request.user,
                    action_type="FEE_STRUCTURE_COPIED",
                    model_name="FeeStructure",
                    description=f"Copied {count} fee structures from {from_term} to {to_term}",
                    before={"source_term": str(from_term)},
                    after={"target_term": str(to_term), "count": count},
                    request=request,
                )

                messages.success(request, f"Copied {count} fee structures from {from_term} to {to_term}.")
            else:
                messages.error(request, "Source term is required.")

            return redirect(f"{reverse('finance:fee_structure_setup')}?term={to_term_id}")

        return redirect("finance:fee_structure_setup")


class SendInvoiceReminderView(RoleRequiredMixin, View):
    """Manually trigger a fee reminder â€” FR-FIN-014."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_invoice"

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        student = invoice.student
        if not student:
            messages.error(request, "This invoice is not linked to a student.")
            return redirect("finance:invoice_detail", pk=pk)

        # Notify parents
        from communications.email_service import send_parent_notification
        from students.models import StudentGuardian
        primary_guardian = StudentGuardian.objects.filter(student=student, is_primary=True).first()
        
        if primary_guardian and (primary_guardian.guardian.email or primary_guardian.guardian.phone):
            msg = f"Reminder: Invoice {invoice.invoice_number} is overdue. Balance: {invoice.total_due}. Please settle as soon as possible."
            detail_url = reverse("finance:parent_invoice_detail", kwargs={"pk": invoice.pk})

            from core.email_templates import send_dynamic_email
            from core.models import SchoolSettings
            school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
            _portal_remind = request.build_absolute_uri("/parent/")
            tpl_context = {
                "guardian_name": primary_guardian.guardian.full_name,
                "student_name": student.first_name if student else "your child",
                "currency": "TZS",
                "balance": f"{invoice.total_due:,.0f}",
                "due_date": str(invoice.due_date) if invoice.due_date else "N/A",
                "invoice_number": invoice.invoice_number,
                "school_name": school_name,
                "portal_url": _portal_remind,
            }
            db_sent = send_dynamic_email(
                template_type="fee_reminder",
                to_email=primary_guardian.guardian.email,
                context=tpl_context,
                actor=request.user,
            )
            if not db_sent:
                send_parent_notification(
                    guardian=primary_guardian.guardian,
                    title="Fee Payment Reminder",
                    message=msg,
                    link=detail_url,
                    actor=request.user
                )
            messages.success(request, f"Reminder sent to {primary_guardian.guardian.full_name}.")
        else:
            messages.error(request, "Primary guardian contact not found.")

        return redirect("finance:invoice_detail", pk=pk)


class InvoiceCorrectionView(RoleRequiredMixin, TemplateView):
    """
    A4 — Correct a non-finalized invoice via reversal entry.
    Only Super Admin and Finance Officer can perform invoice corrections.
    """
    template_name = "finance/invoice_correction.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.FINANCE_OFFICER]
    required_permission = "finance.change_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = get_object_or_404(
            Invoice.objects.select_related("student", "term"),
            pk=self.kwargs["pk"],
        )
        ctx["invoice"] = invoice
        ctx["line_items"] = invoice.line_items.all()
        return ctx

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)

        if invoice.is_finalized:
            messages.error(request, "Finalized invoices cannot be corrected.")
            return redirect("finance:invoice_detail", pk=pk)

        from audit.models import log_event

        try:
            with transaction.atomic():
                before = {
                    "amount_due": str(invoice.amount_due),
                    "discount_amount": str(invoice.discount_amount),
                    "total_due": str(invoice.total_due),
                    "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                    "line_items": [
                        {"description": li.description, "amount": str(li.amount), "is_discount": li.is_discount}
                        for li in invoice.line_items.all()
                    ],
                }

                # Update fields
                amount_due = request.POST.get("amount_due", str(invoice.amount_due))
                discount_amount = request.POST.get("discount_amount", "0")
                due_date = request.POST.get("due_date", "")

                invoice.amount_due = float(amount_due)
                invoice.discount_amount = float(discount_amount)
                if due_date:
                    invoice.due_date = due_date
                invoice.save()  # auto-recalculates total_due

                # Rebuild line items if provided
                descriptions = request.POST.getlist("item_description[]")
                amounts = request.POST.getlist("item_amount[]")
                if descriptions and amounts and descriptions[0]:
                    invoice.line_items.all().delete()
                    for desc, amt in zip(descriptions, amounts):
                        desc = desc.strip()
                        amt = amt.strip()
                        if desc and amt:
                            InvoiceLineItem.objects.create(
                                invoice=invoice,
                                description=desc,
                                amount=float(amt),
                            )

                after = {
                    "amount_due": str(invoice.amount_due),
                    "discount_amount": str(invoice.discount_amount),
                    "total_due": str(invoice.total_due),
                    "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                    "line_items": [
                        {"description": li.description, "amount": str(li.amount), "is_discount": li.is_discount}
                        for li in invoice.line_items.all()
                    ],
                }

                log_event(
                    actor=request.user,
                    action_type="INVOICE_CORRECTED",
                    model_name="Invoice",
                    object_id=invoice.pk,
                    description=f"Invoice {invoice.invoice_number} corrected",
                    before=before,
                    after=after,
                    request=request,
                )

                messages.success(request, f"Invoice {invoice.invoice_number} corrected successfully.")
        except Exception as e:
            messages.error(request, f"Error correcting invoice: {e}")

        return redirect("finance:invoice_detail", pk=pk)


class BulkInvoicePrintView(RoleRequiredMixin, View):
    """
    A4 — Bulk print / PDF multiple invoices at once.
    Accepts comma-separated invoice IDs via GET param `ids`.
    """
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_invoice"

    def get(self, request):
        ids_str = request.GET.get("ids", "")
        if not ids_str:
            messages.error(request, "No invoices selected.")
            return redirect("finance:invoice_list")

        try:
            ids = [int(x.strip()) for x in ids_str.split(",") if x.strip()]
        except ValueError:
            messages.error(request, "Invalid invoice IDs.")
            return redirect("finance:invoice_list")

        invoices = Invoice.objects.filter(pk__in=ids).select_related("student", "term").order_by("student__class_name", "student__first_name")

        if not invoices:
            messages.warning(request, "No invoices found.")
            return redirect("finance:invoice_list")

        # Build context for each invoice (line items + payment info)
        invoice_data = []
        for inv in invoices:
            line_items = list(inv.line_items.all())
            total_paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
            invoice_data.append({
                "invoice": inv,
                "line_items": line_items,
                "total_paid": total_paid,
                "balance": inv.total_due - total_paid,
            })

        html = render_to_string("finance/bulk_invoice_print.html", {
            "invoice_data": invoice_data,
            "today": date.today(),
            "pdf_mode": request.GET.get("format") == "pdf",
        }, request=request)

        if request.GET.get("format") == "pdf":
            try:
                from weasyprint import HTML
                pdf = HTML(string=html).write_pdf()
                response = HttpResponse(pdf, content_type="application/pdf")
                response["Content-Disposition"] = f'inline; filename="invoices-bulk-{date.today().isoformat()}.pdf"'
                return response
            except Exception:
                pass

        return HttpResponse(html)


class CopyFeeStructureView(RoleRequiredMixin, View):
    """Clones fee structures from one term to another â€” FR-FIN-003."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_feestructure"

    def post(self, request):
        from academics.models import Term
        from_term_id = request.POST.get("from_term")
        to_term_id = request.POST.get("to_term")
        
        if not from_term_id or not to_term_id:
            messages.error(request, "Both source and target terms are required.")
            return redirect("finance:fee_structure_setup")
            
        from_term = get_object_or_404(Term, pk=from_term_id)
        to_term = get_object_or_404(Term, pk=to_term_id)
        
        source_structures = FeeStructure.objects.filter(term=from_term)
        count = 0
        for ss in source_structures:
            # Skip if target already exists
            if FeeStructure.objects.filter(term=to_term, class_name=ss.class_name).exists():
                continue
                
            new_fs = FeeStructure.objects.create(
                term=to_term,
                class_name=ss.class_name,
                is_active=ss.is_active,
                late_pickup_charge=ss.late_pickup_charge,
                sibling_discount_mode=ss.sibling_discount_mode,
                sibling_discount_value=ss.sibling_discount_value,
                assessment_fee=ss.assessment_fee,
            )
            for item in ss.items.all():
                FeeStructureItem.objects.create(
                    structure=new_fs,
                    category=item.category,
                    description=item.description,
                    amount=item.amount
                )
            count += 1
            
        messages.success(request, f"Successfully cloned {count} fee structures to {to_term}.")
        return redirect("finance:fee_structure_setup")


class ParentInvoiceListView(RoleRequiredMixin, ListView):
    """Parent portal: invoices for linked children only."""

    template_name = "finance/parent_invoices.html"
    context_object_name = "invoices"
    allowed_roles = [UserRole.PARENT]
    required_permission = "finance.view_invoice"
    paginate_by = 30

    def get_queryset(self):
        from students.models import ParentGuardian, Student

        guardian = ParentGuardian.objects.filter(user=self.request.user).first()
        if not guardian:
            return Invoice.objects.none()
        student_ids = Student.objects.filter(studentguardian__guardian=guardian).values_list("id", flat=True)
        return (
            Invoice.objects.filter(student_id__in=student_ids)
            .select_related("student", "term")
            .order_by("-created_at")
        )


class ExpenseListView(RoleRequiredMixin, ListView):
    """List all expenses with filtering and summary stats."""
    template_name = "finance/expense_list.html"
    context_object_name = "expenses"
    paginate_by = 30
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_expense"

    def get_queryset(self):
        qs = Expense.objects.select_related("created_by", "approved_by").all()
        
        # Filter by status
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        
        # Filter by category
        cat = self.request.GET.get("category", "")
        if cat:
            qs = qs.filter(category=cat)
        
        # Filter by date range
        date_from = self.request.GET.get("from", "")
        date_to = self.request.GET.get("to", "")
        if date_from:
            qs = qs.filter(expense_date__gte=date_from)
        if date_to:
            qs = qs.filter(expense_date__lte=date_to)
        
        # Search
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(description__icontains=q) |
                Q(vendor__icontains=q) |
                Q(reference__icontains=q)
            )
        
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = ExpenseCategory.choices
        ctx["expense_statuses"] = ExpenseStatus.choices
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_category"] = self.request.GET.get("category", "")
        ctx["q"] = self.request.GET.get("q", "")
        ctx["date_from"] = self.request.GET.get("from", "")
        ctx["date_to"] = self.request.GET.get("to", "")

        # Summary metric cards
        base_qs = Expense.objects.all()
        ctx["total_expenses"] = base_qs.aggregate(t=Sum("amount"))["t"] or 0
        ctx["total_count"] = base_qs.count()
        ctx["pending_count"] = base_qs.filter(status=ExpenseStatus.PENDING).count()
        ctx["pending_total"] = base_qs.filter(status=ExpenseStatus.PENDING).aggregate(t=Sum("amount"))["t"] or 0
        ctx["approved_count"] = base_qs.filter(status=ExpenseStatus.APPROVED).count()
        return ctx


class ExpenseCreateView(RoleRequiredMixin, TemplateView):
    """Create a new expense record."""
    template_name = "finance/expense_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_expense"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = ExpenseCategory.choices
        ctx["payment_methods"] = Expense.PAYMENT_METHODS
        ctx["is_edit"] = False
        ctx["today"] = timezone.now().date()
        return ctx

    def post(self, request):
        from audit.models import log_event
        from academics.models import Term
        
        try:
            category = request.POST.get("category", "")
            amount = request.POST.get("amount", "")
            description = request.POST.get("description", "")
            expense_date = request.POST.get("expense_date", "")
            payment_method = request.POST.get("payment_method", "cash")
            reference = request.POST.get("reference", "")
            vendor = request.POST.get("vendor", "")
            notes = request.POST.get("notes", "")
            is_reimbursement = request.POST.get("is_reimbursement") == "on"
            
            # Auto-detect current term
            today = timezone.now().date()
            from academics.utils import get_current_term
            current_term = get_current_term()
            
            expense = Expense.objects.create(
                category=category,
                amount=float(amount),
                description=description,
                expense_date=expense_date or timezone.now().date(),
                payment_method=payment_method,
                reference=reference,
                vendor=vendor,
                notes=notes,
                is_reimbursement=is_reimbursement,
                term=current_term,
                created_by=request.user,
            )
            
            log_event(
                actor=request.user,
                action_type="EXPENSE_CREATED",
                model_name="Expense",
                object_id=expense.pk,
                description=f"Expense of TZS {amount:,.0f} for {description[:50]} created",
                request=request,
            )
            
            messages.success(request, f"Expense of TZS {float(amount):,.0f} recorded successfully.")
            return redirect("finance:expense_list")
        except Exception as e:
            messages.error(request, f"Error recording expense: {e}")
            return redirect("finance:expense_create")


class ExpenseDetailView(RoleRequiredMixin, DetailView):
    """View a single expense record."""
    template_name = "finance/expense_detail.html"
    model = Expense
    context_object_name = "expense"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_expense"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # NFR-PDPA-006: Log access to financial expense data
        from audit.view_audit import log_sensitive_access
        log_sensitive_access(
            self.request, "Expense", self.object.pk, "finance",
            description=f"Expense record viewed: TZS {self.object.amount:,.0f} for {self.object.description[:50]}"
        )
        expense = self.object
        # Find the related budget (same term + category)
        try:
            budget = Budget.objects.filter(term=expense.term, category=expense.category).first()
            if budget:
                ctx["related_budget"] = budget
                ctx["budget_allocated"] = budget.allocated_amount
                ctx["budget_remaining"] = budget.remaining_amount()
                ctx["budget_utilization"] = budget.utilization_pct()
        except Exception:
            pass
        return ctx

    def get_object(self, queryset=None):
        return get_object_or_404(Expense.objects.select_related("created_by", "term"), pk=self.kwargs["pk"])


class ExpenseUpdateView(RoleRequiredMixin, TemplateView):
    """Edit an existing expense record."""
    template_name = "finance/expense_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_expense"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        expense = get_object_or_404(Expense, pk=self.kwargs["pk"])
        ctx["expense"] = expense
        ctx["categories"] = ExpenseCategory.choices
        ctx["payment_methods"] = Expense.PAYMENT_METHODS
        ctx["is_edit"] = True
        ctx["today"] = timezone.now().date()
        return ctx

    def post(self, request, pk):
        from audit.models import log_event
        expense = get_object_or_404(Expense, pk=pk)

        # Don't allow editing approved/rejected expenses
        if expense.status != ExpenseStatus.PENDING:
            messages.error(request, "Only pending expenses can be edited.")
            return redirect("finance:expense_detail", pk=pk)

        try:
            expense.category = request.POST.get("category", expense.category)
            expense.amount = float(request.POST.get("amount", expense.amount))
            expense.description = request.POST.get("description", expense.description)
            expense.expense_date = request.POST.get("expense_date", expense.expense_date.isoformat())
            expense.payment_method = request.POST.get("payment_method", expense.payment_method)
            expense.reference = request.POST.get("reference", expense.reference)
            expense.vendor = request.POST.get("vendor", expense.vendor)
            expense.notes = request.POST.get("notes", expense.notes)
            expense.is_reimbursement = request.POST.get("is_reimbursement") == "on"
            expense.save()

            log_event(
                actor=request.user,
                action_type="EXPENSE_UPDATED",
                model_name="Expense",
                object_id=expense.pk,
                description=f"Expense {expense.pk} updated",
                request=request,
            )
            messages.success(request, "Expense updated successfully.")
            return redirect("finance:expense_detail", pk=pk)
        except Exception as e:
            messages.error(request, f"Error updating expense: {e}")
            return redirect("finance:expense_detail", pk=pk)


class ExpenseApproveView(RoleRequiredMixin, View):
    """Approve or reject a pending expense."""
    allowed_roles = [UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_expense"

    def post(self, request, pk):
        from audit.models import log_event
        expense = get_object_or_404(Expense, pk=pk)

        if expense.status != ExpenseStatus.PENDING:
            messages.error(request, "This expense has already been reviewed.")
            return redirect("finance:expense_detail", pk=pk)

        action = request.POST.get("action", "")
        rejection_reason = request.POST.get("rejection_reason", "")

        try:
            if action == "approve":
                expense.status = ExpenseStatus.APPROVED
                expense.approved_by = request.user
                expense.approved_at = timezone.now()
                expense.save(update_fields=["status", "approved_by_id", "approved_at", "updated_at"])
                log_event(
                    actor=request.user,
                    action_type="EXPENSE_APPROVED",
                    model_name="Expense",
                    object_id=expense.pk,
                    description=f"Expense {expense.pk} for TZS {expense.amount:,.0f} approved",
                    request=request,
                )
                messages.success(request, f"Expense for TZS {expense.amount:,.0f} approved.")
            elif action == "reject":
                if not rejection_reason:
                    messages.error(request, "Please provide a reason for rejection.")
                    return redirect("finance:expense_detail", pk=pk)
                expense.status = ExpenseStatus.REJECTED
                expense.approved_by = request.user
                expense.approved_at = timezone.now()
                expense.rejection_reason = rejection_reason
                expense.save(update_fields=["status", "approved_by_id", "approved_at", "rejection_reason", "updated_at"])
                log_event(
                    actor=request.user,
                    action_type="EXPENSE_REJECTED",
                    model_name="Expense",
                    object_id=expense.pk,
                    description=f"Expense {expense.pk} rejected: {rejection_reason[:100]}",
                    request=request,
                )
                messages.success(request, "Expense rejected.")
            else:
                messages.error(request, "Invalid action.")
        except Exception as e:
            messages.error(request, f"Error: {e}")

        return redirect("finance:expense_detail", pk=pk)


class BudgetListView(RoleRequiredMixin, ListView):
    """List budgets with spending vs allocation."""
    template_name = "finance/budget_list.html"
    context_object_name = "budgets"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_budget"

    def get_queryset(self):
        from academics.models import Term
        term_id = self.request.GET.get("term")
        qs = Budget.objects.select_related("term")
        if term_id:
            qs = qs.filter(term_id=term_id)
        else:
            today = timezone.now().date()
            from academics.utils import get_current_term
            current_term = get_current_term()
            if current_term:
                qs = qs.filter(term=current_term)
        return qs.order_by("-term__academic_year__name", "category")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        from academics.utils import get_current_term
        today = timezone.now().date()
        current_term = get_current_term()
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name")
        ctx["selected_term"] = self.request.GET.get("term", "") or (current_term.pk if current_term else "")
        ctx["categories"] = ExpenseCategory.choices

        # Overall budget summary
        qs = self.object_list if hasattr(self, 'object_list') else self.get_queryset()
        total_allocated = sum(b.allocated_amount for b in qs)
        total_spent = sum(b.spent_amount() for b in qs)
        ctx["total_allocated"] = total_allocated
        ctx["total_spent"] = total_spent
        ctx["total_remaining"] = total_allocated - total_spent
        ctx["overall_utilization"] = round((total_spent / total_allocated * 100), 1) if total_allocated > 0 else 0
        return ctx


class BudgetCreateView(RoleRequiredMixin, TemplateView):
    """Create or edit a budget allocation."""
    template_name = "finance/budget_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_budget"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        ctx["categories"] = ExpenseCategory.choices
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name")

        pk = self.kwargs.get("pk")
        if pk:
            ctx["budget"] = get_object_or_404(Budget, pk=pk)
            ctx["is_edit"] = True
        else:
            ctx["is_edit"] = False
            # Pre-select term
            today = timezone.now().date()
            from academics.utils import get_current_term
            current_term = get_current_term()
            ctx["selected_term_id"] = current_term.pk if current_term else ""
        return ctx

    def post(self, request, pk=None):
        from audit.models import log_event
        from academics.models import Term

        term_id = request.POST.get("term")
        category = request.POST.get("category")
        allocated_amount = request.POST.get("allocated_amount", "0")
        notes = request.POST.get("notes", "")
        is_frozen = request.POST.get("is_frozen") == "on"

        if not term_id or not category or not allocated_amount:
            messages.error(request, "Term, category, and amount are required.")
            return redirect("finance:budget_create")

        term = get_object_or_404(Term, pk=term_id)

        try:
            if pk:
                budget = get_object_or_404(Budget, pk=pk)
                budget.category = category
                budget.allocated_amount = float(allocated_amount)
                budget.notes = notes
                budget.is_frozen = is_frozen
                budget.save()
                messages.success(request, f"Budget for {budget.get_category_display()} updated.")
                log_event(
                    actor=request.user,
                    action_type="BUDGET_UPDATED",
                    model_name="Budget",
                    object_id=budget.pk,
                    description=f"Budget {budget.get_category_display()} updated to TZS {float(allocated_amount):,.0f}",
                    request=request,
                )
            else:
                budget, created = Budget.objects.get_or_create(
                    term=term,
                    category=category,
                    defaults={
                        "allocated_amount": float(allocated_amount),
                        "notes": notes,
                        "is_frozen": is_frozen,
                    }
                )
                if not created:
                    budget.allocated_amount = float(allocated_amount)
                    budget.notes = notes
                    budget.is_frozen = is_frozen
                    budget.save()
                    messages.success(request, f"Budget for {budget.get_category_display()} updated.")
                else:
                    messages.success(request, f"Budget for {budget.get_category_display()} created.")

                log_event(
                    actor=request.user,
                    action_type="BUDGET_CREATED",
                    model_name="Budget",
                    object_id=budget.pk,
                    description=f"Budget {budget.get_category_display()} â€” TZS {float(allocated_amount):,.0f}",
                    request=request,
                )

            return redirect("finance:budget_list")
        except Exception as e:
            messages.error(request, f"Error saving budget: {e}")
            return redirect("finance:budget_create")


class RecurringExpenseListView(RoleRequiredMixin, ListView):
    """List recurring expense templates."""
    template_name = "finance/recurring_list.html"
    context_object_name = "recurring_expenses"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_expense"

    def get_queryset(self):
        return RecurringExpense.objects.select_related("created_by").order_by("next_due_date", "-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date
        today = date.today()

        qs = self.object_list if hasattr(self, 'object_list') else self.get_queryset()
        active = qs.filter(is_active=True)
        inactive = qs.filter(is_active=False)
        due_soon = active.filter(next_due_date__lte=today)

        ctx["active_count"] = active.count()
        ctx["inactive_count"] = inactive.count()
        ctx["due_soon_count"] = due_soon.count()
        ctx["today"] = today
        return ctx


class RecurringExpenseCreateView(RoleRequiredMixin, TemplateView):
    """Create a recurring expense template."""
    template_name = "finance/recurring_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_expense"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = ExpenseCategory.choices
        ctx["frequencies"] = RecurringExpense.FREQUENCY_CHOICES
        ctx["payment_methods"] = RecurringExpense.PAYMENT_METHODS
        ctx["today"] = timezone.now().date()

        pk = self.kwargs.get("pk")
        if pk:
            ctx["recurring"] = get_object_or_404(RecurringExpense, pk=pk)
            ctx["is_edit"] = True
        else:
            ctx["is_edit"] = False
        return ctx

    def post(self, request, pk=None):
        from audit.models import log_event

        try:
            category = request.POST.get("category")
            amount = float(request.POST.get("amount", "0"))
            description = request.POST.get("description", "")
            frequency = request.POST.get("frequency", "monthly")
            vendor = request.POST.get("vendor", "")
            reference = request.POST.get("reference", "")
            notes = request.POST.get("notes", "")
            payment_method = request.POST.get("payment_method", "bank_transfer")
            next_due_date = request.POST.get("next_due_date", "")
            is_active = request.POST.get("is_active") != "off"

            if pk:
                recurring = get_object_or_404(RecurringExpense, pk=pk)
                recurring.category = category
                recurring.amount = amount
                recurring.description = description
                recurring.frequency = frequency
                recurring.vendor = vendor
                recurring.reference = reference
                recurring.notes = notes
                recurring.payment_method = payment_method
                recurring.is_active = is_active
                if next_due_date:
                    recurring.next_due_date = next_due_date
                recurring.save()
                messages.success(request, "Recurring expense updated.")
                log_event(
                    actor=request.user,
                    action_type="RECURRING_EXPENSE_UPDATED",
                    model_name="RecurringExpense",
                    object_id=recurring.pk,
                    request=request,
                )
            else:
                recurring = RecurringExpense.objects.create(
                    category=category,
                    amount=amount,
                    description=description,
                    frequency=frequency,
                    vendor=vendor,
                    reference=reference,
                    notes=notes,
                    payment_method=payment_method,
                    next_due_date=next_due_date or timezone.now().date(),
                    is_active=is_active,
                    created_by=request.user,
                )
                messages.success(request, "Recurring expense created.")
                log_event(
                    actor=request.user,
                    action_type="RECURRING_EXPENSE_CREATED",
                    model_name="RecurringExpense",
                    object_id=recurring.pk,
                    request=request,
                )

            return redirect("finance:recurring_list")
        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect("finance:recurring_create")


class RecurringExpenseProcessView(RoleRequiredMixin, View):
    """Process all due recurring expenses — creates actual Expense entries."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_expense"

    def post(self, request):
        from audit.models import log_event
        from datetime import date

        today = date.today()
        due_expenses = RecurringExpense.objects.filter(is_active=True, next_due_date__lte=today)

        processed = 0
        errors = []
        for re in due_expenses:
            try:
                re.process_due(request.user)
                processed += 1
            except Exception as e:
                errors.append(f"{re.description[:30]}: {e}")

        if processed:
            log_event(
                actor=request.user,
                action_type="RECURRING_EXPENSES_PROCESSED",
                model_name="RecurringExpense",
                description=f"Processed {processed} due recurring expenses",
                request=request,
            )
            messages.success(request, f"Processed {processed} recurring expense(s).")
        if errors:
            messages.warning(request, f"Errors: {'; '.join(errors[:3])}")
        if not processed and not errors:
            messages.info(request, "No recurring expenses are due.")

        return redirect("finance:recurring_list")


class FinancialReportsView(RoleRequiredMixin, TemplateView):
    """Financial reports â€” income statement, collection report, expense report, aging."""
    template_name = "finance/financial_reports.html"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        from .models import Invoice, Payment, Expense, ExpenseCategory, InvoiceStatus
        from datetime import timedelta
        
        today = timezone.now().date()
        report_type = self.request.GET.get("type", "income_statement")
        ctx["report_type"] = report_type
        
        # Get selected term or default to current
        term_id = self.request.GET.get("term")
        if term_id:
            term = get_object_or_404(Term, pk=term_id)
        else:
            from academics.utils import get_current_term
            term = get_current_term()
        ctx["selected_term"] = term
        ctx["terms"] = Term.objects.all().order_by("-academic_year__name")
        
        # Get date range from term or query params
        date_from = self.request.GET.get("from", "")
        date_to = self.request.GET.get("to", "")
        
        if not date_from and term and term.start_date:
            date_from = term.start_date.isoformat()
        if not date_to and term and term.end_date:
            date_to = term.end_date.isoformat()
        if not date_from:
            date_from = (today - timedelta(days=30)).isoformat()
        if not date_to:
            date_to = today.isoformat()
        
        ctx["date_from"] = date_from
        ctx["date_to"] = date_to
        
        # 1. Income Statement â€” revenue vs expenses
        if report_type == "income_statement":
            # Revenue: payments received in period
            revenue_qs = Payment.objects.filter(
                is_reversal=False,
                created_at__date__gte=date_from,
                created_at__date__lte=date_to,
            )
            total_revenue = revenue_qs.aggregate(t=Sum("amount"))["t"] or 0
            
            # Revenue by method
            revenue_by_method = {}
            for method, label in [("cash", "Cash"), ("bank", "Bank Transfer"), ("cheque", "Cheque"), ("mobile_money", "Mobile Money")]:
                amt = revenue_qs.filter(method__startswith=method).aggregate(t=Sum("amount"))["t"] or 0
                if amt:
                    revenue_by_method[method] = {"label": label, "amount": amt}
            ctx["revenue_by_method"] = revenue_by_method
            
            # Expenses in period
            expense_qs = Expense.objects.filter(
                expense_date__gte=date_from,
                expense_date__lte=date_to,
            )
            total_expenses = expense_qs.aggregate(t=Sum("amount"))["t"] or 0
            
            # Expenses by category
            expenses_by_category = []
            for cat_code, cat_label in ExpenseCategory.choices:
                amt = expense_qs.filter(category=cat_code).aggregate(t=Sum("amount"))["t"] or 0
                if amt:
                    pct = round((amt / total_expenses * 100), 1) if total_expenses > 0 else 0
                    expenses_by_category.append({
                        "code": cat_code,
                        "label": cat_label,
                        "amount": amt,
                        "pct": pct,
                    })
            ctx["expenses_by_category"] = expenses_by_category
            
            net_income = total_revenue - total_expenses
            ctx.update({
                "total_revenue": total_revenue,
                "total_expenses": total_expenses,
                "net_income": net_income,
                "expense_count": expense_qs.count(),
                "revenue_count": revenue_qs.count(),
            })
        
        # 2. Collection Report â€” billed vs collected by class
        elif report_type == "collection_report":
            if term:
                term_invoices = Invoice.objects.filter(term=term)
            else:
                term_invoices = Invoice.objects.filter(
                    created_at__date__gte=date_from,
                    created_at__date__lte=date_to,
                )
            
            total_billed = term_invoices.aggregate(t=Sum("total_due"))["t"] or 0
            total_received = Payment.objects.filter(
                invoice__in=term_invoices,
                is_reversal=False,
            ).aggregate(t=Sum("amount"))["t"] or 0
            
            # Collection by class
            classes = term_invoices.values_list("student__class_name", flat=True).distinct()
            collection_by_class = []
            for cls in sorted(filter(None, classes)):
                cls_invoices = term_invoices.filter(student__class_name=cls)
                cls_billed = cls_invoices.aggregate(t=Sum("total_due"))["t"] or 0
                cls_payments = Payment.objects.filter(invoice__in=cls_invoices).aggregate(t=Sum("amount"))["t"] or 0
                cls_rate = round((cls_payments / cls_billed * 100), 1) if cls_billed > 0 else 0
                collection_by_class.append({
                    "class_name": cls,
                    "billed": cls_billed,
                    "received": cls_payments,
                    "outstanding": cls_billed - cls_payments,
                    "rate": cls_rate,
                })
            
            ctx.update({
                "total_billed": total_billed,
                "total_received": total_received,
                "total_outstanding": total_billed - total_received,
                "collection_rate": round((total_received / total_billed * 100), 1) if total_billed > 0 else 0,
                "collection_by_class": collection_by_class,
                "invoice_count": term_invoices.count(),
            })
        
        # 3. Expense Report â€” expenses by category
        elif report_type == "expense_report":
            expense_qs = Expense.objects.filter(
                expense_date__gte=date_from,
                expense_date__lte=date_to,
            )
            total_expenses = expense_qs.aggregate(t=Sum("amount"))["t"] or 0
            
            expenses_by_category = []
            for cat_code, cat_label in ExpenseCategory.choices:
                amt = expense_qs.filter(category=cat_code).aggregate(t=Sum("amount"))["t"] or 0
                if amt:
                    pct = round((amt / total_expenses * 100), 1) if total_expenses > 0 else 0
                    expenses_by_category.append({
                        "code": cat_code,
                        "label": cat_label,
                        "amount": amt,
                        "pct": pct,
                    })
            ctx.update({
                "total_expenses": total_expenses,
                "expenses_by_category": expenses_by_category,
                "expense_count": expense_qs.count(),
                "recent_expenses": expense_qs.select_related("created_by").order_by("-expense_date")[:20],
            })
        
        # 4. Aging Analysis â€” overdue by aging buckets
        elif report_type == "aging":
            overdue_invoices = Invoice.objects.filter(status=InvoiceStatus.OVERDUE).select_related("student")
            
            aging_buckets = {
                "1-7_days": {"label": "1-7 days", "invoices": [], "total": 0},
                "8-14_days": {"label": "8-14 days", "invoices": [], "total": 0},
                "15-30_days": {"label": "15-30 days", "invoices": [], "total": 0},
                "31-60_days": {"label": "31-60 days", "invoices": [], "total": 0},
                "60_plus": {"label": "60+ days", "invoices": [], "total": 0},
            }
            
            for inv in overdue_invoices:
                if not inv.due_date:
                    continue
                days = (today - inv.due_date).days
                total_paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                balance = inv.total_due - total_paid
                
                entry = {
                    "invoice": inv,
                    "student_name": f"{inv.student.first_name} {inv.student.last_name}" if inv.student else "N/A",
                    "class_name": inv.student.class_name if inv.student else "N/A",
                    "balance": balance,
                    "days": days,
                }
                
                if days <= 7:
                    aging_buckets["1-7_days"]["invoices"].append(entry)
                    aging_buckets["1-7_days"]["total"] += balance
                elif days <= 14:
                    aging_buckets["8-14_days"]["invoices"].append(entry)
                    aging_buckets["8-14_days"]["total"] += balance
                elif days <= 30:
                    aging_buckets["15-30_days"]["invoices"].append(entry)
                    aging_buckets["15-30_days"]["total"] += balance
                elif days <= 60:
                    aging_buckets["31-60_days"]["invoices"].append(entry)
                    aging_buckets["31-60_days"]["total"] += balance
                else:
                    aging_buckets["60_plus"]["invoices"].append(entry)
                    aging_buckets["60_plus"]["total"] += balance
            
            ctx["aging_buckets"] = aging_buckets
            ctx["total_overdue"] = sum(b["total"] for b in aging_buckets.values())
            ctx["total_overdue_count"] = overdue_invoices.count()
        
        return ctx


class ClassCollectionDetailView(RoleRequiredMixin, TemplateView):
    """
    Collection details for a specific class — drill-down from dashboard bar chart.
    Shows class summary + per-student breakdown.
    """
    template_name = "finance/class_collection_detail.html"
    allowed_roles = [
        UserRole.FINANCE_OFFICER,
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN_OFFICER,
    ]
    required_permission = "finance.view_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        from finance.models import InvoiceStatus

        class_name = self.kwargs.get("class_name", "")
        ctx["class_name"] = class_name

        today = timezone.now().date()

        # Find current term
        current_term = Term.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
            is_locked=False
        ).first()
        if not current_term:
            from academics.utils import get_current_term
            current_term = get_current_term()
        ctx["current_term"] = current_term

        if current_term:
            term_invoices = Invoice.objects.filter(term=current_term, student__class_name=class_name)
        else:
            term_invoices = Invoice.objects.filter(student__class_name=class_name)

        total_billed = term_invoices.aggregate(t=Sum("total_due"))["t"] or 0
        total_received = Payment.objects.filter(
            invoice__in=term_invoices, is_reversal=False
        ).aggregate(t=Sum("amount"))["t"] or 0
        total_outstanding = total_billed - total_received
        collection_rate = round((total_received / total_billed * 100), 1) if total_billed > 0 else 0
        outstand_rate = round((total_outstanding / total_billed * 100), 1) if total_billed > 0 else 0

        ctx.update({
            "total_billed": total_billed,
            "total_received": total_received,
            "total_outstanding": total_outstanding,
            "collection_rate": collection_rate,
            "outstand_rate": outstand_rate,
            "invoice_count": term_invoices.count(),
        })

        # Per-student breakdown â€” prefetch payments to avoid N+1
        students_data = {}
        for inv in term_invoices.select_related("student").prefetch_related("payments").order_by("student__first_name"):
            student = inv.student
            if not student:
                continue
            key = student.id
            paid = sum(p.amount for p in inv.payments.all() if not p.is_reversal)
            if key not in students_data:
                students_data[key] = {
                    "student": student,
                    "invoices": [],
                    "total_billed": 0,
                    "total_paid": 0,
                    "balance": 0,
                }
            students_data[key]["invoices"].append({
                "invoice": inv,
                "paid": paid,
                "balance": inv.total_due - paid,
            })
            students_data[key]["total_billed"] += inv.total_due
            students_data[key]["total_paid"] += paid
            students_data[key]["balance"] += inv.total_due - paid

        ctx["students"] = sorted(students_data.values(), key=lambda s: s["student"].first_name)

        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["is_hos"] = self.request.user.role == UserRole.HEAD_OF_SCHOOL

        return ctx


class ParentInvoiceDetailView(RoleRequiredMixin, DetailView):
    """Parent portal: single invoice (must belong to a linked child)."""

    model = Invoice
    template_name = "finance/parent_invoice_detail.html"
    context_object_name = "invoice"
    allowed_roles = [UserRole.PARENT]
    required_permission = "finance.view_invoice"

    def get_queryset(self):
        from students.models import ParentGuardian, Student

        guardian = ParentGuardian.objects.filter(user=self.request.user).first()
        if not guardian:
            return Invoice.objects.none()
        student_ids = Student.objects.filter(studentguardian__guardian=guardian).values_list("id", flat=True)
        return Invoice.objects.filter(student_id__in=student_ids).select_related("student", "term")


class ManualInvoiceCreateView(RoleRequiredMixin, TemplateView):
    """A2.3 â€” Create a manual/miscellaneous invoice (MISC- format)."""
    template_name = "finance/invoice_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from students.models import Student
        from academics.models import Term
        ctx["is_new"] = True
        ctx["is_misc"] = True
        ctx["students"] = Student.objects.filter(is_archived=False, status="active").order_by("first_name", "last_name")
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name")
        ctx["today"] = timezone.now().date()
        return ctx

    def post(self, request):
        from students.models import Student
        from academics.models import Term
        from audit.models import log_event
        from .models import InvoiceStatus

        student_id = request.POST.get("student")
        term_id = request.POST.get("term")
        amount = request.POST.get("amount", "0")
        due_date = request.POST.get("due_date", "")
        description = request.POST.get("description", "")
        invoice_type = request.POST.get("invoice_type", "misc")

        if not student_id or not amount:
            messages.error(request, "Student and amount are required.")
            return redirect("finance:manual_invoice_create")

        student = get_object_or_404(Student, pk=student_id)
        term = None
        if term_id:
            term = get_object_or_404(Term, pk=term_id)

        try:
            with transaction.atomic():
                from finance.models import Invoice, InvoiceLineItem

                # Generate invoice number
                last_id = Invoice.objects.aggregate(m=Max("id"))["m"] or 0
                prefix = "ASSESS-" if invoice_type == "assessment" else "MISC-"
                inv_number = f"{prefix}{timezone.now().year}-{last_id + 1:04d}"

                invoice = Invoice.objects.create(
                    student=student,
                    term=term,
                    invoice_number=inv_number,
                    amount_due=float(amount),
                    discount_amount=0,
                    due_date=due_date or timezone.now().date() + timedelta(days=14),
                    status=InvoiceStatus.UNPAID,
                )

                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    description=description or f"{invoice_type.replace('_', ' ').title()} charge",
                    amount=float(amount),
                )

                log_event(
                    actor=request.user,
                    action_type="MANUAL_INVOICE_CREATED",
                    model_name="Invoice",
                    object_id=invoice.pk,
                    description=f"Manual invoice {inv_number} created for {student}",
                    request=request,
                )

                messages.success(request, f"Invoice {inv_number} created for {student}.")
                return redirect("finance:invoice_detail", pk=invoice.pk)

        except Exception as e:
            messages.error(request, f"Error creating invoice: {e}")
            return redirect("finance:manual_invoice_create")


class ReportCSVExportView(RoleRequiredMixin, View):
    """A5.3 — Export any report type as CSV."""
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_invoice"

    def get(self, request):
        import csv
        from academics.models import Term
        from .models import Invoice, Payment, Expense, ExpenseCategory, InvoiceStatus

        report_type = request.GET.get("type", "income_statement")
        term_id = request.GET.get("term")
        date_from = request.GET.get("from", "")
        date_to = request.GET.get("to", "")

        term = None
        if term_id:
            term = get_object_or_404(Term, pk=term_id)

        if not date_from and term and term.start_date:
            date_from = term.start_date.isoformat()
        if not date_to and term and term.end_date:
            date_to = term.end_date.isoformat()
        if not date_from:
            date_from = (timezone.now().date() - timedelta(days=30)).isoformat()
        if not date_to:
            date_to = timezone.now().date().isoformat()

        filename = f"report_{report_type}_{date_from}_{date_to}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)

        if report_type == "income_statement":
            writer.writerow(["Income Statement", f"{date_from} to {date_to}"])
            writer.writerow([])
            writer.writerow(["Revenue (Payments)"])
            payments = Payment.objects.filter(
                is_reversal=False,
                created_at__date__gte=date_from,
                created_at__date__lte=date_to,
            ).select_related("invoice__student")
            writer.writerow(["Date", "Student", "Amount", "Method", "Reference"])
            for p in payments:
                writer.writerow([
                    p.created_at.date(),
                    str(p.invoice.student) if p.invoice and p.invoice.student else "N/A",
                    float(p.amount),
                    p.method,
                    p.reference,
                ])
            writer.writerow([])
            writer.writerow(["Total Revenue", payments.aggregate(t=Sum("amount"))["t"] or 0])
            writer.writerow([])
            writer.writerow(["Expenses"])
            expenses = Expense.objects.filter(
                expense_date__gte=date_from, expense_date__lte=date_to
            )
            writer.writerow(["Date", "Category", "Description", "Amount", "Vendor"])
            for e in expenses:
                writer.writerow([e.expense_date, e.get_category_display(), e.description, float(e.amount), e.vendor])
            writer.writerow([])
            writer.writerow(["Total Expenses", expenses.aggregate(t=Sum("amount"))["t"] or 0])

        elif report_type == "collection_report":
            if term:
                term_invoices = Invoice.objects.filter(term=term)
            else:
                term_invoices = Invoice.objects.filter(
                    created_at__date__gte=date_from, created_at__date__lte=date_to
                )
            writer.writerow(["Collection Report", f"{date_from} to {date_to}"])
            writer.writerow([])
            writer.writerow(["Class", "Total Billed", "Total Collected", "Outstanding", "Rate"])
            classes = term_invoices.values_list("student__class_name", flat=True).distinct()
            for cls in sorted(filter(None, classes)):
                cls_invoices = term_invoices.filter(student__class_name=cls)
                cls_billed = cls_invoices.aggregate(t=Sum("total_due"))["t"] or 0
                cls_payments = Payment.objects.filter(invoice__in=cls_invoices).aggregate(t=Sum("amount"))["t"] or 0
                rate = round((cls_payments / cls_billed * 100), 1) if cls_billed > 0 else 0
                writer.writerow([cls, cls_billed, cls_payments, cls_billed - cls_payments, f"{rate}%"])

        elif report_type == "aging":
            writer.writerow(["Aging Analysis", timezone.now().date().isoformat()])
            writer.writerow([])
            writer.writerow(["Student", "Class", "Invoice", "Due Date", "Balance", "Days Overdue"])
            overdue_invoices = Invoice.objects.filter(status=InvoiceStatus.OVERDUE).select_related("student")
            today = timezone.now().date()
            for inv in overdue_invoices:
                total_paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                balance = inv.total_due - total_paid
                days = (today - inv.due_date).days if inv.due_date else 0
                writer.writerow([
                    str(inv.student) if inv.student else "N/A",
                    inv.student.class_n

                ])
        else:
            writer.writerow(["Unknown report type"])

        return response


class FeeStructurePublishView(RoleRequiredMixin, View):
    """A1.2 â€” Publish a fee structure (Draft â†’ Published)."""
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_feestructure"

    def post(self, request, pk):
        fs = get_object_or_404(FeeStructure, pk=pk)
        if fs.status != FeeStructureStatus.DRAFT:
            messages.error(request, "Only draft fee structures can be published.")
        else:
            fs.status = FeeStructureStatus.PUBLISHED
            fs.save(update_fields=["status", "updated_at"])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="FEE_STRUCTURE_PUBLISHED",
                model_name="FeeStructure",
                object_id=fs.pk,
                description=f"Fee structure for {fs.class_name} published",
                request=request,
            )
            messages.success(request, f"Fee structure for {fs.class_name} published.")
        return redirect("finance:fee_structure_setup")


class FeeStructureLockView(RoleRequiredMixin, View):
    """A1.2 — Lock a published fee structure (Published → Locked). Only Super Admin and Finance Officer."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.FINANCE_OFFICER]
    required_permission = "finance.change_feestructure"

    def post(self, request, pk):
        fs = get_object_or_404(FeeStructure, pk=pk)
        if fs.status != FeeStructureStatus.PUBLISHED:
            messages.error(request, "Only published fee structures can be locked.")
        else:
            fs.status = FeeStructureStatus.LOCKED
            fs.save(update_fields=["status", "updated_at"])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="FEE_STRUCTURE_LOCKED",
                model_name="FeeStructure",
                object_id=fs.pk,
                description=f"Fee structure for {fs.class_name} locked",
                request=request,
            )
            messages.success(request, f"Fee structure for {fs.class_name} locked.")
        return redirect("finance:fee_structure_setup")


class FeeStructureUnlockView(RoleRequiredMixin, View):
    """A1.2 — Unlock a locked fee structure (Locked → Published). Only Super Admin and Finance Officer."""
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.FINANCE_OFFICER]
    required_permission = "finance.change_feestructure"

    def post(self, request, pk):
        fs = get_object_or_404(FeeStructure, pk=pk)
        if fs.status != FeeStructureStatus.LOCKED:
            messages.error(request, "Only locked fee structures can be unlocked.")
        else:
            fs.status = FeeStructureStatus.PUBLISHED
            fs.save(update_fields=["status", "updated_at"])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="FEE_STRUCTURE_UNLOCKED",
                model_name="FeeStructure",
                object_id=fs.pk,
                description=f"Fee structure for {fs.class_name} unlocked",
                request=request,
            )
            messages.success(request, f"Fee structure for {fs.class_name} unlocked.")
        return redirect("finance:fee_structure_setup")


class StudentAccountStatementView(RoleRequiredMixin, TemplateView):
    """A5.1 â€” Student account statement showing all invoices and payments."""
    template_name = "finance/student_statement.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "finance.view_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from students.models import Student
        from academics.models import Term
        
        student_id = self.kwargs.get("student_id")
        student = get_object_or_404(Student, pk=student_id)
        ctx["student"] = student
        
        # All invoices for this student
        invoices = Invoice.objects.filter(student=student).select_related("term", "period").order_by("-created_at")
        ctx["invoices"] = invoices
        
        # Totals
        total_billed = invoices.aggregate(t=Sum("total_due"))["t"] or 0
        total_paid = Payment.objects.filter(invoice__in=invoices, is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
        ctx["total_billed"] = total_billed
        ctx["total_paid"] = total_paid
        ctx["balance"] = total_billed - total_paid
        
        # Per-invoice breakdown
        invoice_details = []
        for inv in invoices:
            paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
            invoice_details.append({
                "invoice": inv,
                "paid": paid,
                "balance": inv.total_due - paid,
                "line_items": inv.line_items.all(),
            })
        ctx["invoice_details"] = invoice_details
        
        # All payments (not grouped by invoice)
        all_payments = Payment.objects.filter(invoice__in=invoices, is_reversal=False).select_related("invoice").order_by("-payment_date")
        ctx["payments"] = all_payments
        
        # Terms
        ctx["terms"] = Term.objects.filter(is_locked=False).order_by("-academic_year__name")
        
        return ctx


class OverdueAccountsListView(RoleRequiredMixin, TemplateView):
    """
    A4.1 — Dedicated Overdue Accounts screen.
    Shows all overdue invoices with sorting, color-coding by days overdue,
    bulk selection, and direct reminder actions.
    """
    template_name = "finance/overdue_accounts.html"
    allowed_roles = [
        UserRole.FINANCE_OFFICER,
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN_OFFICER,
    ]
    required_permission = "finance.view_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .models import InvoiceStatus
        from academics.models import Term

        today = timezone.now().date()

        # Filter params
        sort_by = self.request.GET.get("sort", "days_desc")
        class_filter = self.request.GET.get("class", "")
        days_min = self.request.GET.get("days_min", "")

        # Base queryset: overdue invoices with student
        overdue_qs = Invoice.objects.filter(
            status=InvoiceStatus.OVERDUE,
            student__isnull=False,
        ).select_related("student", "term").order_by("-created_at")

        if class_filter:
            overdue_qs = overdue_qs.filter(student__class_name=class_filter)

        # Build enriched list with balance and days overdue
        accounts = []
        total_billed = 0
        total_outstanding = 0
        for inv in overdue_qs:
            total_paid = inv.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
            balance = float(inv.total_due) - float(total_paid)
            days_overdue = (today - inv.due_date).days if inv.due_date else 0

            # Apply days filter
            if days_min and days_overdue < int(days_min):
                continue

            # Last reminder date
            last_reminder = inv.last_reminder_at

            accounts.append({
                "invoice": inv,
                "student_name": f"{inv.student.first_name} {inv.student.last_name}",
                "class_name": inv.student.class_name or "N/A",
                "total_billed": float(inv.total_due),
                "total_paid": float(total_paid),
                "balance": balance,
                "days_overdue": days_overdue,
                "days_color": "red" if days_overdue >= 30 else ("amber" if days_overdue >= 14 else "gold"),
                "last_reminder": last_reminder,
            })
            total_billed += float(inv.total_due)
            total_outstanding += balance

        # Sorting
        sort_key = "days_overdue"
        reverse = True
        if sort_by == "days_asc":
            reverse = False
        elif sort_by == "amount_desc":
            sort_key = "balance"
            reverse = True
        elif sort_by == "amount_asc":
            sort_key = "balance"
            reverse = False
        elif sort_by == "name_asc":
            sort_key = "student_name"
            reverse = False
        elif sort_by == "name_desc":
            sort_key = "student_name"
            reverse = True
        elif sort_by == "class_asc":
            sort_key = "class_name"
            reverse = False
        elif sort_by == "class_desc":
            sort_key = "class_name"
            reverse = True

        accounts.sort(key=lambda x: x.get(sort_key, 0) or 0, reverse=reverse)

        # Summary stats
        ctx.update({
            "accounts": accounts,
            "total_accounts": len(accounts),
            "total_billed": total_billed,
            "total_outstanding": total_outstanding,
            "total_paid": total_billed - total_outstanding,
            "collection_rate": round((total_billed - total_outstanding) / total_billed * 100, 1) if total_billed > 0 else 0,
        })

        # Filter options
        ctx["classes"] = Invoice.objects.filter(
            status=InvoiceStatus.OVERDUE, student__isnull=False
        ).values_list("student__class_name", flat=True).distinct().order_by("student__class_name")

        ctx["selected_sort"] = sort_by
        ctx["selected_class"] = class_filter
        ctx["days_min"] = days_min

        # Aging buckets summary
        bucket_1_14 = sum(a["balance"] for a in accounts if 1 <= a["days_overdue"] <= 14)
        bucket_15_30 = sum(a["balance"] for a in accounts if 15 <= a["days_overdue"] <= 30)
        bucket_30_plus = sum(a["balance"] for a in accounts if a["days_overdue"] > 30)
        ctx["aging_buckets"] = [
            {"label": "1\u201314 days", "count": sum(1 for a in accounts if 1 <= a["days_overdue"] <= 14), "total": bucket_1_14, "color": "gold"},
            {"label": "15\u201330 days", "count": sum(1 for a in accounts if 15 <= a["days_overdue"] <= 30), "total": bucket_15_30, "color": "amber"},
            {"label": "30+ days", "count": sum(1 for a in accounts if a["days_overdue"] > 30), "total": bucket_30_plus, "color": "red"},
        ]

        # Reminder log for recent activity
        from audit.models import AuditLog
        reminder_log = AuditLog.objects.filter(
            action_type__in=["REMINDER_SENT", "BULK_REMINDER_SENT"],
        ).order_by("-created_at")[:10]
        ctx["reminder_log"] = reminder_log

        # Check if HOS (read-only)
        ctx["is_hos"] = self.request.user.role == UserRole.HEAD_OF_SCHOOL
        ctx["today"] = today

        return ctx


class SendBulkReminderView(RoleRequiredMixin, View):
    """
    A4.2 â€” Send reminders to one or more overdue accounts.
    Supports bulk action from overdue accounts list.
    """
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_invoice"

    def post(self, request):
        from audit.models import log_event
        from communications.email_service import send_parent_notification
        from students.models import ParentGuardian, StudentGuardian

        invoice_ids = request.POST.getlist("invoice_ids[]")
        custom_message = request.POST.get("custom_message", "").strip()
        channel = request.POST.get("channel", "email")

        if not invoice_ids:
            messages.error(request, "No invoices selected for reminder.")
            return redirect("finance:overdue_accounts")

        invoices = Invoice.objects.filter(pk__in=invoice_ids).select_related("student")
        sent_count = 0
        errors = []

        for invoice in invoices:
            try:
                student = invoice.student
                if not student:
                    continue

                balance = float(invoice.total_due) - float(
                    invoice.payments.filter(is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                )

                # Get primary guardian
                guardian_link = StudentGuardian.objects.filter(
                    student=student, is_primary=True
                ).select_related("guardian").first()

                if not guardian_link:
                    errors.append(f"{student}: No primary guardian found")
                    continue

                guardian = guardian_link.guardian

                # Build message
                if custom_message:
                    msg_body = custom_message
                else:
                    msg_body = (
                        f"Dear Parent/Guardian, this is a friendly reminder that the fee balance "
                        f"for {student.first_name} {student.last_name} "
                        f"(TZS {balance:,.0f}) is now overdue by "
                        f"{(timezone.now().date() - invoice.due_date).days} days. "
                        f"Please arrange payment at your earliest convenience. "
                        f"Thank you for your continued support of Hodari Christian School."
                    )

                detail_url = request.build_absolute_uri(
                    reverse("finance:invoice_detail", kwargs={"pk": invoice.pk})
                )

                from core.email_templates import send_dynamic_email
                from core.models import SchoolSettings
                school_name = SchoolSettings.get_settings().school_name or "Hodari Christian School"
                _portal_overdue = request.build_absolute_uri("/parent/")
                tpl_context = {
                    "guardian_name": guardian.full_name,
                    "student_name": f"{student.first_name} {student.last_name}",
                    "currency": "TZS",
                    "balance": f"{balance:,.0f}",
                    "due_date": str(invoice.due_date) if invoice.due_date else "N/A",
                    "invoice_number": invoice.invoice_number or f"INV-{invoice.pk}",
                    "days_overdue": str((timezone.now().date() - invoice.due_date).days) if invoice.due_date else "N/A",
                    "school_name": school_name,
                    "portal_url": _portal_overdue,
                }
                db_sent = send_dynamic_email(
                    template_type="fee_reminder",
                    to_email=guardian.email,
                    context=tpl_context,
                    actor=request.user,
                )
                if not db_sent:
                    send_parent_notification(
                        guardian=guardian,
                        title="Fee Payment Reminder",
                        message=msg_body,
                        link=detail_url,
                        actor=request.user,
                    )

                # Update invoice reminder tracking
                invoice.last_reminder_at = timezone.now()
                invoice.last_reminder_type = channel
                invoice.save(update_fields=["last_reminder_at", "last_reminder_type", "updated_at"])

                # Log event
                log_event(
                    actor=request.user,
                    action_type="REMINDER_SENT",
                    model_name="Invoice",
                    object_id=invoice.pk,
                    description=f"Fee reminder sent to {guardian.full_name or guardian.email} for invoice {invoice.invoice_number} (balance: TZS {balance:,.0f})",
                    request=request,
                )

                sent_count += 1

            except Exception as e:
                errors.append(f"Invoice {invoice.pk}: {e}")

        if sent_count:
            log_event(
                actor=request.user,
                action_type="BULK_REMINDER_SENT",
                description=f"Bulk reminder sent to {sent_count} overdue accounts via {channel}",
                request=request,
            )
            messages.success(request, f"Reminders sent to {sent_count} parent(s).")
        if errors:
            for err in errors[:3]:
                messages.warning(request, f"Reminder error: {err}")
        if not sent_count and not errors:
            messages.warning(request, "No reminders were sent.")

        return redirect("finance:overdue_accounts")


class ReminderConfigurationView(RoleRequiredMixin, TemplateView):
    """
    A4.3 â€” View and configure auto-reminder settings.
    """
    template_name = "finance/reminder_configuration.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_feestructure"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        config = ReminderConfiguration.objects.first()
        if not config:
            config = ReminderConfiguration.objects.create()
        ctx["config"] = config
        ctx["today"] = timezone.now().date()
        return ctx

    def post(self, request):
        from audit.models import log_event

        config = ReminderConfiguration.objects.first()
        if not config:
            config = ReminderConfiguration()

        try:
            before = {
                "is_active": config.is_active,
                "first_reminder_days": config.first_reminder_days,
                "second_reminder_days": config.second_reminder_days,
                "final_reminder_days": config.final_reminder_days,
            }

            config.is_active = request.POST.get("is_active") == "on"
            config.reminder_frequency = request.POST.get("reminder_frequency", "weekly")
            config.reminder_type = request.POST.get("reminder_type", "email")
            config.first_reminder_days = int(request.POST.get("first_reminder_days", 7))
            config.second_reminder_days = int(request.POST.get("second_reminder_days", 14))
            config.final_reminder_days = int(request.POST.get("final_reminder_days", 30))
            config.reminder_template_sms = request.POST.get("reminder_template_sms", "")
            config.reminder_template_email = request.POST.get("reminder_template_email", "")
            config.auto_escalate_to_hos = request.POST.get("auto_escalate_to_hos") == "on"
            config.escalation_days = int(request.POST.get("escalation_days", 45))
            config.notify_finance_officer = request.POST.get("notify_finance_officer") == "on"
            config.updated_by = request.user
            config.save()

            after = {
                "is_active": config.is_active,
                "first_reminder_days": config.first_reminder_days,
                "second_reminder_days": config.second_reminder_days,
                "final_reminder_days": config.final_reminder_days,
            }

            log_event(
                actor=request.user,
                action_type="REMINDER_CONFIG_UPDATED",
                model_name="ReminderConfiguration",
                object_id=config.pk,
                description="Auto-reminder configuration updated",
                before=before,
                after=after,
                request=request,
            )

            messages.success(request, "Auto-reminder configuration saved.")
        except Exception as e:
            messages.error(request, f"Error saving configuration: {e}")

        return redirect("finance:reminder_configuration")


class AssessmentFeeInvoiceView(RoleRequiredMixin, TemplateView):
    """
    A2.2 â€” Create an assessment fee invoice for an admissions applicant.
    Called from the admissions pipeline when an applicant needs to pay
    their assessment fee. Creates an Invoice linked to the applicant.
    """
    template_name = "finance/assessment_fee_invoice.html"
    allowed_roles = [
        UserRole.FINANCE_OFFICER,
        UserRole.SUPER_ADMIN,
        UserRole.ADMIN_OFFICER,
    ]
    required_permission = "finance.add_invoice"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from admissions.models import Applicant

        applicant_id = self.kwargs.get("applicant_id")
        applicant = get_object_or_404(Applicant.objects.select_related("assessment"), pk=applicant_id)
        ctx["applicant"] = applicant

        from academics.models import GradeClass, Term
        from .models import FeeStructure

        assessment_fee = None
        if applicant.grade_applying_for:
            grade = GradeClass.objects.filter(name=applicant.grade_applying_for).first()
            if grade:
                from academics.utils import get_current_term
                current_term = get_current_term()
                if current_term:
                    fs = FeeStructure.objects.filter(term=current_term, class_name=grade.name).first()
                    if fs and fs.assessment_fee > 0:
                        assessment_fee = float(fs.assessment_fee)

        if not assessment_fee:
            assessment_fee = 50000

        ctx["assessment_fee"] = assessment_fee
        ctx["today"] = timezone.now().date()

        from .models import Invoice
        existing = Invoice.objects.filter(applicant=applicant).first()
        ctx["existing_invoice"] = existing
        return ctx

    def post(self, request, applicant_id):
        from admissions.models import Applicant
        from .models import Invoice, InvoiceLineItem, InvoiceStatus
        from audit.models import log_event
        from datetime import timedelta

        applicant = get_object_or_404(Applicant, pk=applicant_id)

        if Invoice.objects.filter(applicant=applicant).exists():
            messages.warning(request, f"An invoice already exists for {applicant.child_full_name}.")
            return redirect("admissions:applicant_detail", pk=applicant_id)

        amount = request.POST.get("amount", "0")
        due_date_str = request.POST.get("due_date", "")
        description = request.POST.get("description", "").strip()

        try:
            with transaction.atomic():
                last_id = (Invoice.objects.aggregate(m=Max("id"))["m"] or 0) + 1
                inv_number = f"ASSESS-{timezone.now().year}-{last_id:04d}"

                due_date = (
                    timezone.now().date() + timedelta(days=14)
                    if not due_date_str
                    else due_date_str
                )

                invoice = Invoice.objects.create(
                    applicant=applicant,
                    invoice_number=inv_number,
                    amount_due=float(amount),
                    discount_amount=0,
                    due_date=due_date,
                    status=InvoiceStatus.UNPAID,
                )

                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    description=description or f"Assessment fee â€” {applicant.grade_applying_for}",
                    amount=float(amount),
                )

                # Status transition is handled by RecordPaymentView.confirm_assessment_fee_paid()
                # when payment is actually received — not at invoice creation time

                log_event(
                    actor=request.user,
                    action_type="ASSESSMENT_INVOICE_CREATED",
                    model_name="Invoice",
                    object_id=invoice.pk,
                    description=f"Assessment fee invoice {inv_number} created for {applicant.child_full_name}",
                    request=request,
                )

                messages.success(
                    request,
                    f"Assessment fee invoice {inv_number} created for {applicant.child_full_name}. Amount: TZS {float(amount):,.0f}",
                )
                return redirect("finance:invoice_detail", pk=invoice.pk)

        except Exception as e:
            messages.error(request, f"Error creating assessment invoice: {e}")
            return redirect("admissions:applicant_detail", pk=applicant_id)


# =====================================================================
# A6 — Finance Period Management, Opening Balances & System Config
# =====================================================================

class FinancePeriodListView(RoleRequiredMixin, ListView):
    """
    A6 — List all finance periods with reconciliation status and summaries.
    """
    template_name = "finance/period_list.html"
    context_object_name = "periods"
    paginate_by = 30
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_financeperiod"

    def get_queryset(self):
        from django.db.models import Count, Sum, Subquery, OuterRef, DecimalField, Value
        from django.db.models.functions import Coalesce
        from .models import Payment

        # Annotate invoice count, total invoiced, and total collected
        # to avoid N+1 queries in the template  
        collected_sub = Subquery(
            Payment.objects.filter(
                invoice__period=OuterRef("pk"),
                is_reversal=False,
            ).values("invoice__period").annotate(
                total=Sum("amount")
            ).values("total")[:1]
        )

        return FinancePeriod.objects.annotate(
            invoice_count=Count("invoices", distinct=True),
            total_invoiced=Coalesce(Sum("invoices__total_due"), Value(0, output_field=DecimalField())),
            total_collected=Coalesce(collected_sub, Value(0, output_field=DecimalField())),
        ).order_by("-start_date", "-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.object_list if hasattr(self, 'object_list') else self.get_queryset()
        reconciled_count = qs.filter(is_reconciled=True).count()
        open_count = qs.filter(is_reconciled=False).count()
        ctx["reconciled_count"] = reconciled_count
        ctx["open_count"] = open_count
        return ctx


class FinancePeriodCreateView(RoleRequiredMixin, TemplateView):
    """
    A6 — Create or edit a finance period.
    """
    template_name = "finance/period_form.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_financeperiod"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        if pk:
            ctx["period"] = get_object_or_404(FinancePeriod, pk=pk)
            ctx["is_edit"] = True
        else:
            ctx["is_edit"] = False
            ctx["today"] = timezone.now().date()
        return ctx

    def post(self, request, pk=None):
        from audit.models import log_event

        name = request.POST.get("name", "").strip()
        start_date = request.POST.get("start_date", "")
        end_date = request.POST.get("end_date", "")
        notes = request.POST.get("notes", "")

        if not name:
            messages.error(request, "Period name is required.")
            return redirect("finance:period_create")

        try:
            if pk:
                period = get_object_or_404(FinancePeriod, pk=pk)
                if period.is_reconciled:
                    messages.error(request, "Cannot edit a reconciled period.")
                    return redirect("finance:period_list")
                period.name = name
                period.start_date = start_date or None
                period.end_date = end_date or None
                period.notes = notes
                period.save()
                messages.success(request, f'Period "{period.name}" updated.')
                log_event(
                    actor=request.user,
                    action_type="FINANCE_PERIOD_UPDATED",
                    model_name="FinancePeriod",
                    object_id=period.pk,
                    description=f"Updated finance period: {period.name}",
                    request=request,
                )
            else:
                period = FinancePeriod.objects.create(
                    name=name,
                    start_date=start_date or None,
                    end_date=end_date or None,
                    notes=notes,
                    created_by=request.user,
                )
                messages.success(request, f'Period "{period.name}" created.')
                log_event(
                    actor=request.user,
                    action_type="FINANCE_PERIOD_CREATED",
                    model_name="FinancePeriod",
                    object_id=period.pk,
                    description=f"Created finance period: {period.name}",
                    request=request,
                )
            return redirect("finance:period_list")
        except Exception as e:
            messages.error(request, f"Error saving period: {e}")
            return redirect("finance:period_create")


class FinancePeriodDetailView(RoleRequiredMixin, DetailView):
    """
    A6 — View finance period details with reconciliation status,
    opening balances, and financial summaries.
    """
    template_name = "finance/period_detail.html"
    model = FinancePeriod
    context_object_name = "period"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_financeperiod"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = self.object

        ctx["total_invoiced"] = period.total_invoiced()
        ctx["total_collected"] = period.total_collected()
        ctx["outstanding"] = period.total_invoiced() - period.total_collected()
        ctx["total_expenses"] = period.total_expenses()
        ctx["opening_balances"] = period.opening_balances.all()
        ctx["invoice_count"] = period.invoices.count()

        # Net position = collected - expenses + opening balances
        opening_total = period.opening_balances.aggregate(t=Sum("amount"))["t"] or 0
        ctx["opening_total"] = opening_total
        ctx["net_position"] = period.total_collected() - period.total_expenses() + opening_total
        return ctx


class FinancePeriodReconcileView(RoleRequiredMixin, View):
    """
    A6 — Reconcile (lock) a finance period.
    Once reconciled, invoices and payments in this period become read-only.
    """
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_financeperiod"

    def post(self, request, pk):
        from audit.models import log_event
        period = get_object_or_404(FinancePeriod, pk=pk)

        if period.is_reconciled:
            messages.error(request, "This period is already reconciled.")
            return redirect("finance:period_detail", pk=pk)

        try:
            period.is_reconciled = True
            period.reconciled_by = request.user
            period.reconciled_at = timezone.now()
            period.save(update_fields=["is_reconciled", "reconciled_by_id", "reconciled_at", "updated_at"])

            log_event(
                actor=request.user,
                action_type="FINANCE_PERIOD_RECONCILED",
                model_name="FinancePeriod",
                object_id=period.pk,
                description=f"Reconciled finance period: {period.name}",
                request=request,
            )
            messages.success(request, f'Period "{period.name}" has been reconciled.')
        except Exception as e:
            messages.error(request, f"Error reconciling period: {e}")

        return redirect("finance:period_detail", pk=pk)


class OpeningBalanceSetupView(RoleRequiredMixin, TemplateView):
    """
    A6 — Set up opening balances for a finance period.
    Allows adding multiple opening balance entries (receivables, payables, etc.).
    """
    template_name = "finance/opening_balance.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_openingbalance"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        period = get_object_or_404(FinancePeriod, pk=self.kwargs["pk"])
        ctx["period"] = period
        ctx["balance_types"] = OpeningBalanceType.choices
        ctx["balances"] = period.opening_balances.all().order_by("balance_type", "-created_at")

        # Totals
        total_debit = period.opening_balances.filter(is_debit=True).aggregate(t=Sum("amount"))["t"] or 0
        total_credit = period.opening_balances.filter(is_debit=False).aggregate(t=Sum("amount"))["t"] or 0
        ctx["total_debit"] = total_debit
        ctx["total_credit"] = total_credit
        ctx["net_balance"] = total_debit - total_credit

        return ctx

    def post(self, request, pk):
        from audit.models import log_event
        period = get_object_or_404(FinancePeriod, pk=pk)

        if period.is_reconciled:
            messages.error(request, "Cannot modify opening balances in a reconciled period.")
            return redirect("finance:period_detail", pk=pk)

        action = request.POST.get("action", "add")

        if action == "add":
            balance_type = request.POST.get("balance_type", "")
            description = request.POST.get("description", "").strip()
            amount_str = request.POST.get("amount", "")
            is_debit = request.POST.get("is_debit", "true") == "true"
            reference = request.POST.get("reference", "")
            notes = request.POST.get("notes", "")

            if not description:
                messages.error(request, "Description is required.")
                return redirect("finance:opening_balance", pk=pk)

            try:
                amount = float(amount_str)
                if amount <= 0:
                    raise ValueError("Amount must be positive")

                OpeningBalance.objects.create(
                    period=period,
                    balance_type=balance_type,
                    description=description,
                    amount=amount,
                    is_debit=is_debit,
                    reference=reference,
                    notes=notes,
                    created_by=request.user,
                )
                messages.success(request, "Opening balance entry added.")
                log_event(
                    actor=request.user,
                    action_type="OPENING_BALANCE_ADDED",
                    model_name="OpeningBalance",
                    description=f"Added opening balance TZS {amount:,.0f} for {period.name}",
                    request=request,
                )
            except Exception as e:
                messages.error(request, f"Error adding entry: {e}")

        elif action == "delete":
            entry_id = request.POST.get("entry_id")
            try:
                entry = get_object_or_404(OpeningBalance, pk=entry_id, period=period)
                entry.delete()
                messages.success(request, "Opening balance entry removed.")
            except Exception as e:
                messages.error(request, f"Error removing entry: {e}")

        return redirect("finance:opening_balance", pk=pk)


class FinanceConfigView(RoleRequiredMixin, TemplateView):
    """
    A6 — System-wide finance configuration settings.
    Manages defaults for invoices, payments, reminders, and late fees.
    """
    template_name = "finance/finance_config.html"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.change_financeconfig"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["config"] = FinanceConfig.get_config()
        ctx["payment_methods"] = PaymentMethod.choices
        return ctx

    def post(self, request):
        from audit.models import log_event
        config = FinanceConfig.get_config()

        try:
            before = {
                "invoice_prefix": config.invoice_prefix,
                "invoice_due_days": config.invoice_due_days,
                "late_fee_percentage": str(config.late_fee_percentage),
                "auto_reminder_enabled": str(config.auto_reminder_enabled),
                "default_payment_method": config.default_payment_method,
            }

            config.invoice_prefix = request.POST.get("invoice_prefix", "INV")
            config.invoice_due_days = int(request.POST.get("invoice_due_days", 30))
            config.invoice_terms = request.POST.get("invoice_terms", "")
            config.default_payment_method = request.POST.get("default_payment_method", PaymentMethod.CASH)
            config.reminder_grace_days = int(request.POST.get("reminder_grace_days", 7))
            config.auto_reminder_enabled = request.POST.get("auto_reminder_enabled") == "on"
            config.finance_officer_email = request.POST.get("finance_officer_email", "")
            config.late_fee_percentage = float(request.POST.get("late_fee_percentage", 0))
            config.late_fee_max_days = int(request.POST.get("late_fee_max_days", 90))
            config.school_currency = request.POST.get("school_currency", "TZS")
            config.updated_by = request.user
            config.save()

            log_event(
                actor=request.user,
                action_type="FINANCE_CONFIG_UPDATED",
                model_name="FinanceConfig",
                description="Updated finance system configuration",
                before=before,
                after={
                    "invoice_prefix": config.invoice_prefix,
                    "invoice_due_days": config.invoice_due_days,
                    "late_fee_percentage": str(config.late_fee_percentage),
                    "auto_reminder_enabled": str(config.auto_reminder_enabled),
                    "default_payment_method": config.default_payment_method,
                },
                request=request,
            )
            messages.success(request, "Finance configuration saved.")
        except Exception as e:
            messages.error(request, f"Error saving configuration: {e}")

        return redirect("finance:finance_config")


# =====================================================================
# A7 — Budget & Expense Enhancements
# =====================================================================

class BudgetDetailView(RoleRequiredMixin, DetailView):
    """
    A7 — Budget detail showing allocation vs actual spending,
    expense breakdown, and remaining budget analysis.
    """
    model = Budget
    template_name = "finance/budget_detail.html"
    context_object_name = "budget"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_budget"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        budget = self.object

        # Actual spending (approved expenses)
        actual = budget.spent_amount()
        allocated = budget.allocated_amount
        remaining = budget.remaining_amount()
        utilization = budget.utilization_pct()

        ctx.update({
            "actual": actual,
            "allocated": allocated,
            "remaining": remaining,
            "utilization": utilization,
            "is_over_budget": actual > allocated,
            "is_low_buffer": remaining < allocated / 4 if allocated > 0 else False,
        })

        # Expense breakdown — approved expenses against this budget
        expenses = Expense.objects.filter(
            term=budget.term,
            category=budget.category,
            status=ExpenseStatus.APPROVED,
        ).select_related("created_by", "approved_by").order_by("-expense_date")

        ctx["budget_expenses"] = expenses
        ctx["expense_count"] = expenses.count()

        # Monthly breakdown for trend analysis
        from django.db.models.functions import TruncMonth
        monthly = expenses.annotate(
            month=TruncMonth("expense_date")
        ).values("month").annotate(
            total=Sum("amount")
        ).order_by("month")

        ctx["monthly_breakdown"] = list(monthly)

        # Related budgets — other categories for the same term
        ctx["related_budgets"] = Budget.objects.filter(
            term=budget.term
        ).exclude(pk=budget.pk).order_by("category")

        # Projection — if days elapsed in term > 50% and spending > 75% of budget, flag
        if budget.term.start_date and budget.term.end_date:
            from datetime import date
            today = date.today()
            term_days = (budget.term.end_date - budget.term.start_date).days
            elapsed = (today - budget.term.start_date).days
            if term_days > 0 and elapsed > 0:
                pct_elapsed = round((elapsed / term_days) * 100, 1)
                projected = round((actual / max(elapsed, 1)) * term_days, 0)
                ctx["pct_elapsed"] = pct_elapsed
                ctx["projected_total"] = projected
                ctx["projected_overrun"] = projected > allocated
                ctx["projected_overrun_amount"] = max(0, projected - allocated)

        return ctx


class BudgetVsActualReportView(RoleRequiredMixin, TemplateView):
    """
    A7 — Budget vs Actual comparison report.
    Shows all budgets with allocation vs actual spending, variance, and utilization.
    Supports CSV export via ?format=csv.
    """
    template_name = "finance/budget_vs_actual.html"
    allowed_roles = _FINANCE_MODULE_ROLES
    required_permission = "finance.view_budget"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import Term
        from django.utils import timezone

        # Selected term
        term_id = self.request.GET.get("term")
        if term_id:
            term = get_object_or_404(Term, pk=term_id)
        else:
            today = timezone.now().date()
            from academics.utils import get_current_term
            term = get_current_term()

        ctx["selected_term"] = term
        ctx["terms"] = Term.objects.all().order_by("-academic_year__name")

        # Budget vs Actual data
        budgets = Budget.objects.filter(term=term).order_by("category")

        budget_data = []
        total_allocated = 0
        total_spent = 0

        for b in budgets:
            allocated = b.allocated_amount
            spent = b.spent_amount()
            variance = allocated - spent
            utilization = b.utilization_pct()

            total_allocated += allocated
            total_spent += spent

            budget_data.append({
                "budget": b,
                "category_code": b.category,
                "category_label": b.get_category_display(),
                "allocated": allocated,
                "spent": spent,
                "variance": variance,
                "utilization": utilization,
                "status": "over" if spent > allocated else "under" if variance > 0 else "exact",
                "is_frozen": b.is_frozen,
            })

        ctx["budget_data"] = budget_data
        ctx["total_allocated"] = total_allocated
        ctx["total_spent"] = total_spent
        ctx["total_variance"] = total_allocated - total_spent
        ctx["overall_utilization"] = round((total_spent / total_allocated * 100), 1) if total_allocated > 0 else 0
        ctx["over_budget_count"] = sum(1 for b in budget_data if b["status"] == "over")
        ctx["is_hos"] = self.request.user.role == UserRole.HEAD_OF_SCHOOL
        ctx["today"] = timezone.now().date()

        return ctx

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "csv":
            return self._render_csv(context)
        return super().render_to_response(context, **response_kwargs)

    def _render_csv(self, context):
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'inline; filename="budget-vs-actual-{context["selected_term"].name if context["selected_term"] else "report"}.csv"'
        response.write("\ufeff")  # BOM for Excel

        writer = csv.writer(response)
        writer.writerow(["Category", "Allocated (TZS)", "Spent (TZS)", "Variance (TZS)", "Utilization %", "Status"])

        for item in context["budget_data"]:
            writer.writerow([
                item["category_label"],
                item["allocated"],
                item["spent"],
                item["variance"],
                item["utilization"],
                item["status"].title(),
            ])

        # Totals row
        writer.writerow([
            "TOTAL",
            context["total_allocated"],
            context["total_spent"],
            context["total_variance"],
            context["overall_utilization"],
            "",
        ])

        return response


class ExpenseBatchApprovalView(RoleRequiredMixin, TemplateView):
    """
    A7 — Batch approval view for pending expenses.
    Lists all pending expenses grouped by category, with select-all and
    bulk approve/reject actions.
    """
    template_name = "finance/expense_batch_approval.html"
    allowed_roles = [
        UserRole.HEAD_OF_SCHOOL,
        UserRole.SUPER_ADMIN,
    ]
    required_permission = "finance.change_expense"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # All pending expenses
        pending = Expense.objects.filter(
            status=ExpenseStatus.PENDING
        ).select_related("created_by", "term").order_by("-expense_date")

        ctx["pending_expenses"] = pending
        ctx["pending_count"] = pending.count()
        ctx["pending_total"] = pending.aggregate(t=Sum("amount"))["t"] or 0

        # Group by category for better overview
        categories = {}
        for exp in pending:
            cat = exp.category
            if cat not in categories:
                categories[cat] = {
                    "label": exp.get_category_display(),
                    "expenses": [],
                    "total": 0,
                    "count": 0,
                }
            categories[cat]["expenses"].append(exp)
            categories[cat]["total"] += exp.amount
            categories[cat]["count"] += 1

        ctx["categories"] = categories
        ctx["category_count"] = len(categories)

        # Recently approved/rejected (last 10)
        recent_action = Expense.objects.filter(
            status__in=[ExpenseStatus.APPROVED, ExpenseStatus.REJECTED]
        ).exclude(approved_by__isnull=True).select_related(
            "created_by", "approved_by"
        ).order_by("-approved_at")[:10]

        ctx["recent_actions"] = recent_action

        return ctx

    def post(self, request):
        from audit.models import log_event

        action = request.POST.get("action", "")
        expense_ids = request.POST.getlist("expense_ids[]")
        rejection_reason = request.POST.get("rejection_reason", "").strip()

        if not expense_ids:
            messages.error(request, "No expenses selected.")
            return redirect("finance:expense_batch_approval")

        if action not in ("approve", "reject"):
            messages.error(request, "Invalid action.")
            return redirect("finance:expense_batch_approval")

        if action == "reject" and not rejection_reason:
            messages.error(request, "A reason is required to reject expenses.")
            return redirect("finance:expense_batch_approval")

        expenses = Expense.objects.filter(pk__in=expense_ids, status=ExpenseStatus.PENDING)
        processed = 0
        errors = []

        for exp in expenses:
            try:
                with transaction.atomic():
                    if action == "approve":
                        exp.status = ExpenseStatus.APPROVED
                        exp.approved_by = request.user
                        exp.approved_at = timezone.now()
                        exp.save(update_fields=["status", "approved_by_id", "approved_at", "updated_at"])

                        log_event(
                            actor=request.user,
                            action_type="EXPENSE_APPROVED",
                            model_name="Expense",
                            object_id=exp.pk,
                            description=f"Batch approved expense {exp.pk} for TZS {exp.amount:,.0f}",
                            request=request,
                        )
                    else:
                        exp.status = ExpenseStatus.REJECTED
                        exp.approved_by = request.user
                        exp.approved_at = timezone.now()
                        exp.rejection_reason = rejection_reason
                        exp.save(update_fields=[
                            "status", "approved_by_id", "approved_at",
                            "rejection_reason", "updated_at",
                        ])

                        log_event(
                            actor=request.user,
                            action_type="EXPENSE_REJECTED",
                            model_name="Expense",
                            object_id=exp.pk,
                            description=f"Batch rejected expense {exp.pk}: {rejection_reason[:100]}",
                            request=request,
                        )

                    processed += 1
            except Exception as e:
                errors.append(f"Expense {exp.pk}: {e}")

        if processed:
            msg = f"Batch {action}d {processed} expense(s)."
            if errors:
                msg += f" {len(errors)} error(s)."
            messages.success(request, msg)

        return redirect("finance:expense_batch_approval")


# ========================================================================
# A5 — Concession (fee discount / scholarship) management
# ========================================================================

class ConcessionListView(RoleRequiredMixin, ListView):
    """List concessions — FO sees own, SA sees all."""
    model = Concession
    template_name = "finance/concession_list.html"
    context_object_name = "concessions"
    paginate_by = 25
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.view_concession"

    def get_queryset(self):
        qs = super().get_queryset().select_related("student", "term", "approved_by", "created_by")
        if self.request.user.role != UserRole.SUPER_ADMIN:
            qs = qs.filter(created_by=self.request.user)
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_filters"] = ConcessionStatus.choices
        return ctx


class ConcessionCreateView(RoleRequiredMixin, CreateView):
    """FO + SA: create a fee concession / discount / scholarship."""
    model = Concession
    template_name = "finance/concession_form.html"
    fields = [
        "student", "term", "concession_type", "reason",
        "discount_mode", "discount_value", "notes",
        "valid_from", "valid_until",
    ]
    success_url = "/finance/concessions/"
    allowed_roles = [UserRole.FINANCE_OFFICER, UserRole.SUPER_ADMIN]
    required_permission = "finance.add_concession"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="CONCESSION_CREATED",
            model_name="Concession",
            object_id=self.object.pk,
            description=(
                f"Concession created: {self.object.get_concession_type_display()} "
                f"for {self.object.student} — "
                f"TZS {self.object.discount_value:,.0f}"
            ),
            request=self.request,
        )
        messages.success(
            self.request,
            f"Concession created for {self.object.student}. Pending approval."
        )
        return response


class ConcessionApproveView(RoleRequiredMixin, View):
    """SA-only: approve or reject a pending concession."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "finance.change_concession"

    def post(self, request, pk):
        concession = get_object_or_404(Concession, pk=pk)
        if concession.status != ConcessionStatus.PENDING:
            messages.error(request, "Only pending concessions can be approved or rejected.")
            return redirect("finance:concession_list")

        action = request.POST.get("action")
        if action == "approve":
            concession.status = ConcessionStatus.APPROVED
            concession.approved_by = request.user
            concession.approved_at = timezone.now()
            concession.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="CONCESSION_APPROVED",
                model_name="Concession",
                object_id=concession.pk,
                description=(
                    f"Concession approved: {concession.get_concession_type_display()} "
                    f"for {concession.student} — TZS {concession.discount_value:,.0f}"
                ),
                request=request,
            )
            messages.success(request, f"Concession for {concession.student} approved.")
        elif action == "reject":
            reason = request.POST.get("reason", "").strip()
            if not reason:
                messages.error(request, "Rejection reason is required.")
                return redirect("finance:concession_list")
            concession.status = ConcessionStatus.REJECTED
            concession.approved_by = request.user
            concession.approved_at = timezone.now()
            concession.rejection_reason = reason
            concession.save(update_fields=[
                "status", "approved_by", "approved_at",
                "rejection_reason", "updated_at",
            ])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="CONCESSION_REJECTED",
                model_name="Concession",
                object_id=concession.pk,
                description=(
                    f"Concession rejected: {concession.get_concession_type_display()} "
                    f"for {concession.student}. Reason: {reason[:200]}"
                ),
                request=request,
            )
            messages.warning(request, f"Concession for {concession.student} rejected.")
        else:
            messages.error(request, "Invalid action.")

        return redirect("finance:concession_list")

