"""
ECD progress reports follow the per-term setup: the setup page edits the lines
(full CRUD, flagged T1/T2/T3), the entry page rates them (4/3/2/1 or N/A for
Academic Progress, the layout's letters for General Traits, marks out of 100),
and the printed report shows them laid out like the school's Word template.
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

import academics.tests  # noqa: F401  (applies the SQLite teardown shim)
from academics import ecd_report
from academics.ecd_utils import build_ecd_report_context
from academics.models import (
    AcademicYear, Department, ECDEvaluation, ECDItem, ECDReportLayout, ECDSection, GradeClass,
    ReportCard, ReportCardStatus, Term,
)
from academics.tests import assign_role_group
from students.models import Student, StudentStatus
from users.models import User, UserRole

COMMENT = "Baraka is a cheerful learner who joins in every activity and is growing in confidence."


class ECDFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.hos = User.objects.create_user(username="hos", email="hos@example.test", password="x",
                                           role=UserRole.HEAD_OF_SCHOOL)
        assign_role_group(cls.hos)
        cls.admin = User.objects.create_user(username="sa", email="sa@example.test", password="x",
                                             role=UserRole.SUPER_ADMIN, is_superuser=True, is_staff=True)
        cls.year = AcademicYear.objects.create(name="2026/2027", is_current=True)
        cls.term = Term.objects.create(
            academic_year=cls.year, name="Second term", term_number=2,
            start_date=today - timedelta(days=20), end_date=today + timedelta(days=60),
        )
        GradeClass.objects.create(name="Pre-School", department=Department.ECD)
        GradeClass.objects.create(name="ABC", department=Department.ECD)
        cls.learner = Student.objects.create(
            admission_no="E-100", first_name="Baraka", last_name="Kimaro",
            class_name="Pre-School", academic_year=cls.year, status=StudentStatus.ACTIVE,
        )

    def structure(self, class_type="pre_school"):
        return ecd_report.ReportStructure(class_type, 2)

    def api(self, name, student, **params):
        params.setdefault("class_name", student.class_name)
        params.setdefault("term", self.term.pk)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{reverse(f'academics:{name}', args=[student.pk])}?{query}"

    def save(self, student, body):
        return self.client.post(self.api("api_ecd_evaluation", student), json.dumps(body),
                                content_type="application/json")

    def rate_everything(self, structure, rating_for=lambda item: None):
        values = {}
        for item in structure.items:
            if item.section.style == ECDSection.Style.MARKS:
                values[str(item.pk)] = {"score": 80}
            elif item.section.part == ECDSection.Part.ACADEMIC:
                values[str(item.pk)] = {"rating": rating_for(item) or "3"}
            else:
                values[str(item.pk)] = {"rating": "G"}
        return values


class ECDEntryTests(ECDFixture):
    def setUp(self):
        self.client.force_login(self.hos)

    def test_page_renders_the_terms_lines(self):
        page = self.client.get(reverse("academics:ecd_assessment"), {"term": self.term.pk, "class_name": "Pre-School"})
        self.assertEqual(page.status_code, 200)
        structure = page.context["structure"]
        self.assertEqual(structure.term_number, 2)
        self.assertContains(page, "Number Sequence 1-100")     # Term 2 wording
        self.assertNotContains(page, "Number Sequence 1-30")   # Term 1 only
        self.assertContains(page, 'data-value="NA"')

    def test_save_and_read_back(self):
        s = self.structure()
        numeracy = next(i for i in s.items if i.label == "Number Value")
        bible = next(i for i in s.items if i.section.style == ECDSection.Style.VERSE)
        trait = next(i for i in s.items if i.section.part == ECDSection.Part.TRAITS)
        resp = self.save(self.learner, {"values": {
            str(numeracy.pk): {"rating": "4"},
            str(bible.pk): {"rating": "3", "note": "Genesis 1:1-5"},
            str(trait.pk): {"rating": "E"},
        }, "teacher_comments": COMMENT})
        self.assertEqual(resp.status_code, 200, resp.content)
        d = self.client.get(self.api("api_ecd_evaluation", self.learner)).json()
        self.assertEqual(d["values"][str(numeracy.pk)]["rating"], "4")
        self.assertEqual(d["values"][str(bible.pk)]["note"], "Genesis 1:1-5")
        self.assertEqual(d["average"], 3.5)
        self.assertEqual(d["remarks"], "Outstanding")
        self.assertEqual(d["rated"], 3)
        ev = ECDEvaluation.objects.get(item=numeracy)
        self.assertEqual((ev.domain, ev.section_title), ("Number Value", "NUMERACY"))

    def test_wrong_scale_is_rejected_and_nothing_saved(self):
        s = self.structure()
        academic = next(i for i in s.items if i.section.part == ECDSection.Part.ACADEMIC)
        trait = next(i for i in s.items if i.section.part == ECDSection.Part.TRAITS)
        resp = self.save(self.learner, {"values": {str(academic.pk): {"rating": "4"}, str(trait.pk): {"rating": "3"}}})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(ECDEvaluation.objects.exists())

    def test_lines_from_another_term_are_rejected(self):
        term1_only = ECDItem.objects.get(section__class_type="pre_school", label="Number Sequence 1-30")
        resp = self.save(self.learner, {"values": {str(term1_only.pk): {"rating": "4"}}})
        self.assertEqual(resp.status_code, 400)

    def test_na_counts_as_rated_but_not_in_the_average(self):
        s = self.structure()
        values = self.rate_everything(s, lambda item: "NA" if item.label == "Basic subtraction" else "2")
        self.save(self.learner, {"values": values})
        d = self.client.get(self.api("api_ecd_evaluation", self.learner)).json()
        self.assertEqual(d["rated"], d["total"])
        self.assertEqual(d["average"], 2.0)

    def test_clearing_a_line_removes_it(self):
        item = self.structure().items[0]
        self.save(self.learner, {"values": {str(item.pk): {"rating": "4"}}})
        self.save(self.learner, {"values": {str(item.pk): {"rating": ""}}})
        self.assertFalse(ECDEvaluation.objects.filter(item=item).exists())

    def test_submit_needs_every_line_and_a_comment(self):
        submit = self.api("api_ecd_submit", self.learner)
        self.save(self.learner, {"values": {}, "teacher_comments": COMMENT})
        resp = self.client.post(submit)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("still to rate", resp.json()["error"])
        self.save(self.learner, {"values": self.rate_everything(self.structure())})
        self.assertEqual(self.client.post(submit).status_code, 200)
        report = ReportCard.objects.get(student=self.learner, term=self.term)
        self.assertEqual(report.status, ReportCardStatus.PENDING_SIGN_OFF)
        # Submitted reports are locked.
        locked = self.save(self.learner, {"values": {}, "teacher_comments": COMMENT + "!"})
        self.assertEqual(locked.status_code, 403)

    def test_abc_term_3_marks(self):
        abc = Student.objects.create(admission_no="E-200", first_name="Neema", last_name="Joseph",
                                     class_name="ABC", academic_year=self.year, status=StudentStatus.ACTIVE)
        self.term.term_number = 3
        self.term.save()
        marks = ECDItem.objects.get(section__class_type="abc", section__style="marks", label="English")
        self.assertEqual(self.save(abc, {"values": {str(marks.pk): {"score": 120}}}).status_code, 400)
        self.assertEqual(self.save(abc, {"values": {str(marks.pk): {"score": 90}}}).status_code, 200)
        self.assertEqual(ECDEvaluation.objects.get(item=marks).score, 90)

    def test_old_url_redirects_to_the_entry_page(self):
        resp = self.client.get(reverse("academics:ecd_evaluations_entry"), {"class_name": "KG"})
        self.assertRedirects(resp, reverse("academics:ecd_assessment") + "?class_name=KG", fetch_redirect_response=False)


class ECDPrintTests(ECDFixture):
    def setUp(self):
        self.client.force_login(self.hos)
        self.report = ReportCard.objects.create(
            student=self.learner, term=self.term, is_ecd_report=True, ecd_template_type="pre_school",
            generated_by=self.hos, teacher_comments=COMMENT,
        )

    def test_print_shows_the_template_layout_and_ratings(self):
        s = self.structure()
        ecd_report.save_values(self.report, s, self.rate_everything(s, lambda item: "4"))
        page = self.client.get(reverse("academics:report_preview", args=[self.report.pk])).content.decode()
        self.assertIn("PRESCHOOL PROGRESS REPORT", page)
        self.assertIn("4-Outstanding", page)
        self.assertIn("E-Excellence", page)
        self.assertIn("Number Sequence 1-100", page)
        self.assertIn("Overall Academic Progress", page)
        self.assertIn("Days Absent", page)
        self.assertIn(COMMENT, page)

    def test_print_keeps_the_wording_as_rated(self):
        s = self.structure()
        item = s.items[0]
        ecd_report.save_values(self.report, s, {item.pk: {"rating": "4"}})
        ECDItem.objects.filter(pk=item.pk).update(label="Renamed later")
        ctx = build_ecd_report_context(self.report)
        labels = [line["label"] for part in ctx["parts"] for sec in part["sections"] for line in sec.lines]
        self.assertIn(item.label, labels)
        self.assertNotIn("Renamed later", labels)

    def test_reports_rated_before_the_templates_print_as_recorded(self):
        ECDEvaluation.objects.create(report_card=self.report, domain="Recognises numbers 1-10", rating="E")
        ctx = build_ecd_report_context(self.report)
        self.assertTrue(ctx["legacy"])
        self.assertEqual(ctx["legacy_rows"], [("Recognises numbers 1-10", "E")])

    def test_class_print_renders_every_learner(self):
        other = Student.objects.create(admission_no="E-101", first_name="Zawadi", last_name="Moshi",
                                       class_name="Pre-School", academic_year=self.year, status=StudentStatus.ACTIVE)
        ReportCard.objects.create(student=other, term=self.term, is_ecd_report=True,
                                  ecd_template_type="pre_school", generated_by=self.hos)
        page = self.client.get(reverse("academics:report_preview_class", args=[self.report.pk])).content.decode()
        self.assertEqual(page.count('class="report-container ecd-sheet'), 2)
        self.assertIn("Zawadi Moshi", page)
        self.assertEqual(page.count("<b>Days Absent:</b> 0"), 2)

    def test_class_print_matches_single_reports(self):
        s = self.structure()
        ecd_report.save_values(self.report, s, self.rate_everything(s, lambda item: "4"))
        single = self.client.get(reverse("academics:report_preview", args=[self.report.pk])).content.decode()
        whole = self.client.get(reverse("academics:report_preview_class", args=[self.report.pk])).content.decode()
        body = lambda html: html[html.index('<div class="report-container'):].split("</body>")[0].strip()
        self.assertEqual(body(single), body(whole))


class ECDSetupTests(ECDFixture):
    URL = "academics:ecd_report_setup"

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, **data):
        data.setdefault("class", "pre_school")
        data.setdefault("term", "2")
        return self.client.post(reverse(self.URL), data, HTTP_HX_REQUEST="true")

    def test_page_lists_classes_and_terms(self):
        page = self.client.get(reverse(self.URL), {"class": "kindergarten", "term": "3"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Number counting 0-20")
        self.assertEqual([t["n"] for t in page.context["terms"]], [1, 2, 3])

    def test_section_and_item_crud(self):
        self.post(action="add_section", part="traits", title="MUSIC", column="right", style="table")
        section = ECDSection.objects.get(class_type="pre_school", title="MUSIC")
        self.post(action="add_item", section=section.pk, label="Sings along\nKeeps the beat")
        self.assertEqual(list(section.items.values_list("label", "in_term_1", "in_term_2")),
                         [("Sings along", False, True), ("Keeps the beat", False, True)])
        item = section.items.get(label="Sings along")
        self.post(action="edit_item", item=item.pk, label="Sings in tune", terms=["1", "2", "3"])
        item.refresh_from_db()
        self.assertEqual((item.label, item.terms), ("Sings in tune", [1, 2, 3]))
        self.post(action="toggle_item_term", item=item.pk, n="3")
        item.refresh_from_db()
        self.assertFalse(item.in_term_3)
        self.post(action="move_item", item=item.pk, dir="down")
        self.assertEqual(list(section.items.order_by("sort_order").values_list("label", flat=True)),
                         ["Keeps the beat", "Sings in tune"])
        self.post(action="edit_section", section=section.pk, title="MUSIC & MOVEMENT", column="left", style="table")
        section.refresh_from_db()
        self.assertEqual((section.title, section.column), ("MUSIC & MOVEMENT", "left"))
        self.post(action="delete_item", item=item.pk)
        self.post(action="delete_section", section=section.pk)
        self.assertFalse(ECDSection.objects.filter(pk=section.pk).exists())

    def test_rated_lines_cannot_be_deleted(self):
        item = self.structure().items[0]
        report = ReportCard.objects.create(student=self.learner, term=self.term, is_ecd_report=True,
                                           ecd_template_type="pre_school", generated_by=self.hos)
        ecd_report.save_values(report, self.structure(), {item.pk: {"rating": "4"}})
        resp = self.post(action="delete_item", item=item.pk)
        self.assertTrue(ECDItem.objects.filter(pk=item.pk).exists())
        self.assertIn("already rated", json.loads(resp["HX-Trigger"])["showToast"]["message"])

    def test_layout_preview_shows_the_blank_report(self):
        page = self.client.get(reverse("academics:ecd_report_setup_preview"), {"class": "abc", "term": "3"})
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("ABC PROGRESS REPORT", html)
        self.assertIn("Pre Grade 1 Assessment", html)
        self.assertIn("Layout preview", html)
        self.assertNotIn("Animal Science", html)  # Terms 1-2 only

    def test_layout_settings_and_copy_term(self):
        self.post(action="save_layout", title="PRESCHOOL REPORT", column_label="Score", accent="blue",
                  academic_4="Excellent", academic_3="Good", academic_2="Fair", academic_1="Not yet",
                  traits_code_0="E", traits_label_0="Excellent", traits_code_1="G", traits_label_1="Good",
                  traits_code_2="M", traits_label_2="Making Progress", traits_code_3="N", traits_label_3="Not yet")
        layout = ECDReportLayout.objects.get(class_type="pre_school", term_number=2)
        self.assertEqual((layout.title, layout.column_label, layout.accent), ("PRESCHOOL REPORT", "Score", "blue"))
        self.assertIn(["M", "Making Progress"], layout.traits_key)
        self.post(action="copy_term", term="3", **{"from": "2"})
        t3 = ECDReportLayout.objects.get(class_type="pre_school", term_number=3)
        self.assertEqual(t3.title, "PRESCHOOL REPORT")
        self.assertTrue(ECDItem.objects.get(section__class_type="pre_school", label="Number Tracing").in_term_3)

    def test_duplicate_trait_letters_are_refused(self):
        resp = self.post(action="save_layout", title="X", traits_code_0="E", traits_code_1="E")
        self.assertEqual(json.loads(resp["HX-Trigger"])["showToast"]["level"], "error")

    def test_teachers_cannot_edit(self):
        teacher = User.objects.create_user(username="t", email="t@example.test", password="x", role=UserRole.TEACHER)
        assign_role_group(teacher)
        self.client.force_login(teacher)
        self.assertIn(self.client.get(reverse(self.URL)).status_code, (302, 403))
        self.assertIn(self.post(action="add_section", part="academic", title="X").status_code, (302, 403))
        self.assertFalse(ECDSection.objects.filter(title="X").exists())
