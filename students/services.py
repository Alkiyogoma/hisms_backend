import re
from datetime import datetime

from django.db.models import Max

from students.models import Student


def generate_admission_number(year: int | None = None) -> str:
    """FRD format: ADM-[YEAR]-[3-digit sequence], e.g. ADM-2026-001."""
    year = year or datetime.now().year
    pattern = rf"^ADM-{year}-(\d{{3}})$"

    max_seq = 0
    for admission_no in Student.objects.filter(admission_no__startswith=f"ADM-{year}-").values_list(
        "admission_no", flat=True
    ):
        m = re.match(pattern, admission_no)
        if m:
            max_seq = max(max_seq, int(m.group(1)))

    return f"ADM-{year}-{max_seq + 1:03d}"
