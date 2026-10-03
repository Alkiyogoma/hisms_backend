"""
Editing a learner: a failed save must say which field is wrong, keep what was
typed, and explain plainly when the failure is not about a field.
"""
from datetime import date
from unittest import mock

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from students.models import Student, StudentGuardian
from users.models import User, UserRole


class StudentEditFormErrorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="registrar", email="registrar@hodari.edu", password="Pass123!",
            role=UserRole.SUPER_ADMIN,
        )
        self.student = Student.objects.create(
            admission_no="HCS-E-001", first_name="Jasiel", last_name="Kajegukaa",
            gender="male", date_of_birth=date(2017, 3, 1), class_name="Grade 4",
            stream_name="Blue", enrolment_date=date(2023, 1, 9),
        )
        self.url = reverse("students:edit", args=[self.student.pk])
        self.client.force_login(self.admin)

    def _page_fields(self, **overrides):
        """Exactly the fields the edit page submits."""
        data = {
            "first_name": "Jasiel", "last_name": "Kajeguka", "preferred_name": "",
            "date_of_birth": "2017-03-01", "gender": "male", "phone": "",
            "nationality": "", "religion": "", "blood_type": "",
            "allergies_medical": "", "status": self.student.status,
        }
        data.update(overrides)
        return data

    def _messages(self, resp):
        return [str(m) for m in get_messages(resp.wsgi_request)]

    def test_correcting_a_name_saves(self):
        resp = self.client.post(self.url, self._page_fields())
        self.assertRedirects(resp, reverse("students:detail", args=[self.student.pk]),
                             fetch_redirect_response=False)
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_name, "Kajeguka")
        # Fields not on the edit page are left untouched.
        self.assertEqual(self.student.class_name, "Grade 4")
        self.assertEqual(self.student.stream_name, "Blue")
        self.assertEqual(self.student.enrolment_date, date(2023, 1, 9))

    def test_edit_page_has_no_hidden_required_fields(self):
        resp = self.client.get(self.url)
        form = resp.context["form"]
        content = resp.content.decode()
        for name, f in form.fields.items():
            if f.required:
                self.assertIn(f'name="{name}"', content, f"required field {name} not on page")

    def test_field_error_is_named_marked_and_input_kept(self):
        resp = self.client.post(self.url, self._page_fields(
            last_name="Kajeguka", date_of_birth="", nationality="Tanzanian",
        ))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('id="form-error-summary"', content)
        self.assertIn('href="#id_date_of_birth"', content)
        self.assertIn('aria-invalid="true"', content)
        self.assertIn('id="id_date_of_birth_error"', content)
        self.assertIn("Date of Birth: This field is required.", content)
        # What the user typed is still there.
        self.assertIn('value="Tanzanian"', content)
        self.assertIn('value="Kajeguka"', content)
        msgs = self._messages(resp)
        self.assertTrue(any("Date of Birth" in m for m in msgs), msgs)
        self.assertFalse(any("errors below" in m for m in msgs), msgs)
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_name, "Kajegukaa")

    def test_non_field_failure_is_explained_plainly(self):
        with mock.patch("students.forms.StudentEditForm.save",
                        side_effect=ValidationError("Student ID (admission number) cannot be changed after creation.")):
            resp = self.client.post(self.url, self._page_fields(nationality="Tanzanian"))
        self.assertEqual(resp.status_code, 200)
        msgs = self._messages(resp)
        self.assertTrue(any(m.startswith("The learner's record could not be saved: Student ID") for m in msgs), msgs)
        content = resp.content.decode()
        self.assertIn('id="form-error-summary"', content)
        self.assertIn("cannot be changed after creation", content)
        self.assertNotIn('class="hf2-error"', content)
        self.assertIn('value="Tanzanian"', content)

    def test_learner_with_no_class_can_be_fixed_from_the_edit_page(self):
        from academics.models import GradeClass
        GradeClass.objects.create(name="Grade 4", department="PRIMARY")
        Student.objects.filter(pk=self.student.pk).update(class_name="")

        resp = self.client.get(self.url)
        self.assertIn('name="class_name"', resp.content.decode())

        resp = self.client.post(self.url, self._page_fields())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Class: This field is required.", resp.content.decode())

        resp = self.client.post(self.url, self._page_fields(class_name="Grade 4"))
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.class_name, "Grade 4")

    def test_audit_records_the_old_value(self):
        from audit.models import AuditLog
        self.client.post(self.url, self._page_fields())
        log = AuditLog.objects.filter(model_name="Student", action_type="UPDATE").latest("pk")
        self.assertEqual(log.before_snapshot, {"last_name": "Kajegukaa"})
        self.assertEqual(log.after_snapshot, {"last_name": "Kajeguka"})


