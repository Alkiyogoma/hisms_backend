from django.core.management.base import BaseCommand
from academics.models import GradeClass, Department
from students.models import Student, ParentGuardian, StudentGuardian
from datetime import date, timedelta
import random
import string


FIRST_NAMES_MALE = [
    "James", "John", "Peter", "David", "Michael", "Joseph", "Daniel", "Samuel",
    "Benjamin", "Elijah", "Ethan", "Lucas", "Henry", "Sebastian", "Owen",
    "Alexander", "Nathan", "Isaac", "Caleb", "Gabriel", "Anthony", "Christian",
    "Aaron", "Joshua", "Andrew", "Kevin", "Brian", "Steven", "Patrick", "Timothy",
    "Jeremiah", "Ezra", "Micah", "Joel", "Amos", "Hosea", "Obadiah", "Jonah",
    "Simeon", "Reuben", "Levi", "Judah", "Zebulun", "Issachar", "Dan", "Gad",
    "Asher", "Naphtali", "Manasseh", "Benjamin", "Ephraim", "Tobias", "Felix",
    "Raphael", "Matias", "Adrian", "Miles", "Jasper", "Hugo", "Liam",
]

FIRST_NAMES_FEMALE = [
    "Mary", "Grace", "Ruth", "Esther", "Hannah", "Sarah", "Rebecca", "Leah",
    "Rachel", "Deborah", "Lydia", "Priscilla", "Phoebe", "Joanna", "Susanna",
    "Elizabeth", "Abigail", "Naomi", "Martha", "Miriam", "Claire", "Nora",
    "Lily", "Chloe", "Ava", "Emma", "Olivia", "Sophia", "Isabella", "Mia",
    "Charlotte", "Amelia", "Harper", "Evelyn", "Abigail", "Emily", "Ella",
    "Scarlett", "Grace", "Luna", "Zoe", "Victoria", "Aurora", "Savannah",
    "Audrey", "Brooklyn", "Leah", "Hazel", "Violet", "Aria", "Rose",
    "Clara", "Lucy", "Anna", "Caroline", "Genesis", "Maya", "Kennedy",
    "Samantha", "Penelope", "Adeline", "Sadie", "Ariana", "Allison",
]

LAST_NAMES = [
    "Mwangi", "Kamau", "Njoroge", "Kariuki", "Wanjiku", "Njeri", "Wambui", "Gichuru",
    "Omondi", "Otieno", "Odhiambo", "Onyango", "Ouma", "Owino", "Anyango", "Achieng",
    "Kipchoge", "Korir", "Kibet", "Kiptoo", "Jepkemoi", "Chebet", "Chepngetich", "Kosgei",
    "Mutua", "Kioko", "Musyoka", "Kilonzo", "Muthama", "Ndambo", "Mweni", "Kavata",
    "Simiyu", "Wafula", "Barasa", "Wekesa", "Masinde", "Juma", "Mwangoka", "Banda",
    "Kimani", "Ngugi", "Kamotho", "Mburu", "Njenga", "Kinyanjui", "Gathua", "Maina",
    "Odongo", "Oduya", "Mideva", "Waenda", "Obiri", "Auma", "Nekesa", "Wamalwa",
    "Sharma", "Patel", "Singh", "Khan", "Ali", "Hassan", "Mohamed", "Ibrahim",
    "Chen", "Liu", "Wang", "Zhang", "Osei", "Mensah", "Koomson", "Acheampong",
    "Dlamini", "Ndlovu", "Moyo", "Nkomo", "Sithole", "Mkhize", "Zulu", "Banda",
]

GUARDIAN_NAMES = [
    "James Mwangi", "Mary Kamau", "Peter Njoroge", "Grace Kariuki", "David Wanjiku",
    "Ruth Njeri", "John Wambui", "Sarah Gichuru", "Michael Omondi", "Elizabeth Otieno",
    "Joseph Odhiambo", "Hannah Onyango", "Daniel Ouma", "Rebecca Owino", "Samuel Anyango",
    "Esther Achieng", "Benjamin Kipchoge", "Abigail Korir", "Elijah Kibet", "Naomi Kiptoo",
    "Ethan Jepkemoi", "Lydia Chebet", "Caleb Chepngetich", "Martha Kosgei", "Aaron Mutua",
    "Priscilla Kioko", "Joshua Musyoka", "Joanna Kilonzo", "Andrew Muthama", "Susanna Ndambo",
    "Timothy Mweni", "Claire Kavata", "Gabriel Simiyu", "Nora Wafula", "Anthony Barasa",
    "Audrey Wekesa", "Christian Masinde", "Leah Juma", "Nathan Mwangoka", "Aurora Banda",
    "Isaac Kimani", "Violet Ngugi", "Caleb Kamotho", "Penelope Mburu", "Jeremiah Njenga",
    "Adeline Kinyanjui", "Ezra Gathua", "Sadie Maina", "Micah Odongo", "Aria Oduya",
    "Felix Osei", "Clara Mensah", "Raphael Koomson", "Lucy Acheampong", "Matias Sharma",
    "Anna Patel", "Adrian Singh", "Miles Khan", "Jasper Ali", "Hugo Hassan",
    "Tobias Mohamed", "Liam Ibrahim", "Harper Chen", "Emma Liu", "Olivia Wang",
    "Sophia Zhang", "Isabella Omondi", "Mia Otieno", "Charlotte Odhiambo", "Amelia Onyango",
    "Scarlett Ouma", "Luna Owino", "Zoe Anyango", "Victoria Achieng", "Savannah Kipchoge",
    "Brooklyn Korir", "Hazel Kibet", "Violet Kiptoo", "Aria Jepkemoi", "Rose Chebet",
    "Kennedy Ndambo", "Samantha Mweni", "Penelope Kavata", "Adeline Simiyu", "Sadie Wafula",
]

