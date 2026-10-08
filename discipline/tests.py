"""The discipline incident record: one scale, one reference, consistent contact
fields, previous incidents from the record, and a clean printed sheet."""
from datetime import date, timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

import academics.tests  # noqa: F401  (applies the shared fixture-teardown shim)
from academics.models import Department, GradeClass
from academics.tests import assign_role_group
from students.models import Student
from users.models import User, UserRole

from .consistency import notes_conflicts
from .models import DisciplineIncident, IncidentAmendment, IncidentSeverity, IncidentStatus

TODAY = date.today()
MEETING_NOTE = (
    "A meeting with Dedrick's mother is arranged for the morning of Friday, "
    "at which this and related concerns will be discussed."
)


class NotesConflictTests(TestCase):
    def test_contact_and_meeting_described(self):
        errors = notes_conflicts([MEETING_NOTE], parent_contacted=False, follow_up_arranged=False)
        self.assertEqual(len(errors), 2)
        self.assertEqual(notes_conflicts([MEETING_NOTE], True, True), [])

    def test_plans_and_failed_attempts_are_not_contact(self):
        for text in ("Parents will be called tomorrow.", "Tried to call the mother, no answer.",
                     "No follow-up needed.", "Verbal warning given, student apologised."):
            self.assertEqual(notes_conflicts([text], False, False), [], text)

    def test_meeting_in_review_note_needs_follow_up(self):
        errors = notes_conflicts(["Will be resolved after the meeting set for 9 October"], True, False)
        self.assertEqual(len(errors), 1)
        self.assertIn("Follow-up", errors[0])


class IncidentBase(TestCase):
    def setUp(self):
        GradeClass.objects.create(name="Grade 3", department=Department.PRIMARY)
        self.student = Student.objects.create(
            admission_no="ADM-150", first_name="Dedrick", last_name="Rwamugila",
            class_name="Grade 3",
        )
        self.hos = User.objects.create_user("hos", "hos@x.edu", "pw", role=UserRole.HEAD_OF_SCHOOL,
                                            first_name="Julieth", last_name="Mugishagwe")
        assign_role_group(self.hos)
        self.hod = User.objects.create_user("hod", "hod@x.edu", "pw", role=UserRole.PRIMARY_HOD,
                                            first_name="Esther", last_name="Agango")
        assign_role_group(self.hod)

    def incident(self, **kw):
        fields = dict(student=self.student, reported_by=self.hos, severity=IncidentSeverity.MEDIUM,
                      summary="Pushed a Grade 2 learner.", incident_date=TODAY - timedelta(days=1),
                      incident_level_2=["Lying to staff"])
        fields.update(kw)
        return DisciplineIncident.objects.create(**fields)

    def submit_data(self, **kw):
        data = {
            "student": self.student.pk, "incident_date": (TODAY - timedelta(days=1)).isoformat(),
            "summary": "Pushed a Grade 2 learner.", "level_2": ["Lying to staff"],
            "parent_contacted": "no", "follow_up": "none",
        }
        data.update(kw)
        return data


