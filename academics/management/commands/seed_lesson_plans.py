"""Seed realistic lesson plan data for testing all tabs."""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from academics.models import LessonPlan, LessonPlanStatus
from academics.utils import get_current_term
from users.models import User


CLASSES = [
    "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6",
    "Pre-K", "Nursery 1", "Nursery 2", "Lower 1", "Lower 2", "Lower 3",
]

SUBJECTS = [
    "Mathematics", "English", "Science", "Social Studies", "Kiswahili",
    "Religious Education", "Physical Education", "Creative Arts", "Music",
]

LESSON_TITLES = [
    "Introduction to Fractions", "Parts of Speech", "The Water Cycle",
    "Community Helpers", "Animal Habitats", "Basic Addition and Subtraction",
    "Reading Comprehension", "Shapes and Patterns", "Plant Life Cycle",
    "Simple Machines", "Weather and Climate", "Story Writing",
    "Multiplication Tables", "Grammar Rules", "Science Experiments",
    "History of Our Community", "Health and Hygiene", "Art Projects",
    "Musical Rhythms", "Environmental Conservation",
]

OBJECTIVES = [
    "Students will be able to identify and classify fractions.",
    "Students will be able to use nouns, verbs, and adjectives correctly.",
    "Students will understand the process of evaporation and condensation.",
    "Students will learn about different roles in a community.",
    "Students will describe where animals live and why.",
    "Students will solve basic math problems with accuracy.",
    "Students will read a passage and answer comprehension questions.",
    "Students will recognize and create geometric patterns.",
    "Students will explain the stages of plant growth.",
    "Students will identify and explain simple machines.",
]

ACTIVITIES = [
    "Group discussion and hands-on worksheet activity.",
    "Teacher-led explanation followed by independent practice.",
    "Video demonstration and class brainstorming session.",
    "Role-play activity with partner work.",
    "Observation and recording data in science journals.",
    "Interactive whiteboard games and quiz.",
    "Reading aloud and answering questions in pairs.",
    "Drawing and labeling activities.",
    "Outdoor exploration and nature walk.",
    "Building models using recycled materials.",
]

RESOURCES = [
    "Textbook Chapter 5, worksheets, colored pencils",
    "Chart paper, markers, grammar workbook",
    "Science kit, water containers, recording sheet",
    "Community helper cards, discussion prompts",
    "Animal habitat pictures, research books",
    "Math manipulatives, practice sheets",
    "Reading passage, comprehension worksheet",
    "Geometry set, pattern blocks",
    "Seed kits, observation journals",
    "Building materials, instruction cards",
]

REVIEWER_FEEDBACK = [
    "Good lesson structure. Please add more assessment criteria.",
    "Excellent objectives. Consider adding differentiation strategies.",
    "Well-organized content. The activities align well with objectives.",
    "Needs more detail on formative assessment.",
    "Strong resource selection. Well done.",
]


class Command(BaseCommand):
    help = "Seed test lesson plan data for different teachers and weeks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count", type=int, default=60,
            help="Number of lesson plans to create (default: 60)",
        )
        parser.add_argument(
            "--clear", action="store_true",
            help="Delete all existing lesson plans first",
        )

    def handle(self, *args, **options):
        count = options["count"]
        clear = options["clear"]

        term = get_current_term()
        if not term:
            self.stderr.write(self.style.ERROR("No active term found. Create one first."))
            return

        teachers = list(User.objects.filter(role="teacher"))
        if not teachers:
            self.stderr.write(self.style.ERROR("No teachers found. Create users first."))
            return

        hod = User.objects.filter(role__in=["primary_hod", "ecd_hod", "lower_secondary_hod"]).first()
        if not hod:
            hod = User.objects.filter(role="admin").first()

        if clear:
            deleted = LessonPlan.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared all lesson plans: {deleted}"))

        now = timezone.now()
        today = now.date()
        # Start from 4 weeks ago, Monday
        start_monday = today - timedelta(days=today.weekday() + 28)

        statuses = list(LessonPlanStatus)
        status_weights = [
            (LessonPlanStatus.APPROVED, 15),
            (LessonPlanStatus.SUBMITTED, 12),
            (LessonPlanStatus.DRAFT, 10),
            (LessonPlanStatus.REVISION_REQUESTED, 5),
            (LessonPlanStatus.REJECTED, 3),
            (LessonPlanStatus.MISSING, 15),
        ]
        weighted = []
        for s, w in status_weights:
            weighted.extend([s] * w)

        import random
        random.seed(42)

        plans = []
        created = 0
        for i in range(count):
            teacher = random.choice(teachers)
            cls = random.choice(CLASSES)
            subj = random.choice(SUBJECTS)
            week_idx = random.randint(0, 7)
            week_start = start_monday + timedelta(weeks=week_idx)
            status = random.choice(weighted)

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

            if status in (LessonPlanStatus.SUBMITTED, LessonPlanStatus.APPROVED,
                          LessonPlanStatus.REJECTED, LessonPlanStatus.REVISION_REQUESTED):
                days_ago = random.randint(1, 20)
                plan.submitted_at = now - timedelta(days=days_ago, hours=random.randint(0, 12))

            if status in (LessonPlanStatus.APPROVED, LessonPlanStatus.REJECTED,
                          LessonPlanStatus.REVISION_REQUESTED):
                plan.reviewed_by = hod
                plan.reviewed_at = plan.submitted_at + timedelta(days=random.randint(0, 3)) if plan.submitted_at else now
                if status == LessonPlanStatus.REVISION_REQUESTED:
                    plan.reviewer_feedback = random.choice(REVIEWER_FEEDBACK)
                elif status == LessonPlanStatus.REJECTED:
                    plan.reviewer_feedback = "Does not meet curriculum standards. Please revise."
                else:
                    plan.reviewer_feedback = random.choice(REVIEWER_FEEDBACK)

            plans.append(plan)
            created += 1

        LessonPlan.objects.bulk_create(plans, batch_size=100)

        self.stdout.write(self.style.SUCCESS(
            f"Created {created} lesson plans across {len(teachers)} teachers, "
            f"{len(CLASSES)} classes, {len(SUBJECTS)} subjects, weeks {start_monday} to {start_monday + timedelta(weeks=7)}"
        ))

        # Print summary
        for status in LessonPlanStatus:
            c = LessonPlan.objects.filter(status=status).count()
            if c > 0:
                self.stdout.write(f"  {status.label}: {c}")
