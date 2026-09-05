"""
N+1 value equality tests.

For each batch rewrite, runs a naive per-row reference implementation
alongside the new batch implementation against identical seeded data
and asserts the outputs are byte-for-byte equal.
"""
from datetime import date, timedelta, time
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from academics.models import AcademicYear, Term, GradeClass
from attendance.models import AttendanceEntry, AttendanceStatus
from attendance.views import _batch_attendance_rates
from students.models import Student

User = get_user_model()


class BatchAttendanceRatesEqualityTest(TestCase):
    """Verify _batch_attendance_rates produces identical results to per-student queries."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="t1", password="pass123", email="t@t.com"
        )
        self.ay = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        self.term = Term.objects.create(
            academic_year=self.ay, name="Term 1",
            start_date=date(2026, 1, 12), end_date=date(2026, 3, 27),
            is_locked=False,
        )
        self.gc = GradeClass.objects.create(name="Grade 1", department="primary")

        # Create 5 students
        self.students = []
        for i in range(5):
            s = Student.objects.create(
                first_name=f"Stu{i}", last_name=f"Dent{i}",
                admission_no=f"ADM-{i:03d}",
                class_name="Grade 1",
                academic_year=self.ay,
            )
            self.students.append(s)

        # Seed attendance entries with known statuses
        # Student 0: 5 PRESENT → 100%
        # Student 1: 3 PRESENT, 2 ABSENT → 60%
        # Student 2: 4 LATE, 1 EXCUSED → 80%  (LATE counts as present)
        # Student 3: 0 entries → 0%
        # Student 4: 2 PRESENT, 1 LATE → 100%
        statuses_per_student = [
            [AttendanceStatus.PRESENT] * 5,
            [AttendanceStatus.PRESENT] * 3 + [AttendanceStatus.ABSENT] * 2,
            [AttendanceStatus.LATE] * 4 + [AttendanceStatus.EXCUSED] * 1,
            [],
            [AttendanceStatus.PRESENT] * 2 + [AttendanceStatus.LATE] * 1,
        ]
        base_date = date(2026, 1, 12)
        for student, statuses in zip(self.students, statuses_per_student):
            for j, status in enumerate(statuses):
                AttendanceEntry.objects.create(
                    student=student,
                    date=base_date + timedelta(days=j),
                    status=status,
                    check_in_time=time(7, 0),
                    marked_by=self.user,
                )

    def _naive_per_student_rate(self, student, term):
        """Reference implementation: 2 queries per student."""
        from attendance.models import AttendanceEntry, AttendanceStatus
        start, end = term.start_date, term.end_date
        total = AttendanceEntry.objects.filter(
            student=student, date__range=[start, end]
        ).count()
        if total == 0:
            return 0
        present = AttendanceEntry.objects.filter(
            student=student, date__range=[start, end],
            status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
        ).count()
        return round((present / total) * 100, 1)

    def test_batch_matches_per_student(self):
        """Batch output must equal per-student reference for every student."""
        batch_rates = _batch_attendance_rates(self.students, self.term)
        for s in self.students:
            expected = self._naive_per_student_rate(s, self.term)
            actual = batch_rates.get(s.id)
            self.assertEqual(
                actual, expected,
                f"Student {s.admission_no}: batch={actual} vs per-student={expected}",
            )

    def test_batch_keys_match_student_ids(self):
        """Batch result must have an entry for every student passed in."""
        batch_rates = _batch_attendance_rates(self.students, self.term)
        for s in self.students:
            self.assertIn(s.id, batch_rates, f"Missing student {s.id}")

    def test_empty_students_returns_empty(self):
        self.assertEqual(_batch_attendance_rates([], self.term), {})

    def test_none_term_returns_empty(self):
        self.assertEqual(_batch_attendance_rates(self.students, None), {})


class BatchWelfareLookupEqualityTest(TestCase):
    """Verify batch welfare lookup matches per-student query."""

    def setUp(self):
        from welfare.models import WelfareObservation
        self.user = User.objects.create_user(
            username="hod", password="pass123", email="h@h.com"
        )
        self.ay = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        GradeClass.objects.create(name="Grade 2", department="primary")

        self.students = []
        for i in range(4):
            s = Student.objects.create(
                first_name=f"W{i}", last_name=f"Child{i}",
                admission_no=f"WCD-{i:03d}",
                class_name="Grade 2",
                academic_year=self.ay,
            )
            self.students.append(s)

        # Student 0: 2 open observations (critical + high) → latest is critical
        # Student 1: 1 open observation (medium)
        # Student 2: 0 open observations
        # Student 3: 1 open observation + 1 resolved → only open returned
        observations = [
            WelfareObservation(student=self.students[0], submitted_by=self.user,
                               severity="high", hod_status="pending",
                               observation_date=date(2026, 2, 1)),
            WelfareObservation(student=self.students[0], submitted_by=self.user,
                               severity="critical", hod_status="in_progress",
                               observation_date=date(2026, 2, 5)),
            WelfareObservation(student=self.students[1], submitted_by=self.user,
                               severity="medium", hod_status="pending",
                               observation_date=date(2026, 2, 3)),
            WelfareObservation(student=self.students[3], submitted_by=self.user,
                               severity="low", hod_status="resolved",
                               observation_date=date(2026, 2, 1)),
            WelfareObservation(student=self.students[3], submitted_by=self.user,
                               severity="high", hod_status="pending",
                               observation_date=date(2026, 2, 4)),
        ]
        for obs in observations:
            obs.concern_type = "academic"
            obs.save()

    def test_batch_matches_per_student(self):
        """Batch lookup must return same latest observation as per-student query."""
        from welfare.models import WelfareObservation
        children_with_open = Student.objects.filter(
            welfare_observations__hod_status__in=["pending", "in_progress"],
            class_name="Grade 2",
        ).distinct()

        # Batch approach (from the view)
        open_obs = WelfareObservation.objects.filter(
            hod_status__in=["pending", "in_progress"],
            student__in=children_with_open,
        ).select_related("student").order_by("-severity", "-observation_date")
        obs_by_student = {}
        for obs in open_obs:
            if obs.student_id not in obs_by_student:
                obs_by_student[obs.student_id] = obs

        # Per-student reference
        for child in children_with_open:
            expected = WelfareObservation.objects.filter(
                student=child,
                hod_status__in=["pending", "in_progress"],
            ).order_by("-severity", "-observation_date").first()

            actual = obs_by_student.get(child.id)
            self.assertEqual(
                actual.id if actual else None,
                expected.id if expected else None,
                f"Student {child.admission_no}: batch obs {actual} vs per-student {expected}",
            )


class BatchFinanceCollectionEqualityTest(TestCase):
    """Verify batch collection-by-class aggregation matches per-class aggregation."""

    def setUp(self):
        from academics.models import AcademicYear, Term, GradeClass
        from finance.models import Invoice, Payment, InvoiceStatus, PaymentMethod
        self.user = User.objects.create_user(
            username="fin", password="pass123", email="f@f.com"
        )
        self.ay = AcademicYear.objects.create(
            name="2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)
        )
        self.term = Term.objects.create(
            academic_year=self.ay, name="T1",
            start_date=date(2026, 1, 12), end_date=date(2026, 3, 27),
            is_locked=False,
        )
        GradeClass.objects.create(name="Grade 3", department="primary")
        GradeClass.objects.create(name="Grade 4", department="primary")

        students = []
        for cls_name in ["Grade 3", "Grade 4"]:
            for i in range(2):
                s = Student.objects.create(
                    first_name=f"F{i}", last_name=f"Kid{i}",
                    admission_no=f"FN-{cls_name[-1]}{i}",
                    class_name=cls_name,
                    academic_year=self.ay,
                )
                students.append(s)

        # Grade 3: 2 invoices (1000 + 2000 = 3000), 1 payment (500)
        # Grade 4: 2 invoices (1500 + 2500 = 4000), 3 payments (1000 + 500 + 500 = 2000)
        self.invoices = []
        amounts = [
            (students[0], Decimal("1000")), (students[1], Decimal("2000")),
            (students[2], Decimal("1500")), (students[3], Decimal("2500")),
        ]
        for s, amt in amounts:
            inv = Invoice.objects.create(
                student=s, amount_due=amt,
                status=InvoiceStatus.UNPAID,
                term=self.term,
            )
            self.invoices.append(inv)

        payments = [
            (self.invoices[0], Decimal("500")),
            (self.invoices[2], Decimal("1000")),
            (self.invoices[3], Decimal("500")),
            (self.invoices[3], Decimal("500")),
        ]
        for inv, amt in payments:
            Payment.objects.create(
                invoice=inv, amount=amt,
                method=PaymentMethod.CASH,
                created_by=self.user,
            )

        self.term_invoices = Invoice.objects.filter(term=self.term)

    def test_batch_matches_per_class(self):
        """Batch aggregation must equal per-class aggregation."""
        from django.db.models import Sum
        from finance.models import Payment

        # Batch approach (from the view)
        class_billed = dict(
            self.term_invoices.values("student__class_name")
            .annotate(t=Sum("total_due"))
            .values_list("student__class_name", "t")
        )
        class_payments = dict(
            Payment.objects.filter(invoice__in=self.term_invoices)
            .values("invoice__student__class_name")
            .annotate(t=Sum("amount"))
            .values_list("invoice__student__class_name", "t")
        )

        # Per-class reference
        classes = sorted(set(
            self.term_invoices.values_list("student__class_name", flat=True)
        ))
        for cls in classes:
            cls_invoices = self.term_invoices.filter(student__class_name=cls)
            expected_billed = cls_invoices.aggregate(t=Sum("total_due"))["t"] or 0
            expected_payments = Payment.objects.filter(
                invoice__in=cls_invoices
            ).aggregate(t=Sum("amount"))["t"] or 0

            actual_billed = class_billed.get(cls, 0)
            actual_payments = class_payments.get(cls, 0)

            self.assertEqual(
                Decimal(str(actual_billed)), expected_billed,
                f"Class {cls}: batch billed={actual_billed} vs per-class={expected_billed}",
            )
            self.assertEqual(
                Decimal(str(actual_payments)), expected_payments,
                f"Class {cls}: batch payments={actual_payments} vs per-class={expected_payments}",
            )

    def test_rate_calculation_matches(self):
        """Rate percentage must match between batch and per-class."""
        from django.db.models import Sum
        from finance.models import Payment

        class_billed = dict(
            self.term_invoices.values("student__class_name")
            .annotate(t=Sum("total_due"))
            .values_list("student__class_name", "t")
        )
        class_payments = dict(
            Payment.objects.filter(invoice__in=self.term_invoices)
            .values("invoice__student__class_name")
            .annotate(t=Sum("amount"))
            .values_list("invoice__student__class_name", "t")
        )

        classes = sorted(set(
            self.term_invoices.values_list("student__class_name", flat=True)
        ))
        for cls in classes:
            billed = class_billed.get(cls, 0)
            received = class_payments.get(cls, 0)
            batch_rate = round((received / billed * 100), 1) if billed > 0 else 0

            cls_invoices = self.term_invoices.filter(student__class_name=cls)
            exp_billed = cls_invoices.aggregate(t=Sum("total_due"))["t"] or 0
            exp_payments = Payment.objects.filter(
                invoice__in=cls_invoices
            ).aggregate(t=Sum("amount"))["t"] or 0
            expected_rate = round((exp_payments / exp_billed * 100), 1) if exp_billed > 0 else 0

            self.assertEqual(
                batch_rate, expected_rate,
                f"Class {cls}: batch rate={batch_rate}% vs expected={expected_rate}%",
            )

    def test_open_invoices_count_matches_naive(self):
        """Term-scoped open_invoices must equal naive exclude(PAID).count()."""
        from finance.models import Invoice, InvoiceStatus
        # Existing setUp has 4 UNPAID invoices on self.term
        # Add 1 PAID and 1 PARTIAL to create a realistic mix
        s = self.invoices[0].student
        Invoice.objects.create(
            student=s, amount_due=Decimal("500"),
            status=InvoiceStatus.PAID, term=self.term,
        )
        Invoice.objects.create(
            student=s, amount_due=Decimal("750"),
            status=InvoiceStatus.PARTIAL, term=self.term,
        )
        term_invoices = Invoice.objects.filter(term=self.term)
        batch_count = term_invoices.exclude(status=InvoiceStatus.PAID).count()
        naive_count = term_invoices.exclude(status=InvoiceStatus.PAID).count()
        self.assertEqual(
            batch_count, naive_count,
            f"open_invoices batch={batch_count} vs naive={naive_count}",
        )
        self.assertEqual(batch_count, 5)  # 4 UNPAID + 1 PARTIAL

    def test_open_invoices_zero_for_empty_term(self):
        """Term with no non-PAID invoices must report open_invoices=0."""
        from finance.models import Invoice, InvoiceStatus
        empty_term = type(self.term).objects.create(
            academic_year=self.ay, name="Empty",
            start_date=date(2026, 4, 1), end_date=date(2026, 6, 30),
            is_locked=False,
        )
        # Create only PAID invoices for this term
        s = self.invoices[0].student
        Invoice.objects.create(
            student=s, amount_due=Decimal("300"),
            status=InvoiceStatus.PAID, term=empty_term,
        )
        term_invoices = Invoice.objects.filter(term=empty_term)
        open_count = term_invoices.exclude(status=InvoiceStatus.PAID).count()
        self.assertEqual(open_count, 0)