class SubmitTests(IncidentBase):
    def test_form_shows_guidance_and_single_scale(self):
        self.client.force_login(self.hod)
        resp = self.client.get(reverse("discipline:submit"))
        self.assertContains(resp, "Do not name other learners.")
        self.assertContains(resp, "Level 2 — Moderate")
        self.assertNotContains(resp, 'name="previous_incidents"')
        self.assertNotContains(resp, 'name="severity"')

    def test_severity_comes_from_highest_level(self):
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("discipline:submit"),
                                self.submit_data(level_3=["Theft"], severity="low"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DisciplineIncident.objects.get().severity, IncidentSeverity.HIGH)

    def test_notes_describing_contact_block_no(self):
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(action_taken=MEETING_NOTE))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DisciplineIncident.objects.exists())
        self.assertContains(resp, "Parent contacted is No")
        # What was typed is still there.
        self.assertContains(resp, "Pushed a Grade 2 learner.")

    def test_contact_and_follow_up_saved_when_they_agree(self):
        self.client.force_login(self.hod)
        contact_day = TODAY - timedelta(days=1)
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(
            action_taken=MEETING_NOTE, parent_contacted="yes",
            parent_contact_date=contact_day.isoformat(), parent_contact_method="telephone",
            parent_contact_person="Mother", follow_up="meeting", follow_up_date=(TODAY + timedelta(days=1)).isoformat(), follow_up_time="08:30",
        ))
        self.assertEqual(resp.status_code, 302)
        inc = DisciplineIncident.objects.get()
        self.assertTrue(inc.parent_contacted)
        self.assertEqual(inc.parent_contact_date, contact_day)
        self.assertTrue(inc.follow_up_required)
        self.assertEqual(inc.follow_up_kind, "meeting")

    def test_contact_needs_a_date_and_method(self):
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(parent_contacted="yes"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Enter the date the parent was contacted.")
        self.assertContains(resp, "Say how the parent was contacted.")
        self.assertContains(resp, "Say which parent or guardian was contacted.")

    def test_follow_up_needs_what_it_is_but_date_only_where_known(self):
        self.client.force_login(self.hod)
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(follow_up="other"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Say what the follow-up is.")
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(
            follow_up="other", follow_up_details="Class teacher to check in weekly"))
        self.assertEqual(resp.status_code, 302)
        inc = DisciplineIncident.objects.get()
        self.assertIsNone(inc.follow_up_date)
        self.assertEqual(inc.follow_up_details, "Class teacher to check in weekly")

    def test_previous_incidents_offered_from_record_not_ticked(self):
        self.incident(incident_date=date(2026, 9, 22))
        self.incident(incident_date=date(2026, 9, 1), status=IncidentStatus.DISMISSED)
        self.client.force_login(self.hod)
        resp = self.client.get(reverse("discipline:submit"))
        self.assertContains(resp, 'data-previous="2026-09-22"')
        self.assertContains(resp, "Previous incidents on record")
        self.assertNotContains(resp, 'name="previous_incidents"')

    def test_shared_account_cannot_be_the_reporter(self):
        admin = User.objects.create_superuser("admin", "admin@x.edu", "pw", role=UserRole.SUPER_ADMIN,
                                              first_name="System", last_name="Admin")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("discipline:submit")), "shared system account")
        resp = self.client.post(reverse("discipline:submit"), self.submit_data())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DisciplineIncident.objects.exists())

    def test_teacher_cannot_record_level_4(self):
        teacher = User.objects.create_user("t", "t@x.edu", "pw", role=UserRole.TEACHER)
        teacher.user_permissions.add(*Permission.objects.filter(
            codename__in=["add_disciplineincident", "view_disciplineincident"]))
        self.client.force_login(teacher)
        resp = self.client.post(reverse("discipline:submit"), self.submit_data(level_4=["Possess weapons"]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DisciplineIncident.objects.exists())


class DetailTests(IncidentBase):
    def page(self, incident, user=None):
        self.client.force_login(user or self.hos)
        return self.client.get(reverse("discipline:detail", args=[incident.pk]))

    def sheet(self, resp):
        html = resp.content.decode()
        return html[html.index('<div class="ds-sheet">'):html.index("Screen only: review notes")]

    def test_one_scale_one_reference(self):
        sheet = self.sheet(self.page(self.incident()))
        self.assertIn("Level 2 — Moderate", sheet)
        self.assertNotIn(">Medium<", sheet)
        self.assertIn("DISC-", sheet)
        self.assertNotIn("Incident #", sheet)

    def test_date_of_incident_filled_everywhere(self):
        inc = self.incident(incident_date=date(2026, 10, 7))
        sheet = self.sheet(self.page(inc))
        self.assertEqual(sheet.count("7 October 2026"), 2)

    def test_previous_incidents_counted_from_record(self):
        self.incident(incident_date=TODAY - timedelta(days=20))
        self.incident(incident_date=TODAY - timedelta(days=10))
        self.incident(incident_date=TODAY - timedelta(days=5), status=IncidentStatus.DISMISSED)
        inc = self.incident()
        resp = self.page(inc)
        self.assertEqual(resp.context["previous_count"], 2)
        self.assertEqual(resp.context["previous_latest"], TODAY - timedelta(days=10))
        self.assertIn("2 previous", self.sheet(resp))

    def test_single_parent_signature_and_no_controls_in_sheet(self):
        sheet = self.sheet(self.page(self.incident()))
        self.assertEqual(sheet.count("Parent / guardian name"), 1)
        self.assertIn("Relationship to learner", sheet)
        self.assertNotIn("<form", sheet)
        self.assertNotIn("<textarea", sheet)
        self.assertNotIn("Digitally Confirm", self.page(self.incident()).content.decode())

    def test_what_happens_next(self):
        sheet = self.sheet(self.page(self.incident()))
        self.assertIn("Return the signed copy to the class teacher.", sheet)
        self.assertIn("It does not mean you agree with it.", sheet)

    def test_reviewer_role_from_user_record(self):
        inc = self.incident(reported_by=self.hod, reviewed_by=self.hos, reviewed_at=self._now())
        sheet = self.sheet(self.page(inc))
        self.assertIn("Reviewed by — Julieth Mugishagwe, Head of School", sheet)
        self.assertIn("Reported by — Esther Agango, Head of Primary", sheet)
        self.assertNotIn("Head of Department", sheet)

    def test_same_reporter_and_reviewer_stated_once(self):
        inc = self.incident(reviewed_by=self.hos, reviewed_at=self._now())
        sheet = self.sheet(self.page(inc))
        self.assertIn("Reported and reviewed by — Julieth Mugishagwe, Head of School", sheet)
        self.assertNotIn("Reviewed by —", sheet)

    def test_contact_detail_printed(self):
        inc = self.incident(parent_contacted=True, parent_contact_date=TODAY, parent_contact_method="telephone",
                            parent_contact_person="Mother", follow_up_required=True, follow_up_kind="meeting",
                            follow_up_details="With the class teacher")
        sheet = self.sheet(self.page(inc))
        self.assertIn("by telephone", sheet)
        self.assertIn("With: Mother", sheet)
        self.assertIn("Meeting arranged</b> — date to be confirmed", sheet)
        self.assertIn("With the class teacher", sheet)

    def test_reporter_named_with_role(self):
        sheet = self.sheet(self.page(self.incident(reported_by=self.hod)))
        self.assertIn("Esther Agango, Head of Primary", sheet)

    def test_review_queue_tab_not_hod_specific(self):
        resp = self.page(self.incident())
        self.assertContains(resp, "Review Queue")
        self.assertNotContains(resp, "HOD Queue")

    def test_contradicting_record_warned_on_screen(self):
        resp = self.page(self.incident(action_taken=MEETING_NOTE))
        self.assertContains(resp, "This record contradicts itself")

    @staticmethod
    def _now():
        from django.utils import timezone
        return timezone.now()


class ReviewTests(IncidentBase):
    def review(self, inc, **data):
        self.client.force_login(self.hos)
        return self.client.post(reverse("discipline:review", args=[inc.pk]), data)

    def test_review_note_describing_meeting_needs_follow_up(self):
        inc = self.incident()
        resp = self.review(inc, status="under_investigation", parent_contacted="no", follow_up="none",
                           hod_notes="Will be resolved after the meeting set for 9 October")
        self.assertEqual(resp.status_code, 200)
        inc.refresh_from_db()
        self.assertEqual(inc.status, IncidentStatus.PENDING_REVIEW)
        self.assertContains(resp, "Follow-up is Not required")

    def test_review_updates_contact_with_notes(self):
        inc = self.incident()
        resp = self.review(
            inc, status="under_investigation", hod_notes="Will be resolved after the meeting set for 9 October",
            parent_contacted="yes", parent_contact_date=TODAY.isoformat(), parent_contact_method="telephone",
            parent_contact_person="Mother", follow_up="meeting", follow_up_date=(TODAY + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(resp.status_code, 302)
        inc.refresh_from_db()
        self.assertEqual(inc.status, IncidentStatus.UNDER_INVESTIGATION)
        self.assertTrue(inc.parent_contacted and inc.follow_up_required)
        self.assertEqual(inc.reviewed_by, self.hos)

    def test_closed_incident_comment_and_contact(self):
        inc = self.incident(status=IncidentStatus.RESOLVED, hod_notes="Closed.")
        resp = self.review(inc, comment="Spoke to his father at pick-up.", parent_contacted="yes",
                           parent_contact_date=TODAY.isoformat(), parent_contact_method="in_person",
                           parent_contact_person="Father", follow_up="none")
        self.assertEqual(resp.status_code, 302)
        inc.refresh_from_db()
        self.assertIn("Spoke to his father", inc.hod_notes)
        self.assertTrue(inc.parent_contacted)
        self.assertEqual(inc.status, IncidentStatus.RESOLVED)


class AmendTests(IncidentBase):
    def amend_data(self, inc, **kw):
        data = {
            "student": inc.student_id, "incident_date": inc.incident_date.isoformat(),
            "time_of_incident": "", "location": inc.location, "summary": inc.summary,
            "action_taken": inc.action_taken, "incident_level_2": inc.incident_level_2,
            "reason": "Wrong date selected when the form was submitted.",
        }
        data.update(kw)
        return data

    def amend(self, inc, user=None, **kw):
        self.client.force_login(user or self.hos)
        return self.client.post(reverse("discipline:amend", args=[inc.pk]), self.amend_data(inc, **kw))

    def test_head_of_school_amends_with_reason_and_history_is_kept(self):
        inc = self.incident(incident_date=date(2026, 10, 8), status=IncidentStatus.RESOLVED,
                            reviewed_by=self.hos)
        resp = self.amend(inc, incident_date="2026-10-07")
        self.assertEqual(resp.status_code, 302)
        inc.refresh_from_db()
        self.assertEqual(inc.incident_date, date(2026, 10, 7))
        amendment = IncidentAmendment.objects.get()
        self.assertEqual(amendment.changed_by, self.hos)
        self.assertEqual(amendment.changes, [{"field": "Date of incident", "before": "8 October 2026",
                                              "after": "7 October 2026", "text": False}])
        page = self.client.get(reverse("discipline:detail", args=[inc.pk])).content.decode()
        self.assertIn("Record of amendment", page)
        self.assertIn("Date of incident changed from 8 October 2026 to 7 October 2026.", page)
        self.assertIn("Reason: Wrong date selected", page)
        self.assertIn("replaces any earlier copy", page)

    def test_reason_is_mandatory(self):
        inc = self.incident()
        resp = self.amend(inc, location="Playground", reason="  ")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(IncidentAmendment.objects.exists())
        inc.refresh_from_db()
        self.assertEqual(inc.location, "")

    def test_nothing_changed_is_not_saved(self):
        resp = self.amend(self.incident())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Nothing has been changed")
        self.assertFalse(IncidentAmendment.objects.exists())

    def test_level_change_recomputes_the_level(self):
        inc = self.incident()
        self.amend(inc, incident_level_2=[], incident_level_3=["Theft"])
        inc.refresh_from_db()
        self.assertEqual(inc.severity, IncidentSeverity.HIGH)
        fields = [c["field"] for c in IncidentAmendment.objects.get().changes]
        self.assertIn("Level", fields)

    def test_old_summary_kept_on_screen_but_not_reprinted(self):
        inc = self.incident(summary="Pushed Amani from Grade 2.")
        self.amend(inc, summary="Pushed a Grade 2 learner.", reason="Another learner was named.")
        resp = self.client.get(reverse("discipline:detail", args=[inc.pk]))
        html = resp.content.decode()
        sheet = html[html.index('<div class="ds-sheet">'):html.index("Screen only: review notes")]
        self.assertIn("Incident summary corrected.", sheet)
        self.assertNotIn("Amani", sheet)
        self.assertIn("Pushed Amani from Grade 2.", html)

    def test_only_super_admin_and_head_of_school(self):
        inc = self.incident()
        resp = self.amend(inc, user=self.hod, location="Playground")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(IncidentAmendment.objects.exists())
        page = self.client.get(reverse("discipline:detail", args=[inc.pk]))
        self.assertNotContains(page, "Amend record")


class ListTests(IncidentBase):
    def setUp(self):
        super().setUp()
        GradeClass.objects.create(name="Grade 8", department=Department.LOWER_SECONDARY)
        self.senior = Student.objects.create(admission_no="ADM-800", first_name="Neema", last_name="Juma",
                                             class_name="Grade 8")

    def get(self, user=None, **params):
        self.client.force_login(user or self.hos)
        return self.client.get(reverse("discipline:list"), params)

    def test_shows_reference_and_date_of_incident(self):
        inc = self.incident(incident_date=date(2026, 9, 22))
        resp = self.get()
        self.assertContains(resp, inc.reference)
        self.assertContains(resp, "22 Sep 2026")

    def test_head_of_primary_sees_only_their_sections(self):
        mine = self.incident()
        other = self.incident(student=self.senior)
        resp = self.get(self.hod)
        self.assertEqual(list(resp.context["incidents"]), [mine])
        self.client.force_login(self.hod)
        self.assertEqual(self.client.get(reverse("discipline:detail", args=[other.pk])).status_code, 404)
        self.assertEqual(len(self.get().context["incidents"]), 2)

    def test_search_by_name_admission_or_reference(self):
        a = self.incident()
        b = self.incident(student=self.senior)
        self.assertEqual(list(self.get(q="neema juma").context["incidents"]), [b])
        self.assertEqual(list(self.get(q="ADM-150").context["incidents"]), [a])
        self.assertEqual(list(self.get(q=b.reference).context["incidents"]), [b])

    def test_cards_count_everything_and_pages_keep_filters(self):
        for _ in range(26):
            self.incident()
        self.incident(status=IncidentStatus.RESOLVED)
        resp = self.get(status="pending_review")
        self.assertEqual(resp.context["stats"]["total"], 27)
        self.assertEqual(resp.context["stats"]["resolved"], 1)
        self.assertContains(resp, "?status=pending_review&amp;page=2")

    def test_amended_incidents_marked(self):
        inc = self.incident()
        IncidentAmendment.objects.create(incident=inc, changed_by=self.hos, reason="x", changes=[])
        self.assertContains(self.get(), "Amended")


class QueueTests(IncidentBase):
    def test_most_serious_first(self):
        low = self.incident(severity=IncidentSeverity.LOW)
        crit = self.incident(severity=IncidentSeverity.CRITICAL)
        med = self.incident(severity=IncidentSeverity.MEDIUM)
        high = self.incident(severity=IncidentSeverity.HIGH)
        self.client.force_login(self.hos)
        resp = self.client.get(reverse("discipline:hod_queue"))
        self.assertEqual(list(resp.context["pending_incidents"]), [crit, high, med, low])

    def test_teacher_has_no_review_queue_tab(self):
        teacher = User.objects.create_user("t2", "t2@x.edu", "pw", role=UserRole.TEACHER)
        teacher.user_permissions.add(*Permission.objects.filter(
            codename__in=["add_disciplineincident", "view_disciplineincident"]))
        self.client.force_login(teacher)
        resp = self.client.get(reverse("discipline:list"))
        self.assertNotContains(resp, "Review Queue")
