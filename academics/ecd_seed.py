"""ECD progress-report content as given in the school's Word templates
(PRE KG, KG, PRESCHOOL, ABC × Term 1-3).

Loaded into ECDReportLayout / ECDSection / ECDItem by migration 0070 and by
``manage.py seed_ecd_templates``. Item terms: ``ALL`` = every term, otherwise
the term numbers the line appears in.
"""

ALL = (1, 2, 3)

KEY_4 = [["4", "Outstanding"], ["3", "Good"], ["2", "Satisfactory"], ["1", "Not yet"]]
KEY_4_ABC = [["4", "Outstanding"], ["3", "Good"], ["2", "Satisfactory"], ["1", "Not Yet"]]
KEY_4_PROGRESS = [["4", "Outstanding"], ["3", "Good"], ["2", "Making Progress"], ["1", "Not Yet"]]
TRAITS_NOT_YET = [["E", "Excellence"], ["G", "Good"], ["S", "Satisfactory"], ["N", "Not yet"]]
TRAITS_NEEDS = [["E", "Excellence"], ["G", "Good"], ["S", "Satisfactory"], ["N", "Needs Improvement"]]
TRAITS_KG = [["E", "Excellent"], ["G", "Good"], ["S", "Satisfactory"], ["N", "Not Yet"]]
TRAITS_KG_PROGRESS = [["E", "Excellent"], ["G", "Good"], ["M", "Making Progress"], ["N", "Not Yet"]]


def _layouts(title, accent, academic_key, traits_key, column_label="Points"):
    """Same layout for all three terms; per-term overrides are applied after."""
    return {
        n: {"title": title, "accent": accent, "academic_key": academic_key,
            "traits_key": traits_key, "column_label": column_label}
        for n in ALL
    }


