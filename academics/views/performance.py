import csv

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView

from academics import performance
from academics.models import AcademicYear, ExamTypeConfiguration, Term
from academics.utils import get_current_academic_year, get_current_term
from core.permissions import RoleRequiredMixin

VIEWS = ("help", "subjects")
# Who-needs-help list filters: which results to show.
SHOW = {
    "help": ("Below pass mark", performance.needs_help),
    "critical": ("Critical", lambda band: band == "critical"),
    "support": ("Requires support", lambda band: band == "support"),
    "all": ("All results", lambda band: True),
}
GROUPS = ("result", "learner")
PAGE_SIZES = (25, 50, 100)


class PerformanceReportView(RoleRequiredMixin, TemplateView):
    """Role-scoped performance report: who needs help, and how subjects
    perform across classes. See academics/performance.py.

    ``?format=print`` renders the same selection as a standalone A4 document
    (every row, both views) ready to print or save as PDF; ``?export=csv``
    downloads the results."""
    template_name = "academics/performance_report.html"
    print_template_name = "academics/performance_report_print.html"
    required_permission = "academics.view_examscore"

    def get_template_names(self):
        if self.request.GET.get("format") == "print":
            return [self.print_template_name]
        return [self.template_name]

    # -- filter resolution --------------------------------------------------

    def _year_and_terms(self, params):
        current_term = get_current_term()
        years = list(AcademicYear.objects.order_by("-name"))
        year = next((y for y in years if str(y.pk) == params.get("year")), None)
        if year is None:
            year = (current_term.academic_year if current_term else None) or get_current_academic_year()
        year_terms = list(Term.objects.filter(academic_year=year).order_by("start_date", "name")) if year else []

        term_param = params.get("term", "")
        if term_param == "all":
            selected = None
        else:
            selected = next((t for t in year_terms if str(t.pk) == term_param), None)
            if selected is None:
                # Default: the current term when it belongs to this year,
                # otherwise the whole year.
                selected = current_term if current_term in year_terms else None
        terms = [selected] if selected else year_terms
        return years, year, year_terms, selected, terms

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET
        years, year, year_terms, term, terms = self._year_and_terms(params)

        scope = performance.resolve_scope(self.request.user, terms)
        if scope is None:
            raise PermissionDenied("The performance report is for the Head of School, Heads of Department and teachers.")

        exam_types = list(ExamTypeConfiguration.objects.filter(is_active=True).order_by("display_order", "name"))
        exam_type = params.get("type", "")
        if exam_type not in {e.code for e in exam_types}:
            exam_type = ""

        all_results = performance.compute_results(scope, terms, year, exam_type or None) if terms else []

        class_options = scope.class_names
        subject_options = performance.subject_options(scope, all_results)
        class_name = params.get("class", "")
        if class_name not in class_options:
            class_name = ""
        subject = params.get("subject", "")
        if subject not in subject_options:
            subject = ""
        # A scope covering a single class or subject is fixed to it.
        class_locked = len(class_options) == 1
        subject_locked = len(subject_options) == 1
        if class_locked:
            class_name = class_options[0]
        if subject_locked:
            subject = subject_options[0]

        in_class = [r for r in all_results if not class_name or r["class_name"] == class_name]
        filtered = [r for r in in_class if not subject or r["subject"] == subject]

        if params.get("export") == "csv":
            self.csv_rows = sorted(filtered, key=lambda r: (r["mark"], r["name"], r["subject"]))
            return ctx

        view = params.get("view") if params.get("view") in VIEWS else "help"
        show = params.get("show") if params.get("show") in SHOW else "help"
        group = params.get("group") if params.get("group") in GROUPS else "result"
        query = params.get("q", "").strip()

        # View 1 — who needs help, worst first.
        rows = performance.learner_summary(filtered) if group == "learner" else filtered
        if query:
            q = query.lower()
            rows = [r for r in rows if q in r["name"].lower() or q in (r["admission_no"] or "").lower()]
        show_counts = {key: sum(1 for r in rows if test(r["band"])) for key, (_, test) in SHOW.items()}
        rows = sorted((r for r in rows if SHOW[show][1](r["band"])),
                      key=lambda r: (r["mark"], r["name"], r.get("subject", "")))
        try:
            page_size = int(params.get("page_size", PAGE_SIZES[0]))
        except ValueError:
            page_size = PAGE_SIZES[0]
        if page_size not in PAGE_SIZES:
            page_size = PAGE_SIZES[0]
        page_obj = Paginator(rows, page_size).get_page(params.get("page"))

        # Grade distribution, counting results (one per learner per subject)
        # or learners (each learner's average across the subjects in view).
        dist_by = params.get("dist") if params.get("dist") in GROUPS else "result"
        dist_rows = performance.learner_summary(filtered) if dist_by == "learner" else filtered

        # View 2 — one subject across classes (ignores the class filter, since
        # the point is to compare classes), every subject ranked within the
        # class filter, and the class x subject heatmap.
        ranking = performance.subject_ranking(in_class)
        ranked_names = [g["name"] for g in ranking]
        focus = subject or params.get("focus", "")
        if focus not in ranked_names:
            focus = ranking[-1]["name"] if ranking else ""  # weakest subject first
        scope_subject_rows = [r for r in all_results if not subject or r["subject"] == subject]

        type_label = next((e.name for e in exam_types if e.code == exam_type), "All assessed so far")
        terms_label = term.name if term else ("All terms" if year_terms else "No terms")
        ctx.update({
            "academics_tab": "performance",
            "scope": scope,
            "years": years,
            "selected_year": year,
            "year_terms": year_terms,
            "selected_term": term,
            "terms_label": terms_label,
            "exam_types": exam_types,
            "selected_type": exam_type,
            "selected_type_label": type_label,
            "class_options": class_options,
            "subject_options": subject_options,
            "selected_class": class_name,
            "selected_subject": subject,
            "class_locked": class_locked,
            "subject_locked": subject_locked,
            "filters_active": any(params.get(k) for k in ("class", "subject", "type"))
                              and not (class_locked and subject_locked),
            "view": view,
            "show": show,
            "show_label": SHOW[show][0],
            "show_options": [(key, label, show_counts[key]) for key, (label, _) in SHOW.items()],
            "group": group,
            "query": query,
            "summary": performance.summarise(filtered),
            "dist_by": dist_by,
            "grade_dist": performance.grade_distribution(dist_rows),
            "page_obj": page_obj,
            "help_rows": rows,
            "help_total": len(rows),
            "page_sizes": ",".join(str(s) for s in PAGE_SIZES),
            "focus_subject": focus,
            "focus_choices": sorted(ranked_names),
            "by_class": performance.subject_by_class(scope_subject_rows, focus, scope) if focus else [],
            "ranking": ranking,
            "matrix": performance.class_subject_matrix(
                [r for r in all_results if (not subject or r["subject"] == subject)], scope),
            "pass_mark": int(performance.PASS_MARK),
            "critical_mark": int(performance.CRITICAL_MARK),
            "strong_mark": int(performance.STRONG_MARK),
            "generated_at": timezone.localtime(),
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if hasattr(self, "csv_rows"):
            return self._csv(self.csv_rows)
        return super().render_to_response(context, **kwargs)

    def _csv(self, rows):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="performance-report.csv"'
        writer = csv.writer(response)
        writer.writerow(["Student", "Admission no", "Class", "Subject", "Mark (%)", "Grade", "Status", "Assessments"])
        for r in rows:
            writer.writerow([r["name"], r["admission_no"], r["class_name"], r["subject"],
                             r["mark"], r["grade"], r["status"], r["assessments"]])
        return response
