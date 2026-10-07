from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone

from students.views import StudentDetailView


class LearnerReportView(StudentDetailView):
    """Official Learner Progress Report for one term: approved results per
    subject and assessment, class average and position, attendance for the
    term and progress across terms. Same access rules as the student profile.

    Opens ready to print (``autoprint=1``); ``format=pdf`` downloads a PDF."""
    template_name = "academics/learner_report_print.html"

    def get_context_data(self, **kwargs):
        from academics import performance
        from academics.grading_utils import get_grade_label
        from attendance import analytics

        ctx = super(StudentDetailView, self).get_context_data(**kwargs)
        student = self.object
        terms = performance.learner_terms(student)
        term_param = self.request.GET.get("term", "")
        term = next((t for t in terms if str(t.pk) == term_param), None) \
            or performance.default_learner_term(student, terms)
        record = performance.learner_record(student, term)
        start, end = analytics.term_range(term)
        attendance = analytics.learner_attendance(student, start, end)
        class_name = record.get("class_name") or student.class_name
        ctx.update({
            "term": term,
            "record": record,
            "grade_label": get_grade_label(record["grade"]) if record["grade"] else "",
            "attendance": attendance,
            "class_name": class_name,
            "class_teacher": analytics.class_teachers().get(class_name, ""),
            "pass_mark": int(performance.PASS_MARK),
            "generated_at": timezone.localtime(),
            "is_pdf": self.request.GET.get("format") == "pdf",
            "scale": [("A+", "90–100", "Outstanding"), ("A", "80–89", "High"), ("B", "70–79", "Good"),
                      ("C", "60–69", "Aspiring"), ("D", "50–59", "Basic"), ("E", "0–49", "Needs Improvement")],
        })
        return ctx

    def render_to_response(self, context, **kwargs):
        if not context["is_pdf"]:
            return super().render_to_response(context, **kwargs)
        html = render_to_string(self.template_name, context, request=self.request)
        try:
            from weasyprint import HTML
            pdf = HTML(string=html, base_url=self.request.build_absolute_uri("/")).write_pdf()
        except (ImportError, OSError):
            # No PDF engine on this server: open the document with the print
            # dialog, where "Save as PDF" gives the same file.
            return super().render_to_response({**context, "is_pdf": False, "print_now": True}, **kwargs)
        term = context["term"]
        name = f"progress-report-{self.object.admission_no}-{term.name if term else 'term'}".replace(" ", "-")
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return resp