# Each section: (part, title, column, style, [items]); an item is a label,
# or (label, terms) / (label, terms, allows_text).
TEMPLATES = {
    "pre_k": {
        "layouts": _layouts("PRE - KINDERGARTEN PROGRESS REPORT", "yellow", KEY_4, TRAITS_NOT_YET),
        "sections": [
            ("academic", "NUMERACY", "left", "table", [
                "Recognizes numbers (1 to 10).",
                "Recites numbers well (1 to 10).",
                "Understands size (big and small)",
                "Identifies colors learned through the Term.",
                "Identifies shapes learned through the Term.",
                "Sorting different shapes & colors",
            ]),
            ("academic", "COMMUNICATION SKILLS", "right", "table", [
                "Knows first name.",
                "Responds to direct questions",
                "Expresses needs clearly through verbal and nonverbal communication",
                "Responds well within a group.",
                "Repeats sentences",
            ]),
            ("traits", "MOTOR SKILLS", "left", "table", [
                "Can tear paper",
                "Can hold and use a paint brush, pencil and crayon",
                "Can thread beads",
                "Can hold a spoon and eat without help",
                "Can jump up & down",
                "Can throw a ball",
                "Can kick a ball",
                "Participates well during music and movement",
            ]),
            ("traits", "SOCIAL / EMOTIONAL SKILLS", "right", "table", [
                "Enjoys school activities",
                "Plays and shares well with others",
                "Listens and follows instructions",
            ]),
            ("traits", "SWIMMING", "right", "table", [
                "Getting in the water",
                "Hand & leg movement",
                "Attitude",
                "Attendance",
            ]),
        ],
    },
    "kindergarten": {
        "layouts": _layouts("KINDERGARTEN PROGRESS REPORT", "yellow", KEY_4_PROGRESS, TRAITS_KG_PROGRESS, "Evaluation"),
        "term_overrides": {1: {"academic_key": [["4", "Outstanding"], ["3", "Good"], ["2", "Satisfactory"], ["1", "Not Yet"]],
                               "traits_key": TRAITS_KG}},
        "sections": [
            ("academic", "NUMERACY", "left", "table", [
                ("Number counting 0-10", (1,)), ("Number counting 0-15", (2,)), ("Number counting 0-20", (3,)),
                ("Number value 0-5", (1,)), ("Number value 0-10", (2, 3)),
                ("Number sequence 0-10", (1,)), ("Number sequence 0-15", (2,)), ("Number sequence 0-20", (3,)),
                ("Number recognition 0-5", (1,)), ("Number recognition 0-15", (2,)), ("Number recognition 0-20", (3,)),
                "Can put together 1 to 5 pieces of puzzles (Critical thinking)",
                "Recognition and association of basic shapes with real objects",
                "Sorting shapes, numbers and colors",
            ]),
            ("academic", "COLORS", "left", "table", [
                "Recognition and association of colors with real life objects",
            ]),
            ("academic", "PRE-READING", "right", "table", [
                "Recognition of sounds",
                "Reciting sounds",
                "Can recognize the relationship between sounds and words",
                "Ability to sit still, listen and understand stories",
            ]),
            ("academic", "PRE-WRITING", "right", "table", [
                ("Number formation 0-5", (1,)), ("Number formation 0-10", (2,)), ("Number formation 0-20", (3,)),
                "Tracing lines, numbers and sounds",
                "Crayon / Pencil grip",
                "Can color within lines",
            ]),
            ("traits", "PERSONAL DEVELOPMENT", "left", "table", [
                "Readiness to participate in class activities",
                "Portrays self-confidence",
                "Can follow two-step instructions",
                "Attention span",
                "Is able to eat on their own",
                "Appropriate grade level maturity",
                "Is potty trained",
            ]),
            ("traits", "PHYSICAL DEVELOPMENT", "right", "table", [
                "Swimming",
                "Ball handling (catch, throw, kick)",
                "Can run with balance",
                "Can climb up and go down",
                "Participates in music and dance",
                "Can cut with scissors",
                "Threading and zipping",
            ]),
            ("traits", "SOCIAL DEVELOPMENT", "left", "table", [
                "Plays well and safely with others",
                "Is willing to share",
                "Responds well to correction",
            ]),
            ("traits", "SPEECH DEVELOPMENT", "right", "table", [
                "Can express feelings through verbal and non-verbal communication",
                "Is able to communicate in comprehensible speech",
                "Can answer simple direct questions",
            ]),
        ],
    },
    "pre_school": {
        "layouts": _layouts("PRESCHOOL PROGRESS REPORT", "yellow", KEY_4, TRAITS_NEEDS),
        "sections": [
            ("academic", "Subject", "full", "strip", [
                "Math", "English", "Science", "Word Building",
            ]),
            ("academic", "NUMERACY", "left", "table", [
                "Number Counting 1-100",
                "Number Formation 0-9",
                ("Number Sequence 1-30", (1,)), ("Number Sequence 1-100", (2, 3)),
                ("Number Recognition 1-30", (1,)), ("Number Recognition 1-100", (2, 3)),
                "Number Value",
                ("Number Tracing", (1, 2)), ("Number Dictation", (3,)),
                "Basic Shapes",
                "Problem solving",
                "Can put together 6 to 9 pieces puzzles (Critical thinking)",
                "Ability to do a maze (Critical thinking)",
                "Basic addition",
                "Basic subtraction",
            ]),
            ("academic", "READING", "right", "table", [
                "Recognize, compare and distinguish sounds",
                "Listening to stories",
                "Vocabulary, grammar and pronunciation",
                "Songs and rhymes",
                "Comprehension",
                "Ability to sit still and listen to recall and answer questions (short term memory)",
                "Reading / Blending two to three sounds",
            ]),
            ("academic", "WRITING", "right", "table", [
                "Sound and Number formation",
                "Hand writing neatness and Pencil grip",
                "Writing first name",
                "Writing last name",
            ]),
            # The verses are the lines, added per term in Report Setup.
            ("academic", "Bible Memory", "full", "verse", []),
            ("traits", "PERSONAL", "left", "table", [
                "Completes work timely",
                "Shows initiative and creativity",
                "Attentive to direction",
                "Works well independently",
                "Exhibits self confidence",
                "Portrays independence",
                "Exhibits self-control",
                "Considerate of others",
                "Responds well to correction",
                "Cleanliness",
                "Food appetite",
            ]),
            ("traits", "PHYSICAL DEVELOPMENT", "right", "table", [
                "Swimming",
                "Catch and throw",
                "Balancing",
                "Running",
                "Kicking",
                "Jumping jacks",
            ]),
            ("traits", "ARTISTRY", "right", "table", [
                "Good hand and eye coordination to perform a task",
                "Participates in music and dance",
                "Fine motor grip",
                "Shows creativity in crafts",
            ]),
        ],
    },
    "abc": {
        "layouts": _layouts("ABC PROGRESS REPORT", "yellow", KEY_4_ABC, TRAITS_NEEDS, "Evaluation"),
        "term_overrides": {3: {"column_label": "Points"}},
        "sections": [
            ("academic", "Subject", "full", "strip", [
                ("Math", (1, 2)), ("Word Building", (1, 2)), ("Social Studies", (1, 2)),
                ("Bible Reading", (1, 2)), ("Animal Science", (1, 2)),
            ]),
            ("academic", "PACE", "full", "strip", [
                ("Math", (3,)), ("Word Building", (3,)), ("English", (3,)),
            ]),
            ("academic", "Pre Grade 1 Assessment", "full", "marks", [
                ("English", (3,)), ("Math", (3,)),
            ]),
            ("academic", "NUMERACY", "left", "table", [
                "Counting",
                "Number Recognition",
                "Number Sequencing",
                "Addition",
                "Subtraction",
                "Quantity & Measurement",
                "Number Words",
                "Shapes",
                "Problem solving and critical thinking",
            ]),
            # The verses are the lines, added per term in Report Setup.
            ("academic", "Bible Memory", "full", "verse", []),
            ("academic", "READING", "right", "table", [
                "Recognize, Compare & Distinguish Sounds",
                "Recognize letters",
                "Distinguish Letters & Sounds",
                "Vocabulary, Grammar & Pronunciation",
                "Songs & Rhymes",
                "Reading",
                "Comprehension",
            ]),
            ("academic", "WRITING", "right", "table", [
                "Pencil grip",
                "Letter & Number Formation",
                "Neat handwriting",
            ]),
            ("traits", "PERSONAL", "left", "table", [
                "Takes care of material",
                "Displays flexibility",
                "Shows initiative & creativity",
                "Considerate of others",
                "Exhibits self-confidence",
                "Portrays Independence",
                "Participates in activities",
                "Food appetite",
                "Courtesies",
                "Cleanliness",
                "Responds well to correction",
            ]),
            ("traits", "WORK ETHIC", "right", "table", [
                "Attention to direction",
                "Works well independently",
                "Completes work timely",
            ]),
            ("traits", "PHYSICAL DEVELOPMENT", "right", "table", [
                "Kicking",
                "Jumping jacks",
                "Fine motor grip",
                "Swimming",
                "Catch and throw",
                "Balancing on one leg",
                "Running",
            ]),
        ],
    },
}


