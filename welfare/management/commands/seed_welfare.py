"""
Step 7 — Seed welfare observations for ECD students
Run: python manage.py seed_welfare
"""
import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from academics.models import GradeClass, Department
from students.models import Student
from users.models import User, UserRole
from welfare.models import WelfareObservation, WelfareSeverity, WelfareConcernType


class Command(BaseCommand):
    help = "Seed welfare observations for ECD students"

    def handle(self, *args, **options):
        # Get ECD students
        ecd_classes = GradeClass.objects.filter(department=Department.ECD).values_list("name", flat=True)
        students = list(Student.objects.filter(is_archived=False, class_name__in=ecd_classes))
        self.stdout.write(f"ECD students: {len(students)}")

        # Get ECD teachers
        teachers = list(User.objects.filter(role=UserRole.TEACHER, is_active=True)[:4])
        if not teachers:
            teachers = [User.objects.filter(is_superuser=True).first()]

        severity_choices = [s for s, _ in WelfareSeverity.choices]
        concern_choices = [c for c, _ in WelfareConcernType.choices]
        sample_texts = [
            "Student appears distracted during class activities.",
            "Observed student struggling with social interaction during playtime.",
            "Student reported feeling unwell. Notified parent.",
            "Showing good improvement in classroom participation.",
            "Requires additional support with numeracy exercises.",
            "Demonstrating positive behavioral changes this week.",
            "Parent reported concerns about sleep patterns at home.",
            "Student showing excellent progress in communication skills.",
        ]

        today = date.today()
        count = 0
        for student in students:
            # 1-3 observations per student
            for _ in range(random.randint(1, 3)):
                obs_date = today - timedelta(days=random.randint(1, 90))
                WelfareObservation.objects.create(
                    student=student,
                    submitted_by=random.choice(teachers),
                    concern_type=random.choice(concern_choices),
                    severity=random.choice(severity_choices),
                    observation_date=obs_date,
                    observation_text=random.choice(sample_texts),
                    action_taken="Notified parent and scheduled follow-up meeting." if random.random() > 0.5 else "",
                    parent_contacted=random.random() > 0.5,
                    follow_up_required=random.random() > 0.7,
                    follow_up_date=today + timedelta(days=random.randint(1, 14)) if random.random() > 0.7 else None,
                    hod_status=random.choice(["pending", "in_progress", "resolved"]),
                )
                count += 1

        self.stdout.write(f"Created {count} welfare observations")
        self.stdout.write(f"Total: {WelfareObservation.objects.count()}")
