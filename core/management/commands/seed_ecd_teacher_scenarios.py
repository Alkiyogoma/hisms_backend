"""
Seed deterministic data to validate:
- ECD vs Primary teacher scoping by TimetableSlot assignments
- ECD report entry domains (Pre-K / KG / Pre-School / ABC)
- ECD report preview rendering (09e-style) from ReportCard + ECDEvaluation

Usage:
  python manage.py seed_ecd_teacher_scenarios
"""

from __future__ import annotations

import random
from datetime import date, time

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed ECD/Primary teacher scenarios (classes, users, timetable slots, students, ECD evals)."

    def handle(self, *args, **options):
        from academics.ecd_utils import ecd_template_type_from_class_name
        from academics.models import AcademicYear, Department, ECDEvaluation, GradeClass, ReportCard, ReportCardStatus, Term
        from academics.views import ECDEvaluationEntryView
        from students.models import Student, StudentStatus
        from timetable.models import TimetableSlot, Weekday
        from users.models import User, UserRole

        self.stdout.write("--- Seeding ECD teacher scenarios ---")

        # Academic year / active term
        ay, _ = AcademicYear.objects.get_or_create(
            name=str(date.today().year),
            defaults={"is_current": True},
        )
        ay.is_current = True
        ay.save(update_fields=["is_current"])

        term, _ = Term.objects.get_or_create(
            academic_year=ay,
            name="Term 2",
            defaults={
                "is_locked": False,
                "start_date": date(date.today().year, 5, 1),
                "end_date": date(date.today().year, 8, 31),
            },
        )
        if term.is_locked:
            term.is_locked = False
            term.save(update_fields=["is_locked"])

        # Canonical class names for heuristic mapping + GradeClass mapping
        ecd_classes = [
            "Pre-Kindergarten",
            "Kindergarten",
            "Pre-School",
            "ABC Class",
        ]
        primary_classes = ["Grade 1", "Grade 2"]

        for c in ecd_classes:
            GradeClass.objects.get_or_create(name=c, defaults={"department": Department.ECD, "max_capacity": 25})
        for c in primary_classes:
            GradeClass.objects.get_or_create(name=c, defaults={"department": Department.PRIMARY, "max_capacity": 30})

        # Teachers
        common_pwd = "hodari2026"

        qa_teacher, _ = User.objects.get_or_create(
            username="qa_teacher",
            defaults={
                "role": UserRole.TEACHER,
                "first_name": "QA",
                "last_name": "Teacher",
                "email": "qa_teacher@hodari.test",
            },
        )
        qa_teacher.role = UserRole.TEACHER
        qa_teacher.set_password(common_pwd)
        qa_teacher.save()

        ecd_teacher, _ = User.objects.get_or_create(
            username="teacher_ecd",
            defaults={
                "role": UserRole.TEACHER,
                "first_name": "Ecd",
                "last_name": "Teacher",
                "email": "teacher_ecd@hodari.test",
            },
        )
        ecd_teacher.role = UserRole.TEACHER
        ecd_teacher.set_password(common_pwd)
        ecd_teacher.save()

        primary_teacher, _ = User.objects.get_or_create(
            username="teacher_primary",
            defaults={
                "role": UserRole.TEACHER,
                "first_name": "Primary",
                "last_name": "Teacher",
                "email": "teacher_primary@hodari.test",
            },
        )
        primary_teacher.role = UserRole.TEACHER
        primary_teacher.set_password(common_pwd)
        primary_teacher.save()

        # Timetable slots (idempotent get_or_create)
        # We use a single slot per class to establish class assignment for scoping.
        slot_start = time(8, 0)
        slot_end = time(9, 0)

        def ensure_slot(teacher: User, class_name: str, subject: str):
            TimetableSlot.objects.get_or_create(
                term=term,
                teacher=teacher,
                day_of_week=Weekday.MON,
                start_time=slot_start,
                end_time=slot_end,
                class_name=class_name,
                defaults={"subject_name": subject},
            )

        # Assign QA + ECD teacher to all ECD classes (QA is your logged-in user)
        for cls in ecd_classes:
            ensure_slot(qa_teacher, cls, "ECD")
            ensure_slot(ecd_teacher, cls, "ECD")

        # Assign primary teacher to primary classes
        for cls in primary_classes:
            ensure_slot(primary_teacher, cls, "Mathematics")

        # Students (create a few per class)
        def mk_student(class_name: str, i: int) -> Student:
            adm = f"ECD-{class_name[:3].upper()}-{i:03d}".replace(" ", "")
            # Must respect unique_together (first_name, last_name, date_of_birth).
            # Use deterministic uniqueness per admission_no to be idempotent and collision-free.
            safe_class = "".join(ch for ch in class_name if ch.isalnum())[:18] or "Class"
            first = f"Stu{adm[-4:]}"
            last = f"{safe_class}{(i % 97):02d}"
            base_year = date.today().year - (5 if "Grade" not in class_name else 8)
            # Spread dates across the year to avoid collisions even if names already exist.
            month = ((i % 12) + 1)
            day = ((i % 27) + 1)
            dob = date(base_year, month, day)
            s, _ = Student.objects.get_or_create(
                admission_no=adm,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "class_name": class_name,
                    "status": StudentStatus.ACTIVE,
                    "date_of_birth": dob,
                },
            )
            # Keep class_name stable for repeat runs
            if s.class_name != class_name:
                s.class_name = class_name
                s.save(update_fields=["class_name"])
            return s

        for idx, cls in enumerate(ecd_classes, start=1):
            for i in range(1, 6):
                mk_student(cls, idx * 100 + i)
        for idx, cls in enumerate(primary_classes, start=1):
            for i in range(1, 6):
                mk_student(cls, 900 + idx * 10 + i)

        # Create ECD ReportCards + ECDEvaluations for 1 student per ECD class
        for cls in ecd_classes:
            tpl = ecd_template_type_from_class_name(cls) or "pre_k"
            domains = ECDEvaluationEntryView.get_flat_domains(tpl)
            student = Student.objects.filter(class_name=cls, is_archived=False).order_by("admission_no").first()
            if not student:
                continue

            rc, _ = ReportCard.objects.get_or_create(
                student=student,
                term=term,
                defaults={
                    "generated_by": qa_teacher,
                    "status": ReportCardStatus.DRAFT,
                    "is_ecd_report": True,
                    "ecd_template_type": tpl,
                },
            )
            # Ensure flags
            changed = False
            if not rc.is_ecd_report:
                rc.is_ecd_report = True
                changed = True
            if rc.ecd_template_type != tpl:
                rc.ecd_template_type = tpl
                changed = True
            if changed:
                rc.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])

            if not (rc.teacher_comments or "").strip() or len((rc.teacher_comments or "").strip()) < 50:
                rc.teacher_comments = f"{student.first_name} is progressing well across the term and participates actively in class activities."
                rc.save(update_fields=["teacher_comments", "updated_at"])

            existing = set(
                ECDEvaluation.objects.filter(report_card=rc).values_list("domain", flat=True)
            )
            to_create = []
            for d in domains:
                if d in existing:
                    continue
                to_create.append(
                    ECDEvaluation(report_card=rc, domain=d, rating=random.choice(["E", "G", "S", "N"]))
                )
            if to_create:
                ECDEvaluation.objects.bulk_create(to_create)

        self.stdout.write(self.style.SUCCESS("--- Done ---"))
        self.stdout.write("Login credentials for testing:")
        self.stdout.write("  qa_teacher / hodari2026  (ECD assigned)")
        self.stdout.write("  teacher_ecd / hodari2026 (ECD assigned)")
        self.stdout.write("  teacher_primary / hodari2026 (Primary assigned)")

