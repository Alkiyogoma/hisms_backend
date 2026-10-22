"""Narrative comments use configured report classes across both departments."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import AcademicYear, Department, GradeClass, ReportCard, Term
from academics.tests import assign_role_group
from hr.models import StaffProfile, TeacherClassAssignment
from students.models import Student
from users.models import User, UserRole


class NarrativeCommentClassTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Term 1", start_date=today - timedelta(days=60),
            end_date=today + timedelta(days=30), endterm_exam_start_date=today - timedelta(days=4),
            endterm_exam_end_date=today - timedelta(days=1), grading_deadline=today + timedelta(days=10),
        )

        def user(name, role):
            account = User.objects.create_user(name, email=f"{name}@example.test", password="x", role=role)
            assign_role_group(account)
            return account

        cls.hos = user("comments-hos", UserRole.HEAD_OF_SCHOOL)
        cls.primary_hod = user("comments-primary-hod", UserRole.PRIMARY_HOD)
        cls.secondary_hod = user("comments-secondary-hod", UserRole.LOWER_SECONDARY_HOD)
        cls.teachers, cls.grades, cls.reports = {}, {}, {}
        for number in range(1, 10):
            department = Department.PRIMARY if number <= 6 else Department.LOWER_SECONDARY
            grade = GradeClass.objects.create(name=f"Grade {number}", department=department, sort_order=number)
            student = Student.objects.create(
                admission_no=f"COMMENTS-{number}", first_name=f"Learner {number}", last_name="Test",
                class_name=grade.name, academic_year=cls.year,
            )
            cls.grades[number] = grade
            cls.reports[number] = ReportCard.objects.create(student=student, term=cls.term, generated_by=cls.hos)
            teacher = user(f"comments-teacher-{number}", UserRole.TEACHER)
            staff = StaffProfile.objects.create(
                user=teacher, full_name=f"Teacher {number}", department=department,
                employment_start_date=today - timedelta(days=365),
            )
            TeacherClassAssignment.objects.create(
                teacher=staff, term=cls.term, grade_class=grade, is_class_teacher=True,
            )
            cls.teachers[number] = teacher

        # The department, rather than a known ECD name, determines exclusion.
        cls.ecd = GradeClass.objects.create(name="Foundation", department=Department.ECD)
        ecd_student = Student.objects.create(
            admission_no="COMMENTS-ECD", first_name="ECD", last_name="Learner", class_name=cls.ecd.name,
        )
        cls.ecd_report = ReportCard.objects.create(
            student=ecd_student, term=cls.term, generated_by=cls.hos, is_ecd_report=True,
        )

    def setUp(self):
        self.url = reverse("academics:primary_comments_entry")
        self.client.force_login(self.hos)

    def test_school_leadership_sees_grades_one_through_nine_without_ecd(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["classes"], [f"Grade {n}" for n in range(1, 10)])
        self.assertNotIn(self.ecd.name, response.context["classes"])
        for number in (7, 8, 9):
            self.assertContains(response, f">Grade {number}</option>")

    def test_new_configured_class_appears_automatically(self):
        GradeClass.objects.create(name="New Report Class", department=Department.LOWER_SECONDARY, sort_order=10)
        response = self.client.get(self.url)
        self.assertIn("New Report Class", response.context["classes"])
        self.assertContains(response, ">New Report Class</option>")

    def test_department_label_follows_the_selected_class(self):
        for number, department in ((3, "Primary"), (7, "Lower Secondary")):
            with self.subTest(number=number):
                response = self.client.get(self.url, {"class_name": f"Grade {number}"})
                self.assertEqual(response.context["selected_department_label"], department)
                self.assertContains(response, f"{department} Department</span>")
                self.assertEqual(response.context["student_reports"][0]["report"], self.reports[number])

    def test_section_heads_see_their_own_departments(self):
        for user, numbers in ((self.primary_hod, range(1, 7)), (self.secondary_hod, range(7, 10))):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.context["classes"], [f"Grade {n}" for n in numbers])

    def test_lower_secondary_class_teachers_can_open_and_save_comments(self):
        for number in (7, 8, 9):
            with self.subTest(number=number):
                self.client.force_login(self.teachers[number])
                response = self.client.get(self.url, {"class_name": f"Grade {number}"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["classes"], [f"Grade {number}"])
                report = self.reports[number]
                comment = f"Grade {number} learner has made steady progress and works confidently with classmates."
                response = self.client.post(self.url, {
                    "term_id": self.term.pk, "class_name": f"Grade {number}", f"comment_{report.pk}": comment,
                })
                self.assertEqual(response.status_code, 302)
                report.refresh_from_db()
                self.assertEqual(report.teacher_comments, comment)

    def test_lower_secondary_comment_autosave(self):
        self.client.force_login(self.teachers[8])
        report = self.reports[8]
        response = self.client.post(
            self.url, json.dumps({"report_id": report.pk, "comment": "Thoughtful contributions in class."}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.teacher_comments, "Thoughtful contributions in class.")

    def test_teacher_cannot_read_or_write_another_class(self):
        self.client.force_login(self.teachers[7])
        report = self.reports[8]
        self.assertEqual(self.client.get(self.url, {"class_name": "Grade 8"}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {
            "term_id": self.term.pk, "class_name": "Grade 8", f"comment_{report.pk}": "Wrong class",
        }).status_code, 403)
        self.assertEqual(self.client.post(
            self.url, json.dumps({"report_id": report.pk, "comment": "Wrong class"}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.teacher_comments, "")

    def test_subject_teacher_has_no_narrative_comment_classes(self):
        TeacherClassAssignment.objects.filter(teacher__user=self.teachers[7]).update(is_class_teacher=False)
        self.client.force_login(self.teachers[7])
        self.assertEqual(self.client.get(self.url).context["classes"], [])
        self.assertEqual(self.client.get(self.url, {"class_name": "Grade 7"}).status_code, 403)

    def test_ecd_cannot_be_selected_or_saved_from_this_page(self):
        self.assertEqual(self.client.get(self.url, {"class_name": self.ecd.name}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {
            "term_id": self.term.pk, "class_name": self.ecd.name, f"comment_{self.ecd_report.pk}": "Wrong format",
        }).status_code, 403)
        self.assertEqual(self.client.post(
            self.url, json.dumps({"report_id": self.ecd_report.pk, "comment": "Wrong format"}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).status_code, 403)

    def test_submissions_notify_the_head_of_the_selected_department(self):
        for number, head in ((3, self.primary_hod), (7, self.secondary_hod)):
            with self.subTest(number=number):
                self.client.force_login(self.teachers[number])
                report = self.reports[number]
                with patch("communications.email_service.dispatch_notification") as notify:
                    response = self.client.post(self.url, {
                        "term_id": self.term.pk, "class_name": f"Grade {number}", "action": "submit_for_review",
                        f"comment_{report.pk}": "A thoughtful learner who makes steady progress and works well with classmates.",
                    })
                self.assertEqual(response.status_code, 302)
                report.refresh_from_db()
                self.assertTrue(report.comments_submitted)
                recipients = {call.kwargs["user"].pk for call in notify.call_args_list}
                self.assertEqual(recipients, {self.hos.pk, head.pk})
