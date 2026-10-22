"""
The filter bar shared by the progress-marking pages (ECD evaluation, score
entry, term remarks, grade approval and Checkpoint): academic year, term,
class, subject and assessment type, the same bar as the performance report.

Each page resolves the filters in order (year and term first, then class,
subject and type, since each list depends on the one before) and renders
``academics/_progress_filters.html`` with ``ctx["pf"]``. A filter that does
not apply to a page (no assessment types for term remarks, say) is given a
note and left out of the bar.

Query parameters: ``year``, ``term``, ``class_name``, ``subject``, ``type``.
An optional "Entry" select switches between sibling pages (score entry and
term remarks), keeping the year, term and class.
"""
from django.utils import timezone

from academics.models import AcademicYear, ExamTypeConfiguration, Term
from academics.utils import get_current_academic_year, get_current_term


class ProgressFilters:
    def __init__(self, request, *, all_terms=False):
        self.params = request.GET
        current_term = get_current_term()
        self.years = list(AcademicYear.objects.order_by("-name"))
        year = next((y for y in self.years if str(y.pk) == self.params.get("year")), None)
        term_param = self.params.get("term") or self.params.get("term_id") or ""
        if year is None and term_param.isdigit():
            # A link that names only the term (say, back from a save) opens its year.
            linked = Term.objects.filter(pk=term_param).first()
            year = linked.academic_year if linked else None
        if year is None:
            year = (current_term.academic_year if current_term else None) or get_current_academic_year()
        self.year = year
        self.year_terms = list(Term.objects.filter(academic_year=year).order_by("start_date", "name")) if year else []

        self.all_terms = all_terms
        term = next((t for t in self.year_terms if str(t.pk) == term_param), None)
        if term is None and not (all_terms and term_param == "all"):
            # Default: the current term when it belongs to this year, else
            # the year's latest term that has started (or its first term).
            if current_term in self.year_terms:
                term = current_term
            elif self.year_terms and not all_terms:
                today = timezone.localdate()
                started = [t for t in self.year_terms if t.start_date and t.start_date <= today]
                term = started[-1] if started else self.year_terms[0]
        self.term = term

        self.class_options, self.class_name, self.class_all, self.class_labels = [], "", False, {}
        self.subject_options, self.subject, self.subject_note = [], "", ""
        self.exam_types, self.exam_type, self.type_note = [], "", ""
        self.class_note = ""

    @property
    def terms(self):
        """Terms in view: the selected one, or the whole year for "All terms"."""
        return [self.term] if self.term else (self.year_terms if self.all_terms else [])

    def pick_class(self, options, *, allow_all=False, labels=None, param="class_name"):
        """Choose from ``options``; without ``allow_all`` the first is the default."""
        self.class_options = list(options)
        self.class_all = allow_all
        self.class_labels = labels or {}
        value = self.params.get(param, "").strip()
        if value not in self.class_options:
            value = "" if allow_all or not self.class_options else self.class_options[0]
        self.class_name = value
        return value

    def pick_subject(self, options, *, note=""):
        """Choose a subject; "" means all subjects. ``note`` disables the filter."""
        self.subject_note = note
        if note:
            return ""
        self.subject_options = sorted(options)
        value = (self.params.get("subject") or self.params.get("subject_name") or "").strip()
        self.subject = value if value in self.subject_options else ""
        return self.subject

    def pick_type(self, *, note=""):
        """Choose an assessment type code; "" means all assessed so far."""
        self.type_note = note
        if note:
            return ""
        self.exam_types = list(ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order", "name"))
        value = self.params.get("type", "")
        self.exam_type = value if value in {e.code for e in self.exam_types} else ""
        return self.exam_type

    @property
    def selected_types(self):
        return [e for e in self.exam_types if not self.exam_type or e.code == self.exam_type]

    def context(self, *, footnote="", hidden=None, views=None):
        """Context for the filter bar partial. ``hidden`` adds hidden inputs;
        ``views`` is [(url, label, active)] for sibling pages (score entry and
        term remarks), picked like a filter."""
        return {
            "years": self.years,
            "year": self.year,
            "year_terms": self.year_terms,
            "term": self.term,
            "all_terms": self.all_terms,
            "class_options": [(c, self.class_labels.get(c, c)) for c in self.class_options],
            "class_name": self.class_name,
            "class_all": self.class_all,
            "class_note": self.class_note,
            "subject_options": self.subject_options,
            "subject": self.subject,
            "subject_note": self.subject_note,
            "exam_types": self.exam_types,
            "exam_type": self.exam_type,
            "type_note": self.type_note,
            "footnote": footnote,
            "hidden": hidden or {},
            "views": views or [],
            "is_current_term": bool(self.term) and self.term == get_current_term(),
        }