class Command(BaseCommand):
    help = "Onboard students to ALL grade classes (ECD through Grade 9)"

    def add_arguments(self, parser):
        parser.add_argument("--students-per-class", type=int, default=15,
                            help="Number of students per class (default: 15)")

    def handle(self, *args, **options):
        per_class = options["students_per_class"]

        GRADES = [
            ("Pre-Kindergarten", Department.ECD, 3, 4),
            ("Kindergarten", Department.ECD, 4, 5),
            ("Pre-School", Department.ECD, 5, 6),
            ("ABC Class", Department.ECD, 5, 6),
            ("Grade 1", Department.PRIMARY, 6, 7),
            ("Grade 2", Department.PRIMARY, 7, 8),
            ("Grade 3", Department.PRIMARY, 8, 9),
            ("Grade 4", Department.PRIMARY, 9, 10),
            ("Grade 5", Department.PRIMARY, 10, 11),
            ("Grade 6", Department.PRIMARY, 11, 12),
            ("Grade 7", Department.LOWER_SECONDARY, 12, 13),
            ("Grade 8", Department.LOWER_SECONDARY, 13, 14),
            ("Grade 9", Department.LOWER_SECONDARY, 14, 15),
        ]

        seq = Student.objects.count() + 1
        total_created = 0
        total_guardians = 0

        for class_name, dept, min_age, max_age in GRADES:
            gc, _ = GradeClass.objects.get_or_create(
                name=class_name,
                defaults={"department": dept, "max_capacity": 35, "sort_order": GRADES.index((class_name, dept, min_age, max_age))},
            )

            existing = Student.objects.filter(class_name=class_name).count()
            needed = per_class - existing
            if needed <= 0:
                self.stdout.write(f"  {class_name}: already has {existing} students, skipping")
                continue

            self.stdout.write(f"  {class_name}: creating {needed} students (existing: {existing}) ...")

            guardian_pool = list(range(len(GUARDIAN_NAMES)))
            random.shuffle(guardian_pool)

            for i in range(needed):
                is_male = random.random() < 0.5
                fn = random.choice(FIRST_NAMES_MALE if is_male else FIRST_NAMES_FEMALE)
                ln = random.choice(LAST_NAMES)
                gender = "male" if is_male else "female"
                dob = date.today() - timedelta(days=365 * random.randint(min_age, max_age) + random.randint(0, 364))
                adm_no = f"ADM-2026-{seq:03d}"

                student, created = Student.objects.get_or_create(
                    first_name=fn,
                    last_name=ln,
                    date_of_birth=dob,
                    defaults={
                        "admission_no": adm_no,
                        "class_name": class_name,
                        "gender": gender,
                        "status": "active",
                        "enrolment_date": date(2026, 1, 15),
                    },
                )
                if not created:
                    adm_no = f"ADM-2026-{seq:03d}"
                    student.admission_no = adm_no
                    student.save(update_fields=["admission_no"])
                    seq += 1
                    total_created += 1
                    continue

                seq += 1
                total_created += 1

                if random.random() < 0.7:
                    g_idx = guardian_pool[i % len(guardian_pool)]
                    g_name = GUARDIAN_NAMES[g_idx]
                    g_phone = f"+2547{random.randint(10000000, 99999999)}"
                    g_email = g_name.split()[0].lower() + "." + g_name.split()[-1].lower() + "@email.com"

                    guardian, g_created = ParentGuardian.objects.get_or_create(
                        full_name=g_name,
                        phone=g_phone,
                        defaults={
                            "email": g_email,
                            "pdpa_consent_given": True,
                            "pdpa_consent_method": "in_person",
                        },
                    )
                    if g_created:
                        total_guardians += 1

                    relationship = random.choice(["mother", "father", "guardian"])
                    is_primary = relationship in ("mother", "father")
                    StudentGuardian.objects.get_or_create(
                        student=student,
                        guardian=guardian,
                        defaults={"relationship": relationship, "is_primary": is_primary},
                    )

            self.stdout.write(self.style.SUCCESS(f"  {class_name}: created {needed} students"))

        self.stdout.write(self.style.SUCCESS(
            f"\nDONE: {total_created} students created across {len(GRADES)} classes, {total_guardians} guardians created"
        ))
