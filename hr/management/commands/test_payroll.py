"""
Management command: python manage.py test_payroll
Tests the payroll auto-calculation engine with 7 sample staff scenarios.
Verifies PAYE, NSSF, NHIF, HESLB, and leave deduction calculations.
"""

import os
import sys
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model

from hr.models import (
    StaffProfile, PayrollRun, PayrollEntry,
    PayrollConfig, PAYETaxBand, PayrollStatus,
)
from academics.models import Department
from users.models import UserRole

User = get_user_model()


def fmt_tzs(val):
    return f"TZS {float(val):,.0f}"


class Command(BaseCommand):
    help = "Test payroll auto-calculation with 7 sample staff scenarios"

    def handle(self, *args, **options):
        self.stdout.write("=" * 95)
        self.stdout.write("  PAYROLL CALCULATION TEST - VERIFICATION REPORT")
        self.stdout.write("=" * 95)

        # -- Seed PayrollConfig ------------------------------------------------
        cfg, _ = PayrollConfig.objects.get_or_create(pk=1, defaults={
            "nssf_employee_rate": Decimal("10.00"),
            "nssf_employer_rate": Decimal("10.00"),
            "working_days_per_month": 26,
        })
        self.stdout.write(self.style.SUCCESS(
            f"\nPayrollConfig: NSSF ee={cfg.nssf_employee_rate}%"
            f"/ er={cfg.nssf_employer_rate}%,"
            f" Working days={cfg.working_days_per_month}"
        ))

        # -- Seed PAYE Tax Bands ------------------------------------------------
        PAYETaxBand.objects.all().update(is_active=False)
        PAYE_BANDS = [
            (0,          270000,    0,      0.0),
            (270000,     520000,    0,      8.0),
            (520000,     760000,    20000,  20.0),
            (760000,     1000000,   68000,  25.0),
            (1000000,    None,      128000, 30.0),
        ]
        for bf, bt, base, rate in PAYE_BANDS:
            PAYETaxBand.objects.create(
                version="2024/2025",
                band_from=Decimal(str(bf)),
                band_to=Decimal(str(bt)) if bt else None,
                base_tax=Decimal(str(base)),
                rate_percentage=Decimal(str(rate)),
                is_active=True,
            )
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(PAYE_BANDS)} PAYE tax bands (v2024/2025)"
        ))

        # -- Define Scenarios ---------------------------------------------------
        scenarios = [
            {
                "id": 1,
                "name": "Alice - Low Earner (below PAYE threshold)",
                "basic": 250_000,
                "housing": 0, "transport": 0, "medical": 0, "other": 0,
                "dept": Department.PRIMARY,
                "nssf": "NSSF-ALICE-001", "tin": "TIN-ALICE-001",
                "heslb": False, "heslb_type": "", "heslb_val": 0,
                "unpaid_days": 0,
                "exp_gross": 250_000,
                "exp_nssf": 25_000,
                "exp_paye": 0,
                "exp_nhif": 20_000,
                "exp_heslb": 0,
                "exp_leave": 0,
                "exp_net": 205_000,
                "note": "Below 270k PAYE threshold - NSSF + NHIF only",
            },
            {
                "id": 2,
                "name": "Bob - Mid Earner (8% PAYE bracket)",
                "basic": 500_000,
                "housing": 50_000, "transport": 30_000, "medical": 20_000, "other": 0,
                "dept": Department.PRIMARY,
                "nssf": "NSSF-BOB-002", "tin": "TIN-BOB-002",
                "heslb": False, "heslb_type": "", "heslb_val": 0,
                "unpaid_days": 0,
                "exp_gross": 600_000,
                "exp_nssf": 50_000,
                "exp_paye": 36_000,
                "exp_nhif": 20_000,
                "exp_heslb": 0,
                "exp_leave": 0,
                "exp_net": 494_000,
                "note": "PAYE on gross 600k: 0-270k=0 + 270-520k=(250kx8%=20k) + 520-600k=(80kx20%=16k) = 36k",
            },
            {
                "id": 3,
                "name": "Carol - Upper Mid Earner (25% PAYE bracket)",
                "basic": 800_000,
                "housing": 100_000, "transport": 0, "medical": 0, "other": 0,
                "dept": Department.LOWER_SECONDARY,
                "nssf": "NSSF-CAROL-003", "tin": "TIN-CAROL-003",
                "heslb": False, "heslb_type": "", "heslb_val": 0,
                "unpaid_days": 0,
                "exp_gross": 900_000,
                "exp_nssf": 80_000,
                "exp_paye": 103_000,
                "exp_nhif": 30_000,
                "exp_heslb": 0,
                "exp_leave": 0,
                "exp_net": 687_000,
                "note": "25% bracket: 68k + (900k-760k)x25% = 103k",
            },
            {
                "id": 4,
                "name": "David - High Earner (30% PAYE bracket)",
                "basic": 1_500_000,
                "housing": 200_000, "transport": 100_000, "medical": 50_000, "other": 50_000,
                "dept": Department.ADMINISTRATION,
                "nssf": "NSSF-DAVID-004", "tin": "TIN-DAVID-004",
                "heslb": False, "heslb_type": "", "heslb_val": 0,
                "unpaid_days": 0,
                "exp_gross": 1_900_000,
                "exp_nssf": 150_000,
                "exp_paye": 398_000,
                "exp_nhif": 50_000,
                "exp_heslb": 0,
                "exp_leave": 0,
                "exp_net": 1_302_000,
                "note": "30% bracket: 128k + (1.9M-1M)x30% = 398k",
            },
            {
                "id": 5,
                "name": "Eve - Mid Earner + HESLB fixed + Unpaid Leave",
                "basic": 600_000,
                "housing": 0, "transport": 0, "medical": 0, "other": 0,
                "dept": Department.ECD,
                "nssf": "NSSF-EVE-005", "tin": "TIN-EVE-005",
                "heslb": True, "heslb_type": "fixed", "heslb_val": 45_000,
                "unpaid_days": 3,
                "exp_gross": 600_000,
                "exp_nssf": 60_000,
                "exp_paye": 36_000,
                "exp_nhif": 30_000,
                "exp_heslb": 45_000,
                "exp_leave": 69_230.77,
                "exp_net": 359_769.23,
                "note": "HESLB fixed 45k + 3 unpaid days = (600k/26)x3 = 69,230.77",
            },
            {
                "id": 6,
                "name": "Frank - Upper Mid + HESLB pct + Unpaid Leave",
                "basic": 900_000,
                "housing": 100_000, "transport": 50_000, "medical": 0, "other": 0,
                "dept": Department.LOWER_SECONDARY,
                "nssf": "NSSF-FRANK-006", "tin": "TIN-FRANK-006",
                "heslb": True, "heslb_type": "percentage", "heslb_val": 5,
                "unpaid_days": 2,
                "exp_gross": 1_050_000,
                "exp_nssf": 90_000,
                "exp_paye": 143_000,
                "exp_nhif": 30_000,
                "exp_heslb": 45_000,
                "exp_leave": 69_230.77,
                "exp_net": 672_769.23,
                "note": "HESLB 5% of 900k=45k + 2 unpaid days = (900k/26)x2 = 69,230.77",
            },
            {
                "id": 7,
                "name": "Grace - Full deductions (all combined)",
                "basic": 1_200_000,
                "housing": 150_000, "transport": 0, "medical": 50_000, "other": 0,
                "dept": Department.ADMINISTRATION,
                "nssf": "NSSF-GRACE-007", "tin": "TIN-GRACE-007",
                "heslb": True, "heslb_type": "fixed", "heslb_val": 80_000,
                "unpaid_days": 1,
                "exp_gross": 1_400_000,
                "exp_nssf": 120_000,
                "exp_paye": 248_000,
                "exp_nhif": 50_000,
                "exp_heslb": 80_000,
                "exp_leave": 46_153.85,
                "exp_net": 855_846.15,
                "note": "All deductions + 1 unpaid day = (1.2M/26)x1 = 46,153.85",
            },
        ]

        # -- Create admin user ---------------------------------------------------
        admin_user, _ = User.objects.get_or_create(
            username="payroll_test_admin",
            defaults={"email": "payroll_test@hodari.ac.tz",
                      "role": UserRole.SUPER_ADMIN, "is_staff": True},
        )
        admin_user.is_active = True
        admin_user.save()

        # -- Create staff profiles ------------------------------------------------
        created_staff = []
        for s in scenarios:
            username = f"payroll_test_staff_{s['id']}"
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@hodari.ac.tz",
                          "role": UserRole.TEACHER},
            )
            user.is_active = True
            user.save()

            staff, _ = StaffProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": s["name"],
                    "employee_id": f"EMP-TEST-{s['id']:03d}",
                    "job_title": "Test Staff",
                    "department": s["dept"],
                    "staff_category": "teaching",
                    "employment_type": "permanent",
                    "employment_start_date": timezone.now().date() - timedelta(days=365),
                    "contact_phone": "0712345678",
                    "contact_email": f"{username}@hodari.ac.tz",
                    "basic_salary": Decimal(str(s["basic"])),
                    "housing_allowance": Decimal(str(s["housing"])),
                    "transport_allowance": Decimal(str(s["transport"])),
                    "medical_allowance": Decimal(str(s["medical"])),
                    "other_allowances": Decimal(str(s["other"])),
                    "nssf_number": s["nssf"],
                    "tin_number": s["tin"],
                    "heslb_has_loan": s["heslb"],
                    "heslb_deduction_type": s["heslb_type"],
                    "heslb_deduction_value": Decimal(str(s["heslb_val"])),
                    "is_active": True,
                },
            )
            created_staff.append(staff)
            self.stdout.write(f"  Created: {s['name']} (basic={fmt_tzs(s['basic'])})")

        # -- Create payroll run ---------------------------------------------------
        payroll, _ = PayrollRun.objects.get_or_create(
            period_name="Test May 2026",
            defaults={
                "status": PayrollStatus.DRAFT,
                "period_start": timezone.now().date() - timedelta(days=30),
                "period_end": timezone.now().date(),
                "payment_date": timezone.now().date() + timedelta(days=5),
            },
        )

        # -- Create and auto-calculate entries ------------------------------------
        entries = []
        for i, staff in enumerate(created_staff):
            s = scenarios[i]
            entry, _ = PayrollEntry.objects.update_or_create(
                payroll_run=payroll, staff=staff,
                defaults={"unpaid_leave_days": s["unpaid_days"]},
            )
            entry.auto_calculate_from_profile()
            entry.save()
            entries.append(entry)

        self.stdout.write(self.style.SUCCESS(
            f"\nPayroll '{payroll.period_name}' with {len(entries)} entries"
        ))

        # -- Verify Results -------------------------------------------------------
        self.stdout.write("\n" + "=" * 95)
        self.stdout.write("  VERIFICATION RESULTS")
        self.stdout.write("=" * 95)

        total_checks = 0
        passed_checks = 0
        failed_checks = 0
        TOLERANCE = 0.02

        for i, (entry, s) in enumerate(zip(entries, scenarios)):
            e = entry
            checks = {
                "Gross Pay":    (float(e.gross_pay), s["exp_gross"]),
                "NSSF (10%)":   (float(e.nssf_employee), s["exp_nssf"]),
                "PAYE":         (float(e.paye_tax), s["exp_paye"]),
                "NHIF":         (float(e.nhif_deduction), s["exp_nhif"]),
                "HESLB":        (float(e.hesb_deduction), s["exp_heslb"]),
                "Leave Deduct": (float(e.leave_deduction), s["exp_leave"]),
            }

            total_ded = (e.paye_tax + e.nssf_employee + e.nhif_deduction +
                         e.hesb_deduction + e.leave_deduction)
            net_val = float(e.gross_pay - total_ded)
            checks["Net Pay"] = (net_val, s["exp_net"])

            all_ok = all(abs(calc - exp) <= TOLERANCE for calc, exp in checks.values())
            total_checks += len(checks)

            if all_ok:
                passed_checks += len(checks)
            else:
                failed_count = sum(
                    1 for calc, exp in checks.values()
                    if abs(calc - exp) > TOLERANCE
                )
                failed_checks += failed_count
                passed_checks += len(checks) - failed_count

            self.stdout.write(f"\n{'=' * 90}")
            sig = self.style.SUCCESS if all_ok else self.style.ERROR
            ok_label = "[ok]" if all_ok else "[FAIL]"
            self.stdout.write(sig(f"  {ok_label} "
                                  f"Scenario {s['id']}: {s['name']}"))
            self.stdout.write(f"  Note: {s['note']}")
            self.stdout.write(f"  Unpaid leave days: {s['unpaid_days']}\n")

            self.stdout.write(f"  {'Item':<25} {'Calculated':>15} "
                              f"{'Expected':>15} {'Status':>8}")
            self.stdout.write(f"  {'=' * 63}")
            for label, (calc, exp) in checks.items():
                ok = abs(calc - exp) <= TOLERANCE
                status_str = "[ok]" if ok else "[NO]"
                self.stdout.write(
                    f"  {label:<25} {fmt_tzs(calc):>15} "
                    f"{fmt_tzs(exp):>15} {status_str:>8}"
                )

        # -- Payroll Summary ------------------------------------------------------
        payroll.calculate_summary()
        self.stdout.write(f"\n{'=' * 95}")
        self.stdout.write("  PAYROLL SUMMARY")
        self.stdout.write(f"{'=' * 95}")
        self.stdout.write(f"  Total staff:   {payroll.employee_count}")
        self.stdout.write(f"  Total gross:   {fmt_tzs(payroll.total_gross_pay)}")
        self.stdout.write(f"  Total deduct:  {fmt_tzs(payroll.total_deductions)}")
        self.stdout.write(f"  Total net:     {fmt_tzs(payroll.total_net_pay)}")

        # -- Final Result ---------------------------------------------------------
        pct = (passed_checks / total_checks) * 100 if total_checks else 0
        self.stdout.write(f"\n{'=' * 95}")
        if failed_checks == 0:
            self.stdout.write(self.style.SUCCESS(
                f"  ALL {total_checks} CHECKS PASSED ({pct:.0f}%)"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"  {passed_checks}/{total_checks} passed, "
                f"{failed_checks} failed ({pct:.0f}%)"
            ))
        self.stdout.write(f"{'=' * 95}")

        # -- Additional: Hardcoded PAYE Fallback ----------------------------------
        self.stdout.write(f"\n\n{'=' * 95}")
        self.stdout.write("  ADDITIONAL TEST: Hardcoded PAYE Fallback")
        self.stdout.write(f"{'=' * 95}")
        self.stdout.write("  Deactivating DB PAYE bands to test fallback...")

        PAYETaxBand.objects.all().update(is_active=False)

        carol = created_staff[2]
        entry_c = PayrollEntry.objects.get(payroll_run=payroll, staff=carol)
        entry_c.auto_calculate_from_profile()
        entry_c.save()

        fallback_paye = float(entry_c.paye_tax)
        self.stdout.write(f"  Carol gross={fmt_tzs(entry_c.gross_pay)}, "
                          f"PAYE={fmt_tzs(fallback_paye)}")
        fallback_ok = abs(fallback_paye - 103_000) <= TOLERANCE
        if fallback_ok:
            self.stdout.write(self.style.SUCCESS(
                f"  Expected PAYE: {fmt_tzs(103_000)} -> [ok]"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"  Expected PAYE: {fmt_tzs(103_000)} -> [FAIL]"
            ))

        PAYETaxBand.objects.filter(version="2024/2025").update(is_active=True)
        self.stdout.write(self.style.SUCCESS("  DB PAYE bands re-activated"))

        self.stdout.write(f"\n{'=' * 95}")
        self.stdout.write("  TEST COMPLETE")
        self.stdout.write(f"{'=' * 95}")
        self.stdout.write(f"  Scenarios:     {len(scenarios)}")
        self.stdout.write(f"  Total checks:  {total_checks}")
        self.stdout.write(f"  Passed:        {passed_checks}")
        self.stdout.write(f"  Failed:        {failed_checks}")
        self.stdout.write(f"{'=' * 95}")

        # Cleanup: remove test data so re-runs work
        PayrollEntry.objects.filter(payroll_run=payroll).delete()
        payroll.delete()
        for staff in created_staff:
            user = staff.user
            staff.delete()
            user.delete()
        if admin_user:
            admin_user.delete()
        PAYETaxBand.objects.filter(version="2024/2025").delete()
        self.stdout.write("\n(Cleanup complete - test data removed)")
