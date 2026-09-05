"""
Seed realistic communication data for testing in-app notification delivery.
Creates notifications, broadcasts, and weekly focus entries for parents and staff.

Usage:
    python manage.py seed_communication_data
    python manage.py seed_communication_data --clear   # clear existing test data first
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import timedelta


class Command(BaseCommand):
    help = "Seed communication data: notifications, broadcasts, weekly focus for parents & staff"

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Clear existing test comms data first')

    def handle(self, *args, **options):
        from communications.models import (
            Notification, NotificationCategory, Broadcast, BroadcastAudience,
            BroadcastStatus, WeeklyFocus, WeeklyFocusStatus,
        )
        from users.models import User, UserRole
        from students.models import Student, StudentGuardian, ParentGuardian

        if options['clear']:
            Notification.objects.all().delete()
            Broadcast.objects.all().delete()
            WeeklyFocus.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared all notifications, broadcasts, and weekly focus."))

        now = timezone.now()

        # --- Gather users ---
        parents = list(User.objects.filter(role=UserRole.PARENT, is_active=True))
        teachers = list(User.objects.filter(role=UserRole.TEACHER, is_active=True))
        hods = list(User.objects.filter(role__in=[UserRole.PRIMARY_HOD, UserRole.ECD_HOD], is_active=True))
        hos_users = list(User.objects.filter(role=UserRole.HEAD_OF_SCHOOL, is_active=True))
        admin_officers = list(User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True))
        finance_officers = list(User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True))
        super_admins = list(User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True))
        all_staff = list(User.objects.exclude(role=UserRole.PARENT).filter(is_active=True))

        # --- Gather students ---
        students = list(Student.objects.filter(is_archived=False))
        student_names = [s.get_full_name() for s in students]

        notif_count = 0
        broadcast_count = 0
        wf_count = 0

        # ============================================================
        # 1. IN-APP NOTIFICATIONS FOR PARENTS
        # ============================================================
        self.stdout.write("\n--- Creating parent notifications ---")

        parent_notifications = []

        # Finance notifications
        finance_templates = [
            ("Fee Reminder: {name}", "School fees for {name} are due on 31 July 2026. Amount: TZS 500,000. Please pay before the due date to avoid disruption.", "/finance/invoices/"),
            ("Overdue Fee Alert: {name}", "The fee payment for {name} is now overdue (TZS 500,000). Please settle immediately.", "/finance/invoices/"),
            ("Payment Confirmed: {name}", "Your payment of TZS 250,000 for {name} has been received and processed. Outstanding balance: TZS 250,000.", "/finance/invoices/"),
            ("Sibling Discount Available", "A 10% sibling discount is now available for families with 2+ children enrolled. Contact the finance office.", "/finance/"),
        ]

        for parent in parents:
            for student in students[:2]:
                for title_tpl, body_tpl, link in finance_templates[:2]:
                    parent_notifications.append(Notification(
                        recipient=parent,
                        category=NotificationCategory.FINANCE,
                        title=title_tpl.format(name=student.get_full_name()),
                        body=body_tpl.format(name=student.get_full_name()),
                        link=link,
                    ))

        # Attendance notifications
        attendance_templates = [
            ("Absent Today: {name}", "{name} was marked absent today. If this is an error, please contact the school.", "/attendance/"),
            ("Late Arrival: {name}", "{name} arrived 15 minutes late today (08:15 AM). Regular school hours start at 8:00 AM.", "/attendance/"),
            ("Good Attendance: {name}", "{name} has maintained perfect attendance this term. Well done!", "/attendance/"),
        ]

        for parent in parents:
            student = students[0] if students else None
            if student:
                for title_tpl, body_tpl, link in attendance_templates[:1]:
                    parent_notifications.append(Notification(
                        recipient=parent,
                        category=NotificationCategory.ATTENDANCE,
                        title=title_tpl.format(name=student.get_full_name()),
                        body=body_tpl.format(name=student.get_full_name()),
                        link=link,
                    ))

        # Welfare notifications
        welfare_templates = [
            ("Welfare Note: {name}", "A welfare observation has been recorded for {name}. Please review the details.", "/welfare/"),
            ("Health Reminder: {name}", "Reminder: {name} is due for a health check-up. Please ensure the school nurse has the latest medical records.", "/welfare/"),
        ]

        for parent in parents:
            for student in students[:1]:
                for title_tpl, body_tpl, link in welfare_templates:
                    parent_notifications.append(Notification(
                        recipient=parent,
                        category=NotificationCategory.WELFARE,
                        title=title_tpl.format(name=student.get_full_name()),
                        body=body_tpl.format(name=student.get_full_name()),
                        link=link,
                    ))

        # Academic notifications
        academic_templates = [
            ("Report Card Ready: {name}", "The Term 1 report card for {name} has been published. Overall average: 85.3%. View the full report.", "/reports/"),
            ("Exam Results: {name}", "Mid-term exam results for {name} are now available. Mathematics: 92, English: 78, Science: 88.", "/reports/"),
        ]

        for parent in parents:
            for student in students[:1]:
                for title_tpl, body_tpl, link in academic_templates:
                    parent_notifications.append(Notification(
                        recipient=parent,
                        category=NotificationCategory.ACADEMIC,
                        title=title_tpl.format(name=student.get_full_name()),
                        body=body_tpl.format(name=student.get_full_name()),
                        link=link,
                    ))

        # Discipline notification
        for parent in parents:
            student = students[0] if students else None
            if student:
                parent_notifications.append(Notification(
                    recipient=parent,
                    category=NotificationCategory.WELFARE,
                    title=f"Discipline Note: {student.get_full_name()}",
                    body=f"A minor discipline incident was recorded for {student.get_full_name()} today. "
                         f"Summary: Talking in class during lesson. Action: Verbal warning. "
                         f"Please discuss this with your child.",
                    link="/discipline/",
                ))

        # System notification
        for parent in parents:
            parent_notifications.append(Notification(
                recipient=parent,
                category=NotificationCategory.SYSTEM,
                title="Parent Portal Account Activated",
                body="Your parent portal account is now active. You can view your children's attendance, "
                     "fees, reports, and communicate with the school through this portal.",
                link="/parent/",
            ))

        created = Notification.objects.bulk_create(parent_notifications)
        notif_count += len(created)
        self.stdout.write(f"  Created {len(created)} parent notifications")

        # ============================================================
        # 2. IN-APP NOTIFICATIONS FOR STAFF
        # ============================================================
        self.stdout.write("\n--- Creating staff notifications ---")

        staff_notifications = []

        # Teacher notifications
        teacher_notif_templates = [
            ("Lesson Plan Due", "Your lesson plan for this week is due for submission. Please complete and submit before Friday.", "/academics/lesson-plans/", NotificationCategory.ACADEMIC),
            ("Attendance Required", "You have 3 classes with pending attendance submission for today. Please submit before 5:00 PM.", "/attendance/", NotificationCategory.ATTENDANCE),
            ("Report Card Review", "Report cards for your class need review and sign-off. 12 reports are pending your approval.", "/reports/", NotificationCategory.ACADEMIC),
            ("Timetable Updated", "Your timetable has been updated for next week. A new Science practical slot has been added on Tuesday.", "/timetable/", NotificationCategory.ACADEMIC),
            ("PTC Meeting Scheduled", "A Parent-Teacher Conference meeting is scheduled for 25 July 2026. Please prepare student progress reports.", "/ptc/", NotificationCategory.ACADEMIC),
        ]

        for teacher in teachers[:10]:
            for title, body, link, cat in teacher_notif_templates[:3]:
                staff_notifications.append(Notification(
                    recipient=teacher,
                    category=cat,
                    title=title,
                    body=body,
                    link=link,
                ))

        # HOD notifications
        hod_templates = [
            ("Department Review Pending", "3 lesson plans from your department are pending review. Please review and provide feedback.", "/academics/lesson-plans/", NotificationCategory.ACADEMIC),
            ("Teacher Compliance", "2 teachers in your department have not submitted lesson plans this week. Follow-up required.", "/academics/", NotificationCategory.ACADEMIC),
            ("Budget Submission", "Department budget proposals for Term 2 are due by 20 July 2026. 1 of 3 departments has submitted.", "/finance/", NotificationCategory.FINANCE),
        ]

        for hod in hods:
            for title, body, link, cat in hod_templates:
                staff_notifications.append(Notification(
                    recipient=hod,
                    category=cat,
                    title=title,
                    body=body,
                    link=link,
                ))

        # Head of School notifications
        hos_templates = [
            ("Daily Summary", "Today's summary: 45 students present, 3 absent, 1 late. 2 new admissions pending review.", "/dashboard/", NotificationCategory.SYSTEM),
            ("Staff Leave Request", "Sarah Wanjiku has requested leave for 22-24 July 2026 (3 days). Reason: Personal. Pending your approval.", "/hr/leave/", NotificationCategory.SYSTEM),
            ("Fee Collection Report", "Weekly fee collection: TZS 2,500,000 received from 15 parents. 8 accounts still overdue.", "/finance/", NotificationCategory.FINANCE),
            ("Compliance Alert", "Term 1 report cards: 85% signed off by teachers. 4 reports still pending. Deadline: 18 July 2026.", "/reports/", NotificationCategory.ACADEMIC),
        ]

        for hos in hos_users:
            for title, body, link, cat in hos_templates:
                staff_notifications.append(Notification(
                    recipient=hos,
                    category=cat,
                    title=title,
                    body=body,
                    link=link,
                ))

        # Finance Officer notifications
        finance_templates_staff = [
            ("Payment Received", "New payment of TZS 750,000 received from Jane Mutua for 2 children. Please verify and reconcile.", "/finance/payments/", NotificationCategory.FINANCE),
            ("Overdue Accounts", "8 accounts are now overdue (7+ days). Total outstanding: TZS 4,200,000. Send reminders?", "/finance/invoices/", NotificationCategory.FINANCE),
            ("Sibling Discount Eligible", "3 families are eligible for sibling discounts. Please review and apply.", "/finance/", NotificationCategory.FINANCE),
        ]

        for fo in finance_officers:
            for title, body, link, cat in finance_templates_staff:
                staff_notifications.append(Notification(
                    recipient=fo,
                    category=cat,
                    title=title,
                    body=body,
                    link=link,
                ))

        # Admin Officer notifications
        admin_templates = [
            ("New Admission Application", "New application received: Grace Muthoni for Grade 2. Assessment scheduled for 20 July 2026.", "/admissions/", NotificationCategory.ADMISSIONS),
            ("Guardian Link Request", "A parent has requested guardian access through the portal. Please review and approve.", "/students/guardians/", NotificationCategory.SYSTEM),
            ("Document Upload", "3 new student documents are pending upload to the system. Priority: High.", "/students/", NotificationCategory.SYSTEM),
        ]

        for ao in admin_officers:
            for title, body, link, cat in admin_templates:
                staff_notifications.append(Notification(
                    recipient=ao,
                    category=cat,
                    title=title,
                    body=body,
                    link=link,
                ))

        created = Notification.objects.bulk_create(staff_notifications)
        notif_count += len(created)
        self.stdout.write(f"  Created {len(created)} staff notifications")

        # ============================================================
        # 3. BROADCAST MESSAGES (sent, with in-app notifications)
        # ============================================================
        self.stdout.write("\n--- Creating broadcast messages ---")

        creator = super_admins[0] if super_admins else (hos_users[0] if hos_users else admin_officers[0])

        broadcasts_data = [
            {
                "audience": BroadcastAudience.ALL_PARENTS,
                "subject": "End of Term 1 -- Report Cards & Break Notice",
                "body": "Dear Parents,\n\nTerm 1 has officially ended. Report cards will be distributed on 25 July 2026. "
                        "The school break runs from 26 July to 15 August 2026.\n\n"
                        "Please ensure all outstanding fees are settled before the break.\n\n"
                        "We wish you and your families a restful break.\n\nBest regards,\nHodari Schools Administration",
                "status": BroadcastStatus.SENT,
            },
            {
                "audience": BroadcastAudience.ALL_PARENTS,
                "subject": "Sports Day -- 30 July 2026",
                "body": "Dear Parents,\n\nOur annual Sports Day will be held on 30 July 2026. "
                        "Students should arrive by 7:30 AM in full sports uniform.\n\n"
                        "Parents are welcome to attend and support. Refreshments will be available.\n\n"
                        "Schedule:\n- 8:00 AM: Opening ceremony\n- 8:30 AM: Track events\n- 11:00 AM: Team sports\n- 1:00 PM: Prize giving\n\n"
                        "Please contact the school if your child has any medical conditions we should be aware of.",
                "status": BroadcastStatus.SENT,
            },
            {
                "audience": BroadcastAudience.GRADE,
                "target_grade": "Grade 1",
                "subject": "Grade 1 Parent Meeting -- Academic Progress",
                "body": "Dear Grade 1 Parents,\n\nA parent meeting is scheduled for 22 July 2026 at 3:00 PM "
                        "to discuss your children's academic progress in Term 1.\n\n"
                        "Topics: Report card review, areas of improvement, holiday study tips.\n\n"
                        "Attendance is strongly encouraged.",
                "status": BroadcastStatus.SENT,
            },
            {
                "audience": BroadcastAudience.STAFF,
                "subject": "Staff Meeting -- Term 2 Planning",
                "body": "Dear Staff,\n\nA mandatory staff meeting is scheduled for 19 July 2026 at 2:00 PM "
                        "in the main hall to discuss Term 2 planning.\n\n"
                        "Agenda:\n1. Term 1 review\n2. Term 2 curriculum plan\n3. Sports Day coordination\n4. Budget review\n\n"
                        "Please come prepared with your department reports.",
                "status": BroadcastStatus.SENT,
            },
            {
                "audience": BroadcastAudience.STAFF,
                "subject": "Lesson Plan Submission Deadline Reminder",
                "body": "Dear Staff,\n\nThis is a reminder that all lesson plans for Week 1 of Term 2 "
                        "must be submitted by Friday, 18 July 2026.\n\n"
                        "HODs are expected to review and approve by Monday, 21 July 2026.\n\n"
                        "Non-compliance will be flagged in the weekly report.",
                "status": BroadcastStatus.SENT,
            },
        ]

        # Also create a draft broadcast
        broadcasts_data.append({
            "audience": BroadcastAudience.ALL_PARENTS,
            "subject": "Fee Structure Update -- Term 2",
            "body": "Dear Parents,\n\nThe Term 2 fee structure has been updated. "
                    "New fees will be reflected in the system by 20 July 2026.\n\n"
                    "Please check the parent portal for updated invoice details.",
            "status": BroadcastStatus.DRAFT,
        })

        for bdata in broadcasts_data:
            b = Broadcast.objects.create(created_by=creator, **bdata)
            broadcast_count += 1

            if b.status == BroadcastStatus.SENT:
                b.sent_at = now - timedelta(days=3)
                b.save(update_fields=["sent_at"])

                # Create notifications for sent broadcasts
                if b.audience == BroadcastAudience.ALL_PARENTS:
                    recipients = parents
                elif b.audience == BroadcastAudience.STAFF:
                    recipients = all_staff
                elif b.audience == BroadcastAudience.GRADE:
                    guardian_ids = StudentGuardian.objects.filter(
                        student__class_name=b.target_grade
                    ).values_list("guardian__user_id", flat=True)
                    recipients = User.objects.filter(pk__in=guardian_ids, is_active=True)
                else:
                    recipients = []

                notifs = [
                    Notification(
                        recipient=u,
                        category=NotificationCategory.BROADCAST,
                        title=b.subject,
                        body=b.body,
                        link=f"/communications/broadcasts/{b.pk}/",
                    )
                    for u in recipients
                ]
                Notification.objects.bulk_create(notifs)
                b.recipient_count = len(notifs)
                b.save(update_fields=["recipient_count"])
                notif_count += len(notifs)
                self.stdout.write(f"  Broadcast '{b.subject}' -> {len(notifs)} notifications ({b.audience})")

        # ============================================================
        # 4. WEEKLY FOCUS ENTRIES (ECD parent communication)
        # ============================================================
        self.stdout.write("\n--- Creating weekly focus entries ---")

        ecd_teachers = list(User.objects.filter(role=UserRole.TEACHER, is_active=True)[:2])
        if not ecd_teachers:
            ecd_teachers = teachers[:1]

        ecd_classes = ["Pre-School", "ECD A", "ECD B"]
        themes = [
            ("Exploring Colors and Shapes", "Children will learn to identify primary colors and basic shapes through hands-on activities.", "Color crayons, shape puzzles"),
            ("My Family", "Children will share about their families and create family portraits.", "Family photo, art supplies"),
            ("Animals Around Us", "Children will learn about farm animals and wild animals.", "Animal picture cards, toy animals"),
            ("Healthy Eating", "Children will learn about fruits, vegetables, and healthy food choices.", "Real fruits for show and tell"),
            ("Weather and Seasons", "Children will explore different weather types and seasons.", "Rain boots, sun hat (for discussion)"),
        ]

        for i, (theme, activities, items) in enumerate(themes):
            wf = WeeklyFocus.objects.create(
                teacher=ecd_teachers[0] if ecd_teachers else creator,
                class_name=ecd_classes[0] if ecd_classes else "Pre-School",
                week_number=i + 1,
                academic_year="2025-2026",
                theme=theme,
                planned_activities=activities,
                items_to_bring=items,
                status=WeeklyFocusStatus.APPROVED,
                is_published=True,
                published_at=now - timedelta(days=7 * (len(themes) - i)),
                reviewed_by=hods[0] if hods else creator,
                reviewed_at=now - timedelta(days=7 * (len(themes) - i) - 1),
            )
            wf_count += 1

            # Notify ECD parents about published weekly focus
            for parent in parents:
                Notification.objects.create(
                    recipient=parent,
                    category=NotificationCategory.ACADEMIC,
                    title=f"Weekly Focus: {theme}",
                    body=f"New weekly focus for {wf.class_name} (Week {wf.week_number}):\n\n"
                         f"Theme: {theme}\n"
                         f"Activities: {activities}\n"
                         f"Items to bring: {items}\n\n"
                         f"Please ensure your child comes prepared.",
                    link="/communications/weekly-focus/parent/",
                )
                notif_count += 1

        self.stdout.write(f"  Created {wf_count} weekly focus entries")

        # ============================================================
        # SUMMARY
        # ============================================================
        total_notifs = Notification.objects.count()
        total_broadcasts = Broadcast.objects.count()
        total_wf = WeeklyFocus.objects.count()

        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*50}\n"
            f"COMMUNICATION DATA SEEDED SUCCESSFULLY\n"
            f"{'='*50}\n"
            f"  Notifications created this run: {notif_count}\n"
            f"  Broadcasts created this run:     {broadcast_count}\n"
            f"  Weekly Focus entries:            {wf_count}\n"
            f"\n  TOTALS in database:\n"
            f"    Notifications:  {total_notifs}\n"
            f"    Broadcasts:     {total_broadcasts}\n"
            f"    Weekly Focus:   {total_wf}\n"
            f"{'='*50}"
        ))
