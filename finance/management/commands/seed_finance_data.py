"""
Management command to seed finance module with test data.
Usage: python manage.py seed_finance_data
"""
from datetime import date, timedelta
from decimal import Decimal


from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed finance module with test data (expenses, fee structures, unmatched payments)"

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        from academics.models import Term
        from finance.models import (
            Expense, ExpenseCategory,
            FeeStructure, FeeStructureItem, FeeCategory,
            UnmatchedPayment,
        )



        finance_user = User.objects.filter(role="finance_officer").first() or User.objects.filter(is_superuser=True).first()
        if not finance_user:
            raise CommandError("No finance officer or superuser found. Create users first.")

        terms = list(Term.objects.filter(is_locked=False).order_by("-start_date")[:2])
        if not terms:
            raise CommandError("No unlocked terms found. Create an academic term first.")

        # ---- 1. Seed Fee Structures ----
        classes = [
            "Pre-K", "Pre-Unit", "Nursery", "Kindergarten",
            "ECD Butterflies", "ECD Stars", "ECD Sunflowers",
            "Grade 1", "Grade 2", "Grade 3", "Grade 4",
            "Grade 5", "Grade 6", "Grade 7", "Grade 8",
            "Form 1", "Form 2",
        ]

        structures_created = 0
        items_created = 0
        for term in terms:
            for cls in classes:
                fs, created = FeeStructure.objects.get_or_create(term=term, class_name=cls, defaults={"is_active": True})
                if created or not fs.items.exists():
                    if created:
                        structures_created += 1
                    # Clear and recreate items
                    fs.items.all().delete()
                    items = [
                        ("tuition", "Tuition Fee", Decimal("850000.00")),
                        ("activity", "Activity Fee", Decimal("120000.00")),
                        ("uniform", "Books & Stationery", Decimal("75000.00")),
                    ]
                    if cls in ("Grade 7", "Grade 8", "Form 1", "Form 2"):
                        items.append(("other", "Science Lab Levy", Decimal("50000.00")))
                    if cls in ("Pre-K", "Pre-Unit", "Nursery", "Kindergarten"):
                        items = [
                            ("tuition", "Tuition Fee", Decimal("650000.00")),
                            ("activity", "Activity & Play Materials", Decimal("85000.00")),
                            ("uniform", "Books & Stationery", Decimal("45000.00")),
                        ]
                    for cat, desc, amt in items:
                        FeeStructureItem.objects.create(structure=fs, category=cat, description=desc, amount=amt)
                        items_created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {structures_created} fee structures with {items_created} items."))

        # ---- 2. Seed Expenses ----
        today = date.today()
        expense_data = [
            # (category, amount, description, vendor, method, date_offset, is_reimbursement)
            ("utilities", "850000.00", "Monthly electricity bill - TANESCO", "TANESCO", "bank_transfer", 5, False),
            ("utilities", "320000.00", "Water bill - DAWASA", "DAWASA", "bank_transfer", 5, False),
            ("utilities", "180000.00", "Internet connectivity (3 months)", "TTCL", "bank_transfer", 10, False),
            ("salary", "3500000.00", "Staff salaries - April 2026", "Staff Payroll", "bank_transfer", 1, False),
            ("salary", "3500000.00", "Staff salaries - May 2026", "Staff Payroll", "bank_transfer", 1, False),
            ("supplies", "245000.00", "Office stationery & printing paper", "Stationery Mart", "cash", 3, False),
            ("supplies", "180000.00", "Classroom chalk, markers & whiteboard pens", "EduSupplies Ltd", "cash", 8, False),
            ("supplies", "560000.00", "Science lab consumables", "LabTech Supplies", "bank_transfer", 12, False),
            ("maintenance", "450000.00", "Plumbing repairs - boys dormitory", "M. Juma Plumbing", "cash", 6, False),
            ("maintenance", "780000.00", "Classroom desk repairs (40 desks)", "Furniture Works", "bank_transfer", 9, False),
            ("transport", "320000.00", "School bus fuel - April", "Total Energies", "cash", 4, False),
            ("transport", "285000.00", "School bus fuel - May", "Total Energies", "cash", 2, False),
            ("transport", "120000.00", "Staff transport allowance", "Staff", "cash", 1, True),
            ("catering", "950000.00", "Catering supplies - April", "Food Distributors Ltd", "bank_transfer", 3, False),
            ("catering", "890000.00", "Catering supplies - May", "Food Distributors Ltd", "bank_transfer", 1, False),
            ("events", "350000.00", "Sports Day - trophies & refreshments", "Event Supplies", "cash", 15, False),
            ("events", "120000.00", "Graduation ceremony decorations", "Party World", "cash", 20, False),
            ("professional", "600000.00", "Audit services - FY 2025/2026", "KPMG Tanzania", "bank_transfer", 7, False),
            ("technology", "1200000.00", "Computer lab maintenance & licensing", "Tech Solutions Ltd", "bank_transfer", 10, False),
            ("technology", "450000.00", "School management software hosting", "CloudServ", "bank_transfer", 5, False),
            ("marketing", "280000.00", "Open Day promotional materials", "PrintPro", "cash", 14, False),
            ("insurance", "750000.00", "School property & liability insurance", "Jubilee Insurance", "bank_transfer", 11, False),
            ("tax", "420000.00", "Withholding tax remittance - Q1 2026", "TRA", "bank_transfer", 8, False),
            ("other", "90000.00", "Miscellaneous - staff tea & refreshments", "Various", "cash", 2, False),
            ("other", "65000.00", "First aid kit restocking", "Pharmacy", "cash", 6, False),
        ]

        expenses_created = 0
        for cat, amt, desc, vendor, method, days_ago, is_reimb in expense_data:
            exp_date = today - timedelta(days=days_ago)
            Expense.objects.create(
                category=cat,
                amount=amt,
                description=desc,
                expense_date=exp_date,
                payment_method=method,
                vendor=vendor,
                created_by=finance_user,
                is_reimbursement=is_reimb,
            )
            expenses_created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {expenses_created} expenses."))

        # ---- 3. Seed Unmatched Payments ----
        unmatched_data = [
            ("250000.00", today - timedelta(days=2), "mobile_money", "M-PESA-REF-88234", "M-PESA payment from unknown sender"),
            ("180000.00", today - timedelta(days=5), "bank_transfer", "TXN-2026-04321", "Bank deposit - no student name on reference"),
            ("95000.00", today - timedelta(days=7), "mobile_money", "TIGO-772345", "Tigo Pesa payment, unknown student"),
            ("500000.00", today - timedelta(days=1), "cash", "CASH-26-05", "Cash deposit at bank - receipt only"),
        ]

        for amt, p_date, method, ref, details in unmatched_data:
            UnmatchedPayment.objects.create(
                amount=amt,
                payment_date=p_date,
                method=method,
                reference=ref,
                bank_statement_details=details,
            )

        self.stdout.write(self.style.SUCCESS(f"Created {len(unmatched_data)} unmatched payments."))

        # ---- 4. Seed Invoices & Payments ----
        from students.models import Student
        from finance.models import Invoice, InvoiceLineItem, Payment, InvoiceStatus, FinancePeriod

        # Clean old seed data first for idempotency
        self.stdout.write(self.style.WARNING("Cleaning previous seed invoices/payments..."))
        Payment.objects.filter(reference__startswith="SEED-PAY-").delete()
        InvoiceLineItem.objects.filter(invoice__invoice_number__startswith="SEED-INV-").delete()
        Invoice.objects.filter(invoice_number__startswith="SEED-INV-").delete()

        self._seed_invoices_and_payments(finance_user, today)

        # ---- Summary ----
        expense_count = Expense.objects.count()
        structure_count = FeeStructure.objects.count()
        item_count = FeeStructureItem.objects.count()
        unmatched_count = UnmatchedPayment.objects.filter(is_resolved=False).count()
        invoice_count = Invoice.objects.count()
        payment_count = Payment.objects.filter(is_reversal=False).count()

        self.stdout.write(self.style.SUCCESS(f"""
Done. Finance test data summary:
  - Fee structures: {structure_count} ({item_count} items)
  - Expenses: {expense_count}
  - Unmatched payments: {unmatched_count} pending
  - Invoices: {invoice_count}
  - Payments: {payment_count}
"""))

    def _seed_invoices_and_payments(self, finance_user, today):
        """Create invoices and payments to populate collection reports & aging."""
        from students.models import Student
        from finance.models import (
            Invoice, InvoiceLineItem, Payment, FinancePeriod,
            FeeStructure, InvoiceStatus,
        )
        from academics.models import Term

        # Use Term 2 (2026) — current term
        term = Term.objects.filter(is_locked=False).order_by("-start_date").first()
        if not term:
            self.stdout.write(self.style.WARNING("No unlocked term found. Skipping invoice seeding."))
            return

        try:
            period = FinancePeriod.objects.first()
        except FinancePeriod.DoesNotExist:
            self.stdout.write(self.style.WARNING("No finance period found. Skipping invoice seeding."))
            return

        # Select ~20 students across diverse classes
        target_classes = [
            "Pre-K", "Pre-Unit", "Nursery", "Kindergarten",
            "ECD Butterflies", "ECD Stars", "ECD Sunflowers",
            "Grade 1", "Grade 2", "Grade 3", "Grade 4",
            "Grade 5", "Grade 6", "Grade 7", "Grade 8",
            "Form 1", "Form 2",
        ]

        students = []
        for cls in target_classes:
            qs = Student.objects.filter(class_name=cls).order_by("?")[:2]
            students.extend(qs)

        students = students[:20]
        if not students:
            self.stdout.write(self.style.WARNING("No students found. Skipping invoice seeding."))
            return

        invoices_created = 0
        payments_created = 0
        line_items_created = 0

        for idx, student in enumerate(students):
            # Find fee structure for this class and term
            fee_structure = FeeStructure.objects.filter(term=term, class_name=student.class_name).prefetch_related("items").first()
            if not fee_structure:
                self.stdout.write(self.style.WARNING(f"No fee structure for {student.class_name}. Skipping {student}."))
                continue

            items = list(fee_structure.items.all())
            if not items:
                continue

            total_amount = sum(item.amount for item in items).quantize(Decimal('0.01'))
            due_date = term.end_date or (today + timedelta(days=30))

            # Determine status based on index
            status_cycle = idx % 10
            if status_cycle < 3:
                # PAID — due date in past, full payment
                inv_due_date = due_date - timedelta(days=60) if isinstance(due_date, date) else today - timedelta(days=30)
                status = InvoiceStatus.PAID
                is_finalized = True
            elif status_cycle < 5:
                # PARTIAL — due date in past, partial payment
                inv_due_date = due_date - timedelta(days=30) if isinstance(due_date, date) else today - timedelta(days=15)
                status = InvoiceStatus.PARTIAL
                is_finalized = False
            elif status_cycle < 8:
                # UNPAID — due date in future
                inv_due_date = today + timedelta(days=14 + idx)
                status = InvoiceStatus.UNPAID
                is_finalized = False
            else:
                # OVERDUE — due date well in past
                days_overdue = 7 + (idx * 3)
                inv_due_date = today - timedelta(days=min(days_overdue, 90))
                status = InvoiceStatus.OVERDUE
                is_finalized = False

            try:
                invoice = Invoice.objects.create(
                    student=student,
                    period=period,
                    term=term,
                    invoice_number=f"SEED-INV-{term.id}-{student.id}-{idx}",
                    amount_due=total_amount,
                    due_date=inv_due_date,
                    status=status,
                    is_finalized=is_finalized,
                )
                # Create line items
                for item in items:
                    InvoiceLineItem.objects.create(
                        invoice=invoice,
                        description=item.description,
                        amount=item.amount,
                    )
                    line_items_created += 1

                invoices_created += 1

                # Create payments for PAID or PARTIAL invoices
                if status == InvoiceStatus.PAID:
                    Payment.objects.create(
                        invoice=invoice,
                        amount=total_amount,
                        method="bank_transfer",
                        reference=f"SEED-PAY-FULL-{invoice.id}",
                        created_by=finance_user,
                    )
                    payments_created += 1
                elif status == InvoiceStatus.PARTIAL:
                    partial_amount = (total_amount * Decimal("0.6")).quantize(Decimal('0.01'))
                    Payment.objects.create(
                        invoice=invoice,
                        amount=partial_amount,
                        method="mobile_money",
                        reference=f"SEED-PAY-PART-{invoice.id}",
                        created_by=finance_user,
                    )
                    payments_created += 1

            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Error creating invoice for {student}: {e}"))
                continue

        self.stdout.write(self.style.SUCCESS(f"Created {invoices_created} invoices, {line_items_created} line items, {payments_created} payments."))