def seed_ecd_templates(Layout, Section, Item, reset=False):
    """Write TEMPLATES into the given models (real or migration-historical).

    Skips a class type that already has sections unless ``reset`` is set, so
    admin edits are never overwritten by accident. Returns the class types seeded.
    """
    seeded = []
    for class_type, spec in TEMPLATES.items():
        if Section.objects.filter(class_type=class_type).exists():
            if not reset:
                continue
            Section.objects.filter(class_type=class_type).delete()
        Layout.objects.filter(class_type=class_type).delete()

        overrides = spec.get("term_overrides", {})
        for term_number, layout in spec["layouts"].items():
            Layout.objects.create(class_type=class_type, term_number=term_number,
                                  **{**layout, **overrides.get(term_number, {})})

        for order, (part, title, column, style, items) in enumerate(spec["sections"]):
            section = Section.objects.create(
                class_type=class_type, part=part, title=title, column=column, style=style,
                counts_in_average=style != "marks", sort_order=order,
            )
            for i, item in enumerate(items):
                if isinstance(item, str):
                    item = (item,)
                label = item[0]
                terms = item[1] if len(item) > 1 else ALL
                allows_text = item[2] if len(item) > 2 else False
                Item.objects.create(
                    section=section, label=label, sort_order=i, allows_text=allows_text,
                    **{f"in_term_{n}": n in terms for n in ALL},
                )
        seeded.append(class_type)
    return seeded
