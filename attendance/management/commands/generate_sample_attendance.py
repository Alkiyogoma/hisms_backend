from __future__ import annotations

import random
from datetime import datetime, timedelta, time

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone

from attendance.models import AttendanceEntry, AttendanceStatus
from students.models import Student, StudentStatus


class Command(BaseCommand):
    help = "Generate randomized attendance sample records for recent days."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Generate attendance for the last N days (default: 7).'
        )
        parser.add_argument(
            '--students',
            type=int,
            default=0,
            help='Limit number of students to populate (default: all active students).'
        )
        parser.add_argument(
            '--absent-chance',
            type=float,
            default=0.1,
            help='Chance a student is absent on a given day (0-1).'
        )
        parser.add_argument(
            '--late-chance',
            type=float,
            default=0.08,
            help='Chance a student is late on a given day (0-1).'
        )
        parser.add_argument(
            '--excused-chance',
            type=float,
            default=0.02,
            help='Chance a student is excused on a given day (0-1).'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            default=True,
            help='Do not overwrite existing attendance entries for a student/date.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Override safety check: allow overwriting existing real attendance data.',
        )

    def handle(self, *args, **options):
        if not options['force']:
            raise CommandError(
                'SAFETY: This command overwrites attendance data with random values. '
                'Use --force to confirm you want to proceed.'
            )

        user_model = get_user_model()
        marker = user_model.objects.filter(is_active=True).first() or user_model.objects.first()
        if marker is None:
            raise CommandError('No active user found to mark attendance.')

        days = max(1, options['days'])
        student_limit = options['students']
        absent_chance = min(max(options['absent_chance'], 0.0), 1.0)
        late_chance = min(max(options['late_chance'], 0.0), 1.0)
        excused_chance = min(max(options['excused_chance'], 0.0), 1.0)

        students_qs = Student.objects.filter(status=StudentStatus.ACTIVE, is_archived=False).order_by('id')
        if student_limit > 0:
            students_qs = students_qs[:student_limit]

        students = list(students_qs)
        if not students:
            raise CommandError('No active students found to generate attendance data.')

        now = timezone.now()
        today = now.date()
        generated = 0
        updated = 0
        skipped = 0

        self.stdout.write(self.style.SUCCESS(
            f'Generating attendance for {len(students)} student(s) over the last {days} day(s).'
        ))

        for day_offset in range(days):
            attendance_date = today - timedelta(days=day_offset)
            for student in students:
                if options['skip_existing'] and AttendanceEntry.objects.filter(student=student, date=attendance_date).exists():
                    skipped += 1
                    continue

                status = AttendanceStatus.PRESENT
                random_val = random.random()
                if random_val < excused_chance:
                    status = AttendanceStatus.EXCUSED
                    check_in = None
                    check_out = None
                elif random_val < excused_chance + absent_chance:
                    status = AttendanceStatus.ABSENT
                    check_in = None
                    check_out = None
                elif random_val < excused_chance + absent_chance + late_chance:
                    status = AttendanceStatus.LATE
                    check_in = self._random_time(start_hour=8, end_hour=9, minute_range=(15, 45))
                    check_out = self._random_time(start_hour=14, end_hour=16, minute_range=(0, 30))
                else:
                    status = AttendanceStatus.PRESENT
                    check_in = self._random_time(start_hour=7, end_hour=8, minute_range=(0, 45))
                    check_out = self._random_time(start_hour=14, end_hour=16, minute_range=(0, 30))

                if check_in and check_out and check_out <= check_in:
                    check_out = (datetime.combine(today, check_in) + timedelta(hours=6)).time()

                is_early_departure = False
                if check_out is not None:
                    is_early_departure = check_out < time(hour=15, minute=30)

                entry_values = {
                    'status': status,
                    'check_in_time': check_in,
                    'check_out_time': check_out,
                    'is_early_departure': is_early_departure,
                    'marked_by': marker,
                    'marked_at': now,
                    'class_name': student.class_name or 'Unknown',
                }

                entry, created = AttendanceEntry.objects.update_or_create(
                    student=student,
                    date=attendance_date,
                    defaults=entry_values,
                )

                if created:
                    generated += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f'Completed attendance generation.'))
        self.stdout.write(f'  Created: {generated}')
        self.stdout.write(f'  Updated: {updated}')
        self.stdout.write(f'  Skipped: {skipped}')

    def _random_time(self, start_hour=7, end_hour=16, minute_range=(0, 59)):
        hour = random.randint(start_hour, end_hour)
        minute = random.randint(minute_range[0], minute_range[1])
        second = random.randint(0, 59)
        return time(hour=hour, minute=minute, second=second)
