"""
Seed demo data matching the Hodari HTML prototype figures.
Usage: python manage.py seed_demo_data
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
import random, datetime


CLASSES = [
    "ECD Butterflies", "ECD Sunflowers", "ECD Stars",
    "Grade 1", "Grade 2", "Grade 3", "Grade 4",
    "Grade 5", "Grade 6", "Grade 7", "Grade 8",
]

FIRST_NAMES = ["Amina", "Baraka", "Chloe", "David", "Esther", "Farhan",
               "Grace", "Hassan", "Irene", "James", "Khadija", "Liam",
               "Mary", "Noah", "Olivia", "Priya", "Queen", "Rayan",
               "Sophia", "Tariq", "Uma", "Victor", "Wanjiku", "Xena", "Yusuf", "Zara"]

LAST_NAMES = ["Kamau", "Ochieng", "Salim", "Mwangi", "Abdi", "Kimani",
              "Wambua", "Achieng", "Otieno", "Njoroge", "Hassan", "Mutua"]

SUBJECTS = ["Mathematics", "English", "Science", "Social Studies",
            "Kiswahili", "Religious Education", "Physical Education", "Art & Craft"]


class Command(BaseCommand):
    help = "Seed demo data for the Hodari SMS prototype"

    def handle(self, *args, **options):
        from students.models import Student, StudentStatus
        from admissions.models import Applicant, ApplicantStatus, InquiryChannel
        from discipline.models import DisciplineIncident, IncidentSeverity
        from academics.models import Term
        from timetable.models import TimetableSlot
        from users.models import User

        self.stdout.write("--- Seeding demo data ---")

        # Get or create admin user
        admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True}
        )
        if not admin.has_usable_password():
            admin.set_password("admin123")
            admin.save()

        # Seed students
        if Student.objects.count() < 10:
            self.stdout.write("  -> Creating students...")
            seq = 1
            for cls in CLASSES:
                count = random.randint(18, 32)
                for _ in range(count):
                    fn = random.choice(FIRST_NAMES)
                    ln = random.choice(LAST_NAMES)
                    adm = f"HOD{2024}{seq:04d}"
                    Student.objects.get_or_create(
                        admission_no=adm,
                        defaults={
                            "first_name": fn,
                            "last_name": ln,
                            "class_name": cls,
                            "status": StudentStatus.ACTIVE,
                            "date_of_birth": datetime.date(2010 + random.randint(0, 8), random.randint(1, 12), random.randint(1, 28)),
                        }
                    )
                    seq += 1
            self.stdout.write(f"  OK: {Student.objects.count()} students")

        # Seed admissions applicants
        if Applicant.objects.count() < 5:
            self.stdout.write("  -> Creating applicants...")
            stage_map = [
                (ApplicantStatus.INQUIRY_RECEIVED, 4),
                (ApplicantStatus.MEETING_SCHEDULED, 3),
                (ApplicantStatus.ASSESSMENT_PENDING, 3),
                (ApplicantStatus.ASSESSMENT_COMPLETED, 2),
                (ApplicantStatus.HOD_REVIEW, 1),
                (ApplicantStatus.ADMITTED, 3),
                (ApplicantStatus.ENROLLED, 2),
                (ApplicantStatus.WAITLISTED, 4),
            ]
            for status, count in stage_map:
                for i in range(count):
                    fn = random.choice(FIRST_NAMES)
                    ln = random.choice(LAST_NAMES)
                    Applicant.objects.create(
                        parent_full_name=f"{random.choice(FIRST_NAMES)} {ln}",
                        parent_phone=f"+255 7{random.randint(10,99)} {random.randint(100,999)} {random.randint(100,999)}",
                        parent_email=f"{ln.lower()}@gmail.com",
                        parent_invoice_name=f"{random.choice(FIRST_NAMES)} {ln}",
                        child_full_name=f"{fn} {ln}",
                        child_date_of_birth=datetime.date(2018 + random.randint(0, 5), random.randint(1, 12), random.randint(1, 28)),
                        grade_applying_for=random.choice(["ECD", "Grade 1", "Grade 2", "Grade 3"]),
                        inquiry_channel=random.choice(list(InquiryChannel)),
                        status=status,
                    )
            self.stdout.write(f"  OK: {Applicant.objects.count()} applicants")

        # Seed academic term
        from academics.models import AcademicYear
        if not Term.objects.exists():
            self.stdout.write("  -> Creating Term 2...")
            ay, _ = AcademicYear.objects.get_or_create(
                name="2025/2026",
                defaults={"is_current": True}
            )
            Term.objects.create(
                academic_year=ay,
                name="Term 2",
                start_date=datetime.date(2025, 5, 5),
                end_date=datetime.date(2025, 8, 8),
                is_locked=False,
            )
            self.stdout.write("  OK: Term 2 created")

        # Seed timetable slots
        if TimetableSlot.objects.count() < 5:
            self.stdout.write("  -> Creating timetable slots...")
            for day in range(1, 6):  # Mon–Fri
                for cls in CLASSES[:6]:
                    for start_h in [8, 9, 10, 11, 13, 14]:
                        TimetableSlot.objects.get_or_create(
                            day_of_week=day,
                            class_name=cls,
                            start_time=datetime.time(start_h, 0),
                            defaults={
                                "end_time": datetime.time(start_h + 1, 0),
                                "subject_name": random.choice(SUBJECTS),
                                "teacher": admin,
                            }
                        )
            self.stdout.write(f"  OK: {TimetableSlot.objects.count()} timetable slots")

        # Seed discipline incidents
        if DisciplineIncident.objects.count() < 3:
            self.stdout.write("  -> Creating discipline incidents...")
            students = list(Student.objects.all()[:10])
            incidents = [
                ("Disrupted class repeatedly", "medium"),
                ("Late submission of coursework", "low"),
                ("Altercation in playground", "high"),
                ("Missing school property", "medium"),
            ]
            for summary, sev in incidents:
                if students:
                    DisciplineIncident.objects.create(
                        student=random.choice(students),
                        reported_by=admin,
                        summary=summary,
                        severity=sev,
                        escalated=False,
                    )
            self.stdout.write(f"  OK: {DisciplineIncident.objects.count()} incidents")

        self.stdout.write(self.style.SUCCESS("--- Demo data seeded successfully! ---"))
        self.stdout.write("   Login: admin / admin123")
        self.stdout.write("   URL:   http://127.0.0.1:8000/")
