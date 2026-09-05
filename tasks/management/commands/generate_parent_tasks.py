from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Generate parent tasks for Kipchoge family (Daniel + Faith)"

    def handle(self, *args, **options):
        with transaction.atomic():
            from students.models import Student
            from tasks.services import generate_parent_tasks_for_student

            total = 0
            for adm in ["HCS0004", "HCS0005"]:
                try:
                    student = Student.objects.get(admission_no=adm)
                    tasks = generate_parent_tasks_for_student(student)
                    total += len(tasks)
                    self.stdout.write(f"  {student.get_full_name()}: {len(tasks)} tasks created")
                except Student.DoesNotExist:
                    self.stdout.write(f"  {adm}: not found, skipping")

            self.stdout.write(self.style.SUCCESS(f"\nDone! {total} parent tasks created."))
