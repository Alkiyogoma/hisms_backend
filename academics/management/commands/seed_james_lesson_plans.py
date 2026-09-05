"""Seed lesson plans for James Mwalimu across all weeks with random statuses."""
import random
from datetime import timedelta, date
from django.core.management.base import BaseCommand
from django.utils import timezone
from academics.models import LessonPlan, LessonPlanStatus
from academics.utils import get_current_term
from users.models import User


CLASSES = ["Grade 1", "Grade 2", "Grade 3", "Grade 4"]
SUBJECTS = ["Mathematics", "English"]

LESSON_TITLES = [
    "Introduction to Fractions", "Parts of Speech", "Basic Addition and Subtraction",
    "Reading Comprehension", "Shapes and Patterns", "Multiplication Tables",
    "Grammar Rules", "Story Writing", "Number Place Value", "Phonics and Blending",
    "Word Problems", "Creative Writing", "Division Basics", "Sentence Structure",
    "Geometry Basics", "Reading Fluency", "Decimals Introduction", "Essay Writing",
    "Mental Math Strategies", "Poetry and Rhyme",
]

OBJECTIVES = [
    "Students will be able to identify and classify fractions.",
    "Students will be able to use nouns, verbs, and adjectives correctly.",
    "Students will solve basic math problems with accuracy.",
    "Students will read a passage and answer comprehension questions.",
    "Students will recognize and create geometric patterns.",
    "Students will master multiplication tables from 1 to 12.",
    "Students will apply grammar rules in writing exercises.",
    "Students will write a short story with proper structure.",
    "Students will understand place value for multi-digit numbers.",
    "Students will blend sounds to read new words.",
]

ACTIVITIES = [
    "Group discussion and hands-on worksheet activity.",
    "Teacher-led explanation followed by independent practice.",
    "Interactive whiteboard games and quiz.",
    "Reading aloud and answering questions in pairs.",
    "Drawing and labeling activities.",
    "Mental math challenges and speed tests.",
    "Peer review of written work.",
    "Hands-on manipulatives for concept exploration.",
    "Partner reading and fluency practice.",
    "Timed problem-solving exercises.",
]

RESOURCES = [
    "Textbook Chapter 5, worksheets, colored pencils",
    "Chart paper, markers, grammar workbook",
    "Math manipulatives, practice sheets",
    "Reading passage, comprehension worksheet",
    "Geometry set, pattern blocks",
    "Multiplication chart, flash cards",
    "Writing notebooks, pencil set",
    "Number line, base-ten blocks",
    "Phonics cards, reading books",
    "Word problem cards, blank paper",
]

REVIEWER_FEEDBACK = [
    "Good lesson structure. Please add more assessment criteria.",
    "Excellent objectives. Consider adding differentiation strategies.",
    "Well-organized content. The activities align well with objectives.",
    "Needs more detail on formative assessment.",
    "Strong resource selection. Well done.",
    "Clear learning goals. Try incorporating more group work.",
    "Solid pacing. Consider adding a warm-up activity.",
    "Great use of examples. Add a closing summary.",
]


class Command(BaseCommand):
    help = "Seed lesson plans for James Mwalimu across all weeks with random statuses"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete existing lesson plans for James Mwalimu first",
        )

    def handle(self, *args, **options):
        clear = options["clear"]

        term = get_current_term()
        if not term:
            self.stderr.write(self.style.ERROR("No active term found."))
            return

        try:
            teacher = User.objects.get(username="teacher1")
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR("User 'teacher1' (James Mwalimu) not found."))
            return

        hod = User.objects.filter(
            role__in=["primary_hod", "ecd_hod", "lower_secondary_hod"]
        ).first()

        if clear:
            deleted = LessonPlan.objects.filter(teacher=teacher).delete()
            self.stdout.write(self.style.WARNING(f"Cleared James Mwalimu's lesson plans: {deleted}"))

        if not term.start_date or not term.end_date:
            self.stderr.write(self.style.ERROR("Current term has no start/end dates."))
            return

        start = term.start_date
        end = term.end_date

        # Generate all Mondays from term start to term end
        days_to_monday = (7 - start.weekday()) % 7
        first_monday = start + timedelta(days=days_to_monday) if days_to_monday != 0 else start
        if first_monday < start:
            first_monday += timedelta(weeks=1)

        weeks = []
        current = first_monday
        while current <= end:
            weeks.append(current)
            current += timedelta(weeks=1)

        if not weeks:
            self.stderr.write(self.style.ERROR("No weeks found in the current term."))
            return

        self.stdout.write(f"Term: {term.name} ({start} to {end})")
        self.stdout.write(f"Teacher: {teacher.get_full_name()} ({teacher.username})")
        self.stdout.write(f"Found {len(weeks)} weeks: {weeks[0]} to {weeks[-1]}")

        statuses = list(LessonPlanStatus)
        now = timezone.now()
        plans = []

        for week_start in weeks:
            # Create 1-2 lesson plans per week with random statuses
            num_plans = random.choice([1, 2])
            used_combos = set()

            for _ in range(num_plans):
                cls = random.choice(CLASSES)
                subj = random.choice(SUBJECTS)

                # Avoid duplicate class+subject+week
                combo = (cls, subj, week_start)
                if combo in used_combos:
                    subj = SUBJECTS[1 - SUBJECTS.index(subj)]  # pick the other subject
                    combo = (cls, subj, week_start)
                if combo in used_combos:
                    continue
                used_combos.add(combo)

                status = random.choice(statuses)

                plan = LessonPlan(
                    teacher=teacher,
                    term=term,
                    class_name=cls,
                    subject_name=subj,
                    week_start_date=week_start,
                    lesson_title=random.choice(LESSON_TITLES),
                    objectives=random.choice(OBJECTIVES),
                    activities=random.choice(ACTIVITIES),
                    assessment_strategy="Quiz, observation, and class participation",
                    resources=random.choice(RESOURCES),
                    status=status,
                )

                if status in (
                    LessonPlanStatus.SUBMITTED, LessonPlanStatus.APPROVED,
                    LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED,
                ):
                    plan.submitted_at = now - timedelta(
                        days=random.randint(1, 14), hours=random.randint(0, 12)
                    )

                if status in (
                    LessonPlanStatus.APPROVED, LessonPlanStatus.REJECTED,
                    LessonPlanStatus.REVISION_REQUESTED,
                ):
                    plan.reviewed_by = hod
                    plan.reviewed_at = (
                        plan.submitted_at + timedelta(days=random.randint(0, 3))
                        if plan.submitted_at else now
                    )
                    plan.reviewer_feedback = random.choice(REVIEWER_FEEDBACK)

                plans.append(plan)

        LessonPlan.objects.bulk_create(plans, batch_size=100)

        self.stdout.write(self.style.SUCCESS(
            f"\nCreated {len(plans)} lesson plans for James Mwalimu "
            f"across {len(weeks)} weeks ({weeks[0]} to {weeks[-1]})"
        ))

        for status in LessonPlanStatus:
            c = LessonPlan.objects.filter(teacher=teacher, status=status).count()
            if c > 0:
                self.stdout.write(f"  {status.label}: {c}")
