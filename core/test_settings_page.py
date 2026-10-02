"""Settings page: every tab renders, and add/edit use modals and drawers."""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, Department, GradeClass, Term
from users.models import User, UserRole


class SettingsPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("sa", "sa@x.edu", "pw", role=UserRole.SUPER_ADMIN,
                                              is_staff=True, is_superuser=True)
        year = AcademicYear.objects.create(name="2026/2027", start_date=date(2026, 9, 1),
                                           end_date=date(2099, 7, 31), is_current=True)
        Term.objects.create(academic_year=year, name="Term 2", start_date=date(2027, 1, 6), end_date=date(2027, 4, 1))
        Term.objects.create(academic_year=year, name="Term 1", start_date=date(2026, 9, 1), end_date=date(2026, 12, 15))
        for name, dept, order in [("Grade 8", Department.LOWER_SECONDARY, 8), ("Grade 1", Department.PRIMARY, 1),
                                  ("Pre-K", Department.ECD, 0)]:
            GradeClass.objects.create(name=name, department=dept, sort_order=order)
        self.url = reverse("core:school_settings")
        self.client.force_login(self.admin)

    def get(self, tab):
        resp = self.client.get(self.url, {"tab": tab})
        self.assertEqual(resp.status_code, 200, tab)
        return resp

    def test_every_tab_renders(self):
        expected = {
            "system": "Save system settings",
            "messaging": "Save messaging settings",
            "classes": 'id="classModal"',
            "academic_year": 'id="termDrawer"',
            "media": 'id="mediaDrawer"',
            "induction_checklist": 'id="checklistModal"',
            "email_templates": 'id="editEmailTplModal"',
        }
        for tab, marker in expected.items():
            self.assertContains(self.get(tab), marker)

    def test_terms_listed_in_date_order_under_their_year(self):
        html = self.get("academic_year").content.decode()
        self.assertIn('id="yearModal"', html)
        self.assertLess(html.index(">Term 1<"), html.index(">Term 2<"))
        self.assertNotIn("settings-modal-overlay", html)  # old ad-hoc modals are gone

    def test_classes_in_school_order(self):
        names = [c.name for c in self.get("classes").context["classes"]]
        self.assertEqual(names, ["Pre-K", "Grade 1", "Grade 8"])

    def test_login_preview_matches_real_login_label(self):
        self.assertContains(self.get("dynamic_pages"), "Username")
        self.assertNotContains(self.get("dynamic_pages"), "you@school.com")
