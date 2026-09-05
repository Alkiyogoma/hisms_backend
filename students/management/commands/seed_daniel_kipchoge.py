import random
import secrets
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academics.models import (
    AcademicYear,
    CambridgeCheckpointScore,
    ExamScore,
    ExamTypeConfiguration,
    GradeClass,
    ProgressionCase,
    ProgressionConfig,
    ReportCard,
    Subject,
    Term,
)
from attendance.models import AttendanceEntry
from discipline.models import DisciplineIncident
from finance.models import FeeStructure, FeeStructureItem, Invoice, InvoiceLineItem, Payment
from students.models import (
    EnrollmentHistory,
    ParentGuardian,
    Student,
    StudentGuardian,
    StudentSibling,
)
from welfare.models import WelfareObservation


class Command(BaseCommand):
    help = "Seed comprehensive data for Daniel Kipchoge (HCS0004) across 6 terms"

    def handle(self, *args, **options):
        with transaction.atomic():
            self._seed()

    def _seed(self):
        self.stdout.write("Setting up Daniel Kipchoge (HCS0004)...")

        # ── Get or create admin user ──
        from users.models import User
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.create_superuser(
                username="admin", password="admin123",
                first_name="Admin", last_name="User",
                email="admin@hodari.cs", role="super_admin",
            )

        # ── Get or create teachers ──
        teachers = {}
        teacher_data = [
            ("teacher.math", "James", "Mwangi", "teacher", "Mathematics"),
            ("teacher.english", "Sarah", "Otieno", "teacher", "English"),
            ("teacher.science", "Peter", "Kamau", "teacher", "Science"),
            ("teacher.social", "Mary", "Wanjiku", "teacher", "Social Studies"),
            ("teacher.kiswahili", "John", "Mushi", "teacher", "Kiswahili"),
            ("teacher.art", "Grace", "Lugongo", "teacher", "Art"),
            ("teacher.pe", "David", "Msuya", "teacher", "Physical Education"),
            ("teacher.ict", "Alice", "Kimaro", "teacher", "ICT"),
        ]
        for uname, fn, ln, role, subj in teacher_data:
            u, _ = User.objects.get_or_create(
                username=uname,
                defaults={
                    "first_name": fn, "last_name": ln, "role": role,
                    "email": f"{uname}@hodari.cs",
                },
            )
            teachers[subj] = u

        hos = User.objects.filter(role="head_of_school").first() or admin_user
        hod = User.objects.filter(role="primary_hod").first() or admin_user
        finance_user = User.objects.filter(role="finance_officer").first() or admin_user

        # ── Academic Years & Terms ──
        ay2025, _ = AcademicYear.objects.get_or_create(
            name="2025", defaults={"number_of_terms": 3, "start_date": date(2025, 1, 6), "end_date": date(2025, 10, 31)}
        )
        ay2026, _ = AcademicYear.objects.get_or_create(
            name="2026", defaults={
                "number_of_terms": 3, "is_current": True,
                "start_date": date(2026, 1, 5), "end_date": date(2026, 10, 23),
            }
        )

        term_data = [
            (ay2025, "Term 1", date(2025, 1, 6), date(2025, 3, 28)),
            (ay2025, "Term 2", date(2025, 4, 28), date(2025, 6, 27)),
            (ay2025, "Term 3", date(2025, 7, 21), date(2025, 10, 24)),
            (ay2026, "Term 1", date(2026, 1, 5), date(2026, 3, 27)),
            (ay2026, "Term 2", date(2026, 4, 27), date(2026, 6, 26)),
            (ay2026, "Term 3", date(2026, 7, 20), date(2026, 10, 23)),
        ]
        terms = []
        for ay, name, start, end in term_data:
            t, _ = Term.objects.get_or_create(
                academic_year=ay, name=name,
                defaults={"start_date": start, "end_date": end, "grading_deadline": end + timedelta(days=7)},
            )
            terms.append(t)

        all_terms = terms

        # ── Grade Class ──
        grade1, _ = GradeClass.objects.get_or_create(
            name="Grade 1", defaults={"department": "PRIMARY", "max_capacity": 25}
        )
        grade2, _ = GradeClass.objects.get_or_create(
            name="Grade 2", defaults={"department": "PRIMARY", "max_capacity": 25}
        )

        # ── Subjects (PRIMARY) ──
        subject_names = [
            "Mathematics", "English", "Science", "Social Studies",
            "Kiswahili", "Art", "Physical Education", "ICT",
        ]
        subject_codes = ["MATH", "ENG", "SCI", "SST", "KIS", "ART", "PE", "ICT"]
        subjects = {}
        for sname, scode in zip(subject_names, subject_codes):
            s, _ = Subject.objects.get_or_create(
                name=sname,
                defaults={
                    "code": scode, "department": "PRIMARY",
                    "departments": ["PRIMARY"],
                },
            )
            s.classes.add(grade1, grade2)
            subjects[sname] = s

        # ── Exam type configs ──
        exam_types = {}
        for ename, ecode, eweight in [("Quiz", "quiz", 20), ("Mid-Term", "mid_term", 30), ("End-Term", "end_of_term", 50)]:
            et, _ = ExamTypeConfiguration.objects.get_or_create(
                code=ecode,
                defaults={"name": ename, "weight_percentage": Decimal(str(eweight)), "max_score": Decimal("100")},
            )
            exam_types[ecode] = et

        # ── Student Daniel Kipchoge ──
        daniel, created = Student.objects.get_or_create(
            admission_no="HCS0004",
            defaults={
                "first_name": "Daniel",
                "last_name": "Kipchoge",
                "date_of_birth": date(2015, 1, 1),
                "gender": "male",
                "class_name": "Grade 1",
                "status": "active",
                "enrolment_date": date(2025, 1, 6),
                "nationality": "Tanzanian",
                "academic_year": ay2025,
            },
        )
        if not created:
            self.stdout.write(f"  Daniel already exists (pk={daniel.pk}), adding data...")
        else:
            self.stdout.write(f"  Created Daniel Kipchoge (pk={daniel.pk})")

        # ── Guardians ──

        # Primary parent: ElimCore Group
        elimcore_user, _ = User.objects.get_or_create(
            username="parent_98789323",
            defaults={
                "first_name": "ElimCore",
                "last_name": "Group",
                "email": "elimcoregroup@gmail.com",
                "role": "parent",
            },
        )
        elimcore_pg, _ = ParentGuardian.objects.get_or_create(
            full_name="ElimCore Group",
            defaults={"phone": "+255700000001", "email": "elimcoregroup@gmail.com"},
        )

        # Father: Joseph Kipchoge
        joseph_pg, _ = ParentGuardian.objects.get_or_create(
            full_name="Joseph Kipchoge",
            defaults={"phone": "+255712345678", "email": "joseph.kipchoge@email.com"},
        )

        # Link both guardians to both students
        StudentGuardian.objects.get_or_create(student=daniel, guardian=elimcore_pg, defaults={"relationship": "parent", "is_primary": True})
        StudentGuardian.objects.get_or_create(student=daniel, guardian=joseph_pg, defaults={"relationship": "father", "is_primary": False})

        # ── Enrollment History ──
        EnrollmentHistory.objects.get_or_create(
            student=daniel, academic_year=ay2025, term=terms[0],
            defaults={"class_name": "Grade 1", "action": "enrolled", "notes": "Initial enrollment"},
        )
        for t in terms[1:]:
            EnrollmentHistory.objects.get_or_create(
                student=daniel, academic_year=t.academic_year, term=t,
                defaults={"class_name": "Grade 1", "action": "enrolled"},
            )

        # ── Daniel's performance profile (improving over time) ──
        # scores[term_index][subject] = base_score (will add variance)
        base_scores_2025 = {
            "Mathematics": [62, 68, 74],
            "English": [58, 65, 71],
            "Science": [66, 70, 76],
            "Social Studies": [72, 75, 78],
            "Kiswahili": [60, 64, 70],
            "Art": [78, 80, 82],
            "Physical Education": [80, 82, 85],
            "ICT": [70, 74, 78],
        }
        base_scores_2026 = {
            "Mathematics": [78, 82, 86],
            "English": [75, 79, 83],
            "Science": [80, 84, 88],
            "Social Studies": [82, 85, 87],
            "Kiswahili": [74, 78, 82],
            "Art": [85, 87, 90],
            "Physical Education": [88, 90, 92],
            "ICT": [80, 84, 88],
        }

        grade_labels = {
            (90, 101): "A*",
            (80, 90): "A",
            (70, 80): "B",
            (60, 70): "C",
            (50, 60): "D",
            (0, 50): "E",
        }

        def score_to_grade(sc):
            for lo, hi in grade_labels:
                if lo <= sc < hi:
                    return grade_labels[(lo, hi)]
            return "E"

        self.stdout.write("  Creating exam scores, report cards, attendance, finance, welfare, discipline...")

        for ti, term in enumerate(all_terms):
            year_idx = 0 if ti < 3 else 1
            scores_ref = base_scores_2025 if year_idx == 0 else base_scores_2026
            term_in_year = ti  # 0,1,2 or 3,4,5

            # ── EXAM SCORES (3 exam types × 8 subjects) ──
            for subj_name, base_list in scores_ref.items():
                base_sc = base_list[ti % 3]
                for exam_code in ["quiz", "mid_term", "end_of_term"]:
                    variance = random.randint(-5, 5)
                    sc = max(20, min(100, base_sc + variance))
                    ExamScore.objects.get_or_create(
                        student=daniel,
                        term=term,
                        subject_name=subj_name,
                        exam_type=exam_code,
                        defaults={
                            "score": Decimal(str(sc)),
                            "max_score": Decimal("100"),
                            "exam_type_config": exam_types[exam_code],
                            "entered_by": teachers.get(subj_name, admin_user),
                            "status": "approved",
                            "approved_by": admin_user,
                        },
                    )

            # ── REPORT CARD ──
            avg_scores = []
            for subj_name, base_list in scores_ref.items():
                avg_scores.append(base_list[ti % 3])
            overall_avg = sum(avg_scores) / len(avg_scores) if avg_scores else 0

            days_present = random.randint(45, 55)
            days_absent = random.randint(1, 5)
            days_late = random.randint(0, 3)
            total_days = days_present + days_absent + days_late
            att_rate = (days_present + days_late) / total_days * 100 if total_days > 0 else 0

            comments_pool = [
                "Daniel has shown steady improvement this term. He is a diligent student who participates actively in class.",
                "A well-behaved student who consistently meets expectations. Keep up the good work.",
                "Daniel demonstrates strong understanding in most subjects. He should focus on time management.",
                "Good progress overall. Daniel is encouraged to read more widely to improve his English skills.",
                "An enthusiastic learner who contributes positively to class discussions. Continue striving for excellence.",
                "Daniel has made remarkable progress this term. His mathematics skills have improved significantly.",
            ]
            hos_comments_pool = [
                "Approved for progression. Daniel is performing well.",
                "Satisfactory progress. Keep encouraging him.",
                "Good performance. Approved.",
                "Daniel is on track. Well done.",
                "Progress approved. Continue monitoring.",
                "Excellent improvement. HOS approved.",
            ]

            ReportCard.objects.get_or_create(
                student=daniel, term=term,
                defaults={
                    "status": "published",
                    "generated_by": admin_user,
                    "signed_off_by": hos,
                    "overall_average": Decimal(str(round(overall_avg, 1))),
                    "teacher_comments": random.choice(comments_pool),
                    "hos_comments": random.choice(hos_comments_pool),
                    "attendance_days_present": days_present,
                    "attendance_days_absent": days_absent,
                    "attendance_days_late": days_late,
                    "attendance_rate": Decimal(str(round(att_rate, 1))),
                },
            )

            # ── ATTENDANCE (school days during term) ──
            current_date = term.start_date
            while current_date <= term.end_date:
                if current_date.weekday() < 5:  # Mon-Fri
                    roll = random.random()
                    if roll < 0.85:
                        att_status = "present"
                    elif roll < 0.92:
                        att_status = "late"
                    elif roll < 0.96:
                        att_status = "absent"
                    else:
                        att_status = "excused"

                    check_in = None
                    if att_status == "late":
                        check_in = time(8, random.randint(5, 25))
                    elif att_status == "present":
                        check_in = time(7, random.randint(25, 50))

                    AttendanceEntry.objects.get_or_create(
                        date=current_date, student=daniel,
                        defaults={
                            "status": att_status,
                            "check_in_time": check_in,
                            "marked_by": teachers.get("Mathematics", admin_user),
                            "class_name": "Grade 1",
                        },
                    )
                current_date += timedelta(days=1)

            # ── FINANCE: Fee Structure + Invoice + Payments ──
            fee_items_data = [
                ("Tuition Fee", Decimal("350000")),
                ("Assessment Fee", Decimal("50000")),
                ("Activity Fee", Decimal("30000")),
                ("Uniform", Decimal("45000")),
                ("Stationery", Decimal("25000")),
            ]
            total_fee = sum(amt for _, amt in fee_items_data)

            fs, _ = FeeStructure.objects.get_or_create(
                term=term, class_name="Grade 1",
                defaults={"status": "published"},
            )
            for desc, amt in fee_items_data:
                FeeStructureItem.objects.get_or_create(
                    structure=fs, description=desc,
                    defaults={"amount": amt},
                )

            # Invoice
            inv_number = f"INV-{term.academic_year.name}-{term.name.replace(' ', '')}-HCS0004"
            payment_ratio = {0: 0.6, 1: 0.75, 2: 0.85, 3: 0.9, 4: 1.0, 5: 1.0}
            paid_pct = payment_ratio.get(ti, 1.0)
            amount_paid = total_fee * Decimal(str(paid_pct))

            inv, inv_created = Invoice.objects.get_or_create(
                student=daniel, term=term,
                defaults={
                    "invoice_number": inv_number,
                    "amount_due": total_fee,
                    "discount_amount": Decimal("0"),
                    "total_due": total_fee,
                    "due_date": term.start_date + timedelta(days=30),
                    "status": "paid" if paid_pct >= 1.0 else ("partial" if paid_pct > 0 else "unpaid"),
                    "is_finalized": True,
                },
            )
            if inv_created:
                for desc, amt in fee_items_data:
                    InvoiceLineItem.objects.create(
                        invoice=inv, description=desc, amount=amt,
                    )

                # Payment(s)
                if amount_paid > 0:
                    Payment.objects.get_or_create(
                        invoice=inv,
                        amount=amount_paid,
                        defaults={
                            "method": "mobile_money",
                            "payment_date": term.start_date + timedelta(days=random.randint(5, 20)),
                            "reference": f"MPESA-{secrets.token_hex(4).upper()}",
                            "created_by": finance_user,
                        },
                    )

            # ── WELFARE OBSERVATIONS (1-2 per term) ──
            welfare_data = [
                ("health", "medium", "Daniel complained of headache during class. Given first aid and parents notified."),
                ("academic", "low", "Daniel needs extra support in English reading comprehension."),
                ("behavioral", "low", "Daniel was involved in a minor disagreement with a classmate during break time. Resolved by teacher."),
                ("health", "low", "Daniel had a mild cold. Sent home early with parent notification."),
                ("academic", "medium", "Daniel's math scores dipped slightly. After-school tutoring recommended."),
                ("behavioral", "low", "Daniel forgot his homework. Spoke with him about responsibility."),
            ]
            w_idx = ti % len(welfare_data)
            w_type, w_severity, w_text = welfare_data[w_idx]
            WelfareObservation.objects.get_or_create(
                student=daniel, observation_date=term.start_date + timedelta(days=random.randint(10, 40)),
                observation_text=w_text,
                defaults={
                    "submitted_by": teachers.get("Mathematics", admin_user),
                    "concern_type": w_type,
                    "severity": w_severity,
                    "action_taken": "Monitored and reported to HOD.",
                    "parent_contacted": True,
                    "follow_up_required": w_severity in ["medium", "high"],
                    "hod_status": "resolved",
                },
            )

            # ── DISCIPLINE INCIDENTS (0-1 per term) ──
            if ti in [1, 3, 5]:
                disc_data = [
                    ("Talking during lesson time", "low", "Given verbal warning."),
                    ("Late submission of homework", "low", "Reminded of homework policy."),
                    ("Minor disruption in class", "low", "Counseling session with class teacher."),
                ]
                d_idx = (ti // 2) % len(disc_data)
                d_summary, d_severity, d_action = disc_data[d_idx]
                DisciplineIncident.objects.get_or_create(
                    student=daniel, incident_date=term.start_date + timedelta(days=random.randint(15, 60)),
                    summary=d_summary,
                    defaults={
                        "reported_by": teachers.get("English", admin_user),
                        "severity": d_severity,
                        "action_taken": d_action,
                        "status": "resolved",
                        "parent_contacted": True,
                        "location": "Classroom",
                    },
                )

        # ── PROGRESSION (end of year 2025 → 2026) ──
        pc, _ = ProgressionConfig.objects.get_or_create(
            academic_year_from=ay2025, academic_year_to=ay2026,
            defaults={"minimum_average": 50.0, "minimum_attendance": 80.0, "created_by": admin_user},
        )
        avg_2025 = 0
        for subj_name, base_list in base_scores_2025.items():
            avg_2025 += sum(base_list) / len(base_list)
        avg_2025 /= len(base_scores_2025)

        prog_case, pc_created = ProgressionCase.objects.get_or_create(
            student=daniel, progression_config=pc,
            defaults={
                "calculated_average": avg_2025,
                "calculated_attendance_rate": 92.5,
                "system_suggested_outcome": "promote",
                "status": "calculated",
            },
        )
        if pc_created:
            prog_case.hod_recommendation = "promote"
            prog_case.status = "pending_hod_review"
            prog_case.save()
            prog_case.hos_decision = "promote"
            prog_case.status = "pending_hos_decision"
            prog_case.save()
            prog_case.status = "finalized"
            prog_case.save()

        # ── CAMBRIDGE CHECKPOINT SCORES (Grade 3+) - skip for Grade 1 ──

        # ════════════════════════════════════════════════════════════════════
        # SIBLING: Faith Kipchoge (HCS0005) — ECD Pre-School
        # ════════════════════════════════════════════════════════════════════
        self.stdout.write("\nSetting up Faith Kipchoge (HCS0005) — sibling...")

        faith, faith_created = Student.objects.get_or_create(
            admission_no="HCS0005",
            defaults={
                "first_name": "Faith",
                "last_name": "Kipchoge",
                "date_of_birth": date(2019, 6, 15),
                "gender": "female",
                "class_name": "Pre-School",
                "status": "active",
                "enrolment_date": date(2025, 1, 6),
                "nationality": "Tanzanian",
                "academic_year": ay2025,
            },
        )
        if not faith_created:
            self.stdout.write(f"  Faith already exists (pk={faith.pk})")
        else:
            self.stdout.write(f"  Created Faith Kipchoge (pk={faith.pk})")

        # ── Link siblings ──
        StudentSibling.objects.get_or_create(student_a=daniel, student_b=faith)
        StudentSibling.objects.get_or_create(student_a=faith, student_b=daniel)

        # ── Link same guardians ──
        StudentGuardian.objects.get_or_create(student=faith, guardian=elimcore_pg, defaults={"relationship": "parent", "is_primary": True})
        StudentGuardian.objects.get_or_create(student=faith, guardian=joseph_pg, defaults={"relationship": "father", "is_primary": False})

        # ── ECD Classes ──
        pre_school, _ = GradeClass.objects.get_or_create(
            name="Pre-School", defaults={"department": "ECD", "max_capacity": 20}
        )

        # ── ECD Subjects ──
        ecd_subjects_data = [
            ("Pre-School Activities", "PSA"),
            ("Art", "ART"),
            ("Physical Education", "PE"),
            ("Music", "MUS"),
        ]
        ecd_subjects = {}
        for sname, scode in ecd_subjects_data:
            s, _ = Subject.objects.get_or_create(
                name=sname,
                defaults={
                    "code": scode, "department": "ECD",
                    "departments": ["ECD"],
                },
            )
            s.classes.add(pre_school)
            ecd_subjects[sname] = s

        # ── ECD Teachers ──
        ecd_teachers = {}
        ecd_teacher_data = [
            ("teacher.ecd1", "Rose", "Mwangi", "teacher"),
            ("teacher.ecd2", "Florence", "Msanga", "teacher"),
        ]
        for uname, fn, ln, role in ecd_teacher_data:
            u, _ = User.objects.get_or_create(
                username=uname,
                defaults={
                    "first_name": fn, "last_name": ln, "role": role,
                    "email": f"{uname}@hodari.cs",
                },
            )
            ecd_teachers[uname] = u

        # ── Enrollment History for Faith ──
        for t in all_terms:
            EnrollmentHistory.objects.get_or_create(
                student=faith, academic_year=t.academic_year, term=t,
                defaults={"class_name": "Pre-School", "action": "enrolled"},
            )

        # ── Faith's performance (starting lower, improving) ──
        faith_base_2025 = {
            "Pre-School Activities": [45, 52, 60],
            "Art": [70, 74, 78],
            "Physical Education": [65, 70, 75],
            "Music": [60, 66, 72],
        }
        faith_base_2026 = {
            "Pre-School Activities": [65, 72, 78],
            "Art": [80, 83, 86],
            "Physical Education": [75, 79, 82],
            "Music": [74, 78, 82],
        }

        faith_comments_pool = [
            "Faith is a cheerful learner who enjoys group activities. She is making good progress.",
            "Faith participates enthusiastically in art and music. She needs more practice with writing.",
            "A kind and friendly student. Faith is developing her fine motor skills well.",
            "Faith shows growing confidence in classroom activities. Keep encouraging her.",
            "She enjoys pre-school activities and gets along well with peers.",
            "Faith has improved significantly in recognizing letters and numbers.",
        ]
        faith_hos_comments = [
            "Faith is progressing well in ECD. Approved.",
            "Good development. Approved for continuation.",
            "Faith is on track. Keep up.",
            "Satisfactory progress. HOS approved.",
            "Faith is doing well. Approved.",
            "Excellent improvement this term. Approved.",
        ]

        self.stdout.write("  Creating Faith's exam scores, report cards, attendance, finance, welfare...")

        for ti, term in enumerate(all_terms):
            year_idx = 0 if ti < 3 else 1
            scores_ref = faith_base_2025 if year_idx == 0 else faith_base_2026

            # ── EXAM SCORES ──
            for subj_name, base_list in scores_ref.items():
                base_sc = base_list[ti % 3]
                for exam_code in ["quiz", "mid_term", "end_of_term"]:
                    variance = random.randint(-5, 5)
                    sc = max(20, min(100, base_sc + variance))
                    ExamScore.objects.get_or_create(
                        student=faith, term=term, subject_name=subj_name,
                        exam_type=exam_code,
                        defaults={
                            "score": Decimal(str(sc)),
                            "max_score": Decimal("100"),
                            "exam_type_config": exam_types[exam_code],
                            "entered_by": ecd_teachers.get("teacher.ecd1", admin_user),
                            "status": "approved",
                            "approved_by": admin_user,
                        },
                    )

            # ── REPORT CARD ──
            avg_scores = [base_list[ti % 3] for base_list in scores_ref.values()]
            overall_avg = sum(avg_scores) / len(avg_scores) if avg_scores else 0

            days_present = random.randint(42, 52)
            days_absent = random.randint(2, 7)
            days_late = random.randint(0, 4)
            total_days = days_present + days_absent + days_late
            att_rate = (days_present + days_late) / total_days * 100 if total_days > 0 else 0

            ReportCard.objects.get_or_create(
                student=faith, term=term,
                defaults={
                    "status": "published",
                    "generated_by": admin_user,
                    "signed_off_by": hos,
                    "overall_average": Decimal(str(round(overall_avg, 1))),
                    "teacher_comments": random.choice(faith_comments_pool),
                    "hos_comments": random.choice(faith_hos_comments),
                    "attendance_days_present": days_present,
                    "attendance_days_absent": days_absent,
                    "attendance_days_late": days_late,
                    "attendance_rate": Decimal(str(round(att_rate, 1))),
                },
            )

            # ── ATTENDANCE ──
            current_date = term.start_date
            while current_date <= term.end_date:
                if current_date.weekday() < 5:
                    roll = random.random()
                    if roll < 0.82:
                        att_status = "present"
                    elif roll < 0.90:
                        att_status = "late"
                    elif roll < 0.95:
                        att_status = "absent"
                    else:
                        att_status = "excused"

                    check_in = None
                    if att_status == "late":
                        check_in = time(8, random.randint(5, 30))
                    elif att_status == "present":
                        check_in = time(7, random.randint(30, 55))

                    AttendanceEntry.objects.get_or_create(
                        date=current_date, student=faith,
                        defaults={
                            "status": att_status,
                            "check_in_time": check_in,
                            "marked_by": ecd_teachers.get("teacher.ecd1", admin_user),
                            "class_name": "Pre-School",
                        },
                    )
                current_date += timedelta(days=1)

            # ── FINANCE ──
            faith_fee_items = [
                ("Tuition Fee", Decimal("300000")),
                ("Assessment Fee", Decimal("40000")),
                ("Activity Fee", Decimal("25000")),
                ("Uniform", Decimal("40000")),
                ("Stationery", Decimal("20000")),
            ]
            faith_total = sum(amt for _, amt in faith_fee_items)

            fs_f, _ = FeeStructure.objects.get_or_create(
                term=term, class_name="Pre-School",
                defaults={"status": "published"},
            )
            for desc, amt in faith_fee_items:
                FeeStructureItem.objects.get_or_create(
                    structure=fs_f, description=desc,
                    defaults={"amount": amt},
                )

            inv_number_f = f"INV-{term.academic_year.name}-{term.name.replace(' ', '')}-HCS0005"
            paid_pct_f = payment_ratio.get(ti, 1.0)
            amount_paid_f = faith_total * Decimal(str(paid_pct_f))

            inv_f, inv_f_created = Invoice.objects.get_or_create(
                student=faith, term=term,
                defaults={
                    "invoice_number": inv_number_f,
                    "amount_due": faith_total,
                    "discount_amount": Decimal("0"),
                    "total_due": faith_total,
                    "due_date": term.start_date + timedelta(days=30),
                    "status": "paid" if paid_pct_f >= 1.0 else ("partial" if paid_pct_f > 0 else "unpaid"),
                    "is_finalized": True,
                },
            )
            if inv_f_created:
                for desc, amt in faith_fee_items:
                    InvoiceLineItem.objects.create(invoice=inv_f, description=desc, amount=amt)
                if amount_paid_f > 0:
                    Payment.objects.get_or_create(
                        invoice=inv_f, amount=amount_paid_f,
                        defaults={
                            "method": "mobile_money",
                            "payment_date": term.start_date + timedelta(days=random.randint(5, 20)),
                            "reference": f"MPESA-{secrets.token_hex(4).upper()}",
                            "created_by": finance_user,
                        },
                    )

            # ── WELFARE ──
            faith_welfare = [
                ("health", "low", "Faith had a minor bump during play time. First aid applied, parents notified."),
                ("behavioral", "low", "Faith was shy to participate in group activity. Encouraged by teacher."),
                ("academic", "low", "Faith needs more practice with letter recognition."),
                ("health", "medium", "Faith had a stomach ache. Parents called to pick her up."),
                ("behavioral", "low", "Faith shared her toys nicely — positive behavior noted."),
                ("academic", "low", "Faith is improving in counting activities."),
            ]
            fw = faith_welfare[ti % len(faith_welfare)]
            WelfareObservation.objects.get_or_create(
                student=faith, observation_date=term.start_date + timedelta(days=random.randint(10, 40)),
                observation_text=fw[2],
                defaults={
                    "submitted_by": ecd_teachers.get("teacher.ecd1", admin_user),
                    "concern_type": fw[0], "severity": fw[1],
                    "action_taken": "Monitored and reported to HOD.",
                    "parent_contacted": True,
                    "follow_up_required": fw[1] in ["medium", "high"],
                    "hod_status": "resolved",
                },
            )

            # ── DISCIPLINE (rare for ECD) ──
            if ti == 2:
                DisciplineIncident.objects.get_or_create(
                    student=faith, incident_date=term.start_date + timedelta(days=random.randint(15, 50)),
                    summary="Faith pushed a classmate during outdoor play.",
                    defaults={
                        "reported_by": ecd_teachers.get("teacher.ecd1", admin_user),
                        "severity": "low",
                        "action_taken": "Talked to Faith about sharing and taking turns. Parents informed.",
                        "status": "resolved", "parent_contacted": True, "location": "Playground",
                    },
                )

        # ── PROGRESSION for Faith (Pre-School → Kindergarten) ──
        pre_school_class, _ = GradeClass.objects.get_or_create(
            name="Kindergarten", defaults={"department": "ECD", "max_capacity": 20}
        )
        faith_avg = sum(faith_base_2025[s][1] for s in faith_base_2025) / len(faith_base_2025)

        faith_prog, fp_created = ProgressionCase.objects.get_or_create(
            student=faith, progression_config=pc,
            defaults={
                "calculated_average": faith_avg,
                "calculated_attendance_rate": 88.0,
                "system_suggested_outcome": "promote",
                "status": "calculated",
            },
        )
        if fp_created:
            faith_prog.hod_recommendation = "promote"
            faith_prog.status = "pending_hod_review"
            faith_prog.save()
            faith_prog.hos_decision = "promote"
            faith_prog.status = "pending_hos_decision"
            faith_prog.save()
            faith_prog.status = "finalized"
            faith_prog.save()

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Daniel Kipchoge (HCS0004) data:\n"
            f"  - {len(all_terms) * len(subjects) * 3} exam scores (3 exam types x 8 subjects x 6 terms)\n"
            f"  - 6 report cards with attendance stats\n"
            f"  - ~{6 * 22 * 5} attendance entries (school days x 6 terms)\n"
            f"  - 6 invoices with payments\n"
            f"  - 6 welfare observations\n"
            f"  - 3 discipline incidents\n"
            f"  - 1 progression case (promoted to Grade 2)\n"
            f"  - Guardians linked (ElimCore Group primary, Joseph Kipchoge secondary)\n\n"
            f"Faith Kipchoge (HCS0005) -- sibling, ECD Pre-School:\n"
            f"  - {len(all_terms) * len(ecd_subjects) * 3} exam scores (3 exam types x 4 subjects x 6 terms)\n"
            f"  - 6 report cards with attendance stats\n"
            f"  - ~{6 * 22 * 4} attendance entries\n"
            f"  - 6 invoices with payments\n"
            f"  - 6 welfare observations\n"
            f"  - 1 discipline incident\n"
            f"  - 1 progression case (promoted to Kindergarten)\n"
            f"  - Same guardians as Daniel (ElimCore Group + Joseph Kipchoge)\n"
            f"  - Sibling link to Daniel"
        ))
