from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "FRD OP 7.5: Check that every active class has an assigned class teacher for the active term."

    def handle(self, *args, **options):
        from academics.models import GradeClass
        from academics.utils import get_current_term
        from hr.models import TeacherClassAssignment

        term = get_current_term()
        if not term:
            self.stdout.write(self.style.WARNING("No active term found. Skipping check."))
            return

        classes = GradeClass.objects.all()
        missing = []
        for gc in classes:
            has_teacher = TeacherClassAssignment.objects.filter(
                grade_class=gc,
                term=term,
                is_class_teacher=True,
            ).exists()
            if not has_teacher:
                missing.append(gc.name)

        if missing:
            self.stdout.write(self.style.ERROR(
                f"FRD OP 7.5 VIOLATION: {len(missing)} class(es) have no assigned class teacher for {term.name}:"
            ))
            for name in sorted(missing):
                self.stdout.write(f"  - {name}")
            self.stdout.write(self.style.WARNING(
                "Assign a class teacher to each class before the term progresses."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"OK: All {classes.count()} classes have assigned class teachers for {term.name}."
            ))
