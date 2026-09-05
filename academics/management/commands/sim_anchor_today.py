"""
management/commands/sim_anchor_today.py
Reset database and anchor academic year to today's real date for testing.
"""
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction, connection


class Command(BaseCommand):
    help = "Reset DB and create academic year anchored to today for live-date testing"

    def handle(self, *args, **options):
        today = date.today()
        self.stdout.write(f"\n  Today's date: {today} ({today.strftime('%A')})\n")

        # ── STEP 0: Clean reset via raw SQL (avoids protected-FK ordering hell) ──
        self.stdout.write("STEP 0: Clean reset...")

        cursor = connection.cursor()
        # Disable FK checks for SQLite so we can truncate everything
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("PRAGMA journal_mode = WAL")

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'django_%' AND name NOT LIKE 'auth_%'")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cursor.fetchone()[0]
            if count > 0:
                cursor.execute(f"DELETE FROM [{table}]")
                self.stdout.write(f"  Cleared {table}: {count} rows")

        cursor.execute("PRAGMA foreign_keys = ON")
        self.stdout.write(self.style.SUCCESS("  Reset complete.\n"))

        # Re-import after reset
        from academics.models import (
            AcademicYear, Term, ExamTypeConfiguration, ExamScore,
            ReportCard, ECDEvaluation, LessonPlan, LessonPlanStatus,
            GradeClass, Subject, ProgressionCase, ProgressionConfig,
            ABCPaceProgress, ABCScripture, ABCReadingProgramme,
        )
        from students.models import Student, EnrollmentHistory
        from users.models import User, UserRole

        # ── STEP 0b: Create AcademicYear + Terms ────────────────
        self.stdout.write("STEP 0b: Creating academic year and terms...")

        # Term 2 must contain today, with all exam windows open today
        # Quiz: always open once term started
        # Mid-Term window: must include today
        # End-Term window: must include today
        term2_start = date(2026, 4, 27)
        term2_end = date(2026, 7, 31)

        # Set exam windows so today (Jul 14) falls inside both
        mid_start = today - timedelta(days=7)   # Jul 7
        mid_end = today + timedelta(days=7)     # Jul 21
        end_start = today                       # Jul 14
        end_end = today + timedelta(days=14)    # Jul 28

        # Term 1: fully past
        term1_start = date(2026, 1, 5)
        term1_end = date(2026, 3, 27)
        term1_mid_start = date(2026, 2, 2)
        term1_mid_end = date(2026, 2, 13)
        term1_end_start = date(2026, 3, 9)
        term1_end_end = date(2026, 3, 27)

        # Term 3: fully future
        term3_start = date(2026, 8, 1)
        term3_end = date(2026, 10, 23)

        year = AcademicYear.objects.create(
            name="2026",
            is_current=True,
            is_published=True,
            number_of_terms=3,
            start_date=term1_start,
            end_date=term3_end,
        )

        t1 = Term.objects.create(
            academic_year=year, name="Term 1",
            start_date=term1_start, end_date=term1_end,
            grading_deadline=date(2026, 3, 31),
            midterm_exam_start_date=term1_mid_start,
            midterm_exam_end_date=term1_mid_end,
            endterm_exam_start_date=term1_end_start,
            endterm_exam_end_date=term1_end_end,
            is_locked=True,
        )
        t2 = Term.objects.create(
            academic_year=year, name="Term 2",
            start_date=term2_start, end_date=term2_end,
            grading_deadline=date(2026, 8, 7),
            midterm_exam_start_date=mid_start,
            midterm_exam_end_date=mid_end,
            endterm_exam_start_date=end_start,
            endterm_exam_end_date=end_end,
        )
        t3 = Term.objects.create(
            academic_year=year, name="Term 3",
            start_date=term3_start, end_date=term3_end,
            grading_deadline=date(2026, 10, 30),
            midterm_exam_start_date=date(2026, 9, 7),
            midterm_exam_end_date=date(2026, 9, 18),
            endterm_exam_start_date=date(2026, 10, 5),
            endterm_exam_end_date=date(2026, 10, 23),
        )

        self.stdout.write(f"  AcademicYear: {year.name} (current={year.is_current})")
        self.stdout.write(f"  Term 1: {t1.start_date} to {t1.end_date} [LOCKED, past]")
        self.stdout.write(f"  Term 2: {t2.start_date} to {t2.end_date} [LIVE, contains today]")
        self.stdout.write(f"    Quiz window: open (term started)")
        self.stdout.write(f"    Mid-Term window: {t2.midterm_exam_start_date} to {t2.midterm_exam_end_date}")
        self.stdout.write(f"    End-Term window: {t2.endterm_exam_start_date} to {t2.endterm_exam_end_date}")
        self.stdout.write(f"    Grading deadline: {t2.grading_deadline}")
        self.stdout.write(f"  Term 3: {t3.start_date} to {t3.end_date} [FUTURE]")

        # Verify today is inside Term 2
        assert t2.start_date <= today <= t2.end_date, f"Term 2 does not contain today ({today})"
        assert t2.midterm_exam_start_date <= today <= t2.midterm_exam_end_date, "Mid-term window does not contain today"
        assert t2.endterm_exam_start_date <= today <= t2.endterm_exam_end_date, "End-term window does not contain today"
        self.stdout.write(self.style.SUCCESS("  All date assertions passed.\n"))

        # ── Exam type configuration ─────────────────────────────
        from academics.models import ExamTypeConfiguration
        et_quiz = ExamTypeConfiguration.objects.create(
            code="quiz", name="Quiz", weight_percentage=20, max_score=100, display_order=1, is_active=True
        )
        et_mid = ExamTypeConfiguration.objects.create(
            code="mid_term", name="Mid-term Exam", weight_percentage=30, max_score=100, display_order=2, is_active=True
        )
        et_end = ExamTypeConfiguration.objects.create(
            code="end_of_term", name="End of Term Exam", weight_percentage=50, max_score=100, display_order=3, is_active=True
        )
        self.stdout.write(f"  ExamTypes: quiz(20%), mid_term(30%), end_of_term(50%)\n")

        # ── STEP 1: Seed staff and students ─────────────────────
        self.stdout.write("STEP 1: Seeding staff and classes...")

        # Create staff users
        staff_data = [
            ("admin.hodari", "Admin", "User", UserRole.SUPER_ADMIN),
            ("hos.robert", "Robert", "Temu", UserRole.HEAD_OF_SCHOOL),
            ("hod.primary", "Mary", "Mwangi", UserRole.PRIMARY_HOD),
            ("hod.ecd", "Grace", "Ochieng", UserRole.ECD_HOD),
            ("hod.lower", "James", "Kamau", UserRole.LOWER_SECONDARY_HOD),
            ("admin.officer", "Patricia", "Nyongesa", UserRole.ADMIN_OFFICER),
            ("finance.officer", "John", "Kioko", UserRole.FINANCE_OFFICER),
            ("teacher.pamela", "Pamela", "Francis", UserRole.TEACHER),
            ("teacher.sarah", "Sarah", "Wanjiku", UserRole.TEACHER),
            ("teacher.david", "David", "Mwangi", UserRole.TEACHER),
            ("teacher.anne", "Anne", "Njeri", UserRole.TEACHER),
            ("teacher.peter", "Peter", "Odhiambo", UserRole.TEACHER),
            ("teacher.lucy", "Lucy", "Wambui", UserRole.TEACHER),
            ("teacher.james", "James", "Otieno", UserRole.TEACHER),
            ("teacher.fatuma", "Fatuma", "Hassan", UserRole.TEACHER),
            ("teacher.brian", "Brian", "Kimani", UserRole.TEACHER),
            ("teacher.mercy", "Mercy", "Achieng", UserRole.TEACHER),
            ("parent.jane", "Jane", "Mutua", UserRole.PARENT),
        ]

        created_users = {}
        created_profiles = {}
        for uname, first, last, role in staff_data:
            u = User.objects.create_user(
                username=uname, first_name=first, last_name=last,
                email=f"{uname}@hodari.cs", role=role,
                password="Hodari@2026",
            )
            created_users[uname] = u
            # Signal auto-creates StaffProfile for non-parents — update it with correct dept
            if role != UserRole.PARENT:
                from hr.models import StaffProfile
                sp = StaffProfile.objects.get(user=u)
                dept_code = "PRIMARY"
                if "ecd" in uname:
                    dept_code = "ECD"
                elif "lower" in uname:
                    dept_code = "LOWER_SECONDARY"
                elif "admin" in uname or "finance" in uname:
                    dept_code = "ADMINISTRATION"
                sp.department = dept_code
                sp.save(update_fields=["department"])
                created_profiles[uname] = sp
            self.stdout.write(f"  User: {uname} ({role})")

        # Department is a TextChoices enum, not a model — GradeClass.department is a CharField
        self.stdout.write(f"  Departments: Primary, ECD, Lower Secondary")

        # Create grade classes
        classes_data = [
            # ECD
            ("Pre-School", "ECD", "teacher.pamela"),
            ("Pre-Kindergarten", "ECD", "teacher.sarah"),
            ("Kindergarten", "ECD", "teacher.david"),
            # Primary
            ("Grade 1", "PRIMARY", "teacher.anne"),
            ("Grade 2", "PRIMARY", "teacher.peter"),
            ("Grade 3", "PRIMARY", "teacher.lucy"),
            ("Grade 4", "PRIMARY", "teacher.james"),
            ("Grade 5", "PRIMARY", "teacher.fatuma"),
            ("Grade 6", "PRIMARY", "teacher.brian"),
            ("Grade 7", "PRIMARY", "teacher.mercy"),
            # Lower Secondary
            ("Grade 8", "LOWER_SECONDARY", "teacher.pamela"),
        ]

        created_classes = {}
        for cls_name, dept_code, teacher_uname in classes_data:
            gc = GradeClass.objects.create(
                name=cls_name,
                department=dept_code,
                max_capacity=35,
            )
            created_classes[cls_name] = gc
            # Assign teacher
            from hr.models import TeacherClassAssignment
            TeacherClassAssignment.objects.create(
                teacher=created_profiles[teacher_uname],
                term=t2,
                grade_class=gc,
                is_class_teacher=True,
            )
            self.stdout.write(f"  Class: {cls_name} ({dept_code}) -> {teacher_uname}")

        # Create subjects — name is unique, so use department prefix for shared subjects
        subjects_data = {
            "ECD": ["Literacy", "Numeracy", "Creative Arts", "Physical Development", "Social Studies"],
            "PRIMARY": ["English", "Mathematics", "Science", "Social Studies", "Kiswahili", "Religious Education", "Art", "Music", "Physical Education"],
            "LOWER_SECONDARY": ["English", "Mathematics", "Science", "Kiswahili", "Social Studies", "Religious Education", "Computer Studies"],
        }
        created_subjects = {}
        for dept_code, subj_names in subjects_data.items():
            for sn in subj_names:
                key = f"{dept_code}:{sn}"
                # Prefix to avoid unique constraint conflicts across departments
                display_name = sn
                if dept_code == "ECD" and sn == "Social Studies":
                    display_name = "ECD Social Studies"
                elif dept_code == "LOWER_SECONDARY" and sn in ("English", "Mathematics", "Science", "Kiswahili", "Social Studies", "Religious Education"):
                    display_name = f"Sec {sn}"
                s = Subject.objects.create(name=display_name, department=dept_code)
                created_subjects[key] = s
        self.stdout.write(f"  Subjects: {len(created_subjects)} created across departments")

        # Create students via simulated admissions
        self.stdout.write("\n  Seeding students...")
        student_names = {
            "Grade 1": [("Alice", "Wanjiru"), ("Ben", "Omondi"), ("Chloe", "Njeri"), ("Daniel", "Kipchoge"), ("Eva", "Mutua")],
            "Grade 2": [("Fiona", "Atieno"), ("George", "Kimani"), ("Hannah", "Wambui"), ("Ian", "Odhiambo"), ("Julie", "Njeri")],
            "Grade 3": [("Kevin", "Mwangi"), ("Laura", "Achieng"), ("Mike", "Kamau"), ("Nancy", "Wanjiku"), ("Oscar", "Otieno")],
            "Grade 4": [("Patricia", "Nyongesa"), ("Quinn", "Ochieng"), ("Rachel", "Hassan"), ("Sam", "Mwenda"), ("Tina", "Wairimu")],
            "Grade 5": [("Uma", "Njeri"), ("Victor", "Kiprop"), ("Wendy", "Muthoni"), ("Xavier", "Owino"), ("Yvonne", "Kimani")],
            "Grade 6": [("Zack", "Otieno"), ("Amy", "Wanjiru"), ("Brian", "Kipchoge"), ("Cathy", "Auma"), ("Dennis", "Mwangi")],
            "Grade 7": [("Ella", "Njeri"), ("Frank", "Kamau"), ("Grace", "Wambui"), ("Henry", "Omondi"), ("Irene", "Mutua")],
            "Grade 8": [("Jack", "Kiprop"), ("Karen", "Atieno"), ("Leo", "Mwenda"), ("Mona", "Hassan"), ("Nathan", "Odhiambo")],
            "Pre-School": [("Olive", "Wanjiku"), ("Pablo", "Kimani"), ("Queen", "Njeri")],
            "Pre-Kindergarten": [("Rita", "Achieng"), ("Steve", "Otieno"), ("Tara", "Wambui")],
            "Kindergarten": [("Ugo", "Mwangi"), ("Vera", "Ochieng"), ("Walter", "Kamau")],
        }

        from students.models import EnrollmentHistory
        all_students = []
        for cls_name, names in student_names.items():
            gc = created_classes[cls_name]
            for first, last in names:
                adm_no = f"HCS{str(len(all_students)+1).zfill(4)}"
                s = Student.objects.create(
                    first_name=first, last_name=last,
                    admission_no=adm_no,
                    class_name=gc.name,
                    date_of_birth=date(2015, 1, 1),
                    gender="M" if len(all_students) % 2 == 0 else "F",
                    status="active",
                    is_archived=False,
                )
                EnrollmentHistory.objects.create(
                    student=s,
                    action="enrolled",
                    class_name=gc.name,
                    academic_year=year,
                    term=t2,
                )
                all_students.append(s)
        self.stdout.write(f"  Students: {len(all_students)} created across {len(student_names)} classes")

        # ── Verification ────────────────────────────────────────
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(self.style.SUCCESS("  SETUP COMPLETE"))
        self.stdout.write(f"{'='*60}")
        self.stdout.write(f"  Today: {today} ({today.strftime('%A')})")
        self.stdout.write(f"  Active term: {t2.name} ({t2.start_date} to {t2.end_date})")
        self.stdout.write(f"  Mid-term exam window: {t2.midterm_exam_start_date} to {t2.midterm_exam_end_date}")
        self.stdout.write(f"  End-term exam window: {t2.endterm_exam_start_date} to {t2.endterm_exam_end_date}")
        self.stdout.write(f"  Users: {User.objects.count()}")
        self.stdout.write(f"  Classes: {GradeClass.objects.count()}")
        self.stdout.write(f"  Students: {Student.objects.count()}")
        self.stdout.write(f"  Subjects: {Subject.objects.count()}")
        self.stdout.write(f"\n  Admin: admin.hodari / Hodari@2026")
        self.stdout.write(f"  HOS: hos.robert / Hodari@2026")
        self.stdout.write(f"  Primary HOD: hod.primary / Hodari@2026")
        self.stdout.write(f"  ECD HOD: hod.ecd / Hodari@2026")
        self.stdout.write(f"  Lower Sec HOD: hod.lower / Hodari@2026")
        self.stdout.write(f"  Teachers: teacher.pamela .. teacher.mercy / Hodari@2026")
        self.stdout.write(f"  Parent: parent.jane / Hodari@2026\n")
