import random
from datetime import date, time, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model

from academics.models import Term
from admissions.models import Applicant, ApplicantStatus, AdmissionGrade, ApplicantDocumentReceipt, ApplicantDocumentType, AssessmentSchedule
from students.models import Student, StudentStatus, ParentGuardian, StudentGuardian, GuardianRelationship
from attendance.models import AttendanceEntry, AttendanceStatus
from finance.models import Invoice, Payment
from users.models import UserRole

User = get_user_model()

class Command(BaseCommand):
    help = "Populate the school system with comprehensive sample data for all scenarios."

    def handle(self, *args, **options):
        self.stdout.write("Cleaning up existing sample data (optional)...")
        # For safety, we just add to existing data, but you can clear it if you want.
        
        from academics.models import AcademicYear, GradeClass, Term, Department, Subject, Room
        from timetable.models import TimetableSlot, Weekday
        
        self.stdout.write("Creating Academic Years, Classes, and Subjects...")
        ay, _ = AcademicYear.objects.get_or_create(name="2026", defaults={"is_current": True})
        
        primary_subjects = [
            {"name": "Mathematics", "code": "MATH", "color": "#FF5733"},
            {"name": "English", "code": "ENG", "color": "#33FF57"},
            {"name": "Science", "code": "SCI", "color": "#3357FF"},
            {"name": "Kiswahili", "code": "KIS", "color": "#FF33A1"},
            {"name": "Social Studies", "code": "SOC", "color": "#FFC300"},
            {"name": "CRE", "code": "CRE", "color": "#900C3F"},
            {"name": "Music", "code": "MUS", "color": "#1ABC9C"},
            {"name": "Physical Education", "code": "PE", "color": "#E74C3C"}
        ]
        for subj in primary_subjects:
            Subject.objects.get_or_create(
                name=subj["name"],
                defaults={
                    "code": subj["code"],
                    "color": subj["color"],
                    "department": Department.PRIMARY
                }
            )
            
        ecd_subjects = [
            {"name": "Creative Arts", "code": "ECD-ART", "color": "#FF9FF3"},
            {"name": "Language Development", "code": "ECD-LANG", "color": "#54A0FF"},
            {"name": "Numeracy", "code": "ECD-NUM", "color": "#5F27CD"},
            {"name": "Physical Development", "code": "ECD-PHY", "color": "#00D2D3"},
            {"name": "Social & Emotional", "code": "ECD-SEM", "color": "#FF6B6B"},
            {"name": "Environmental Awareness", "code": "ECD-ENV", "color": "#1DD1A1"}
        ]
        for subj in ecd_subjects:
            Subject.objects.get_or_create(
                name=subj["name"],
                defaults={
                    "code": subj["code"],
                    "color": subj["color"],
                    "department": Department.ECD
                }
            )

        grades = ["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8"]
        for g in grades:
            GradeClass.objects.get_or_create(name=g, defaults={"department": Department.PRIMARY, "max_capacity": 30})
            AdmissionGrade.objects.get_or_create(name=g, defaults={"is_active": True})

        ecd_grades = ["Kindergarten", "Pre-Unit", "Nursery"]
        for g in ecd_grades:
            GradeClass.objects.get_or_create(name=g, defaults={"department": Department.ECD, "max_capacity": 25})
            AdmissionGrade.objects.get_or_create(name=g, defaults={"is_active": True})

        self.stdout.write("Creating Terms...")
        current_year = date.today().year
        t1, _ = Term.objects.get_or_create(
            academic_year=ay,
            name="Term 1",
            defaults={
                "start_date": date(current_year, 1, 5),
                "end_date": date(current_year, 4, 10),
                "is_locked": False
            }
        )
        current_term, _ = Term.objects.get_or_create(
            academic_year=ay,
            name="Term 2",
            defaults={
                "start_date": date(current_year, 5, 4),
                "end_date": date(current_year, 8, 15),
                "is_locked": False
            }
        )

        self.stdout.write("Creating Rooms (Venues)...")
        room_data = [
            ("Science Lab 1", "Main Block"),
            ("Computer Lab", "Library Wing"),
            ("Music Room", "Arts Center"),
            ("Main Field", "Sports Complex"),
            ("Swimming Pool", "Sports Complex"),
            ("School Hall", "Administration"),
            ("Library", "Library Wing"),
        ]
        # Add class specific rooms
        for g in grades + ecd_grades:
            room_data.append((f"{g} Classroom", "Academic Block"))
            
        rooms = []
        for name, building in room_data:
            r, _ = Room.objects.get_or_create(name=name, defaults={"building": building})
            rooms.append(r)

        self.stdout.write("Creating Staff Accounts (Teachers)...")
        teacher_data = [
            ("James", "Mwalimu", ["Mathematics", "Science"]),
            ("Sarah", "Wanjiru", ["English", "Music"]),
            ("David", "Ochieng", ["Social Studies", "CRE"]),
            ("Fatuma", "Ali", ["Kiswahili", "Physical Education"]),
            ("John", "Doe", ["Mathematics", "English"]),
            ("Esther", "Neri", ["Science", "Kiswahili"]),
            ("Peter", "Kamau", ["Social Studies", "Music"]),
            ("Lucy", "Achieng", ["Physical Education", "CRE"]),
        ]
        
        teachers = []
        for i, (fname, lname, ts_subjects) in enumerate(teacher_data):
            u, _ = User.objects.get_or_create(
                username=f"teacher{i+1}",
                defaults={
                    "first_name": fname,
                    "last_name": lname,
                    "role": UserRole.TEACHER,
                    "email": f"teacher{i+1}@example.com"
                }
            )
            u.set_password("hodari2026")
            u.save()
            teachers.append({"user": u, "subjects": ts_subjects})

        self.stdout.write("Creating Timetable Slots...")
        times = [
            (time(8, 0), time(8, 40)), (time(8, 40), time(9, 20)),
            (time(9, 20), time(10, 0)), (time(10, 30), time(11, 10)),
            (time(11, 10), time(11, 50)), (time(11, 50), time(12, 30)),
        ]
        days = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI]
        
        primary_subject_names = [s["name"] for s in primary_subjects]
        for g in grades[:6]: # Fill first 6 grades
            for day in days:
                for i, (start, end) in enumerate(times):
                    # Pick a teacher who teaches the chosen subject
                    subj = random.choice(primary_subject_names)
                    eligible_teachers = [t["user"] for t in teachers if subj in t["subjects"]]
                    assigned_teacher = random.choice(eligible_teachers) if eligible_teachers else teachers[0]["user"]
                    
                    # Pick a room (try to match class name or use a random one)
                    class_room = next((r for r in rooms if r.name == f"{g} Classroom"), random.choice(rooms))
                    
                    TimetableSlot.objects.get_or_create(
                        term=current_term,
                        day_of_week=day,
                        start_time=start,
                        end_time=end,
                        class_name=g,
                        defaults={
                            "subject_name": subj,
                            "teacher": assigned_teacher,
                            "room": class_room
                        }
                    )

        self.stdout.write("Creating Guardians and Students (Families)...")
        first_names = ["Baraka", "Esther", "Jabari", "Zahara", "Kofi", "Amani", "Tendai", "Zola", "Neo", "Lulu"]
        last_names = ["Abdi", "Kamau", "Moyo", "Osei", "Diallo", "Mensah", "Toure", "Kone", "Bello", "Sani"]
        
        admin_user = User.objects.filter(role=UserRole.SUPER_ADMIN).first() or User.objects.first()
        
        for i in range(15):
            lname = random.choice(last_names)
            parent = ParentGuardian.objects.create(
                full_name=f"{random.choice(first_names)} {lname}",
                phone=f"+254 7{random.randint(10, 99)} {random.randint(100, 999)} {random.randint(100, 999)}",
                secondary_phone=f"+254 20 {random.randint(200, 999)} {random.randint(100, 999)}",
                email=f"parent{i}@example.com",
                address=f"{random.randint(1, 500)} {random.choice(['Ngong Road', 'Waiyaki Way', 'Langata Rd'])}, Nairobi",
                preferred_language=random.choice(["en", "sw"]),
                pdpa_consent_given=True,
                pdpa_consent_method="in_person",
                pdpa_consent_version="v1.0",
                pdpa_consented_at=timezone.now()
            )
            
            # Create 1-2 children for this parent
            for j in range(random.randint(1, 2)):
                fname = random.choice(first_names)
                cls = random.choice(grades + ecd_grades)
                ts = int(timezone.now().timestamp()) % 100000
                student = Student.objects.create(
                    admission_no=f"ADM-{current_year}-{ts}{i}{j}",
                    first_name=fname,
                    last_name=lname,
                    date_of_birth=date(current_year - random.randint(5, 13), random.randint(1, 12), random.randint(1, 28)),
                    gender=random.choice(["male", "female"]),
                    nationality=random.choice(["Kenyan", "Tanzanian", "Ugandan", "British"]),
                    religion=random.choice(["Christian", "Muslim", "None"]),
                    blood_type=random.choice(["a+", "o+", "b+", "ab+"]),
                    allergies_medical=random.choice(["None", "Dust allergy", "Penicillin sensitive", "Lactose intolerant"]),
                    class_name=cls,
                    enrolment_date=date(current_year, 1, 1),
                    status=StudentStatus.ACTIVE
                )
                StudentGuardian.objects.create(
                    student=student,
                    guardian=parent,
                    relationship=random.choice([GuardianRelationship.MOTHER, GuardianRelationship.FATHER]),
                    is_primary=True if j == 0 else False
                )
                
                # Add some attendance for today
                if random.random() > 0.1:
                    AttendanceEntry.objects.create(
                        student=student,
                        date=date.today(),
                        status=AttendanceStatus.PRESENT,
                        class_name=cls,
                        marked_by=admin_user
                    )

        from events.models import CalendarEvent, EventCategory
        from discipline.models import DisciplineIncident, IncidentSeverity, IncidentStatus
        from welfare.models import WelfareObservation, WelfareSeverity, WelfareConcernType
        from communications.models import Notification, NotificationCategory

        self.stdout.write("Creating Calendar Events...")
        CalendarEvent.objects.create(
            title="Term 2 Mid-term Break",
            category=EventCategory.ACADEMIC,
            start_date=date.today() + timedelta(days=14),
            end_date=date.today() + timedelta(days=18),
            description="Short break for students and staff."
        )
        CalendarEvent.objects.create(
            title="Annual Sports Day",
            category=EventCategory.SOCIAL,
            start_date=date.today() + timedelta(days=30),
            description="Inter-house sports competitions at the main field."
        )

        self.stdout.write("Creating Welfare Observations...")
        students_list = list(Student.objects.all())
        ecd_grades = ["Kindergarten", "Pre-Unit", "Nursery"]
        ecd_students = [s for s in students_list if s.class_name in ecd_grades]
        primary_students = [s for s in students_list if s.class_name not in ecd_grades]
        
        if students_list:
            # ECD Welfare Observations
            ecd_welfare_samples = [
                {"concern_type": WelfareConcernType.BEHAVIORAL, "severity": WelfareSeverity.LOW, "observation_text": "Child is adjusting well to classroom routines, but occasionally needs gentle reminders to share toys with peers.", "action_taken": "Modeled sharing behavior during group play."},
                {"concern_type": WelfareConcernType.HEALTH, "severity": WelfareSeverity.MEDIUM, "observation_text": "Child had a mild cough and runny nose today. Parents were informed to monitor symptoms.", "action_taken": "Child kept in separate area, parents contacted via phone."},
                {"concern_type": WelfareConcernType.ACADEMIC, "severity": WelfareSeverity.LOW, "observation_text": "Showing great progress in recognizing colors and counting to 10.", "action_taken": "Provided additional counting games for reinforcement."},
                {"concern_type": WelfareConcernType.ATTENDANCE, "severity": WelfareSeverity.MEDIUM, "observation_text": "Absent 3 times this month. Will follow up with parents about consistent attendance.", "action_taken": "Scheduled parent meeting to discuss attendance."},
                {"concern_type": WelfareConcernType.SOCIAL_EMOTIONAL if hasattr(WelfareConcernType, 'SOCIAL_EMOTIONAL') else WelfareConcernType.BEHAVIORAL, "severity": WelfareSeverity.LOW, "observation_text": "Child gets upset easily when routines change. Working on transition warnings.", "action_taken": "Implemented 5-minute transition warnings before activities change."}
            ]
            for i, sample in enumerate(ecd_welfare_samples):
                if i < len(ecd_students):
                    WelfareObservation.objects.create(
                        student=ecd_students[i],
                        submitted_by=admin_user,
                        concern_type=sample["concern_type"],
                        severity=sample["severity"],
                        observation_date=date.today() - timedelta(days=i+1),
                        observation_text=sample["observation_text"],
                        action_taken=sample["action_taken"],
                        parent_contacted=random.choice([True, False]),
                        follow_up_required=random.choice([True, False])
                    )
            
            # Primary Welfare Observations
            primary_welfare_samples = [
                {"concern_type": WelfareConcernType.ACADEMIC, "severity": WelfareSeverity.MEDIUM, "observation_text": "Student is struggling with multiplication tables. Needs extra practice at home.", "action_taken": "Provided practice worksheets, parent notified via email."},
                {"concern_type": WelfareConcernType.BEHAVIORAL, "severity": WelfareSeverity.LOW, "observation_text": "Student is very talkative during lessons but participates well when called upon.", "action_taken": "Implemented hand-raising system, positive reinforcement for quiet listening."},
                {"concern_type": WelfareConcernType.HOME_SITUATION, "severity": WelfareSeverity.HIGH, "observation_text": "Student mentioned family stress at home. HOD follow-up recommended.", "action_taken": "HOD notified, will conduct home visit if needed."},
                {"concern_type": WelfareConcernType.HEALTH, "severity": WelfareSeverity.LOW, "observation_text": "Student has mild allergy symptoms during pollen season.", "action_taken": "Kept windows closed, student provided with water."}
            ]
            for i, sample in enumerate(primary_welfare_samples):
                if i < len(primary_students):
                    WelfareObservation.objects.create(
                        student=primary_students[i],
                        submitted_by=admin_user,
                        concern_type=sample["concern_type"],
                        severity=sample["severity"],
                        observation_date=date.today() - timedelta(days=i+2),
                        observation_text=sample["observation_text"],
                        action_taken=sample["action_taken"],
                        parent_contacted=random.choice([True, False]),
                        follow_up_required=random.choice([True, False])
                    )
        
        self.stdout.write("Creating Discipline Incidents...")
        if students_list:
            discipline_samples = [
                {"severity": IncidentSeverity.LOW, "summary": "Talking during lesson time, disruptive to peers.", "action_taken": "Verbal warning given, student apologized."},
                {"severity": IncidentSeverity.MEDIUM, "summary": "Physical altercation with another student over playground equipment.", "action_taken": "Both students separated, parents contacted, conflict resolution session held."},
                {"severity": IncidentSeverity.LOW, "summary": "Forgot to bring homework for the third time this week.", "action_taken": "Homework reminder note sent home."},
                {"severity": IncidentSeverity.HIGH, "summary": "Disrespectful language used towards staff member.", "action_taken": "Sent to HOD, parent meeting scheduled, detention assigned."},
                {"severity": IncidentSeverity.MEDIUM, "summary": "Vandalism of school property (drawing on desk).", "action_taken": "Student required to clean desk, parent notified, reflection essay written."}
            ]
            for i, sample in enumerate(discipline_samples):
                if i < len(students_list):
                    DisciplineIncident.objects.create(
                        student=random.choice(students_list),
                        reported_by=admin_user,
                        severity=sample["severity"],
                        summary=sample["summary"],
                        action_taken=sample["action_taken"],
                        status=random.choice(IncidentStatus.choices)[0],
                        incident_date=date.today() - timedelta(days=i+1)
                    )

        self.stdout.write("Creating Notifications...")
        for _ in range(10):
            Notification.objects.create(
                recipient=admin_user,
                category=random.choice(NotificationCategory.choices)[0],
                title=f"Sample Notification {random.randint(100,999)}",
                body="This is a generated notification to populate the user dashboard feed.",
                is_read=random.choice([True, False])
            )

        self.stdout.write("Creating Admissions Pipeline Scenarios...")
        
        # Scenario 1: New Inquiry
        for _ in range(3):
            Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 001",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-7, 1, 1),
                grade_applying_for=random.choice(grades),
                status=ApplicantStatus.INQUIRY_RECEIVED
            )

        # Scenario 2: Assessment Scheduled (Fee Unpaid - ACTION NEEDED)
        for _ in range(2):
            app = Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 002",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-8, 1, 1),
                grade_applying_for=random.choice(grades),
                status=ApplicantStatus.MEETING_SCHEDULED
            )
            AssessmentSchedule.objects.create(
                applicant=app,
                scheduled_date=date.today() - timedelta(days=1), # Past due
                scheduled_time=timezone.now().time(),
                location="Room 101",
                facilitating_teacher_name="Mr. Smith",
                assessment_fee_confirmed_paid=False
            )

        # Scenario 3: HOD Review Pending (ACTION NEEDED)
        for _ in range(2):
            Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 003",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-9, 1, 1),
                grade_applying_for=random.choice(grades),
                status=ApplicantStatus.HOD_REVIEW
            )

        # Scenario 4: HOS Decision Pending (ACTION NEEDED)
        for _ in range(1):
            Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 004",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-10, 1, 1),
                grade_applying_for=random.choice(grades),
                status=ApplicantStatus.HOS_DECISION
            )

        # Scenario 5: Admitted but Missing Documents (ACTION NEEDED)
        for _ in range(2):
            app = Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 005",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-11, 1, 1),
                grade_applying_for=random.choice(grades),
                status=ApplicantStatus.ADMITTED
            )
            # Create some but not all docs
            ApplicantDocumentReceipt.objects.create(applicant=app, document_type=ApplicantDocumentType.BIRTH_CERTIFICATE, is_received=True)
            ApplicantDocumentReceipt.objects.create(applicant=app, document_type=ApplicantDocumentType.CLEARANCE_FORM, is_received=False)

        # Scenario 6: Waitlisted
        for _ in range(5):
            Applicant.objects.create(
                parent_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                parent_phone="+254 700 000 006",
                child_full_name=f"{random.choice(first_names)} {random.choice(last_names)}",
                child_date_of_birth=date(current_year-12, 1, 1),
                grade_applying_for="Grade 1",
                status=ApplicantStatus.WAITLISTED
            )

        self.stdout.write("Creating Financial Data...")
        for i, student in enumerate(Student.objects.all()[:10]):
            amt = random.choice([45000, 52000, 38000])
            ts = int(timezone.now().timestamp()) % 100000
            inv = Invoice.objects.create(
                student=student,
                term=current_term,
                invoice_number=f"INV-2026-{ts}{i}",
                amount_due=amt,
                total_due=amt,
                is_finalized=True
            )
            if random.random() > 0.5:
                Payment.objects.create(
                    invoice=inv,
                    amount=inv.total_due / 2,
                    method="bank_transfer",
                    reference=f"TXN{random.randint(1000,9999)}",
                    created_by=admin_user
                )

        self.stdout.write(self.style.SUCCESS("Successfully populated database with realistic school data!"))