class GuardianFormErrorTests(TestCase):
    def setUp(self):
        from students.models import ParentGuardian
        self.admin = User.objects.create_superuser(
            username="registrar", email="registrar@hodari.edu", password="Pass123!",
            role=UserRole.SUPER_ADMIN,
        )
        self.client.force_login(self.admin)
        self.guardian = ParentGuardian.objects.create(
            full_name="Mary Kajeguka", phone="+255767000001", email="mary@example.com",
            address="Arusha", secondary_phone="+255743000001", preferred_language="sw",
        )
        ParentGuardian.objects.create(full_name="John Kajeguka", phone="+255767000002")
        self.url = reverse("students:guardian_edit", args=[self.guardian.pk])

    def _post(self, **overrides):
        data = {
            "full_name": "Mary Kajeguka", "phone": "+255767000001", "secondary_phone": "+255743000001",
            "email": "mary@example.com", "address": "Arusha", "preferred_invoice_name": "",
            "preferred_language": "sw",
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_edit_page_shows_current_details(self):
        content = self.client.get(self.url).content.decode()
        self.assertIn("Edit Guardian", content)
        self.assertIn('value="mary@example.com"', content)
        self.assertIn(">Arusha</textarea>", content)
        self.assertIn('value="sw" selected', content)
        self.assertNotIn('name="pdpa_consent_version"', content)

    def test_missing_phone_is_marked_and_input_kept(self):
        resp = self._post(phone="", address="Moshi")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('id="id_phone_error"', content)
        self.assertIn("Primary Phone is required.", content)
        self.assertIn(">Moshi</textarea>", content)
        self.guardian.refresh_from_db()
        self.assertEqual(self.guardian.address, "Arusha")

    def test_duplicate_name_and_phone_does_not_crash(self):
        resp = self._post(full_name="John Kajeguka", phone="+255767000002")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already exists", resp.content.decode())

    def test_invalid_email_is_named(self):
        resp = self._post(email="not-an-email")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Email Address is not a valid email address.", resp.content.decode())

    def test_saving_keeps_unchanged_details(self):
        resp = self._post(full_name="Mary A. Kajeguka")
        self.assertEqual(resp.status_code, 302)
        self.guardian.refresh_from_db()
        self.assertEqual(self.guardian.full_name, "Mary A. Kajeguka")
        self.assertEqual(self.guardian.email, "mary@example.com")
        self.assertEqual(self.guardian.secondary_phone, "+255743000001")

    def test_create_without_consent_keeps_input(self):
        resp = self.client.post(reverse("students:guardian_create"), {
            "full_name": "Grace Mushi", "phone": "+255767000003", "address": "Dodoma",
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('value="Grace Mushi"', content)
        self.assertIn(">Dodoma</textarea>", content)
        self.assertIn("PDPA consent must be recorded", content)

    def test_link_flow_duplicate_guardian_does_not_crash(self):
        student = Student.objects.create(
            admission_no="HCS-E-002", first_name="Asha", last_name="Kajeguka",
            gender="female", date_of_birth=date(2018, 1, 1), class_name="Grade 3",
        )
        resp = self.client.post(reverse("students:guardian_link", args=[student.pk]), {
            "mode": "new", "full_name": "John Kajeguka", "phone": "+255767000002", "address": "Moshi",
            "pdpa_consent_given": "on", "pdpa_consent_method": "in_person", "pdpa_consent_version": "v1",
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("already exists", content)
        self.assertIn(">Moshi</textarea>", content)
        self.assertIn('value="new"', content)  # reopens the Create New tab
        self.assertIn("switchMode('new')", content)


class GuardianLinkDataTests(TestCase):
    """Relationship and primary contact are captured on every path and shown."""

    def setUp(self):
        from django.utils import timezone
        from students.models import ParentGuardian
        self.admin = User.objects.create_superuser(
            username="registrar", email="registrar@hodari.edu", password="Pass123!",
            role=UserRole.SUPER_ADMIN,
        )
        self.client.force_login(self.admin)
        self.student = Student.objects.create(
            admission_no="HCS-L-001", first_name="Jasiel", last_name="Kajeguka",
            gender="male", date_of_birth=date(2017, 3, 1), class_name="Grade 4",
        )
        consent = dict(pdpa_consent_given=True, pdpa_consent_method="in_person",
                       pdpa_consent_version="v1", pdpa_consented_at=timezone.now())
        self.mother = ParentGuardian.objects.create(full_name="Mary Kajeguka", phone="+255767000001",
                                                    preferred_language="sw", **consent)
        self.father = ParentGuardian.objects.create(full_name="John Kajeguka", phone="+255767000002", **consent)
        StudentGuardian.objects.create(student=self.student, guardian=self.mother,
                                       relationship="mother", is_primary=True)

    def _link(self, guardian):
        return StudentGuardian.objects.get(student=self.student, guardian=guardian)

    def test_guardian_page_has_edit_button_and_language(self):
        content = self.client.get(reverse("students:guardian_detail", args=[self.mother.pk])).content.decode()
        self.assertIn(reverse("students:guardian_edit", args=[self.mother.pk]), content)
        self.assertIn("Edit Guardian", content)
        self.assertIn("Preferred Language", content)
        self.assertIn("Swahili", content)

    def test_edit_button_hidden_without_permission(self):
        teacher = User.objects.create_user(username="t1", email="t1@hodari.edu", password="Pass123!",
                                           role=UserRole.TEACHER)
        from django.contrib.auth.models import Permission
        teacher.user_permissions.add(Permission.objects.get(codename="view_parentguardian"))
        self.client.force_login(teacher)
        resp = self.client.get(reverse("students:guardian_detail", args=[self.mother.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(reverse("students:guardian_edit", args=[self.mother.pk]), resp.content.decode())

    def test_create_with_student_captures_relationship_and_primary(self):
        resp = self.client.post(reverse("students:guardian_create"), {
            "full_name": "Grace Mushi", "phone": "+255767000003",
            "student_id": self.student.pk, "relationship": "aunt", "is_primary": "on",
            "pdpa_consent_given": "on", "pdpa_consent_method": "digital", "pdpa_consent_version": "v1",
        })
        self.assertEqual(resp.status_code, 302)
        from students.models import ParentGuardian
        grace = ParentGuardian.objects.get(full_name="Grace Mushi")
        link = self._link(grace)
        self.assertEqual(link.relationship, "aunt")
        self.assertTrue(link.is_primary)
        self.assertFalse(self._link(self.mother).is_primary)

    def test_create_failure_keeps_selected_student(self):
        resp = self.client.post(reverse("students:guardian_create"), {
            "full_name": "Grace Mushi", "phone": "", "student_id": self.student.pk, "relationship": "aunt",
        })
        content = resp.content.decode()
        self.assertIn(f'value="{self.student.pk}"', content)
        self.assertIn("Jasiel Kajeguka — HCS-L-001", content)
        self.assertIn('value="aunt" selected', content)

    def test_edit_page_updates_relationship_and_primary(self):
        StudentGuardian.objects.create(student=self.student, guardian=self.father, relationship="guardian")
        link = self._link(self.father)
        content = self.client.get(reverse("students:guardian_edit", args=[self.father.pk])).content.decode()
        self.assertIn(f'name="relationship_{link.pk}"', content)
        resp = self.client.post(reverse("students:guardian_edit", args=[self.father.pk]), {
            "full_name": "John Kajeguka", "phone": "+255767000002", "preferred_language": "en",
            f"relationship_{link.pk}": "father", f"primary_{link.pk}": "on",
        })
        self.assertEqual(resp.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.relationship, "father")
        self.assertTrue(link.is_primary)
        self.assertFalse(self._link(self.mother).is_primary)

    def test_link_existing_without_selection_says_so(self):
        resp = self.client.post(reverse("students:guardian_link", args=[self.student.pk]), {
            "mode": "existing", "guardian_id": "", "relationship": "father",
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("search for and select a guardian", content)
        self.assertNotIn("Full Name is required", content)

    def test_link_existing_as_primary_moves_primary(self):
        resp = self.client.post(reverse("students:guardian_link", args=[self.student.pk]), {
            "mode": "existing", "guardian_id": self.father.pk, "relationship": "father", "is_primary": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self._link(self.father).is_primary)
        self.assertEqual(self._link(self.father).relationship, "father")
        self.assertFalse(self._link(self.mother).is_primary)

    def test_relinking_updates_relationship(self):
        resp = self.client.post(reverse("students:guardian_link", args=[self.student.pk]), {
            "mode": "existing", "guardian_id": self.mother.pk, "relationship": "step_mother", "is_primary": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._link(self.mother).relationship, "step_mother")

    def test_link_new_guardian_with_language_and_primary(self):
        resp = self.client.post(reverse("students:guardian_link", args=[self.student.pk]), {
            "mode": "new", "full_name": "Grace Mushi", "phone": "+255767000003", "preferred_language": "sw",
            "relationship": "aunt", "is_primary": "on",
            "pdpa_consent_given": "on", "pdpa_consent_method": "in_person", "pdpa_consent_version": "v1",
        })
        self.assertEqual(resp.status_code, 302)
        from students.models import ParentGuardian
        grace = ParentGuardian.objects.get(full_name="Grace Mushi")
        self.assertEqual(grace.preferred_language, "sw")
        self.assertTrue(self._link(grace).is_primary)
        self.assertFalse(self._link(self.mother).is_primary)

    def test_over_length_value_is_a_field_error(self):
        resp = self.client.post(reverse("students:guardian_edit", args=[self.mother.pk]), {
            "full_name": "Mary Kajeguka", "phone": "+255767000001", "address": "x" * 300,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Physical Address must be at most 255 characters", resp.content.decode())

    def test_learner_edit_sidebar_links_to_add_guardian(self):
        content = self.client.get(reverse("students:edit", args=[self.student.pk])).content.decode()
        self.assertIn(reverse("students:guardian_link", args=[self.student.pk]), content)
