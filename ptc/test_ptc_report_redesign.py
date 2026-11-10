"""PTC module rework: the report matches the school's PTC design, attribute and
comment saves work and report errors, comments are for mark-bearing subjects
only, Comments/Attributes have a class filter, every report opening is
logged, and Compliance/Enrichment are gone."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from academics.models import (
    AcademicYear, AssessmentMode, Department, ExamScore, ExamTypeConfiguration,
    GradeClass, ScoreStatus, Subject, SubjectTermRemark, Term,
)
from hr.models import StaffProfile, TeacherClassAssignment
from ptc.models import (
    LEARNER_ATTRIBUTES, LearnerAttributeRatingEntry, PTCGenerationLog, PTCSubjectComment,
    PTCWindow, TermSlot,
)
from ptc.services import build_ptc_report, get_current_ptc_window
from students.models import Student, StudentStatus
from users.models import User, UserRole

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
MARKS = ["English", "Humanities", "Kiswahili", "Mathematics", "Science"]
REMARKS = ["Art", "Music"]


class PTCFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.today = today
        cls.year = AcademicYear.objects.create(
            name="2026/2027", is_current=True,
            start_date=today - timedelta(days=60), end_date=today + timedelta(days=300),
        )
        cls.t1 = Term.objects.create(academic_year=cls.year, name="Term 1", term_number=1,
                                     start_date=today - timedelta(days=60), end_date=today + timedelta(days=30))
        cls.t2 = Term.objects.create(academic_year=cls.year, name="Term 2", term_number=2,
                                     start_date=today + timedelta(days=45), end_date=today + timedelta(days=150))
        cls.t3 = Term.objects.create(academic_year=cls.year, name="Term 3", term_number=3,
                                     start_date=today + timedelta(days=165), end_date=today + timedelta(days=290))
        for code, w in (("quiz", 20), ("mid_term", 30), ("end_of_term", 50)):
            ExamTypeConfiguration.objects.update_or_create(
                code=code, defaults={"name": code, "weight_percentage": Decimal(w),
                                     "max_score": Decimal(100), "is_active": True})
        cls.gc, _ = GradeClass.objects.get_or_create(name="Grade 8", defaults={"department": Department.PRIMARY, "sort_order": 8})
        cls.gc2, _ = GradeClass.objects.get_or_create(name="Grade 2", defaults={"department": Department.PRIMARY, "sort_order": 2})
        subjects = []
        for name in MARKS + REMARKS:
            mode = AssessmentMode.REMARK if name in REMARKS else AssessmentMode.MARKS
            s = Subject.objects.filter(name=name).first() or Subject(name=name, code=f"PTC{len(subjects)}")
            s.department = Department.PRIMARY
            s.departments = [Department.PRIMARY]
            s.assessment_mode = mode
            s.is_active = True
            s.save()
            subjects.append(s)
        cls.gc.subjects.set(subjects)
        cls.gc2.subjects.set(subjects)
        cls.window = PTCWindow.objects.create(academic_year=cls.year, term_slot=TermSlot.TERM_1,
                                              ptc_date=today + timedelta(days=7))
        cls.amani = Student.objects.create(admission_no="A1", first_name="Amani", last_name="Juma",
                                           class_name="Grade 8", academic_year=cls.year, status=StudentStatus.ACTIVE)
        cls.baraka = Student.objects.create(admission_no="B1", first_name="Baraka", last_name="Kweka",
                                            class_name="Grade 8", academic_year=cls.year, status=StudentStatus.ACTIVE)
        cls.other = Student.objects.create(admission_no="C1", first_name="Chausiku", last_name="Lema",
                                           class_name="Grade 2", academic_year=cls.year, status=StudentStatus.ACTIVE)
        cls.admin = User.objects.create_superuser(username="sa", email="sa@x.test", password="x",
                                                  role=UserRole.SUPER_ADMIN, first_name="Super", last_name="Admin")
        cls.ct = User.objects.create_user(username="ct", email="ct@x.test", password="x", role=UserRole.TEACHER,
                                          first_name="Naa", last_name="Adams")
        cls.st = User.objects.create_user(username="st", email="st@x.test", password="x", role=UserRole.TEACHER,
                                          first_name="Sam", last_name="Teacher")
        for u in (cls.ct, cls.st):
            prof = StaffProfile.objects.create(user=u, full_name=u.get_full_name(), job_title="Teacher",
                                               department=Department.PRIMARY, departments=[Department.PRIMARY],
                                               employment_start_date=today - timedelta(days=900))
            u.profile = prof
        TeacherClassAssignment.objects.create(teacher=cls.ct.profile, term=cls.t1, grade_class=cls.gc,
                                              is_class_teacher=True, subjects_taught=["English"])
        # Subject teacher of Mathematics and Music (remark-only) in Grade 8.
        TeacherClassAssignment.objects.create(teacher=cls.st.profile, term=cls.t1, grade_class=cls.gc,
                                              subjects_taught=["Mathematics", "Music"])
        _grant(cls.ct)
        _grant(cls.st)


def _grant(user):
    from django.contrib.auth.models import Permission
    for code in ("view_ptcsubjectcomment", "change_ptcsubjectcomment",
                 "view_learnerattributeratingentry", "change_learnerattributeratingentry"):
        user.user_permissions.add(Permission.objects.get(codename=code, content_type__app_label="ptc"))


class AttributeSaveTests(PTCFixture):
    def _post(self, user, student, ratings):
        self.client.force_login(user)
        data = {"student_id": student.pk, **{f"attr_{n}": r for n, r in ratings.items()}}
        return self.client.post(reverse("ptc:attribute_entry"), data, **AJAX)

    def test_super_admin_saves_all_twelve_and_learner_is_done(self):
        resp = self._post(self.admin, self.amani, {n: "G" for n, _ in LEARNER_ATTRIBUTES})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["done"], True)
        self.assertEqual(LearnerAttributeRatingEntry.objects.filter(
            student=self.amani, ptc_window=self.window).exclude(rating="").count(), 12)
        # The class page counts them as done and the report shows them.
        page = self.client.get(reverse("ptc:attribute_entry"), {"class": "Grade 8"})
        self.assertEqual(page.context["complete_count"], 1)
        report = self.client.get(reverse("ptc:ptc_screen", args=[self.amani.pk])).context["report"]
        self.assertEqual(report["attribute_rows"][0]["cells"][0], "G")

    def test_class_teacher_saves(self):
        resp = self._post(self.ct, self.amani, {1: "E", 2: "S"})
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(resp.json()["done"])
        self.assertEqual(resp.json()["ratings"], {"1": "E", "2": "S"})

    def test_other_teacher_gets_a_clear_error_and_nothing_is_saved(self):
        resp = self._post(self.st, self.amani, {1: "E"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Only the class teacher of Grade 8", resp.json()["error"])
        self.assertFalse(LearnerAttributeRatingEntry.objects.exists())

    def test_non_class_teacher_sees_read_only_note(self):
        self.client.force_login(self.st)
        page = self.client.get(reverse("ptc:attribute_entry"))
        self.assertFalse(page.context["can_save"])
        self.assertContains(page, "Only the class teacher of Grade 8")

    def test_invalid_rating_is_rejected_whole(self):
        resp = self._post(self.admin, self.amani, {1: "E", 2: "X"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(LearnerAttributeRatingEntry.objects.exists())

    def test_plain_post_redirects_back_to_same_learner(self):
        self.client.force_login(self.ct)
        resp = self.client.post(reverse("ptc:attribute_entry"), {"student_id": self.baraka.pk, "attr_1": "E"})
        self.assertRedirects(resp, reverse("ptc:attribute_entry") + f"?class=Grade 8&ptc={self.window.pk}&student={self.baraka.pk}",
                             fetch_redirect_response=False)


class CommentEntryTests(PTCFixture):
    def test_only_mark_bearing_subjects_the_teacher_teaches(self):
        self.client.force_login(self.st)
        page = self.client.get(reverse("ptc:comment_entry"))
        self.assertEqual(page.context["subjects"], ["Mathematics"])  # Music is remark-only

    def test_admin_gets_all_five_mark_bearing_subjects(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ptc:comment_entry"), {"class": "Grade 8"})
        self.assertEqual(page.context["subjects"], MARKS)
        self.assertEqual(page.context["selected_class"], "Grade 8")
        self.assertContains(page, "<b>0 of 2</b> students done", html=False)

    def test_save_is_json_and_done_only_when_server_confirms(self):
        self.client.force_login(self.st)
        paragraph = "Amani works steadily in Mathematics. " * 20
        resp = self.client.post(reverse("ptc:comment_entry"), {
            "student_id": self.amani.pk, "comment_mathematics": paragraph, "comment_music": "ignored",
        }, **AJAX)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["done"], True)
        c = PTCSubjectComment.objects.get(student=self.amani, ptc_window=self.window)
        self.assertEqual((c.subject_name, c.comment_text), ("Mathematics", paragraph.strip()))
        self.assertFalse(PTCSubjectComment.objects.filter(subject_name="Music").exists())

    def test_teacher_of_other_class_gets_error(self):
        self.client.force_login(self.st)
        resp = self.client.post(reverse("ptc:comment_entry"),
                                {"student_id": self.other.pk, "comment_mathematics": "x"}, **AJAX)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["ok"])
        self.assertFalse(PTCSubjectComment.objects.exists())

    def test_class_filter_defaults_to_teachers_class(self):
        self.client.force_login(self.ct)
        page = self.client.get(reverse("ptc:comment_entry"))
        self.assertEqual(page.context["selected_class"], "Grade 8")
        self.assertEqual([d["id"] for d in page.context["students_data"]], [self.amani.pk, self.baraka.pk])


class FilterAndOverviewTests(PTCFixture):
    def test_subject_filter_shows_one_subject(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ptc:comment_entry"), {"class": "Grade 8", "subject": "Science"})
        self.assertEqual([s["name"] for s in page.context["subjects_data"]], ["Science"])
        self.assertEqual(page.context["filters"]["subject"], "Science")

    def test_ptc_filter_picks_another_terms_window(self):
        later = PTCWindow.objects.create(academic_year=self.year, term_slot=TermSlot.TERM_2,
                                         ptc_date=self.today + timedelta(days=120))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ptc:comment_entry"), {"class": "Grade 8", "ptc": later.pk})
        self.assertEqual(page.context["ptc_window"], later)
        # Saving goes to the chosen window.
        self.client.post(reverse("ptc:comment_entry"), {
            "student_id": self.amani.pk, "ptc": later.pk, "comment_science": "Term 2 comment",
        }, **AJAX)
        self.assertEqual(PTCSubjectComment.objects.get().ptc_window, later)

    def test_students_overview_ticks_subjects_with_comments(self):
        PTCSubjectComment.objects.create(student=self.amani, ptc_window=self.window, subject_name="Science",
                                         comment_text="Good.")
        self.client.force_login(self.ct)
        page = self.client.get(reverse("ptc:student_picker"))
        self.assertEqual(page.context["selected_class"], "Grade 8")
        self.assertEqual([s["name"] for s in page.context["subjects"]], MARKS)
        amani = next(r for r in page.context["rows"] if r["student"] == self.amani)
        self.assertEqual(amani["ticks"], [False, False, False, False, True])
        self.assertFalse(amani["done"])
        self.assertTrue(page.context["can_open"])
        self.assertContains(page, "Open</a>")


class LogTests(PTCFixture):
    def test_every_opening_is_logged_for_any_role(self):
        self.client.force_login(self.admin)
        url = reverse("ptc:ptc_screen", args=[self.amani.pk])
        self.client.get(url)
        self.client.get(url)
        self.client.force_login(self.ct)
        self.client.get(url)
        logs = PTCGenerationLog.objects.filter(student=self.amani)
        self.assertEqual(logs.count(), 3)
        self.assertEqual(set(logs.values_list("class_name", flat=True)), {"Grade 8"})
        self.assertEqual(logs.filter(class_teacher=self.admin).count(), 2)

    def test_view_as_records_the_real_user(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["impersonate_user_id"] = self.ct.pk
        session.save()
        self.client.get(reverse("ptc:ptc_screen", args=[self.amani.pk]))
        log = PTCGenerationLog.objects.get()
        self.assertEqual((log.class_teacher, log.viewed_as), (self.admin, self.ct))


class ReportTests(PTCFixture):
    def _score(self, subject, exam, score, term=None):
        ExamScore.objects.create(student=self.amani, term=term or self.t1, subject_name=subject, exam_type=exam,
                                 score=Decimal(score), entered_by=self.admin, status=ScoreStatus.APPROVED)

    def test_unmarked_assessments_excluded_and_flagged(self):
        self._score("English", "quiz", 60)       # 12.0 /20
        self._score("English", "mid_term", 75)   # 22.5 /30
        self._score("Science", "quiz", 75)       # 15.0 /20, nothing else
        report = build_ptc_report(self.amani, self.year, self.window)
        eng = next(r for r in report["marks_rows"] if r["subject"] == "English")["cells"][0]
        self.assertEqual([p["value"] for p in eng["parts"]], [12.0, 22.5, None])
        self.assertTrue(eng["parts"][2]["missing"])
        self.assertEqual((eng["avg"], eng["rating"]), (69.0, "Aspiring"))
        maths = next(r for r in report["marks_rows"] if r["subject"] == "Mathematics")["cells"][0]
        self.assertIsNone(maths["avg"])  # no marks: not zero
        self.assertEqual(report["summary"][0]["avg"], 72.0)  # (69 + 75) / 2
        # Terms 2 and 3 have not started.
        self.assertFalse(report["terms"][1]["started"])
        self.assertFalse(report["marks_rows"][0]["cells"][1]["parts"][0]["missing"])

    def test_remark_only_subjects_show_remark_grade_not_marks(self):
        SubjectTermRemark.objects.create(student=self.amani, term=self.t1, subject_name="Music",
                                         remark="High", entered_by=self.st)
        report = build_ptc_report(self.amani, self.year, self.window)
        self.assertNotIn("Music", [r["subject"] for r in report["marks_rows"]])
        music = next(r for r in report["remark_rows"] if r["subject"] == "Music")
        self.assertEqual(music["cells"][0]["remark"], "High")
        self.assertEqual(report["check"]["remarks_done"], 1)
        self.assertEqual(report["check"]["missing_remarks"], ["Art"])

    def test_before_the_ptc_check(self):
        PTCSubjectComment.objects.create(student=self.amani, ptc_window=self.window, subject_name="English",
                                         comment_text="Good progress.")
        self._score("English", "end_of_term", 80)
        check = build_ptc_report(self.amani, self.year, self.window)["check"]
        self.assertEqual((check["comments_done"], check["marks_total"]), (1, 5))
        self.assertEqual(len(check["end_term_outstanding"]), 4)
        self.assertFalse(check["all_clear"])

    def test_incidents_only_when_present_and_discipline_shows_level_only(self):
        from discipline.models import DisciplineIncident, IncidentSeverity
        self.assertEqual(build_ptc_report(self.amani, self.year, self.window)["incidents"], [])
        DisciplineIncident.objects.create(student=self.amani, reported_by=self.ct, severity=IncidentSeverity.MEDIUM,
                                          summary="Private details", incident_date=self.today)
        rows = build_ptc_report(self.amani, self.year, self.window)["incidents"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["type"], rows[0]["level"], rows[0]["summary"]), ("Discipline", "Level 2", ""))
        self.assertEqual(rows[0]["term"], "Term 1")

    def test_attendance_per_term_excludes_unmarked(self):
        from attendance.models import AttendanceEntry, AttendanceStatus
        for i, st in enumerate([AttendanceStatus.PRESENT, AttendanceStatus.PRESENT, AttendanceStatus.LATE,
                                AttendanceStatus.ABSENT, AttendanceStatus.UNCONFIRMED]):
            AttendanceEntry.objects.create(student=self.amani, date=self.today - timedelta(days=i + 1),
                                           status=st, marked_by=self.ct)
        att = build_ptc_report(self.amani, self.year, self.window)["attendance"]
        self.assertEqual(att[0], {"present": 3, "absent": 1, "late": 1, "rate": 75.0})
        self.assertIsNone(att[1])

    def test_report_page_has_no_remark_box_reopening_date_or_signatures(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("ptc:ptc_screen", args=[self.amani.pk]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        body = body[body.index('<div class="ptc-report">'):]
        for gone in ("Signature", "Reopening", "Class teacher remark", "Progress Trend", "Enrichment"):
            self.assertNotIn(gone, body)
        for shown in ("Marks subjects", "Remark-only subjects", "Student attributes", "Attendance",
                      "Before the PTC meeting", "Term 1 · 2026/2027", "Term 3 · 2026/2027"):
            self.assertIn(shown, body)
        self.assertNotIn("Welfare and discipline incidents", body)


class RemovedPagesTests(TestCase):
    def test_compliance_and_enrichment_urls_are_gone(self):
        for name in ("ptc:compliance_dashboard", "ptc:enrichment_entry", "ptc:enrichment_config"):
            with self.assertRaises(NoReverseMatch):
                reverse(name)


class CurrentWindowTests(PTCFixture):
    def test_open_window_beats_later_ones(self):
        PTCWindow.objects.create(academic_year=self.year, term_slot=TermSlot.TERM_3,
                                 ptc_date=self.today + timedelta(days=200))
        self.assertEqual(get_current_ptc_window(self.year), self.window)


class ReviewGapTests(PTCFixture):
    def test_head_of_school_reads_comments_but_cannot_save(self):
        hos = User.objects.create_user(username="hos", email="hos@x.test", password="x",
                                       role=UserRole.HEAD_OF_SCHOOL, first_name="Head", last_name="School")
        _grant(hos)
        self.client.force_login(hos)
        page = self.client.get(reverse("ptc:comment_entry"), {"class": "Grade 8"})
        self.assertEqual(page.context["subjects"], MARKS)
        self.assertFalse(page.context["can_save"])
        self.assertContains(page, "Read only. Subject comments are written by each subject")
        resp = self.client.post(reverse("ptc:comment_entry"),
                                {"student_id": self.amani.pk, "comment_english": "Good"}, **AJAX)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PTCSubjectComment.objects.exists())

    def test_open_link_and_back_link_keep_ptc_and_class(self):
        later = PTCWindow.objects.create(academic_year=self.year, term_slot=TermSlot.TERM_2,
                                         ptc_date=self.today + timedelta(days=120))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ptc:student_picker"), {"class": "Grade 8", "ptc": later.pk})
        self.assertContains(page, f"?ptc={later.pk}&amp;class=Grade%208")
        report = self.client.get(reverse("ptc:ptc_screen", args=[self.amani.pk]), {"ptc": later.pk, "class": "Grade 8"})
        self.assertEqual(report.context["ptc_window"], later)
        self.assertEqual(report.context["report"]["check"]["term_label"], "Term 2")
        self.assertIn(f"ptc={later.pk}", report.context["back_url"])
        self.assertIn("class=Grade+8", report.context["back_url"])

    def test_log_is_paginated(self):
        PTCGenerationLog.objects.bulk_create([
            PTCGenerationLog(student=self.amani, class_teacher=self.admin, ptc_window=self.window, class_name="Grade 8")
            for _ in range(60)
        ])
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ptc:generation_log"))
        self.assertEqual(len(page.context["logs"]), 50)
        self.assertEqual(page.context["page_obj"].paginator.count, 60)
        self.assertEqual(len(self.client.get(reverse("ptc:generation_log"), {"page": 2}).context["logs"]), 10)


class QueryBudgetTests(PTCFixture):
    """Ceilings on queries per page, so repeated lookups don't creep back.
    About 17 of each count are the site layout (session, permissions, sidebar,
    task badges); the rest is the PTC page itself. Before the request cache a
    class teacher's Students page took 44 and the report 53."""
    BUDGETS = {
        "ptc:student_picker": 34,
        "ptc:comment_entry": 34,
        "ptc:attribute_entry": 34,
        "ptc:ptc_screen": 44,
    }

    def _count(self, user, name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(user)
        args = [self.amani.pk] if name == "ptc:ptc_screen" else []
        self.client.get(reverse(name, args=args))  # warm session / content types
        with CaptureQueriesContext(connection) as c:
            resp = self.client.get(reverse(name, args=args))
        self.assertEqual(resp.status_code, 200)
        return len(c)

    def test_pages_stay_within_budget(self):
        for user in (self.admin, self.ct, self.st):
            for name, budget in self.BUDGETS.items():
                if name == "ptc:ptc_screen" and user is self.st:
                    continue  # a subject teacher cannot open the report
                with self.subTest(user=user.username, page=name):
                    self.assertLessEqual(self._count(user, name), budget)
