from django.db import migrations


DOMAIN_GROUPS = {
    "pre_k": [
        ("Numeracy & Cognitive", [
            "Recognises numbers 1–10", "Recites numbers 1–10",
            "Understands size (big / small)", "Identifies colours learned this term",
            "Identifies shapes learned this term", "Sorting shapes & colours",
        ]),
        ("Communication Skills", [
            "Knows first name", "Responds to direct questions",
            "Expresses needs clearly", "Responds well within a group",
            "Repeats sentences",
        ]),
        ("Motor Skills", [
            "Can tear paper", "Holds & uses paint brush, pencil, crayon",
            "Can thread beads", "Holds spoon and eats without help",
            "Can jump up and down", "Can throw a ball",
            "Can kick a ball", "Participates in music and movement",
        ]),
        ("Social / Emotional Skills", [
            "Enjoys school activities", "Plays and shares well with others",
            "Listens and follows instructions",
        ]),
        ("Swimming", [
            "Getting in the water", "Hand & leg movement",
            "Attitude", "Swimming attendance",
        ]),
    ],
    "kindergarten": [
        ("Numeracy", [
            "Number counting 1–100", "Number formation 0–9",
            "Number sequence 1–40", "Number recognition 1–40",
            "Number value", "Number tracing",
            "Basic shapes", "Problem solving",
            "Can put together puzzles (critical thinking)",
            "Ability to do a maze (critical thinking)",
            "Basic addition", "Basic subtraction",
        ]),
        ("Reading", [
            "Recognise, compare & distinguish sounds",
            "Listening to stories",
            "Vocabulary, grammar & pronunciation",
            "Songs and rhymes", "Comprehension",
            "Ability to sit still and listen to recall",
            "Reading / blending two to three sounds",
        ]),
        ("Writing", [
            "Sound and number formation",
            "Handwriting neatness and pencil grip",
            "Writing first name", "Writing last name",
        ]),
        ("Bible Memory", [
            "Scripture memorisation",
        ]),
        ("Personal", [
            "Completes work timely", "Shows initiative and creativity",
            "Attentive to direction", "Works well independently",
            "Exhibits self-confidence", "Portrays independence",
            "Exhibits self-control", "Considerate of others",
            "Responds well to correction", "Cleanliness",
            "Food appetite",
        ]),
        ("Physical Development", [
            "Swimming", "Catch and throw",
            "Balancing", "Running",
            "Kicking", "Jumping jacks",
        ]),
        ("Artistry", [
            "Good hand and eye coordination to perform a task",
            "Participates in music and dance",
            "Fine motor grip", "Shows creativity in crafts",
        ]),
    ],
    "pre_school": [
        ("Numeracy", [
            "Counting 1–10: counting", "Counting 1–10: recognition", "Counting 1–10: matching",
            "Shapes recognition", "Shapes association", "Colour recognition", "Colour association",
        ]),
        ("Pre-Writing", [
            "colouring, painting, moulding", "eye-hand coordination",
        ]),
        ("School Readiness", [
            "Readiness to participate in class activities", "Attention span", "Grade level maturity",
            "Able to eat on their own", "Able to comprehend and follow instructions",
            "Able to communicate in comprehensible speech", "Potty trained",
        ]),
        ("Social Skills", [
            "Willing to share", "Plays well and safely with others", "Attitude towards discipline and correction",
        ]),
    ],
    "abc": [
        ("Academic Progress", [
            "Mathematics", "English", "Science",
            "Social Studies", "Word Building", "Scripture",
        ]),
        ("General Assignments", [
            "Completes assignments on time",
            "Quality of homework",
            "Independent work habits",
        ]),
        ("Character", [
            "Punctuality", "Respectful", "Obedient",
            "Honest", "Neat and tidy",
        ]),
    ],
}


def seed_ecd_domains(apps, schema_editor):
    ECDDomainConfig = apps.get_model("academics", "ECDDomainConfig")
    for class_type, domains in DOMAIN_GROUPS.items():
        for sort_order, (domain_name, competencies) in enumerate(domains):
            ECDDomainConfig.objects.get_or_create(
                class_type=class_type,
                domain_name=domain_name,
                defaults={"competencies": competencies, "sort_order": sort_order},
            )


def reverse_seed(apps, schema_editor):
    ECDDomainConfig = apps.get_model("academics", "ECDDomainConfig")
    ECDDomainConfig.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0047_ecddomainconfig"),
    ]

    operations = [
        migrations.RunPython(seed_ecd_domains, reverse_seed),
    ]
