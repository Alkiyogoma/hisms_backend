from django.core.management.base import BaseCommand
from academics.models import GradeClass, Department
from students.models import Student
from users.models import User, UserRole
from timetable.models import TimetableSlot
from django.utils import timezone
from datetime import date, timedelta
import random

class Command(BaseCommand):
    help = 'Seed ECD students and assign to teachers'

    def handle(self, *args, **options):
        classes_to_create = ["Pre-Kindergarten", "Kindergarten", "Pre-School", "ABC Class"]
        
        first_names = ["Grace", "Iris", "Arlene", "Channah", "Peter", "Amara", "Leon", "Sara", "James", "Priya"]
        last_names = ["M", "S", "T", "K", "N", "D", "W", "M", "A", "H"]

        created_count = 0

        for cn in classes_to_create:
            GradeClass.objects.get_or_create(name=cn, defaults={'department': Department.ECD})
            
            # Assign to ALL teachers so they can see the data
            for teacher in User.objects.filter(role=UserRole.TEACHER):
                TimetableSlot.objects.get_or_create(
                    teacher=teacher,
                    class_name=cn,
                    subject_name="General",
                    day_of_week="monday",
                    defaults={'start_time': "08:00", 'end_time': "16:00"}
                )

            existing_students = Student.objects.filter(class_name=cn).count()
            needed = 10 - existing_students

            if needed > 0:
                for _ in range(needed):
                    fn = random.choice(first_names)
                    ln = random.choice(last_names)
                    dob = date.today() - timedelta(days=365*random.randint(4, 6))
                    student, created = Student.objects.get_or_create(
                        first_name=fn,
                        last_name=ln,
                        date_of_birth=dob,
                        defaults={
                            'admission_no': f"ADM{random.randint(10000, 99999)}",
                            'class_name': cn,
                            'gender': random.choice(['male', 'female'])
                        }
                    )
                    if created:
                        created_count += 1
                    created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Added {needed} students to {cn}"))
            else:
                self.stdout.write(f"{cn} already has enough students.")

        self.stdout.write(self.style.SUCCESS(f"Total students created: {created_count}"))
