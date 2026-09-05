"""
Step 6 — Finance: Fee Structures, Invoices & Payments
Run: python manage.py seed_finance
"""
import json
import random
from decimal import Decimal
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from academics.models import Term, GradeClass, Department
from finance.models import (
    FinancePeriod, FeeStructure, FeeStructureItem, FeeStructureStatus,
    FeeCategory, Invoice, InvoiceLineItem, InvoiceStatus,
    Payment, PaymentMethod,
)
from students.models import Student
from users.models import User, UserRole

TUITION_FEES = {
    "Pre-Kindergarten": 350000,
    "Kindergarten": 350000,
    "Pre-School": 400000,
    "ABC Class": 450000,
    "Grade 1": 500000,
    "Grade 2": 500000,
    "Grade 3": 550000,
    "Grade 4": 550000,
    "Grade 5": 600000,
    "Grade 6": 650000,
    "Grade 7": 700000,
    "Grade 8": 700000,
    "Grade 9": 750000,
}
ADDITIONAL_FEES = [
    ("Activity fee", 50000, FeeCategory.ACTIVITY),
    ("ICT levy", 30000, FeeCategory.OTHER),
    ("Sports & games", 25000, FeeCategory.ACTIVITY),
]


class Command(BaseCommand):
    help = "Seed finance data: fee structures, invoices, and payments"

    def handle(self, *args, **options):
        actor = User.objects.filter(is_superuser=True).first() or User.objects.filter(role=UserRole.FINANCE_OFFICER).first()
        self.stdout.write(f"Using actor: {actor.username}")

        terms = list(Term.objects.all().order_by("id"))
        students = list(Student.objects.filter(is_archived=False).order_by("id"))
        grade_classes = {gc.name: gc for gc in GradeClass.objects.all()}
        self.stdout.write(f"Terms: {len(terms)}, Students: {len(students)}")

        # 1. FINANCE PERIODS
        self.stdout.write("\n--- Finance Periods ---")
        period_names = ["Term 1 2025/2026", "Term 2 2025/2026", "Term 3 2025/2026"]
        period_dates = [
            (date(2026, 1, 12), date(2026, 3, 27)),
            (date(2026, 4, 6), date(2026, 6, 26)),
            (date(2026, 9, 7), date(2026, 12, 11)),
        ]
        finance_periods = []
        for i, term in enumerate(terms):
            name = period_names[i] if i < len(period_names) else f"Term {i+1}"
            sd, ed = period_dates[i] if i < len(period_dates) else (None, None)
            fp, created = FinancePeriod.objects.get_or_create(
                name=name,
                defaults={"start_date": sd, "end_date": ed, "is_reconciled": False, "created_by": actor},
            )
            finance_periods.append(fp)
            self.stdout.write(f"  {'Created' if created else 'Exists'}: {fp.name}")

        # 2. FEE STRUCTURES
        self.stdout.write("\n--- Fee Structures ---")
        fs_count = 0
        for term in terms:
            for class_name, tuition in TUITION_FEES.items():
                gc = grade_classes.get(class_name)
                if not gc:
                    continue
                fs, created = FeeStructure.objects.get_or_create(
                    term=term,
                    class_name=class_name,
                    defaults={
                        "status": FeeStructureStatus.PUBLISHED,
                        "late_pickup_charge": Decimal("5000"),
                        "sibling_discount_mode": "percentage",
                        "sibling_discount_value": Decimal("10"),
                        "assessment_fee": Decimal("50000"),
                    },
                )
                if created:
                    FeeStructureItem.objects.get_or_create(
                        structure=fs, category=FeeCategory.TUITION,
                        description=f"Tuition fee ({class_name})",
                        defaults={"amount": Decimal(str(tuition))},
                    )
                    for desc, amt, cat in ADDITIONAL_FEES:
                        FeeStructureItem.objects.get_or_create(
                            structure=fs, category=cat, description=desc,
                            defaults={"amount": Decimal(str(amt))},
                        )
                    fs_count += 1
        self.stdout.write(f"  Created {fs_count} new fee structures")

        # 3. INVOICES
        self.stdout.write("\n--- Invoices ---")
        invoice_count = 0
        for student in students:
            class_name = student.class_name
            tuition = TUITION_FEES.get(class_name, 500000)
            for term_idx, term in enumerate(terms):
                fp = finance_periods[term_idx] if term_idx < len(finance_periods) else finance_periods[-1]
                inv, created = Invoice.objects.get_or_create(
                    student=student,
                    term=term,
                    defaults={
                        "period": fp,
                        "amount_due": Decimal(str(tuition + 105000)),
                        "discount_amount": Decimal("0"),
                        "total_due": Decimal(str(tuition + 105000)),
                        "due_date": date(2026, term_idx * 4 + 2, 15) if term_idx < 3 else date(2026, 10, 15),
                        "status": InvoiceStatus.UNPAID,
                        "invoice_number": f"INV-{term.id:02d}-{student.admission_no}",
                        "is_finalized": True,
                    },
                )
                if created:
                    InvoiceLineItem.objects.get_or_create(
                        invoice=inv, description=f"Tuition ({class_name})",
                        defaults={"amount": Decimal(str(tuition))},
                    )
                    for desc, amt, _cat in ADDITIONAL_FEES:
                        InvoiceLineItem.objects.get_or_create(
                            invoice=inv, description=desc,
                            defaults={"amount": Decimal(str(amt))},
                        )
                    invoice_count += 1
        self.stdout.write(f"  Created {invoice_count} new invoices")

        # 4. PAYMENTS
        self.stdout.write("\n--- Payments ---")
        all_invoices = list(Invoice.objects.all())
        random.shuffle(all_invoices)
        pay_count = int(len(all_invoices) * 0.6)
        payment_count = 0
        for inv in all_invoices[:pay_count]:
            if inv.term_id == terms[0].id:
                amount = inv.total_due
                status = InvoiceStatus.PAID
            elif inv.term_id == terms[1].id:
                amount = (inv.total_due * Decimal("0.5")).quantize(Decimal("0.01"))
                status = InvoiceStatus.PARTIAL
            else:
                amount = Decimal("0")
                status = InvoiceStatus.UNPAID
            if amount > 0:
                Payment.objects.get_or_create(
                    invoice=inv,
                    payment_date=inv.due_date - timedelta(days=random.randint(1, 10)),
                    amount=amount,
                    defaults={
                        "method": random.choice([PaymentMethod.CASH, PaymentMethod.BANK_TRANSFER, PaymentMethod.MPESA]),
                        "reference": f"PAY-{inv.id:04d}",
                        "created_by": actor,
                    },
                )
                payment_count += 1
            if status != inv.status:
                inv.status = status
                inv.save(update_fields=["status", "updated_at"])
        self.stdout.write(f"  Created {payment_count} payments")

        # VERIFICATION
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write("VERIFICATION")
        self.stdout.write(f"  FinancePeriods: {FinancePeriod.objects.count()}")
        self.stdout.write(f"  FeeStructures: {FeeStructure.objects.count()}")
        self.stdout.write(f"  FeeStructureItems: {FeeStructureItem.objects.count()}")
        self.stdout.write(f"  Invoices: {Invoice.objects.count()}")
        invoice_statuses = {}
        for status, label in InvoiceStatus.choices:
            count = Invoice.objects.filter(status=status).count()
            if count > 0:
                invoice_statuses[label] = count
        self.stdout.write(f"  Invoices by status: {json.dumps(invoice_statuses)}")
        self.stdout.write(f"  Payments: {Payment.objects.count()}")
        self.stdout.write("Done!")
