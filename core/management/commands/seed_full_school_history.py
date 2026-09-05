"""
seed_full_school_history -- Full local-only data reset and sequential re-seed.

Usage:
    python manage.py seed_full_school_history --i-am-sure-this-is-local

Safety:
    - ABORTS if DJANGO_USE_SQLITE is not set or DEBUG is False
    - ABORTS if database engine is not sqlite3
    - Backs up current DB before flushing
    - Idempotent: safe to re-run (flushes first)
"""
from __future__ import annotations

import os
import shutil
import random
import hashlib
from datetime import date, time, timedelta, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Full local-only reset + sequential re-seed (2025 -> 2026)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--i-am-sure-this-is-local",
            action="store_true",
            required=True,
            help="Safety flag: confirms this is a local dev database.",
        )
        parser.add_argument(
            "--no-backup",
            action="store_true",
            help="Skip backup step (not recommended).",
        )

    def handle(self, *args, **options):
        self._safety_check()
        if not options["no_backup"]:
            self._backup_db()
        self._flush()
        self._seed_all()

    #  SAFETY 

    def _safety_check(self):
        use_sqlite = os.getenv("DJANGO_USE_SQLITE", "").lower() in ("1", "true", "yes")
        if not use_sqlite:
            raise CommandError(
                "ABORT: DJANGO_USE_SQLITE is not set. "
                "This command only runs against local SQLite dev database."
            )
        from django.conf import settings
        if not settings.DEBUG:
            raise CommandError(
                "ABORT: DEBUG=False. This command only runs in debug/development mode."
            )
        db_engine = settings.DATABASES["default"]["ENGINE"]
        if "sqlite" not in db_engine:
            raise CommandError(
                f"ABORT: Database engine is {db_engine}, not SQLite. "
                "This command only runs against local SQLite dev database."
            )
        self.stdout.write(self.style.SUCCESS(
            "SAFETY CHECK PASSED: SQLite dev database confirmed."
        ))

    def _backup_db(self):
        from django.conf import settings
        db_path = str(settings.DATABASES["default"]["NAME"])
        if not os.path.exists(db_path):
            self.stdout.write("No existing DB to backup.")
            return
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.backup_{ts}"
        shutil.copy2(db_path, backup_path)
        size_mb = os.path.getsize(backup_path) / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(
            f"BACKUP: {backup_path} ({size_mb:.1f} MB)"
        ))

    def _flush(self):
        from django.core.management import call_command
        self.stdout.write("Flushing all data...")
        call_command("flush", "--no-input", verbosity=0)
        self.stdout.write(self.style.SUCCESS("FLUSH COMPLETE"))

    #  MAIN SEED 

    def _seed_all(self):
        self.stdout.write(self.style.WARNING("\n" + "=" * 60))
        self.stdout.write(self.style.WARNING("  SEEDING FULL SCHOOL HISTORY"))
        self.stdout.write(self.style.WARNING("=" * 60))

        # Step 1: Superuser + Academic Years + Terms
        self._step1_academic_years()

        # Step 2: Staff
        self._step2_staff()

        # Step 3: Classes, Students, Enrollment (messy)
        self._step3_classes_and_students()

        # Step 4: Attendance with variance
        self._step4_attendance()

        # Step 5: Scores with gaps
        self._step5_scores()

        # Step 6: Welfare + Communications
        self._step6_welfare_and_comms()

        # Step 6b: Discipline, Lesson Plans, Reports, Finance, Tasks
        self._step6b_remaining_models()

        # Step 7: Progression
        self._step7_progression()

        # Step 8: Roll into 2026
        self._step8_roll_into_2026()

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("  SEED COMPLETE"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self._print_summary()

    #  STEP 1: ACADEMIC YEARS + TERMS 

    def _step1_academic_years(self):
        self.stdout.write(self.style.WARNING("\n STEP 1: Academic Years + Terms "))

        from academics.models import AcademicYear, Term, Subject, GradeClass, ExamTypeConfiguration
        from users.models import User, UserRole

        # Superuser
        created_su = False
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser(
                username="admin", email="admin@hodari.local",
                password="admin1234", first_name="System", last_name="Admin",
                role=UserRole.SUPER_ADMIN,
            )
            created_su = True
        self.admin_user = User.objects.get(username="admin")
        if created_su:
            self.stdout.write("  Created superuser: admin / admin1234")

        # Academic Year 2025
        ay2025, _ = AcademicYear.objects.get_or_create(
            name="2025",
            defaults={
                "is_current": False,
                "number_of_terms": 3,
                "start_date": date(2025, 1, 6),
                "end_date": date(2025, 12, 19),
            },
        )

        terms_2025 = []
        term_defs_2025 = [
            ("Term 1", date(2025, 1, 6), date(2025, 3, 28), date(2025, 4, 4),
             date(2025, 2, 10), date(2025, 2, 14),
             date(2025, 3, 24), date(2025, 3, 28)),
            ("Term 2", date(2025, 4, 28), date(2025, 6, 27), date(2025, 7, 4),
             date(2025, 5, 19), date(2025, 5, 23),
             date(2025, 6, 23), date(2025, 6, 27)),
            ("Term 3", date(2025, 7, 21), date(2025, 10, 24), date(2025, 10, 31),
             date(2025, 8, 25), date(2025, 8, 29),
             date(2025, 10, 20), date(2025, 10, 24)),
        ]
        for (name, sd, ed, gd, mt_s, mt_e, et_s, et_e) in term_defs_2025:
            t, _ = Term.objects.get_or_create(
                academic_year=ay2025, name=name,
                defaults={
                    "start_date": sd, "end_date": ed, "grading_deadline": gd,
                    "midterm_exam_start_date": mt_s, "midterm_exam_end_date": mt_e,
                    "endterm_exam_start_date": et_s, "endterm_exam_end_date": et_e,
                },
            )
            terms_2025.append(t)
        self.stdout.write(f"  Created AY 2025 with {len(terms_2025)} terms")

        # Academic Year 2026 (current)
        ay2026, _ = AcademicYear.objects.get_or_create(
            name="2026",
            defaults={
                "is_current": True,
                "number_of_terms": 3,
                "start_date": date(2026, 1, 5),
                "end_date": date(2026, 12, 18),
            },
        )
        # Flip 2025
        ay2025.is_current = False
        ay2025.save(update_fields=["is_current"])

        terms_2026 = []
        term_defs_2026 = [
            ("Term 1", date(2026, 1, 5), date(2026, 3, 27), date(2026, 4, 3),
             date(2026, 2, 9), date(2026, 2, 13),
             date(2026, 3, 23), date(2026, 3, 27)),
            ("Term 2", date(2026, 4, 27), date(2026, 6, 26), date(2026, 7, 3),
             date(2026, 5, 18), date(2026, 5, 22),
             date(2026, 6, 22), date(2026, 6, 26)),
            ("Term 3", date(2026, 7, 20), date(2026, 10, 23), date(2026, 10, 30),
             date(2026, 8, 24), date(2026, 8, 28),
             date(2026, 10, 19), date(2026, 10, 23)),
        ]
        for (name, sd, ed, gd, mt_s, mt_e, et_s, et_e) in term_defs_2026:
            t, _ = Term.objects.get_or_create(
                academic_year=ay2026, name=name,
                defaults={
                    "start_date": sd, "end_date": ed, "grading_deadline": gd,
                    "midterm_exam_start_date": mt_s, "midterm_exam_end_date": mt_e,
                    "endterm_exam_start_date": et_s, "endterm_exam_end_date": et_e,
                },
            )
            terms_2026.append(t)
        self.stdout.write(f"  Created AY 2026 with {len(terms_2026)} terms")

        # Ensure ExamTypeConfiguration exists
        exam_types = [
            ("Quiz", "quiz", 20, 100, 1),
            ("Mid-Term", "mid_term", 30, 100, 2),
            ("End-Term", "end_of_term", 50, 100, 3),
        ]
        for name, code, weight, max_score, order in exam_types:
            ExamTypeConfiguration.objects.get_or_create(
                code=code,
                defaults={
                    "name": name, "weight_percentage": weight,
                    "max_score": max_score, "is_active": True,
                    "display_order": order,
                },
            )
        self.stdout.write("  ExamTypeConfiguration seeded")

        # Subjects
        subjects_data = [
            ("Mathematics", "MATH", "#023AA5", "PRIMARY", ["PRIMARY"]),
            ("English", "ENG", "#1565C0", "PRIMARY", ["PRIMARY"]),
            ("Science", "SCI", "#2E7D32", "PRIMARY", ["PRIMARY"]),
            ("Social Studies", "SST", "#E65100", "PRIMARY", ["PRIMARY"]),
            ("Art", "ART", "#7B1FA2", "PRIMARY", ["PRIMARY"]),
            ("Physical Education", "PE", "#C62828", "PRIMARY", ["PRIMARY"]),
            ("ICT", "ICT", "#00838F", "PRIMARY", ["PRIMARY"]),
            ("Kiswahili", "KIS", "#283593", "PRIMARY", ["PRIMARY"]),
            ("Pre-Kindergarten Activities", "PKA", "#FF8F00", "ECD", ["ECD"]),
            ("Kindergarten Activities", "KA", "#FF6F00", "ECD", ["ECD"]),
            ("Pre-School Activities", "PSA", "#F57F17", "ECD", ["ECD"]),
            ("ABC Activities", "ABC", "#EF6C00", "ECD", ["ECD"]),
        ]
        for name, code, color, dept, depts in subjects_data:
            Subject.objects.get_or_create(
                name=name,
                defaults={"code": code, "color": color, "department": dept, "departments": depts},
            )
        self.stdout.write(f"  Created {len(subjects_data)} subjects")

        # Store for later steps
        self.ay2025 = ay2025
        self.ay2026 = ay2026
        self.terms_2025 = terms_2025
        self.terms_2026 = terms_2026

    #  STEP 2: STAFF 

    def _step2_staff(self):
        self.stdout.write(self.style.WARNING("\n STEP 2: Staff "))

        from users.models import User, UserRole
        from hr.models import StaffProfile, TeacherClassAssignment, OnboardingChecklistItem, StaffOnboardingProgress

        staff_data = [
            # (username, first, last, role, job_title, department)
            ("teacher.mmath", "James", "Mwangi", UserRole.TEACHER, "Math Teacher", "PRIMARY"),
            ("teacher.english", "Sarah", "Otieno", UserRole.TEACHER, "English Teacher", "PRIMARY"),
            ("teacher.science", "Peter", "Kamau", UserRole.TEACHER, "Science Teacher", "PRIMARY"),
            ("teacher.social", "Mary", "Wanjiku", UserRole.TEACHER, "Social Studies Teacher", "PRIMARY"),
            ("teacher.art", "Grace", "Njeri", UserRole.TEACHER, "Art Teacher", "PRIMARY"),
            ("teacher.pe", "David", "Odhiambo", UserRole.TEACHER, "PE Teacher", "PRIMARY"),
            ("teacher.icd", "John", "Kioko", UserRole.TEACHER, "ICT Teacher", "PRIMARY"),
            ("teacher.kiswahili", "Alice", "Akinyi", UserRole.TEACHER, "Kiswahili Teacher", "PRIMARY"),
            ("teacher.ecd1", "Fatma", "Hassan", UserRole.TEACHER, "ECD Teacher", "ECD"),
            ("teacher.ecd2", "Esther", "Mushi", UserRole.TEACHER, "ECD Teacher", "ECD"),
            ("teacher.ecd3", "Joseph", "Baraka", UserRole.TEACHER, "ECD Teacher", "ECD"),
            ("teacher.lower1", "Daniel", "Mwas", UserRole.TEACHER, "Lower Secondary Teacher", "LOWER_SECONDARY"),
            ("hod.primary", "Michael", "Ouma", UserRole.PRIMARY_HOD, "Primary HOD", "PRIMARY"),
            ("hod.ecd", "Agnes", "Kimaro", UserRole.ECD_HOD, "ECD HOD", "ECD"),
            ("hod.lower_secondary", "Samuel", "Kiula", UserRole.LOWER_SECONDARY_HOD, "Lower Secondary HOD", "LOWER_SECONDARY"),
            ("hos", "Dr. Robert", "Temu", UserRole.HEAD_OF_SCHOOL, "Head of School", "ADMINISTRATION"),
            ("admin.officer", "Linda", "Mwakio", UserRole.ADMIN_OFFICER, "Admin Officer", "ADMINISTRATION"),
            ("finance.officer", "Charles", "Mwamba", UserRole.FINANCE_OFFICER, "Finance Officer", "ADMINISTRATION"),
        ]

        self.staff_users = {}
        for uname, first, last, role, job, dept in staff_data:
            user, created = User.objects.get_or_create(
                username=uname,
                defaults={
                    "first_name": first, "last_name": last, "role": role,
                    "email": f"{uname}@hodari.local", "is_active": True,
                },
            )
            if created:
                user.set_password("test1234")
                user.save()
            self.staff_users[uname] = user

            # Staff profile
            sp, _ = StaffProfile.objects.get_or_create(
                user=user,
                defaults={
                    "full_name": f"{first} {last}",
                    "department": dept,
                    "job_title": job,
                    "employment_start_date": date(2024, 8, 1),
                    "is_active": True,
                    "onboarding_completed": True,
                },
            )

        # Onboarding -- mark complete
        for uname, user in self.staff_users.items():
            sp = StaffProfile.objects.filter(user=user).first()
            if sp:
                StaffOnboardingProgress.objects.get_or_create(
                    staff=sp,
                    defaults={"is_completed": True, "completed_at": timezone.now()},
                )

        self.stdout.write(f"  Created {len(staff_data)} staff users + profiles")
        self._create_tca()

    def _create_tca(self):
        from academics.models import GradeClass, Subject, Term
        from hr.models import StaffProfile, TeacherClassAssignment

        term_2025_t1 = self.terms_2025[0]

        primary_subjects = ["Mathematics", "English", "Science", "Social Studies", "Art", "Physical Education", "ICT", "Kiswahili"]
        ecd_subjects = ["Pre-Kindergarten Activities", "Kindergarten Activities", "Pre-School Activities", "ABC Activities"]
        lower_sec_subjects = ["Mathematics", "English", "Science", "Social Studies"]

        primary_classes = ["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6"]
        ecd_classes = ["Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"]
        lower_sec_classes = ["Grade 7", "Grade 8"]

        # Create GradeClasses
        for name in primary_classes:
            GradeClass.objects.get_or_create(name=name, defaults={"department": "PRIMARY", "max_capacity": 40})
        for name in ecd_classes:
            GradeClass.objects.get_or_create(name=name, defaults={"department": "ECD", "max_capacity": 25})
        for i, name in enumerate(lower_sec_classes):
            GradeClass.objects.get_or_create(name=name, defaults={"department": "LOWER_SECONDARY", "max_capacity": 35, "sort_order": 7 + i})

        # Link subjects to their grade classes
        all_gc = {gc.name: gc for gc in GradeClass.objects.all()}
        for subj in Subject.objects.all():
            if subj.department == "PRIMARY":
                target = [gc for n, gc in all_gc.items() if gc.department == "PRIMARY"]
            elif subj.department == "LOWER_SECONDARY":
                target = [gc for n, gc in all_gc.items() if gc.department == "LOWER_SECONDARY"]
            else:
                target = [gc for n, gc in all_gc.items() if gc.department == "ECD"]
            subj.classes.set(target)

        # Assign primary teachers: each teaches 1-2 subjects across 1-2 classes
        primary_teachers = [
            ("teacher.mmath", ["Grade 1", "Grade 2"], ["Mathematics"]),
            ("teacher.english", ["Grade 1", "Grade 3"], ["English"]),
            ("teacher.science", ["Grade 2", "Grade 4"], ["Science"]),
            ("teacher.social", ["Grade 3", "Grade 5"], ["Social Studies"]),
            ("teacher.art", ["Grade 4", "Grade 6"], ["Art"]),
            ("teacher.pe", ["Grade 1", "Grade 2", "Grade 3"], ["Physical Education"]),
            ("teacher.icd", ["Grade 5", "Grade 6"], ["ICT"]),
            ("teacher.kiswahili", ["Grade 4", "Grade 5", "Grade 6"], ["Kiswahili"]),
        ]
        for uname, classes, subjects in primary_teachers:
            user = self.staff_users[uname]
            sp = StaffProfile.objects.get(user=user)
            for cname in classes:
                gc = GradeClass.objects.get(name=cname)
                TeacherClassAssignment.objects.get_or_create(
                    teacher=sp, term=term_2025_t1, grade_class=gc,
                    defaults={
                        "is_class_teacher": (subjects[0] == "Mathematics"),
                        "subjects_taught": subjects,
                    },
                )

        # ECD teachers
        ecd_assignments = [
            ("teacher.ecd1", "Pre-Kindergarten", ["Pre-Kindergarten Activities"]),
            ("teacher.ecd2", "Kindergarten", ["Kindergarten Activities"]),
            ("teacher.ecd3", "Pre-School", ["Pre-School Activities"]),
            ("hod.ecd", "ABC Class", ["ABC Activities"]),
        ]
        for uname, cname, subjects in ecd_assignments:
            user = self.staff_users[uname]
            sp = StaffProfile.objects.get(user=user)
            gc = GradeClass.objects.get(name=cname)
            TeacherClassAssignment.objects.get_or_create(
                teacher=sp, term=term_2025_t1, grade_class=gc,
                defaults={"is_class_teacher": True, "subjects_taught": subjects},
            )

        # Lower secondary teachers
        lower_sec_assignments = [
            ("teacher.lower1", "Grade 7", ["Mathematics", "English", "Science", "Social Studies"]),
            ("hod.lower_secondary", "Grade 8", ["Mathematics", "English", "Science", "Social Studies"]),
        ]
        for uname, cname, subjects in lower_sec_assignments:
            user = self.staff_users[uname]
            sp = StaffProfile.objects.get(user=user)
            gc = GradeClass.objects.get(name=cname)
            TeacherClassAssignment.objects.get_or_create(
                teacher=sp, term=term_2025_t1, grade_class=gc,
                defaults={"is_class_teacher": True, "subjects_taught": subjects},
            )

        # Also create TCA for 2025 T2/T3 (same assignments)
        for term in self.terms_2025[1:]:
            for tca in TeacherClassAssignment.objects.filter(term=term_2025_t1):
                TeacherClassAssignment.objects.get_or_create(
                    teacher=tca.teacher, term=term, grade_class=tca.grade_class,
                    defaults={
                        "is_class_teacher": tca.is_class_teacher,
                        "subjects_taught": tca.subjects_taught,
                    },
                )

        # Also create TCA for all 2026 terms (same assignments)
        for term in self.terms_2026:
            for tca in TeacherClassAssignment.objects.filter(term=term_2025_t1):
                TeacherClassAssignment.objects.get_or_create(
                    teacher=tca.teacher, term=term, grade_class=tca.grade_class,
                    defaults={
                        "is_class_teacher": tca.is_class_teacher,
                        "subjects_taught": tca.subjects_taught,
                    },
                )

        self.stdout.write("  TeacherClassAssignments created for all 2025 & 2026 terms")

        # Timetable slots
        self._create_timetable_slots()

    def _create_timetable_slots(self):
        from academics.models import Subject, GradeClass
        from timetable.models import TimetableSlot
        from hr.models import TeacherClassAssignment

        days = ["mon", "tue", "wed", "thu", "fri"]
        periods = [
            (time(8, 0), time(8, 45)),
            (time(8, 50), time(9, 35)),
            (time(9, 40), time(10, 25)),
            (time(10, 25), time(11, 0)),  # break overlap handled by model
            (time(11, 0), time(11, 45)),
            (time(11, 50), time(12, 35)),
            (time(13, 30), time(14, 15)),
            (time(14, 20), time(15, 5)),
        ]

        terms = self.terms_2025 + self.terms_2026
        for term in terms:
            bulk = []
            for tca in TeacherClassAssignment.objects.filter(term=term):
                user = tca.teacher.user
                gc_name = tca.grade_class.name
                subj_names = tca.subjects_taught if isinstance(tca.subjects_taught, list) else []
                for subj_name in subj_names:
                    subj_obj = Subject.objects.filter(name=subj_name).first()
                    num_periods = random.randint(2, 4)
                    chosen_days = random.sample(days, k=min(num_periods, len(days)))
                    for d in chosen_days:
                        start, end = random.choice(periods)
                        bulk.append(TimetableSlot(
                            term=term,
                            class_name=gc_name,
                            subject=subj_obj,
                            subject_name=subj_name,
                            teacher=user,
                            day_of_week=d,
                            start_time=start,
                            end_time=end,
                        ))
            TimetableSlot.objects.bulk_create(bulk, ignore_conflicts=True, batch_size=500)
        total = TimetableSlot.objects.count()
        self.stdout.write(f"  Created {total} timetable slots across all terms")

    #  STEP 3: CLASSES, STUDENTS, ENROLLMENT (MESSY) 

    def _step3_classes_and_students(self):
        self.stdout.write(self.style.WARNING("\n STEP 3: Students + Enrollment (messy) "))

        from academics.models import GradeClass, Term
        from students.models import Student, ParentGuardian, StudentGuardian, EnrollmentHistory
        from admissions.models import Applicant, AssessmentSchedule, ApplicantTimelineEntry
        from users.models import User, UserRole

        first_names_m = [
            "David", "Marcus", "Omar", "Victor", "Xavier", "Noah", "Ethan", "Liam",
            "Benjamin", "Samuel", "Daniel", "Matthew", "Joseph", "Andrew", "Timothy",
            "Aaron", "Brian", "Carlos", "Emmanuel", "Felix", "George", "Henry",
            "Isaac", "Jacob", "Kevin", "Luke", "Michael", "Nathan", "Oliver", "Patrick",
            "Raymond", "Simon", "Thomas", "Vincent", "William",
        ]
        first_names_f = [
            "Maria", "Alice", "Grace", "Fatma", "Esther", "Sarah", "Mary", "Agnes",
            "Janet", "Joyce", "Claire", "Diana", "Evelyn", "Fiona", "Hannah",
            "Irene", "Juliet", "Karen", "Lydia", "Monica", "Nancy", "Patricia",
            "Queen", "Rachel", "Sandra", "Teresa", "Unity", "Valerie", "Wendy", "Zainab",
        ]
        last_names = [
            "Akinyi", "Mwangi", "Otieno", "Kamau", "Wanjiku", "Njeri", "Odhiambo",
            "Kioko", "Hassan", "Mushi", "Baraka", "Ouma", "Kimaro", "Temu",
            "Mwakio", "Mwamba", "Ochieng", "Onyango", "Simiyu", "Wekesa",
            "Mutua", "Kilonzo", "Kyalo", "Muia", "Ndung'u", "Njoroge", "Gichuru",
            "Maina", "Kariuki", "Njenga",
        ]

        all_classes = list(GradeClass.objects.all().order_by("name"))
        primary_classes = [c for c in all_classes if c.department == "PRIMARY"]
        ecd_classes = [c for c in all_classes if c.department == "ECD"]

        students_per_class = {}
        all_students = []
        guardians_created = []
        student_idx = 0

        # Seed students per class with some variation
        for gc in all_classes:
            if gc.department == "ECD":
                count = random.randint(12, 18)
            else:
                count = random.randint(28, 38)

            for i in range(count):
                gender = random.choice(["male", "female"])
                fn = random.choice(first_names_m if gender == "male" else first_names_f)
                ln = random.choice(last_names)
                adm_no = f"ADM-{2025}-{student_idx + 1:03d}"
                student_idx += 1

                # Random enrollment date: most on day 1, some late
                if random.random() < 0.85:
                    enroll_date = gc.name in [c.name for c in primary_classes] and date(2025, 1, 6) or date(2025, 1, 6)
                    enroll_term = self.terms_2025[0]
                elif random.random() < 0.5:
                    enroll_date = date(2025, 2, random.randint(1, 28))
                    enroll_term = self.terms_2025[0]
                else:
                    enroll_date = date(2025, random.choice([4, 7, 9]), random.randint(1, 28))
                    enroll_term = self.terms_2025[1] if enroll_date.month <= 6 else self.terms_2025[2]

                dob_year = random.randint(2015, 2021) if gc.department == "PRIMARY" else random.randint(2019, 2023)
                dob = date(dob_year, random.randint(1, 12), random.randint(1, 28))

                student, _ = Student.objects.get_or_create(
                    admission_no=adm_no,
                    defaults={
                        "first_name": fn, "last_name": ln,
                        "date_of_birth": dob, "gender": gender,
                        "class_name": gc.name, "status": "active",
                        "enrolment_date": enroll_date,
                        "academic_year": self.ay2025,
                        "nationality": "Tanzanian",
                    },
                )
                all_students.append(student)
                students_per_class.setdefault(gc.name, []).append(student)

                # Guardian -- share some guardians across students to create multi-child households
                if len(guardians_created) < 20 or random.random() < 0.15:
                    if guardians_created and random.random() < 0.3:
                        # Reuse an existing guardian (multi-child household)
                        pg = random.choice(guardians_created)
                    else:
                        g_fn = random.choice(["John", "Peter", "James", "Robert", "William", "Margaret", "Rose", "Anne", "Jane", "Catherine"])
                        g_ln = ln  # same last name as student
                        phone = f"+255{random.randint(600000000, 799999999)}"
                        pg, _ = ParentGuardian.objects.get_or_create(
                            phone=phone,
                            defaults={
                                "full_name": f"{g_fn} {g_ln}",
                                "email": f"{g_fn.lower()}.{g_ln.lower()}@email.com",
                            },
                        )
                        guardians_created.append(pg)
                    StudentGuardian.objects.get_or_create(
                        student=student, guardian=pg,
                        defaults={"relationship": "parent", "is_primary": True},
                    )

                # EnrollmentHistory
                EnrollmentHistory.objects.get_or_create(
                    student=student, academic_year=self.ay2025,
                    defaults={
                        "term": enroll_term,
                        "class_name": gc.name,
                        "action": "enrolled",
                        "enrolled_at": datetime.combine(enroll_date, time(8, 0)),
                    },
                )

        self.stdout.write(f"  Created {len(all_students)} students across {len(all_classes)} classes")
        self.all_students = all_students
        self.students_per_class = students_per_class

        # Admissions pipeline: messy incomplete applicants
        self._seed_admissions_pipeline()

    def _seed_admissions_pipeline(self):
        from admissions.models import Applicant, AssessmentSchedule, ApplicantTimelineEntry

        pipeline_data = [
            # Stuck at inquiry_received
            ("Grace Mwangi", "+255700000001", "Grade 1", "inquiry_received"),
            ("Samuel Ochieng", "+255700000002", "Grade 2", "inquiry_received"),
            ("Nancy Kimaro", "+255700000003", "Grade 3", "inquiry_received"),
            # Stuck at assessment_pending
            ("Felix Wanjiku", "+255700000004", "Grade 1", "assessment_pending"),
            ("Hannah Njoroge", "+255700000005", "Grade 4", "assessment_pending"),
            # Admitted but not yet enrolled
            ("Brian Simiyu", "+255700000006", "Grade 2", "admitted"),
            ("Claire Wekesa", "+255700000007", "Grade 5", "admitted"),
            # Enrolled (completed pipeline)
            ("Isaac Kilonzo", "+255700000008", "Grade 3", "enrolled"),
            ("Juliet Kyalo", "+255700000009", "Grade 6", "enrolled"),
        ]

        for name, phone, grade, status in pipeline_data:
            fn, ln = name.split(" ", 1)
            applicant, created = Applicant.objects.get_or_create(
                child_full_name=name,
                defaults={
                    "parent_full_name": f"{fn} {ln}",
                    "parent_phone": phone,
                    "parent_email": f"parent.{ln.lower()}@email.com",
                    "child_date_of_birth": date(random.randint(2015, 2019), random.randint(1, 12), random.randint(1, 28)),
                    "grade_applying_for": grade,
                    "inquiry_channel": "walk_in",
                    "status": status,
                },
            )
            if created:
                admin_user = self.staff_users.get("admin", list(self.staff_users.values())[0])
                ApplicantTimelineEntry.objects.create(
                    applicant=applicant,
                    actor=admin_user,
                    from_status="inquiry_received",
                    to_status=status,
                    reason="Seeded pipeline data",
                )
                if status == "assessment_scheduled":
                    AssessmentSchedule.objects.create(
                        applicant=applicant,
                        scheduled_date=date(2025, random.randint(1, 6), random.randint(1, 28)),
                        scheduled_time=time(9, 0),
                        location="Main Campus",
                    )

        self.stdout.write(f"  Created {len(pipeline_data)} pipeline applicants (stuck + completed)")

    #  STEP 4: ATTENDANCE WITH REAL VARIANCE 

    def _step4_attendance(self):
        self.stdout.write(self.style.WARNING("\n STEP 4: Attendance (realistic variance) "))

        from academics.models import Term, GradeClass
        from students.models import Student
        from attendance.models import AttendanceEntry

        total_entries = 0

        for term in self.terms_2025:
            school_days = self._get_school_days(term.start_date, term.end_date)
            self.stdout.write(f"  {term.name}: {len(school_days)} school days")

            for gc_name, students in self.students_per_class.items():
                flu_week_start = school_days[random.randint(0, max(0, len(school_days) - 5))]
                flu_week_end = flu_week_start + timedelta(days=4)
                perfect_week_start = school_days[random.randint(0, max(0, len(school_days) - 5))]
                perfect_week_end = perfect_week_start + timedelta(days=4)
                skip_day = random.choice(school_days) if random.random() < 0.15 else None

                bulk = []
                for student in students:
                    start_date = student.enrolment_date or term.start_date
                    if start_date < term.start_date:
                        start_date = term.start_date

                    for day in school_days:
                        if day < start_date:
                            continue
                        if day == skip_day:
                            continue

                        in_flu_week = flu_week_start <= day <= flu_week_end
                        in_perfect_week = perfect_week_start <= day <= perfect_week_end

                        if in_perfect_week:
                            status = "present"
                        elif in_flu_week and random.random() < 0.4:
                            status = random.choice(["absent", "absent", "late"])
                        else:
                            r = random.random()
                            if r < 0.88:
                                status = "present"
                            elif r < 0.94:
                                status = "late"
                            elif r < 0.97:
                                status = "absent"
                            else:
                                status = "excused"

                        check_in = time(random.randint(7, 8), random.randint(0, 29)) if status != "absent" else None
                        check_out = time(15, random.randint(0, 30)) if status != "absent" else None

                        bulk.append(AttendanceEntry(
                            student=student, date=day,
                            status=status,
                            class_name=gc_name,
                            check_in_time=check_in,
                            check_out_time=check_out,
                            marked_by=self.admin_user,
                        ))

                AttendanceEntry.objects.bulk_create(bulk, ignore_conflicts=True, batch_size=500)
                total_entries += len(bulk)

        self.stdout.write(f"  Created {total_entries} attendance entries")

    def _get_school_days(self, start: date, end: date) -> list[date]:
        """Get weekdays within date range, excluding obvious holidays."""
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # Mon-Fri
                days.append(current)
            current += timedelta(days=1)
        return days

    #  STEP 5: SCORES WITH GAPS 

    def _step5_scores(self):
        self.stdout.write(self.style.WARNING("\n STEP 5: Scores (with gaps) "))

        from academics.models import Term, ExamScore, ExamTypeConfiguration, ScoreStatus, GradeClass
        from students.models import Student
        from users.models import User

        primary_subjects = ["Mathematics", "English", "Science", "Social Studies", "Art", "Physical Education", "ICT", "Kiswahili"]
        ecd_subjects = ["Pre-Kindergarten Activities", "Kindergarten Activities", "Pre-School Activities", "ABC Activities"]

        quiz_type = ExamTypeConfiguration.objects.get(code="quiz")
        midterm_type = ExamTypeConfiguration.objects.get(code="mid_term")
        endterm_type = ExamTypeConfiguration.objects.get(code="end_of_term")

        total_scores = 0

        for term in self.terms_2025:
            for gc_name, students in self.students_per_class.items():
                is_ecd = gc_name in ["Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"]
                subjects = ecd_subjects if is_ecd else primary_subjects

                from hr.models import TeacherClassAssignment
                tca = TeacherClassAssignment.objects.filter(
                    grade_class__name=gc_name, term=term,
                ).first()
                entered_by = tca.teacher.user if tca else self.admin_user

                bulk = []
                for student in students:
                    if student.enrolment_date and student.enrolment_date > term.end_date:
                        continue

                    for subj in subjects:
                        if random.random() < 0.92:
                            score_val = self._gen_score()
                            bulk.append(ExamScore(
                                student=student, term=term, subject_name=subj, exam_type="quiz",
                                score=score_val,
                                entered_by=entered_by,
                                status=ScoreStatus.APPROVED,
                                is_locked=True,
                                exam_type_config=quiz_type,
                                approved_by=entered_by,
                                approved_at=timezone.now(),
                            ))
                            total_scores += 1

                        if random.random() < 0.88:
                            score_val = self._gen_score()
                            status = random.choice([
                                ScoreStatus.APPROVED,
                                ScoreStatus.APPROVED,
                                ScoreStatus.SUBMITTED,
                            ])
                            bulk.append(ExamScore(
                                student=student, term=term, subject_name=subj, exam_type="mid_term",
                                score=score_val,
                                entered_by=entered_by,
                                status=status,
                                is_locked=(status == ScoreStatus.APPROVED),
                                exam_type_config=midterm_type,
                            ))
                            total_scores += 1

                        if random.random() < 0.82:
                            score_val = self._gen_score()
                            status = random.choice([
                                ScoreStatus.APPROVED,
                                ScoreStatus.SUBMITTED,
                                ScoreStatus.SUBMITTED,
                                ScoreStatus.DRAFT,
                            ])
                            bulk.append(ExamScore(
                                student=student, term=term, subject_name=subj, exam_type="end_of_term",
                                score=score_val,
                                entered_by=entered_by,
                                status=status,
                                is_locked=(status == ScoreStatus.APPROVED),
                                exam_type_config=endterm_type,
                            ))
                            total_scores += 1

                ExamScore.objects.bulk_create(bulk, ignore_conflicts=True, batch_size=500)

        self.stdout.write(f"  Created {total_scores} exam scores")

    def _gen_score(self) -> int:
        """Generate realistic score: mostly 50-85, occasional outliers."""
        r = random.random()
        if r < 0.05:
            return random.randint(15, 35)  # Low outliers
        elif r < 0.15:
            return random.randint(36, 55)
        elif r < 0.80:
            return random.randint(56, 80)
        elif r < 0.95:
            return random.randint(81, 95)
        else:
            return random.randint(96, 100)  # High outliers

    #  STEP 6: WELFARE + COMMUNICATIONS

    def _step6_welfare_and_comms(self):
        self.stdout.write(self.style.WARNING("\n STEP 6: Welfare + Communications "))

        from welfare.models import WelfareObservation
        from communications.models import Notification, Broadcast
        from students.models import Student

        # Welfare observations
        concern_types = ["behavioral", "health", "attendance", "academic", "home_situation", "other"]
        severities = ["low", "medium", "high", "critical"]
        observations_text = [
            "Student seems distracted in class lately",
            "Frequent absence noted this term",
            "Excellent improvement in participation",
            "Parent requested meeting about progress",
            "Student reported bullying incident",
            "Health concern: recurring headaches",
            "Outstanding performance in group activities",
            "Student showing signs of fatigue",
            "Late submission of assignments becoming habitual",
            "Positive peer interaction observed during break",
        ]
        actions_taken = [
            "Spoke with student individually",
            "Contacted parent via phone",
            "Referred to school counselor",
            "Implemented behavior plan",
            "Monitored attendance closely",
            "Sent progress report to parent",
            "Coordinated with class teacher",
            "",
            "",
            "",
        ]

        welfare_bulk = []
        for student in Student.objects.filter(status="active")[:80]:
            num_obs = random.randint(0, 3)
            for _ in range(num_obs):
                obs_date = date(2025, random.randint(1, 12), random.randint(1, 28))
                welfare_bulk.append(WelfareObservation(
                    student=student,
                    submitted_by=self.admin_user,
                    concern_type=random.choice(concern_types),
                    severity=random.choice(severities),
                    observation_date=obs_date,
                    observation_text=random.choice(observations_text),
                    action_taken=random.choice(actions_taken),
                    parent_contacted=random.random() < 0.6,
                    follow_up_required=random.random() < 0.3,
                    hod_status=random.choice(["pending", "in_progress", "resolved"]),
                    parent_signed=random.random() < 0.5,
                ))

        WelfareObservation.objects.bulk_create(welfare_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(welfare_bulk)} welfare observations")

        # Notifications
        notif_categories = ["admissions", "attendance", "finance", "academic", "welfare", "system"]
        notif_titles = [
            "New applicant inquiry received",
            "Attendance report ready for review",
            "Fee payment reminder overdue",
            "Exam scores awaiting approval",
            "Welfare observation requires attention",
            "System maintenance scheduled",
            "Parent meeting request",
            "Staff meeting reminder",
        ]
        users = list(self.staff_users.values())
        notif_bulk = []
        for _ in range(40):
            notif_bulk.append(Notification(
                recipient=random.choice(users),
                category=random.choice(notif_categories),
                title=random.choice(notif_titles),
                body="This is an automated notification from the Hodari school management system.",
                is_read=random.random() < 0.4,
            ))
        Notification.objects.bulk_create(notif_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(notif_bulk)} notifications")

        # Broadcasts
        broadcast_bulk = []
        for i in range(5):
            broadcast_bulk.append(Broadcast(
                created_by=self.admin_user,
                audience=random.choice(["all_parents", "grade", "staff"]),
                subject=f"School Update #{i + 1}",
                body="Dear parents and guardians, this is an important update from Hodari Christian School regarding upcoming events and academic activities.",
                status="sent",
                sent_at=timezone.now() - timedelta(days=random.randint(1, 30)),
                recipient_count=random.randint(50, 200),
            ))
        Broadcast.objects.bulk_create(broadcast_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(broadcast_bulk)} broadcasts")

    #  STEP 6b: DISCIPLINE, LESSON PLANS, REPORTS, FINANCE, TASKS

    def _step6b_remaining_models(self):
        self.stdout.write(self.style.WARNING("\n STEP 6b: Discipline, Lesson Plans, Reports, Finance, Tasks "))

        from students.models import Student
        from users.models import User

        active_students = list(Student.objects.filter(status="active"))
        teachers = list(User.objects.filter(role="teacher"))

        # --- Discipline Incidents ---
        from discipline.models import DisciplineIncident
        summaries = [
            "Disruptive behavior during mathematics lesson",
            "Talking during assembly without permission",
            "Late to school three times this week",
            "Not wearing proper uniform",
            "Bullying incident reported by classmate",
            "Refusing to follow teacher instructions",
            "Running in the corridors",
            "Using phone during class time",
            "Fighting during break time",
            "Homework not submitted for a week",
        ]
        disc_bulk = []
        for student in random.sample(active_students, min(40, len(active_students))):
            disc_bulk.append(DisciplineIncident(
                student=student,
                reported_by=random.choice(teachers) if teachers else self.admin_user,
                severity=random.choice(["low", "medium", "high", "critical"]),
                summary=random.choice(summaries),
                escalated=random.random() < 0.2,
                incident_date=date(2025, random.randint(1, 12), random.randint(1, 28)),
            ))
        DisciplineIncident.objects.bulk_create(disc_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(disc_bulk)} discipline incidents")

        # --- Lesson Plans ---
        from academics.models import LessonPlan, Term
        lesson_bulk = []
        subjects_primary = ["Mathematics", "English", "Science", "Social Studies"]
        for term in self.terms_2025:
            for teacher in teachers[:8]:
                for subj in random.sample(subjects_primary, k=random.randint(1, 3)):
                    for week_offset in range(0, 12, 2):
                        week_date = term.start_date + timedelta(weeks=week_offset)
                        if week_date > term.end_date:
                            break
                        status = random.choice(["draft", "submitted", "approved", "approved", "approved"])
                        lesson_bulk.append(LessonPlan(
                            teacher=teacher,
                            term=term,
                            class_name=random.choice(["Grade 1", "Grade 2", "Grade 3", "Grade 4"]),
                            subject_name=subj,
                            week_start_date=week_date,
                            status=status,
                            objectives="Cover key concepts for the week as per curriculum",
                            activities="Introduction, guided practice, independent work, assessment",
                            assessment_strategy="Class quiz and homework review",
                            resources="Textbook, workbook, chalkboard",
                            reviewed_by=self.admin_user if status == "approved" else None,
                        ))
        LessonPlan.objects.bulk_create(lesson_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(lesson_bulk)} lesson plans")

        # --- Report Cards ---
        from academics.models import ReportCard
        report_bulk = []
        for term in self.terms_2025:
            for student in active_students:
                if student.enrolment_date and student.enrolment_date > term.end_date:
                    continue
                status = random.choice(["draft", "pending_sign_off", "published", "published", "published"])
                report_bulk.append(ReportCard(
                    student=student,
                    term=term,
                    generated_by=self.admin_user,
                    status=status,
                    signed_off_by=self.admin_user if status == "published" else None,
                ))
        ReportCard.objects.bulk_create(report_bulk, ignore_conflicts=True, batch_size=500)
        self.stdout.write(f"  Created {len(report_bulk)} report cards")

        # --- Weekly Focus (ECD) ---
        from communications.models import WeeklyFocus
        ecd_classes = ["Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"]
        ecd_teachers = [t for t in teachers if any(
            t.username.startswith(p) for p in ["teacher.ecd", "hod.ecd"]
        )]
        if not ecd_teachers:
            ecd_teachers = teachers[:4]
        themes = [
            "Colors and Shapes", "Animals and Their Habitats", "My Family",
            "Weather and Seasons", "Numbers 1-20", "Letters and Sounds",
            "Healthy Eating", "Community Helpers", "Water and Conservation",
            "Music and Movement",
        ]
        wf_bulk = []
        for term in self.terms_2025:
            for cls in ecd_classes:
                teacher = random.choice(ecd_teachers)
                for week in range(1, 14):
                    wf_bulk.append(WeeklyFocus(
                        teacher=teacher,
                        class_name=cls,
                        week_number=week,
                        academic_year="2025",
                        theme=random.choice(themes),
                        planned_activities="Circle time, outdoor play, art activity, story time",
                        items_to_bring="Crayons, exercise book",
                        status=random.choice(["draft", "submitted", "approved", "approved"]),
                        is_published=random.random() < 0.6,
                    ))
        WeeklyFocus.objects.bulk_create(wf_bulk, ignore_conflicts=True, batch_size=500)
        self.stdout.write(f"  Created {len(wf_bulk)} weekly focus entries")

        # --- Finance ---
        from finance.models import FinancePeriod, FeeStructure, FeeStructureItem, Invoice, InvoiceLineItem, Payment

        # Finance Periods
        fp1, _ = FinancePeriod.objects.get_or_create(
            name="2025 Term 1",
            defaults={"start_date": date(2025, 1, 6), "end_date": date(2025, 3, 28), "created_by": self.admin_user},
        )
        fp2, _ = FinancePeriod.objects.get_or_create(
            name="2025 Term 2",
            defaults={"start_date": date(2025, 4, 28), "end_date": date(2025, 6, 27), "created_by": self.admin_user},
        )
        fp3, _ = FinancePeriod.objects.get_or_create(
            name="2025 Term 3",
            defaults={"start_date": date(2025, 7, 21), "end_date": date(2025, 10, 24), "created_by": self.admin_user},
        )
        self.stdout.write("  Created 3 finance periods")

        # Fee Structures + Items
        fee_items_data = [
            ("Tuition Fee", 500000),
            ("Development Levy", 100000),
            ("Laboratory Fee", 50000),
            ("Sports Fee", 30000),
            ("Library Fee", 20000),
            ("Insurance", 25000),
        ]
        all_classes = ["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6",
                       "Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"]
        fs_bulk = []
        fsi_bulk = []
        for term in self.terms_2025:
            for cls in all_classes:
                fs, created = FeeStructure.objects.get_or_create(
                    term=term, class_name=cls,
                    defaults={"status": "published"},
                )
                if created:
                    fs_bulk.append(fs)
                    for desc, amt in fee_items_data:
                        fsi_bulk.append(FeeStructureItem(
                            structure=fs, description=desc,
                            amount=Decimal(str(amt)),
                        ))
        FeeStructureItem.objects.bulk_create(fsi_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(fs_bulk)} fee structures + {len(fsi_bulk)} items")

        # Invoices + Line Items + Payments
        inv_bulk = []
        invli_bulk = []
        pay_bulk = []
        for student in random.sample(active_students, min(150, len(active_students))):
            for term in self.terms_2025:
                if student.enrolment_date and student.enrolment_date > term.end_date:
                    continue
                total = Decimal("725000")
                inv = Invoice(
                    student=student,
                    term=term,
                    amount_due=total,
                    total_due=total,
                    status=random.choice(["unpaid", "unpaid", "partial", "paid"]),
                )
                inv_bulk.append(inv)
        Invoice.objects.bulk_create(inv_bulk, batch_size=500)

        # Create line items for created invoices
        for inv in Invoice.objects.select_related("student", "term")[:200]:
            for desc, amt in fee_items_data:
                invli_bulk.append(InvoiceLineItem(
                    invoice=inv, description=desc,
                    amount=Decimal(str(amt)),
                ))
        InvoiceLineItem.objects.bulk_create(invli_bulk, batch_size=500)

        # Payments for paid/partial invoices
        for inv in Invoice.objects.filter(status__in=["paid", "partial"])[:100]:
            pay_amt = inv.total_due if inv.status == "paid" else inv.total_due * Decimal("0.5")
            pay_bulk.append(Payment(
                invoice=inv,
                amount=pay_amt,
                method=random.choice(["cash", "mobile_money", "bank_transfer", "mpesa"]),
                created_by=self.admin_user,
            ))
        Payment.objects.bulk_create(pay_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(inv_bulk)} invoices, {len(invli_bulk)} line items, {len(pay_bulk)} payments")

        # --- Tasks ---
        from tasks.models import Task
        task_types = [
            ("admission_review", "Review new admission application"),
            ("lesson_plan_review", "Review submitted lesson plan"),
            ("grade_approval", "Approve end-of-term grades"),
            ("welfare_alert", "Follow up on welfare observation"),
            ("payment_matching", "Match incoming payment to invoice"),
            ("general_task", "General administrative task"),
        ]
        task_bulk = []
        for teacher in teachers[:8]:
            for _ in range(3):
                tt, title = random.choice(task_types)
                task_bulk.append(Task(
                    task_type=tt,
                    title=f"{title} - {teacher.get_full_name()}",
                    description="This task was auto-generated for local development testing.",
                    assigned_to=teacher,
                    created_by=self.admin_user,
                    due_date=timezone.now() + timedelta(days=random.randint(1, 30)),
                    status=random.choice(["pending", "pending", "in_progress", "completed"]),
                    priority=random.choice(["low", "medium", "high"]),
                ))
        Task.objects.bulk_create(task_bulk, batch_size=500)
        self.stdout.write(f"  Created {len(task_bulk)} tasks")

    def _step7_progression(self):
        self.stdout.write(self.style.WARNING("\n STEP 7: Progression "))

        from academics.models import (
            ProgressionConfig, ProgressionCase, PromotionRun,
            ProgressionStatus, ProgressionOutcome, GradeClass, ExamTypeConfiguration,
        )
        from students.models import Student, StudentStatus, EnrollmentHistory

        # Create progression config
        config, _ = ProgressionConfig.objects.get_or_create(
            academic_year_from=self.ay2025,
            academic_year_to=self.ay2026,
            defaults={
                "minimum_average": 40,
                "minimum_attendance": 60,
                "retention_threshold": 40,
                "created_by": self.admin_user,
            },
        )

        promoted = 0
        retained = 0
        graduated = 0

        grade_order = list(
            GradeClass.objects.filter().order_by("sort_order", "name").values_list("name", flat=True)
        )
        grade_index = {name: i for i, name in enumerate(grade_order)}
        grade_next = {}
        for i, name in enumerate(grade_order):
            if i < len(grade_order) - 1:
                grade_next[name] = grade_order[i + 1]
            else:
                grade_next[name] = None  # Graduate

        # Determine outcomes for all students first
        student_outcomes = {}  # student_pk -> (outcome, next_class)
        deterministic_low_score_student = None

        for student in Student.objects.filter(academic_year=self.ay2025, status="active"):
            # Calculate average from ExamScores
            from academics.models import ExamScore
            scores = ExamScore.objects.filter(student=student, term__academic_year=self.ay2025)
            if scores.exists():
                avg = sum(float(s.score) for s in scores) / scores.count()
            else:
                avg = random.uniform(30, 80)  # No scores: assign random

            # Determine outcome
            is_top_grade = grade_next.get(student.class_name) is None

            if is_top_grade:
                outcome = "graduated"
                next_class = None
            elif avg < 40:
                outcome = "retained"
                next_class = student.class_name
            else:
                outcome = "promoted"
                next_class = grade_next.get(student.class_name, student.class_name)
                if next_class is None:
                    next_class = student.class_name

            student_outcomes[student.pk] = (outcome, next_class, avg)

        # Ensure at least one student has a deliberately low average (deterministic probe)
        # Find the student with the lowest average among those with scores
        scored_students = [(pk, oc, avg) for pk, (oc, nc, avg) in student_outcomes.items() if avg >= 40]
        if scored_students:
            # Pick a student to force to low average
            probe_pk, probe_oc, probe_avg = min(scored_students, key=lambda x: x[2])
            probe_student = Student.objects.get(pk=probe_pk)
            # Create deliberate low scores for this student across all terms and subjects
            low_score_value = 10
            for term in self.terms_2025:
                for subj in ["Mathematics", "English", "Science", "Social Studies", "Art", "Physical Education", "ICT", "Kiswahili"]:
                    ExamScore.objects.update_or_create(
                        student=probe_student, term=term, subject_name=subj, exam_type="quiz",
                        defaults={
                            "score": low_score_value,
                            "entered_by": self.admin_user,
                            "status": "approved",
                            "is_locked": True,
                            "exam_type_config": ExamTypeConfiguration.objects.get(code="quiz"),
                            "approved_by": self.admin_user,
                            "approved_at": timezone.now(),
                        },
                    )
            # Recalculate average
            all_scores = ExamScore.objects.filter(student=probe_student, term__academic_year=self.ay2025)
            new_avg = sum(float(s.score) for s in all_scores) / all_scores.count()
            student_outcomes[probe_student.pk] = ("retained", probe_student.class_name, new_avg)
            deterministic_low_score_student = probe_student
            self.stdout.write(f"  Deterministic low-score probe: {probe_student.admission_no} avg={new_avg:.1f}")

        # Create ProgressionCases at "calculated" status, then move through workflow
        for student in Student.objects.filter(academic_year=self.ay2025, status="active"):
            outcome, next_class, avg = student_outcomes[student.pk]

            # Map outcome to ProgressionOutcome
            outcome_map = {
                "promoted": ProgressionOutcome.PROMOTE,
                "retained": ProgressionOutcome.RETAIN,
                "graduated": ProgressionOutcome.GRADUATE,
            }
            system_suggested = outcome_map[outcome]

            # Create ProgressionCase at "calculated" (only allowed creation status)
            case, created = ProgressionCase.objects.get_or_create(
                student=student,
                progression_config=config,
                defaults={
                    "calculated_average": round(avg, 1),
                    "calculated_attendance_rate": round(random.uniform(70, 98), 1),
                    "calculation_basis": "complete",
                    "system_suggested_outcome": system_suggested,
                    "status": ProgressionStatus.CALCULATED,
                },
            )
            if not created:
                continue  # Already exists

            # Move through workflow: calculated → pending_hod_review → pending_hos_decision → finalized
            case.hod_recommendation = system_suggested
            case.status = ProgressionStatus.PENDING_HOD_REVIEW
            case.save(update_fields=["hod_recommendation", "status", "updated_at"])

            case.status = ProgressionStatus.PENDING_HOS_DECISION
            case.save(update_fields=["status", "updated_at"])

            case.hos_decision = system_suggested
            case.status = ProgressionStatus.FINALIZED
            case.save(update_fields=["hos_decision", "status", "updated_at"])

            # Execute promotion based on outcome
            current_class = student.class_name
            current_idx = grade_index.get(current_class, -1)

            if outcome == "graduated":
                student.status = StudentStatus.GRADUATED
                student.academic_year = self.ay2026
                student.save(update_fields=["status", "academic_year", "updated_at"])
                EnrollmentHistory.objects.create(
                    student=student,
                    academic_year=self.ay2026,
                    class_name=current_class,
                    stream_name=student.stream_name,
                    action="graduated",
                    progression_case=case,
                )
                graduated += 1
            elif outcome == "promoted" and next_class:
                EnrollmentHistory.objects.create(
                    student=student,
                    academic_year=self.ay2025,
                    class_name=current_class,
                    stream_name=student.stream_name,
                    action="promoted",
                    notes=f"Promoted from {current_class} to {next_class}",
                    progression_case=case,
                )
                student.class_name = next_class
                student.academic_year = self.ay2026
                student.save(update_fields=["class_name", "academic_year", "updated_at"])
                promoted += 1
            elif outcome == "retained":
                EnrollmentHistory.objects.create(
                    student=student,
                    academic_year=self.ay2025,
                    class_name=current_class,
                    stream_name=student.stream_name,
                    action="retained",
                    notes=f"Retained in {current_class}",
                    progression_case=case,
                )
                student.academic_year = self.ay2026
                student.save(update_fields=["academic_year", "updated_at"])
                retained += 1

        # Create PromotionRun
        PromotionRun.objects.get_or_create(
            academic_year_from=self.ay2025,
            academic_year_to=self.ay2026,
            defaults={
                "promoted_count": promoted,
                "retained_count": retained,
                "graduated_count": graduated,
                "status": "completed",
                "completed_at": timezone.now(),
                "executed_by": self.admin_user,
            },
        )

        self.stdout.write(f"  Promoted: {promoted}, Retained: {retained}, Graduated: {graduated}")

    #  STEP 8: ROLL INTO 2026

    def _step8_roll_into_2026(self):
        self.stdout.write(self.style.WARNING("\n STEP 8: Roll into 2026 (lighter seed) "))

        from academics.models import GradeClass, Subject
        from students.models import Student, EnrollmentHistory
        from attendance.models import AttendanceEntry
        from academics.models import ExamScore, ScoreStatus, ExamTypeConfiguration
        from users.models import User

        term_2026_t1 = self.terms_2026[0]

        # New intake students for 2026
        new_students = []
        new_first_m = ["Elijah", "Caleb", "Joshua", "Seth", "Adam", "Daniel", "Ezra", "Moses"]
        new_first_f = ["Ruth", "Naomi", "Rebecca", "Leah", "Sarah", "Esther", "Martha", "Lydia"]
        new_last = ["Mushi", "Kamara", "Diallo", "Tour", "Mensah", "Owusu", "Adeyemi", "Okonkwo"]

        for i in range(12):
            gender = random.choice(["male", "female"])
            fn = random.choice(new_first_m if gender == "male" else new_first_f)
            ln = random.choice(new_last)
            adm = f"ADM-2026-{i + 1:03d}"
            gc_name = random.choice(["Grade 1", "Grade 2", "Pre-School", "Kindergarten"])

            student, _ = Student.objects.get_or_create(
                admission_no=adm,
                defaults={
                    "first_name": fn, "last_name": ln,
                    "date_of_birth": date(random.randint(2017, 2022), random.randint(1, 12), random.randint(1, 28)),
                    "gender": gender,
                    "class_name": gc_name,
                    "status": "active",
                    "enrolment_date": date(2026, 1, random.randint(5, 20)),
                    "academic_year": self.ay2026,
                    "nationality": "Tanzanian",
                },
            )
            new_students.append(student)
            EnrollmentHistory.objects.get_or_create(
                student=student, academic_year=self.ay2026,
                defaults={
                    "term": term_2026_t1,
                    "class_name": gc_name,
                    "action": "enrolled",
                    "enrolled_at": datetime.combine(student.enrolment_date, time(8, 0)),
                },
            )

        # Attendance for 2026 T1 (partial -- we're mid-year)
        total_att = 0
        today = date.today()
        att_bulk = []
        for student in Student.objects.filter(academic_year=self.ay2026, status="active"):
            start = max(student.enrolment_date or term_2026_t1.start_date, term_2026_t1.start_date)
            end = min(today, term_2026_t1.end_date)
            for day in self._get_school_days(start, end):
                r = random.random()
                status = "present" if r < 0.90 else ("late" if r < 0.95 else "absent")
                att_bulk.append(AttendanceEntry(
                    student=student, date=day,
                    status=status,
                    class_name=student.class_name,
                    check_in_time=time(random.randint(7, 8), random.randint(0, 29)) if status != "absent" else None,
                    marked_by=self.admin_user,
                ))
                total_att += 1
        AttendanceEntry.objects.bulk_create(att_bulk, ignore_conflicts=True, batch_size=500)

        # Quiz scores for 2026 T1 (partial)
        primary_subjects = ["Mathematics", "English", "Science", "Social Studies"]
        quiz_type = ExamTypeConfiguration.objects.get(code="quiz")
        total_scores = 0
        score_bulk = []
        for student in Student.objects.filter(academic_year=self.ay2026, status="active"):
            for subj in random.sample(primary_subjects, k=random.randint(2, 4)):
                score_bulk.append(ExamScore(
                    student=student, term=term_2026_t1, subject_name=subj, exam_type="quiz",
                    score=self._gen_score(),
                    status=ScoreStatus.DRAFT,
                    exam_type_config=quiz_type,
                    entered_by=self.admin_user,
                ))
                total_scores += 1
        ExamScore.objects.bulk_create(score_bulk, ignore_conflicts=True, batch_size=500)

        self.stdout.write(f"  New 2026 intake: {len(new_students)} students")
        self.stdout.write(f"  2026 T1 attendance: {total_att} entries")
        self.stdout.write(f"  2026 T1 quiz scores: {total_scores} entries")

    #  SUMMARY 

    def _print_summary(self):
        from academics.models import AcademicYear, Term, GradeClass, ExamScore, Subject, LessonPlan, ReportCard
        from students.models import Student, ParentGuardian
        from attendance.models import AttendanceEntry
        from admissions.models import Applicant
        from users.models import User
        from hr.models import StaffProfile, TeacherClassAssignment
        from events.models import CalendarEvent
        from timetable.models import TimetableSlot
        from welfare.models import WelfareObservation
        from communications.models import Notification, Broadcast, WeeklyFocus
        from discipline.models import DisciplineIncident
        from finance.models import Invoice, Payment, FeeStructure
        from tasks.models import Task

        self.stdout.write(self.style.WARNING("\n DATA SUMMARY "))
        self.stdout.write(f"  Users:              {User.objects.count()}")
        self.stdout.write(f"  Staff profiles:     {StaffProfile.objects.count()}")
        self.stdout.write(f"  TCA assignments:    {TeacherClassAssignment.objects.count()}")
        self.stdout.write(f"  Grade classes:      {GradeClass.objects.count()}")
        self.stdout.write(f"  Subjects:           {Subject.objects.count()}")
        self.stdout.write(f"  Students:           {Student.objects.count()}")
        self.stdout.write(f"  Attendance entries: {AttendanceEntry.objects.count()}")
        self.stdout.write(f"  Exam scores:        {ExamScore.objects.count()}")
        self.stdout.write(f"  Lesson plans:       {LessonPlan.objects.count()}")
        self.stdout.write(f"  Report cards:       {ReportCard.objects.count()}")
        self.stdout.write(f"  Applicants:         {Applicant.objects.count()}")
        self.stdout.write(f"  Timetable slots:    {TimetableSlot.objects.count()}")
        self.stdout.write(f"  Welfare obs:        {WelfareObservation.objects.count()}")
        self.stdout.write(f"  Discipline:         {DisciplineIncident.objects.count()}")
        self.stdout.write(f"  Notifications:      {Notification.objects.count()}")
        self.stdout.write(f"  Broadcasts:         {Broadcast.objects.count()}")
        self.stdout.write(f"  Weekly focus:       {WeeklyFocus.objects.count()}")
        self.stdout.write(f"  Fee structures:     {FeeStructure.objects.count()}")
        self.stdout.write(f"  Invoices:           {Invoice.objects.count()}")
        self.stdout.write(f"  Payments:           {Payment.objects.count()}")
        self.stdout.write(f"  Tasks:              {Task.objects.count()}")
        self.stdout.write(f"  Calendar events:    {CalendarEvent.objects.count()}")

        from django.conf import settings
        db_path = str(settings.DATABASES["default"]["NAME"])
        size_mb = os.path.getsize(db_path) / 1024 / 1024
        self.stdout.write(f"\n  Database: {db_path} ({size_mb:.1f} MB)")
