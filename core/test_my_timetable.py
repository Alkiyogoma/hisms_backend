from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse

from academics.models import AcademicYear, Term
from core.teaching import get_my_timetable
from timetable.models import TimetableSlot, Weekday
from users.models import User, UserRole


class MyTimetableOnAdminDashboardTests(TestCase):
    def setUp(self):
        today = date.today()
        year = AcademicYear.objects.create(name=str(today.year), is_current=True)
        self.term = Term.objects.create(academic_year=year, name="Term 1",
                                        start_date=today - timedelta(days=30), end_date=today + timedelta(days=30))
        self.hos = User.objects.create_user("hos1", "hos1@x.edu", "pw", role=UserRole.HEAD_OF_SCHOOL)
        self.other = User.objects.create_user("hos2", "hos2@x.edu", "pw", role=UserRole.HEAD_OF_SCHOOL)
        day = [Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU, Weekday.FRI][min(today.weekday(), 4)]
        TimetableSlot.objects.create(term=self.term, class_name="Grade 5", subject_name="Mathematics",
                                     teacher=self.hos, day_of_week=day,
                                     start_time=time(8, 0), end_time=time(8, 40))

    def test_helper_none_when_no_classes(self):
        self.assertIsNone(get_my_timetable(self.other))

    def test_helper_lists_lessons(self):
        data = get_my_timetable(self.hos, today=date.today(), now_time=time(7, 0))
        self.assertEqual([s.subject_name for s in data["slots"]], ["Mathematics"])

    def test_hos_who_teaches_sees_timetable_first(self):
        self.client.force_login(self.hos)
        html = self.client.get("/").content.decode()
        self.assertIn("My teaching timetable", html)
        self.assertIn("Mathematics", html)
        self.assertIn('id="myWeekTimetable"', html)  # full week opens in a drawer

    def test_hos_without_classes_sees_no_card(self):
        self.client.force_login(self.other)
        html = self.client.get("/").content.decode()
        self.assertNotIn("My teaching timetable", html)
