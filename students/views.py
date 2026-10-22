import logging
import re
import time
from datetime import date
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from core.permissions import RoleRequiredMixin
from users.models import UserRole

logger = logging.getLogger(__name__)

from attendance.models import PRESENT_STATUSES
from .models import GuardianRelationship, ParentGuardian, Student, StudentGuardian, StudentStatus, PDPAConsentLog
from .forms import StudentCreateForm, StudentEditForm, StudentPhotoUploadForm
from .services import describe_delivery, generate_admission_number, grant_portal_access

# HOD academic scope derived from role (more reliable than staff_profile.department,
# which may reflect an administrative department). staff_profile.department is a fallback.
HOD_ROLE_DEPARTMENT = {
    UserRole.ECD_HOD: "ECD",
    UserRole.PRIMARY_HOD: "PRIMARY",
    UserRole.LOWER_SECONDARY_HOD: "LOWER_SECONDARY",
}


def _hod_departments(user):
    """List of department keys an HOD is scoped to.

    Source of truth is RoleConfig.departments (DB / UI-configurable per role), so an
    admin can override a role's departments live via the Role Management UI. Falls back
    to the role's default department, then to staff_profile.department.
    """
    if user.section_departments:
        return list(user.section_departments)  # all HOD roles held (incl. additional)
    sp_dept = getattr(getattr(user, "staff_profile", None), "department", "")
    return [sp_dept] if sp_dept else []


def notify_sibling_confirmation(request, student: Student, guardian: ParentGuardian) -> None:
    """
    FR-STU-005: Notify all Finance Officers when a sibling relationship is confirmed
    and log the sibling link to the audit trail.
    """
    other_sibs = Student.objects.filter(
        studentguardian__guardian=guardian
    ).exclude(pk=student.pk)
    if not other_sibs.exists():
        return

    # Ensure sibling_discount_eligible is set (signal may already handle this)
    student.sibling_discount_eligible = True
    student.save(update_fields=["sibling_discount_eligible"])

    from communications.email_service import dispatch_notification
    from users.models import User, UserRole
    from audit.models import log_event

    finance_officers = User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True)
    sib_names = ", ".join([f"{s.first_name} {s.last_name} ({s.class_name})" for s in other_sibs])
    msg = (
        f"Sibling link confirmed for {student.first_name} {student.last_name} "
        f"({student.admission_no}). Siblings: {sib_names}. "
        "Review fee structure for sibling discount eligibility."
    )
    for fo in finance_officers:
        dispatch_notification(
            user=fo,
            title="Sibling Link Confirmed",
            message=msg,
            link=f"/finance/invoices/?q={student.admission_no}",
            actor=request.user
        )

    # Log to audit trail
    for sib in other_sibs:
        log_event(
            actor=request.user,
            action_type="SIBLING_LINKED",
            model_name="Student",
            object_id=student.pk,
            description=f"Student {student.admission_no} linked as sibling to {sib.admission_no}",
            request=request,
        )


class StudentListView(RoleRequiredMixin, ListView):
    template_name = "students/list.html"
    context_object_name = "students"
    paginate_by = 30
    required_permission = "students.view_student"

    def get_queryset(self):
        from core.teacher_context import get_teacher_assigned_classes

        include_archived = self.request.GET.get("include_archived", "") == "1"
        archive_status = self.request.GET.get("archive_status", "all" if include_archived else "active")
        qs = Student.objects.all()
        if archive_status == "archived":
            qs = qs.filter(is_archived=True)
        elif archive_status != "all":
            qs = qs.filter(is_archived=False)
        if self.request.user.role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(self.request.user)
            if my_classes:
                qs = qs.filter(class_name__in=my_classes)
            else:
                qs = qs.none()

        # FR-STU-001 / NFR-SEC-004: HODs only see students in their department(s)
        hod_roles = {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}
        if self.request.user.has_role(*hod_roles) and not self.request.user.is_school_wide:
            my_depts = _hod_departments(self.request.user)
            if my_depts:
                from academics.models import GradeClass
                dept_class_names = GradeClass.objects.filter(
                    department__in=my_depts
                ).values_list("name", flat=True)
                qs = qs.filter(class_name__in=dept_class_names)

        qs = qs.order_by("class_name", "last_name")
        q = self.request.GET.get("q", "").strip()
        cls = self.request.GET.get("class_name", "").strip()
        status = self.request.GET.get("status", "").strip()
        gender = self.request.GET.get("gender", "").strip()
        dept = self.request.GET.get("department", "").strip()
        ay = self.request.GET.get("academic_year", "").strip()

        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(admission_no__icontains=q)
            )
        if cls:
            qs = qs.filter(class_name=cls)
        if status:
            qs = qs.filter(status=status)
        if gender:
            qs = qs.filter(gender=gender)
        if ay and ay.isdigit():
            qs = qs.filter(academic_year_id=ay)
        
        if dept:
            from academics.models import GradeClass, Department
            classes_in_dept = GradeClass.objects.filter(department__iexact=dept).values_list("name", flat=True)
            qs = qs.filter(class_name__in=classes_in_dept)

        # Prefetch academic_year for display
        qs = qs.select_related("academic_year")

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx["archive_status"] = self.request.GET.get(
            "archive_status", "all" if self.request.GET.get("include_archived") == "1" else "active",
        )
        
        from academics.models import Department, GradeClass
        # Scope filter dropdowns to the user's allowed departments (DB/UI-configurable)
        hod_roles = {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}
        allowed_depts = (_hod_departments(self.request.user)
                         if self.request.user.has_role(*hod_roles) and not self.request.user.is_school_wide else None)
        ctx["departments"] = (
            [(k, v) for (k, v) in Department.choices if k in allowed_depts]
            if allowed_depts else Department.choices
        )
        if allowed_depts:
            ctx["classes"] = list(
                GradeClass.objects.filter(department__in=allowed_depts)
                .values_list("name", flat=True).distinct().order_by("name")
            )
        else:
            ctx["classes"] = list(
                GradeClass.objects.values_list("name", flat=True).distinct().order_by("name")
            )
        ctx["statuses"] = StudentStatus.choices
        ctx["genders"] = [("male", "Male"), ("female", "Female"), ("other", "Other")]
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["total_students"] = qs.count()
        from academics.models import AcademicYear
        ctx["academic_years"] = AcademicYear.objects.all().order_by("-is_current", "-name")
        return ctx


class StudentDetailView(RoleRequiredMixin, DetailView):
    template_name = "students/detail.html"
    model = Student
    context_object_name = "student"
    required_permission = "students.view_student"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Dynamic permission-based scoping: teacher sees only own-class students
        from users.role_models import RoleConfig
        user = self.request.user
        if user.has_perm("students.view_student"):
            rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
            depts = rc.departments if rc and rc.departments else []
            is_teacher_role = not depts and user.role not in (
                UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
                UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,
            )
            if is_teacher_role or user.role == UserRole.TEACHER:
                from core.teacher_context import get_teacher_assigned_classes
                my_classes = get_teacher_assigned_classes(user)
                if my_classes and obj.class_name not in my_classes:
                    from django.http import Http404
                    raise Http404("Student not found in your assigned classes.")
                elif not my_classes:
                    from django.http import Http404
                    raise Http404("No classes assigned to you.")
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = self.object
        
        # Discovery: Find siblings via shared guardians
        guardian_ids = student.studentguardian_set.values_list("guardian_id", flat=True)
        siblings = Student.objects.filter(
            studentguardian__guardian_id__in=guardian_ids,
            is_archived=False
        ).exclude(id=student.id).distinct()
        
        # Welfare notes  same visibility rules as the welfare module:
        # concerns/health only for class teacher, HOD and leadership;
        # safeguarding never on the profile.
        from welfare.models import WelfareObservation, WelfareNoteType
        from welfare.visibility import visible_observations
        welfare_incidents = visible_observations(
            self.request.user,
            WelfareObservation.objects.filter(student=student).exclude(note_type=WelfareNoteType.SAFEGUARDING),
        ).select_related('submitted_by', 'reviewed_by').order_by('-observation_date')
        
        # Discipline/behaviour incidents
        from discipline.models import DisciplineIncident
        discipline_incidents = DisciplineIncident.objects.filter(
            student=student
        ).select_related('reported_by', 'reviewed_by').order_by('-created_at')
        
        # Attendance data for graphs
        recent_qs = student.attendance_entries.order_by('-date')[:30]  # Last 30 days
        attendance_data = list(recent_qs)
        attendance_stats = {
            'total': len(attendance_data),
            'present': sum(1 for a in attendance_data if a.status in PRESENT_STATUSES),  # includes late
            'absent': sum(1 for a in attendance_data if a.status == 'absent'),
            'late': sum(1 for a in attendance_data if a.status == 'late'),
        }
        attendance_stats['attendance_rate'] = (attendance_stats['present'] / attendance_stats['total'] * 100) if attendance_stats['total'] > 0 else 0
        
        # Real Academic Performance (no mock data)
        from academics.models import ReportCard, ExamScore, ReportCardStatus, ECDEvaluation, GradeClass, Department
        from academics.ecd_utils import ecd_template_type_from_class_name
        from academics.grading_utils import get_grade_from_score
        from academics.models import get_exam_weights
        
        # First try to find most recent report of any status, preferring published
        recent_report = ReportCard.objects.filter(
            student=student
        ).order_by('-status', '-term__end_date').first()
        
        # If no published report, find any report for this student
        if not recent_report or recent_report.status != ReportCardStatus.PUBLISHED:
            # Look for any report in the current or most recent term
            latest_term_report = ReportCard.objects.filter(
                student=student
            ).select_related('term').order_by('-term__end_date').first()
            if latest_term_report:
                recent_report = latest_term_report
        
        # Fetch all reports for this student (any status), max 10
        all_reports = ReportCard.objects.filter(
            student=student
        ).select_related('term', 'term__academic_year').order_by('-term__end_date')[:10]
        
        # Robust ECD detection using ecd_utils
        ecd_type = ecd_template_type_from_class_name(student.class_name)
        is_ecd = ecd_type is not None
        if not is_ecd:
            # Fallback: check GradeClass model
            gc = GradeClass.objects.filter(name=student.class_name).first()
            if gc and gc.department == Department.ECD:
                is_ecd = True
                ecd_type = ecd_template_type_from_class_name(gc.name)

        # Compute real weighted average from exam scores if no report with average exists
        computed_avg = None
        computed_term_name = None
        if recent_report and recent_report.overall_average:
            computed_avg = float(recent_report.overall_average)
            computed_term_name = recent_report.term.name
        else:
            # Calculate from exam scores directly
            from academics.utils import get_current_term
            from django.db.models import Sum, F, Case, When, Value, FloatField, ExpressionWrapper
            active_term = get_current_term()
            if active_term:
                scores = ExamScore.objects.filter(student=student, term=active_term)
                if scores.exists():
                    weights = get_exam_weights()
                    w_scores = scores.annotate(
                        weight_val=Case(
                            *[When(exam_type=code, then=Value(w/100.0)) for code, w in weights.items()],
                            default=Value(0.0),
                            output_field=FloatField()
                        )
                    ).annotate(
                        weighted_val=ExpressionWrapper(F('score') * F('weight_val'), output_field=FloatField())
                    )
                    student_totals = w_scores.values("student").annotate(
                        total_w=Sum('weighted_val'),
                        sum_w=Sum('weight_val')
                    )
                    for s in student_totals:
                        if s['sum_w'] > 0:
                            computed_avg = round(float(s['total_w'] / s['sum_w']), 1)
                            computed_term_name = active_term.name
                            break

        academic_summary = {
            'has_report': recent_report is not None,
            'term_name': computed_term_name or (recent_report.term.name if recent_report else "Current Term"),
            'is_ecd': is_ecd,
            'ecd_type': ecd_type or '',
            'average': computed_avg if computed_avg is not None else (float(recent_report.overall_average) if recent_report and recent_report.overall_average else 0),
            'subjects_count': 0,
            'teacher_comment': recent_report.teacher_comments if recent_report else "",
        }

        if academic_summary['is_ecd']:
            # ECD logic: Average of ratings (E=4, G=3, S=2, N=1)
            evals = ECDEvaluation.objects.filter(report_card=recent_report) if recent_report else []
            rating_map = {'E': 4, 'G': 3, 'S': 2, 'N': 1}
            total_rating = 0
            count = 0
            for ev in evals:
                val = rating_map.get(ev.rating, 0)
                if val > 0:
                    total_rating += val
                    count += 1
            
            academic_summary['average_rating'] = round(total_rating / count, 1) if count > 0 else 0
            academic_summary['max_rating'] = 4
            academic_summary['eval_count'] = count
            
            # Map average rating back to a letter and label
            if academic_summary['average_rating'] >= 3.5:
                academic_summary['rating_letter'] = 'E'
                academic_summary['grade'] = 'Outstanding'
            elif academic_summary['average_rating'] >= 2.5:
                academic_summary['rating_letter'] = 'G'
                academic_summary['grade'] = 'Good'
            elif academic_summary['average_rating'] >= 1.5:
                academic_summary['rating_letter'] = 'S'
                academic_summary['grade'] = 'Satisfactory'
            elif academic_summary['average_rating'] > 0:
                academic_summary['rating_letter'] = 'N'
                academic_summary['grade'] = 'Needs Improvement'
            else:
                academic_summary['rating_letter'] = ''
                academic_summary['grade'] = '--'
        else:
            # Primary/Secondary logic
            academic_summary['subjects_count'] = ExamScore.objects.filter(student=student, term=recent_report.term).values('subject_name').distinct().count() if recent_report else 0
            
            # Determine grade and trend
            if academic_summary['average'] and academic_summary['average'] > 0:
                academic_summary['grade'] = get_grade_from_score(academic_summary['average'])
                
                # Simple trend analysis
                previous_report = ReportCard.objects.filter(
                    student=student, 
                    status=ReportCardStatus.PUBLISHED
                ).order_by('-term__end_date')[1:2].first()
                
                if previous_report and previous_report.overall_average:
                    diff = academic_summary['average'] - float(previous_report.overall_average)
                    if diff > 1: academic_summary['trend'] = 'improving'
                    elif diff < -1: academic_summary['trend'] = 'declining'
                    else: academic_summary['trend'] = 'stable'
                else:
                    academic_summary['trend'] = 'new'
            else:
                academic_summary['grade'] = '--'
                academic_summary['trend'] = '--'

        # Approved marks only, for the selected term (default: the current term,
        # else the latest term with marks)  the same figures as the
        # Performance Report. ECD is rating-based and keeps its own summary.
        from academics import performance
        from attendance import analytics
        record_terms = performance.learner_terms(student)
        term_param = self.request.GET.get("term", "")
        record_term = next((t for t in record_terms if str(t.pk) == term_param), None) \
            or performance.default_learner_term(student, record_terms)
        record = None if is_ecd else performance.learner_record(student, record_term)
        if record and record["average"] is not None:
            academic_summary.update({
                "average": record["average"], "grade": record["grade"], "term_name": record_term.name,
                "subjects_count": len(record["subjects"]), "trend": record["trend"] or "new",
                "has_report": True,
            })
        elif not is_ecd:
            academic_summary.update({"average": None, "grade": "--", "trend": "--", "subjects_count": 0,
                                     "term_name": record_term.name if record_term else "Current term"})
        att_start, att_end = analytics.term_range(record_term)
        attendance = analytics.learner_attendance(student, att_start, att_end)
        # Cards are shown only when they have something to show.
        perf_has_att = bool(attendance["row"] and attendance["row"]["marked"])
        perf_has_acad = bool(record and record["average"] is not None) or bool(is_ecd and academic_summary.get("eval_count"))

        # Financial information  visible to roles with finance view permission
        financial_info = {}
        if self.request.user.has_perm("finance.view_invoice"):
            from finance.models import Invoice, Payment
            from django.db.models import Sum
            
            # NFR-PDPA-006: Log access to student financial data
            from audit.view_audit import log_sensitive_access
            log_sensitive_access(
                self.request, "Student", student.pk, "finance",
                description=f"Student financial data viewed for {student.first_name} {student.last_name} ({student.admission_no})"
            )
            
            # Total Billed (student-linked invoices)
            total_billed = Invoice.objects.filter(student=student).aggregate(t=Sum("total_due"))["t"] or 0
            # Admission fee (applicant-linked invoices)
            from admissions.models import Applicant
            admission_app = Applicant.objects.filter(enrolled_student=student).first()
            admission_billed = Invoice.objects.filter(applicant=admission_app).aggregate(t=Sum("total_due"))["t"] if admission_app else 0
            # Total Paid
            total_paid = Payment.objects.filter(invoice__student=student).aggregate(t=Sum("amount"))["t"] or 0
            admission_paid = Payment.objects.filter(invoice__applicant=admission_app).aggregate(t=Sum("amount"))["t"] if admission_app else 0
            
            balance = total_billed - total_paid
            admission_balance = (admission_billed or 0) - (admission_paid or 0)
            
            # Last Payment
            last_pay = Payment.objects.filter(
                Q(invoice__student=student) | Q(invoice__applicant=admission_app) if admission_app else Q(invoice__student=student),
                is_reversal=False
            ).order_by("-created_at").first()
            
            # All invoices for this student + applicant
            invoice_qs = Invoice.objects.filter(Q(student=student) | Q(applicant=admission_app) if admission_app else Q(student=student)).select_related("period", "term").order_by("-created_at")
            invoices_list = []
            for inv in invoice_qs[:10]:
                inv_paid = Payment.objects.filter(invoice=inv, is_reversal=False).aggregate(t=Sum("amount"))["t"] or 0
                invoices_list.append({
                    "number": inv.invoice_number,
                    "amount": inv.total_due,
                    "paid": inv_paid,
                    "balance": inv.total_due - inv_paid,
                    "status": inv.status,
                    "due_date": inv.due_date,
                    "is_admission": bool(inv.applicant_id),
                })
            
            financial_info = {
                'fees_status': 'Overdue' if balance > 0 else 'Current',
                'balance': balance,
                'total_billed': total_billed,
                'total_paid': total_paid,
                'last_payment': f"TZS {last_pay.amount:,.0f}" if last_pay else "No payments recorded",
                'payment_method': last_pay.get_method_display() if last_pay and hasattr(last_pay, 'get_method_display') else (last_pay.method if last_pay else ""),
                'admission_balance': admission_balance,
                'admission_billed': admission_billed or 0,
                'invoices': invoices_list,
                'student_pk': student.pk,
            }
            financial_info['has_data'] = bool(total_billed or admission_billed or invoices_list or last_pay)
        
        # Evaluate stats BEFORE slicing the queryset  single aggregated query
        welfare_stats = welfare_incidents.aggregate(
            total=Count('id'),
            positive=Count('id', filter=Q(note_type=WelfareNoteType.POSITIVE)),
            observation=Count('id', filter=Q(note_type=WelfareNoteType.OBSERVATION)),
            concern=Count('id', filter=Q(note_type=WelfareNoteType.CONCERN)),
        )

        # Computed age from date_of_birth
        from datetime import date as date_cls
        student_age = None
        student_age_remaining_months = None
        if student.date_of_birth:
            today = date_cls.today()
            student_age = today.year - student.date_of_birth.year - ((today.month, today.day) < (student.date_of_birth.month, student.date_of_birth.day))
            month_diff = today.month - student.date_of_birth.month
            if today.day < student.date_of_birth.day:
                month_diff -= 1
            student_age_remaining_months = month_diff % 12

        # Years enrolled
        years_enrolled = None
        if student.enrolment_date:
            delta = date_cls.today() - student.enrolment_date
            years_enrolled = round(delta.days / 365.25, 1)

        # NFR-PDPA-006: Log access to sensitive data
        from audit.view_audit import log_sensitive_access
        if student.allergies_medical or student.blood_type:
            log_sensitive_access(
                self.request, "Student", student.pk, "medical",
                description=f"Medical data viewed for {student.first_name} {student.last_name} ({student.admission_no})"
            )

        # ECD class names for template department-aware links
        ecd_class_names = list(GradeClass.objects.filter(
            department=Department.ECD
        ).values_list("name", flat=True))

        # Admissions documents  visible to users with admissions permission
        admission_docs = []
        if self.request.user.has_perm("admissions.view_applicant"):
            from admissions.models import Applicant
            admission_app = Applicant.objects.filter(enrolled_student=student).first()
            if admission_app:
                admission_docs = list(admission_app.documents.select_related("received_by").order_by("document_type"))

        ctx.update({
            "student_age": student_age,
            "student_age_remaining_months": student_age_remaining_months,
            "years_enrolled": years_enrolled,
            "guardians": student.studentguardian_set.select_related("guardian"),
            "attendance_recent": attendance_data[:10],
            "attendance_stats": attendance_stats,
            "attendance_data": attendance_data,
            "welfare_incidents": list(welfare_incidents[:10]),
            "welfare_stats": welfare_stats,
            "can_view_welfare": self.request.user.has_perm("welfare.view_welfareobservation")
                or self.request.user.has_perm("welfare.add_welfareobservation"),
            "discipline_incidents": list(discipline_incidents[:5]),
            "discipline_stats": discipline_incidents.aggregate(
                total=Count('id'),
                open=Count('id', filter=Q(status='pending_review') | Q(status='under_investigation')),
                resolved=Count('id', filter=Q(status='resolved')),
                critical=Count('id', filter=Q(severity='critical')),
            ),
            "academic_summary": academic_summary,
            "record": record,
            "record_term": record_term,
            "record_terms": record_terms + ([record_term] if record_term and record_term not in record_terms else []),
            "attendance": attendance,
            "perf_has_att": perf_has_att,
            "perf_has_acad": perf_has_acad,
            "siblings": siblings,
            "sidebar_has_cards": bool(perf_has_acad or all_reports or perf_has_att or siblings.exists()
                                      or financial_info.get("has_data")),
            "att_start": att_start,
            "att_end": att_end,
            "recent_report": recent_report,
            "all_reports": all_reports,
            "financial_info": financial_info,
            "admission_docs": admission_docs,
            "doc_stats": self._get_doc_stats(student),
            "today_date": date.today().strftime("%d %B %Y"),
            "id_standard_compliant": bool(re.match(r"^ADM-\d{4}-\d{3}$", student.admission_no or "")),
            "ecd_class_names": ecd_class_names,
        })
        
        return ctx

    def _get_doc_stats(self, student):
        from students.models import StudentDocument
        docs = student.documents.all()
        by_type = {t: 0 for t, _ in StudentDocument.DOCUMENT_TYPES}
        for doc in docs:
            by_type[doc.document_type] = by_type.get(doc.document_type, 0) + 1
        required = ["birth_certificate", "medical_record", "transfer_slip", "report_card"]
        uploaded_required = [t for t in required if by_type.get(t, 0) > 0]
        missing_required = [t for t in required if by_type.get(t, 0) == 0]
        return {
            "total": docs.count(),
            "by_type": by_type,
            "required_count": len(required),
            "uploaded_required_count": len(uploaded_required),
            "missing_required": missing_required,
        }


class StudentPhotoUploadView(RoleRequiredMixin, View):
    """Handle student photo uploads"""
    required_permission = "students.change_student"
    
    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        form = StudentPhotoUploadForm(request.POST, request.FILES, instance=student)
        
        if form.is_valid():
            form.save()
            messages.success(request, f"Photo uploaded successfully for {student.first_name} {student.last_name}")
        else:
            # Surface the actual validation errors instead of a generic message
            for field, errors in form.errors.items():
                for err in errors:
                    messages.error(request, f"Photo upload failed: {err}")
        
        return redirect("students:detail", pk=pk)


class StudentCreateView(RoleRequiredMixin, TemplateView):
    """Direct student creation is disabled. Students must be enrolled via the
    Admissions Pipeline or Bulk Import. A hard block is enforced on the UI
    create path (DEF-7 / 14.1): a POST without a linked guardian AND recorded
    PDPA consent is rejected outright instead of silently redirecting."""
    template_name = "students/form.html"
    required_permission = "students.add_student"

    def _blocked_response(self, message):
        from django.http import HttpResponse
        return HttpResponse(
            "<div class='msg-toast msg-error' style='padding:16px;margin:24px;font-size:13px'>"
            + message
            + "</div><p style='margin:0 24px;font-size:12px;color:var(--text-muted)'>"
            + "Enrol students via the Admissions Pipeline.</p>",
            status=403,
        )

    def get(self, request, *args, **kwargs):
        # Authorised staff enrol directly (transfer, staff child, ...);
        # everyone else starts an inquiry in the admissions pipeline.
        if request.user.has_perm("students.add_student") or request.user.role == UserRole.SUPER_ADMIN:
            return redirect("admissions:direct_enrol")
        return redirect("admissions:new_inquiry")

    def post(self, request, *args, **kwargs):
        # FR-PDPA-001 / DEF-7: a student record may not be created without
        # PDPA consent and at least one linked guardian.
        pdpa_consent_given = request.POST.get("pdpa_consent_given") == "on"
        guardian_name = (
            request.POST.get("guardian_full_name")
            or request.POST.get("parent_full_name")
            or request.POST.get("guardian_name")
            or ""
        ).strip()
        guardian_phone = (
            request.POST.get("guardian_phone")
            or request.POST.get("parent_phone")
            or request.POST.get("phone")
            or ""
        ).strip()

        if not pdpa_consent_given:
            return self._blocked_response(
                "<b>Blocked:</b> PDPA consent must be recorded before a student can be created."
            )
        if not (guardian_name and guardian_phone):
            return self._blocked_response(
                "<b>Blocked:</b> at least one parent (name and phone) must be linked "
                "before a student can be created."
            )
        return redirect("admissions:new_inquiry")

class StudentEditView(RoleRequiredMixin, TemplateView):
    template_name = "students/form.html"
    required_permission = "students.change_student"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        student = get_object_or_404(Student, pk=kwargs["pk"])
        ctx["student"] = student
        ctx["form_title"] = "Edit Student"
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        ctx["form"] = kwargs.get("form") or StudentEditForm(instance=student)

        # Linked guardians
        ctx["linked_guardians"] = StudentGuardian.objects.filter(
            student=student
        ).select_related("guardian").order_by("-is_primary", "guardian__full_name")

        # Siblings (students sharing at least one guardian)
        my_guardian_ids = StudentGuardian.objects.filter(
            student=student
        ).values_list("guardian_id", flat=True)
        if my_guardian_ids:
            sibling_ids = StudentGuardian.objects.filter(
                guardian_id__in=my_guardian_ids
            ).exclude(student=student).values_list("student_id", flat=True).distinct()
            ctx["siblings"] = Student.objects.filter(id__in=sibling_ids).order_by("first_name")
        else:
            ctx["siblings"] = Student.objects.none()

        # Build merged document list (onboarding + uploaded)
        from admissions.models import ApplicantDocumentReceipt
        from students.models import StudentDocument

        all_docs = []
        applicant = getattr(student, "from_applicant", None)
        if applicant:
            for od in ApplicantDocumentReceipt.objects.filter(applicant=applicant).order_by("document_type"):
                all_docs.append({
                    "id": f"onb_{od.pk}",
                    "title": od.get_document_type_display(),
                    "doc_type_display": od.get_document_type_display(),
                    "file": od.file if hasattr(od, "file") and od.file else None,
                    "date": od.received_at,
                    "is_received": od.is_received,
                    "source": "onboarding",
                    "pk": od.pk,
                })
        for sd in StudentDocument.objects.filter(student=student).order_by("-uploaded_at"):
            all_docs.append({
                "id": f"stu_{sd.pk}",
                "title": sd.title,
                "doc_type_display": sd.get_document_type_display(),
                "file": sd.file,
                "date": sd.uploaded_at,
                "is_received": True,
                "source": "uploaded",
                "pk": sd.pk,
            })

        ctx["all_documents"] = all_docs

        # Admission pipeline data from linked applicant
        if applicant:
            ctx["admission_applicant"] = applicant

        return ctx

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        # FR-STU-001: Editing archived records requires superuser access
        if student.is_archived and not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Archived student records can only be edited by Super Admin.")
        # Handle document upload (separate form in sidebar)
        if "upload_document" in request.POST:
            from students.models import StudentDocument
            doc_file = request.FILES.get("doc_file")
            doc_title = request.POST.get("doc_title", "").strip()
            doc_type = request.POST.get("doc_type", "other")
            if doc_file and doc_title:
                StudentDocument.objects.create(
                    student=student,
                    title=doc_title,
                    document_type=doc_type,
                    file=doc_file,
                    uploaded_by=request.user,
                )
                from audit.models import log_event
                log_event(
                    actor=request.user, action_type="CREATE", model_name="StudentDocument",
                    object_id=student.pk,
                    description=f"Document '{doc_title}' uploaded for {student.admission_no}",
                    request=request,
                )
                messages.success(request, f"Document '{doc_title}' uploaded successfully.")
            else:
                messages.error(request, "Please provide a document title and select a file.")
            return redirect("students:edit", pk=pk)

        # Handle document change (replace file for existing doc)
        if "change_document_id" in request.POST:
            from students.models import StudentDocument
            change_id = request.POST.get("change_document_id")
            new_file = request.FILES.get("change_doc_file")
            if new_file and change_id:
                if change_id.startswith("onb_"):
                    # Onboarding doc → create a new StudentDocument as replacement
                    od_pk = int(change_id.replace("onb_", ""))
                    from admissions.models import ApplicantDocumentReceipt
                    od = ApplicantDocumentReceipt.objects.filter(pk=od_pk).first()
                    doc_title = od.get_document_type_display() if od else "Updated Document"
                    doc_type = od.document_type if od else "other"
                    StudentDocument.objects.create(
                        student=student, title=doc_title, document_type=doc_type,
                        file=new_file, uploaded_by=request.user,
                    )
                    messages.success(request, f"Replacement uploaded for '{doc_title}'.")
                elif change_id.startswith("stu_"):
                    # Uploaded doc → replace the file
                    sd_pk = int(change_id.replace("stu_", ""))
                    sd = StudentDocument.objects.filter(pk=sd_pk, student=student).first()
                    if sd:
                        sd.file = new_file
                        sd.save(update_fields=["file", "updated_at"])
                        messages.success(request, f"Document '{sd.title}' updated.")
                from audit.models import log_event
                log_event(
                    actor=request.user, action_type="UPDATE", model_name="StudentDocument",
                    object_id=student.pk,
                    description=f"Document replaced for {student.admission_no}",
                    request=request,
                )
            return redirect("students:edit", pk=pk)

        # Handle document delete
        if "delete_document_id" in request.POST:
            from students.models import StudentDocument
            doc_id = request.POST.get("delete_document_id")
            doc = StudentDocument.objects.filter(pk=doc_id, student=student).first()
            if doc:
                doc_title = doc.title
                doc.delete()
                from audit.models import log_event
                log_event(
                    actor=request.user, action_type="DELETE", model_name="StudentDocument",
                    object_id=student.pk,
                    description=f"Document '{doc_title}' deleted for {student.admission_no}",
                    request=request,
                )
                messages.success(request, f"Document '{doc_title}' deleted.")
            return redirect("students:edit", pk=pk)

        # Snapshot first: validation writes the submitted values onto the instance.
        original = {
            f: getattr(student, f) for f in [*StudentEditForm._meta.fields, "class_name"]
        }
        form = StudentEditForm(request.POST, instance=student)
        if form.is_valid():
            # FR-AUD-001: Capture all changed fields with before/after values
            changed_fields = [f for f in form.changed_data if f != "photo"]
            before = {}
            for f in changed_fields:
                val = original.get(f)
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                before[f] = val

            try:
                with transaction.atomic():
                    student = form.save()
            except (ValidationError, IntegrityError) as exc:
                # Model-level checks run again on save; report them plainly
                # rather than as "errors below" that are not on the page.
                detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
                form.add_error(None, detail)
                messages.error(request, f"The student's record could not be saved: {detail}")
                return self.render_to_response(self.get_context_data(pk=pk, form=form))

            after = {}
            for f in changed_fields:
                val = form.cleaned_data[f]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                after[f] = val

            fields_str = ", ".join(changed_fields) if changed_fields else "(none)"

            from audit.models import log_event
            log_event(
                actor=request.user, action_type="UPDATE", model_name="Student",
                object_id=student.pk,
                description=f"Student {student.admission_no} updated fields: {fields_str}",
                before=before,
                after=after,
                request=request,
            )
            messages.success(request, "Student record updated.")
            return redirect("students:detail", pk=pk)

        messages.error(request, _form_error_summary(form))
        return self.render_to_response(self.get_context_data(pk=pk, form=form))


def _form_error_summary(form):
    """One plain sentence naming what stopped the save."""
    field_labels = [
        form.fields[name].label or name.replace("_", " ").capitalize()
        for name in form.errors if name != "__all__" and name in form.fields
    ]
    general = list(form.non_field_errors())
    parts = []
    if field_labels:
        parts.append("please fix " + ", ".join(field_labels))
    if general:
        parts.append(" ".join(general))
    if not parts:
        return "The student's record could not be saved. Please try again."
    return "The student's record was not saved: " + "; ".join(parts) + "."


class StudentLifecycleMixin:
    def scoped_students(self, request):
        qs = Student.objects.all()
        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            qs = qs.filter(class_name__in=get_teacher_assigned_classes(request.user))
        from core.scoping import hod_class_names
        names = hod_class_names(request.user)
        if names is not None:
            qs = qs.filter(class_name__in=names)
        return qs

    def get_student(self, request, pk):
        return get_object_or_404(self.scoped_students(request), pk=pk)


class StudentArchiveView(StudentLifecycleMixin, RoleRequiredMixin, View):
    required_permission = "students.change_student"

    def post(self, request, pk):
        student = self.get_student(request, pk)
        if student.is_archived:
            messages.warning(request, "Student is already archived.")
        else:
            student.is_archived = True
            student.archived_at = timezone.now()
            student.archived_by = request.user
            student.status = StudentStatus.WITHDRAWN
            student.save(update_fields=["is_archived", "archived_at", "archived_by", "status", "updated_at"])

            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="ARCHIVE",
                model_name="Student",
                object_id=student.pk,
                description=f"Student {student.admission_no} archived by {request.user.username}",
                request=request,
            )
            
            # NOTIF-14: Waitlist space available
            from admissions.models import Applicant, ApplicantStatus
            from communications.email_service import dispatch_notification
            from users.models import User, UserRole
            
            waitlisted = Applicant.objects.filter(
                grade_applying_for__iexact=student.class_name,
                status=ApplicantStatus.WAITLISTED
            )
            if waitlisted.exists():
                count = waitlisted.count()
                admins = User.objects.filter(role=UserRole.ADMIN_OFFICER, is_active=True)
                msg = f"A space is now available in {student.class_name}. {count} applicant(s) on the waitlist. Please contact the next applicant."
                for admin in admins:
                    dispatch_notification(
                        user=admin,
                        title="Waitlist Space Available",
                        message=msg,
                        link="/admissions/waitlist/",
                        actor=request.user
                    )
            
            messages.success(request, f"Student {student} has been archived.")

        return _safe_next(request, f"/students/{pk}/")


class StudentRestoreView(StudentLifecycleMixin, RoleRequiredMixin, View):
    """Restore an archived student back to active status."""
    required_permission = "students.change_student"

    def post(self, request, pk):
        student = self.get_student(request, pk)

        if not student.is_archived:
            messages.warning(request, "Student is not archived.")
            return _safe_next(request, f"/students/{pk}/")

        from academics.models import GradeClass, get_class_capacity
        from students.models import StudentStatus

        target_class = GradeClass.objects.filter(name=student.class_name).first()
        if not target_class:
            messages.error(
                request,
                f"Cannot restore {student}: class '{student.class_name}' no longer exists. "
                f"Update the student's class first.",
            )
            return _safe_next(request, f"/students/{pk}/")

        if target_class.is_exit_grade:
            messages.error(
                request,
                f"Cannot restore {student} into exit grade '{student.class_name}'. "
                f"Exit grades cannot accept restored students.",
            )
            return _safe_next(request, f"/students/{pk}/")

        cap = get_class_capacity(target_class)
        if cap:
            enrolled = Student.objects.filter(
                class_name=student.class_name, is_archived=False,
                status=StudentStatus.ACTIVE,
            ).count()
            if enrolled >= cap:
                messages.error(
                    request,
                    f"Cannot restore {student}: class '{student.class_name}' is at capacity "
                    f"({enrolled}/{cap}).",
                )
                return _safe_next(request, f"/students/{pk}/")

        student.is_archived = False
        student.archived_at = None
        student.archived_by = None
        student.status = StudentStatus.ACTIVE
        student.save(update_fields=["is_archived", "archived_at", "archived_by", "status", "updated_at"])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="RESTORE",
            model_name="Student",
            object_id=student.pk,
            description=f"Student {student.admission_no} restored by {request.user.username}",
            request=request,
        )
        messages.success(request, f"Student {student} has been restored and is now active.")

        return _safe_next(request, f"/students/{pk}/")


class GuardianListView(RoleRequiredMixin, ListView):
    template_name = "students/guardian_list.html"
    context_object_name = "guardians"
    paginate_by = 30
    required_permission = "students.view_parentguardian"

    def get_queryset(self):
        qs = ParentGuardian.objects.all().order_by("full_name")
        archive_status = self.request.GET.get("archive_status", "active")
        if archive_status != "all" and not getattr(self, "include_all_archives", False):
            qs = qs.filter(is_archived=archive_status == "archived")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
        pdpa = self.request.GET.get("pdpa", "")
        if pdpa in ("given", "pending"):
            qs = qs.filter(pdpa_consent_given=pdpa == "given")
        portal = self.request.GET.get("portal", "")
        if portal in ("yes", "no"):
            qs = qs.filter(user__isnull=portal == "no")
        links = self.request.GET.get("links", "")
        if links in ("none", "some"):
            qs = qs.filter(studentguardian__isnull=links == "none").distinct()
        user = self.request.user
        if not user.is_school_wide and user.role not in (UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER):
            from users.role_models import RoleConfig
            rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
            depts = user.section_departments or (rc.departments if rc else [])
            if user.role == UserRole.TEACHER or not depts:
                from core.teacher_context import get_teacher_assigned_classes
                class_names = get_teacher_assigned_classes(user)
            else:
                from academics.models import GradeClass
                class_names = GradeClass.objects.filter(department__in=depts).values_list("name", flat=True)
            qs = qs.filter(studentguardian__student__class_name__in=class_names).distinct()
        return qs.select_related('user').prefetch_related('studentguardian_set__student')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["archive_status"] = self.request.GET.get("archive_status", "active")
        return ctx


class GuardianArchiveView(RoleRequiredMixin, View):
    required_permission = "students.change_parentguardian"
    restore = False

    @transaction.atomic
    def post(self, request, pk):
        # Reuse directory visibility so knowing an ID cannot bypass its scope.
        directory = GuardianListView()
        directory.request = request
        directory.include_all_archives = True
        guardian = get_object_or_404(directory.get_queryset(), pk=pk)
        if guardian.is_archived == (not self.restore):
            messages.info(request, "Parent is already in the requested state.")
            return _safe_next(request, f"/students/guardians/{pk}/")
        if guardian.user_id and guardian.user.role != UserRole.PARENT:
            messages.error(request, "This parent record is linked to a staff account. Correct the account link first.")
            return _safe_next(request, f"/students/guardians/{pk}/")
        guardian.is_archived = not self.restore
        guardian.archived_at = None if self.restore else timezone.now()
        guardian.archived_by = None if self.restore else request.user
        guardian.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])
        if guardian.user_id:
            guardian.user.is_active = self.restore
            guardian.user.save(update_fields=["is_active"])
            if not self.restore:
                from users.lifecycle import invalidate_user_sessions
                invalidate_user_sessions(guardian.user_id)
        from audit.models import log_event
        log_event(
            actor=request.user, action_type="GUARDIAN_RESTORED" if self.restore else "GUARDIAN_ARCHIVED",
            model_name="ParentGuardian", object_id=guardian.pk,
            description=f"Parent {guardian.full_name} {'restored' if self.restore else 'archived'}.", request=request,
        )
        messages.success(request, f"Parent {guardian.full_name} has been {'restored' if self.restore else 'archived'}.")
        return _safe_next(request, f"/students/guardians/{pk}/")


class GuardianDetailView(RoleRequiredMixin, DetailView):
    template_name = "students/guardian_detail.html"
    model = ParentGuardian
    context_object_name = "guardian"
    required_permission = "students.view_parentguardian"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        linked = self.object.studentguardian_set.select_related("student")
        ctx["linked_students"] = linked
        ctx["relationships"] = GuardianRelationship.choices

        # Relationship breakdown stats
        from django.db.models import Count, Q
        rel_counts = linked.values("relationship").annotate(count=Count("id")).order_by("-count")
        rel_map = {item["relationship"]: item["count"] for item in rel_counts}
        primary_count = linked.filter(is_primary=True).count()

        ctx["rel_stats"] = {
            "total": linked.count(),
            "primary_count": primary_count,
            "mother_count": rel_map.get("mother", 0),
            "father_count": rel_map.get("father", 0),
            "parent_count": rel_map.get("parent", 0),
            "guardian_count": rel_map.get("guardian", 0) + rel_map.get("step_mother", 0) + rel_map.get("step_father", 0),
            "other_count": sum(v for k, v in rel_map.items() if k not in ("mother", "father", "parent", "guardian", "step_mother", "step_father")),
            "rel_map": rel_map,
        }
        # FR-PAR-004: Consent logs only visible to roles with change_parentguardian perm
        if self.request.user.has_perm("students.change_parentguardian"):
            ctx["consent_logs"] = self.object.consent_logs.select_related("actor").all()
        else:
            ctx["consent_logs"] = []
        return ctx


GUARDIAN_TEXT_FIELDS = (
    "full_name", "phone", "secondary_phone", "email", "address", "preferred_invoice_name",
)
GUARDIAN_FIELD_LABELS = {
    "full_name": "Full Name",
    "phone": "Primary Phone",
    "secondary_phone": "Secondary Phone",
    "email": "Email Address",
    "address": "Physical Address",
    "preferred_invoice_name": "Preferred Invoice Name",
}


def _set_guardian_link(student, guardian, relationship, is_primary):
    """Create or update a student-guardian link. Making this guardian primary
    moves the primary flag from the student's current primary contact
    (FR-PAR-002) instead of failing the save."""
    if relationship not in GuardianRelationship.values:
        relationship = GuardianRelationship.GUARDIAN
    if is_primary:
        StudentGuardian.objects.filter(student=student, is_primary=True).exclude(
            guardian=guardian
        ).update(is_primary=False)
    link, created = StudentGuardian.objects.get_or_create(
        student=student, guardian=guardian,
        defaults={"relationship": relationship, "is_primary": is_primary},
    )
    if not created and (link.relationship != relationship or link.is_primary != is_primary):
        link.relationship = relationship
        link.is_primary = is_primary
        link.save()
    return link, created


def _clean_guardian_post(post, exclude_pk=None):
    """Read and check guardian contact fields. Returns (values, errors) where
    errors maps a field name to a message that names the field."""
    from django.core.validators import validate_email

    values = {f: post.get(f, "").strip() for f in GUARDIAN_TEXT_FIELDS}
    values["preferred_language"] = post.get("preferred_language", "en")
    errors = {}
    if not values["full_name"]:
        errors["full_name"] = "Full Name is required."
    if not values["phone"]:
        errors["phone"] = "Primary Phone is required."
    if values["email"]:
        try:
            validate_email(values["email"])
        except ValidationError:
            errors["email"] = "Email Address is not a valid email address."
    if values["preferred_language"] not in ("en", "sw"):
        values["preferred_language"] = "en"
    for name, label in GUARDIAN_FIELD_LABELS.items():
        limit = ParentGuardian._meta.get_field(name).max_length
        if name not in errors and limit and len(values[name]) > limit:
            errors[name] = f"{label} must be at most {limit} characters (it has {len(values[name])})."
    if values["full_name"] and values["phone"]:
        dupes = ParentGuardian.objects.filter(full_name=values["full_name"], phone=values["phone"])
        if exclude_pk:
            dupes = dupes.exclude(pk=exclude_pk)
        if dupes.exists():
            errors["phone"] = (
                "Primary Phone: a parent with this name and phone number already exists. "
                "Link the existing parent instead."
            )
    return values, errors


def _clean_consent_post(post, values, errors):
    """Read the optional PDPA consent fields into values. Consent is not
    required, but when it is ticked the method and statement version must be
    recorded with it (FR-PAR-004). Returns True when consent was given."""
    given = post.get("pdpa_consent_given") == "on"
    values.update(
        pdpa_consent_given=given,
        pdpa_consent_method=post.get("pdpa_consent_method", "").strip(),
        pdpa_consent_version=post.get("pdpa_consent_version", "").strip(),
    )
    if given:
        if values["pdpa_consent_method"] not in dict(ParentGuardian._meta.get_field("pdpa_consent_method").choices):
            errors["pdpa"] = "PDPA Consent: choose how consent was given (in-person or digital)."
        elif not values["pdpa_consent_version"]:
            errors["pdpa"] = "The version of the PDPA consent statement must be recorded with the consent."
    return given


def _record_consent(guardian, values, actor, notes):
    """Set the guardian's consent fields from values and log it (unsaved guardian
    fields are saved by the caller)."""
    guardian.pdpa_consent_given = True
    guardian.pdpa_consent_method = values["pdpa_consent_method"]
    guardian.pdpa_consent_version = values["pdpa_consent_version"]
    guardian.pdpa_consented_at = timezone.now()
    PDPAConsentLog.objects.create(
        guardian=guardian, action=PDPAConsentLog.Action.GIVEN,
        method=values["pdpa_consent_method"], version=values["pdpa_consent_version"],
        actor=actor, notes=notes,
    )


def _guardian_error_message(errors, general=""):
    if general:
        return f"The parent record was not saved: {general}"
    labels = {**GUARDIAN_FIELD_LABELS, "pdpa": "PDPA Consent", "guardian_id": "Parent"}
    names = ", ".join(labels.get(k, k.replace("_", " ").title()) for k in errors)
    return f"The parent record was not saved: please fix {names}."


class GuardianCreateView(RoleRequiredMixin, TemplateView):
    template_name = "students/guardian_form.html"
    required_permission = "students.add_parentguardian"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["relationships"] = GuardianRelationship.choices
        return ctx

    def _invalid(self, request, values, errors, general=""):
        messages.error(request, _guardian_error_message(errors, general))
        student_id = request.POST.get("student_id")
        if student_id and student_id.isdigit():
            values["student"] = Student.objects.filter(pk=student_id).first()
        ctx = self.get_context_data(values=values, errors=errors, general_error=general)
        return self.render_to_response(ctx)

    def post(self, request):
        values, errors = _clean_guardian_post(request.POST)
        # PDPA consent is optional; it can be recorded later from Edit Parent.
        pdpa_consent_given = _clean_consent_post(request.POST, values, errors)
        values.update(
            portal_access=request.POST.get("portal_access") == "on",
            relationship=request.POST.get("relationship", GuardianRelationship.GUARDIAN),
            is_primary=request.POST.get("is_primary") == "on",
        )
        if errors:
            return self._invalid(request, values, errors)

        phone = values["phone"]
        student_id = request.POST.get("student_id")

        student = None
        if student_id:
            try:
                student = Student.objects.get(pk=student_id)
            except (Student.DoesNotExist, ValueError):
                messages.warning(request, "Selected student not found: parent created without link.")


        try:
            with transaction.atomic():
                guardian = ParentGuardian.objects.create(
                    full_name=values["full_name"],
                    phone=phone,
                    secondary_phone=values["secondary_phone"],
                    email=values["email"],
                    address=values["address"],
                    preferred_invoice_name=values["preferred_invoice_name"],
                    preferred_language=values["preferred_language"],
                )
                if pdpa_consent_given:
                    _record_consent(guardian, values, request.user, "Consent recorded during parent creation.")
                    guardian.save()
                # Link student only if one was selected
                if student:
                    _set_guardian_link(
                        student, guardian,
                        request.POST.get("relationship", GuardianRelationship.GUARDIAN),
                        request.POST.get("is_primary") == "on",
                    )
                # Portal access: create linked User account if requested
                portal_access = request.POST.get("portal_access") == "on"
                if portal_access:
                    _created, username, delivered = grant_portal_access(guardian, request.user)

                # Tracker 12.1: audit guardian creation
                from audit.models import log_event
                log_event(
                    actor=request.user,
                    action_type="GUARDIAN_CREATED",
                    model_name="ParentGuardian",
                    object_id=guardian.pk,
                    description=(
                        f"Parent {guardian.full_name} (phone {guardian.phone}) created by "
                        f"{request.user.get_full_name() or request.user.username}"
                    ),
                    request=request,
                )
        except Exception as e:
            logger.exception("Parent create failed")
            return self._invalid(
                request, values, {},
                general=f"The system could not save it ({e}). Nothing was saved; please try again.",
            )
        if portal_access:
            (messages.success if delivered else messages.warning)(
                request,
                f"Parent {guardian.full_name} created with portal login {username}; details {describe_delivery(delivered)}."
            )
        else:
            messages.success(request, f"Parent {guardian.full_name} created.")
        return redirect("students:guardian_detail", pk=guardian.pk)



class StudentDataExportView(RoleRequiredMixin, View):
    """FR-PDPA-003: Full data export within 30 min for DSAR compliance."""
    required_permission = "students.view_student"

    # 30-minute SLA timeout in seconds
    DSAR_SLA_TIMEOUT_SECONDS = 30 * 60

    def get(self, request, pk):
        student = get_object_or_404(Student, pk=pk)

        # NFR-PDPA-003: Enforce 30-minute SLA  log start and warn if exceeded
        started_at = time.time()

        guardians = list(
            student.studentguardian_set.select_related("guardian").values(
                "guardian__full_name", "guardian__phone", "guardian__email",
                "guardian__pdpa_consent_given", "guardian__pdpa_consented_at", "relationship"
            )
        )

        # Attendance records
        attendance = list(
            student.attendance_entries.values(
                "date", "status", "check_in_time", "check_out_time", "reason"
            )
        )

        # Welfare / discipline records
        welfare = []
        try:
            from welfare.models import WelfareObservation
            welfare = list(
                WelfareObservation.objects.filter(student=student).values(
                    "observation_date", "note_type", "concern_type", "severity",
                    "observation_text", "action_taken", "status"
                )
            )
        except Exception:
            logger.exception("Failed to load welfare records for student %s", student.pk)

        discipline = []
        try:
            from discipline.models import DisciplineIncident
            discipline = list(
                DisciplineIncident.objects.filter(student=student).values(
                    "incident_date", "severity", "summary", "action_taken", "status", "created_at"
                )
            )
        except Exception:
            logger.exception("Failed to load discipline records for student %s", student.pk)

        # Academic records
        exam_scores = []
        try:
            from academics.models import ExamScore
            exam_scores = list(
                ExamScore.objects.filter(student=student).values(
                    "term__name", "subject_name", "exam_type", "score", "max_score", "status"
                )
            )
        except Exception:
            logger.exception("Failed to load exam scores for student %s", student.pk)

        report_cards = []
        try:
            from academics.models import ReportCard
            report_cards = list(
                ReportCard.objects.filter(student=student).values(
                    "term__name", "overall_average", "status", "published_at"
                )
            )
        except Exception:
            logger.exception("Failed to load report cards for student %s", student.pk)

        # Financial records (amounts only, no sensitive payment details)
        invoices = []
        try:
            from finance.models import Invoice
            invoices = list(
                Invoice.objects.filter(student=student).values(
                    "invoice_number", "total_due", "status", "created_at"
                )
            )
        except Exception:
            logger.exception("Failed to load invoices for student %s", student.pk)

        data = {
            "student": {
                "admission_no": student.admission_no,
                "name": f"{student.first_name} {student.last_name}",
                "class_name": student.class_name,
                "date_of_birth": str(student.date_of_birth),
                "gender": student.gender,
                "nationality": student.nationality,
                "religion": student.religion,
                "blood_type": student.blood_type,
                "allergies_medical": student.allergies_medical,
                "status": student.status,
                "enrolment_date": str(student.enrolment_date),
            },
            "guardians": guardians,
            "attendance": attendance,
            "welfare_observations": welfare,
            "discipline_incidents": discipline,
            "exam_scores": exam_scores,
            "report_cards": report_cards,
            "invoices": invoices,
            "exported_at": timezone.now().isoformat(),
            "exported_by": request.user.username,
        }

        # NFR-PDPA-003: SLA enforcement  check if export exceeded 30 minutes
        duration_seconds = time.time() - started_at
        sla_breached = duration_seconds > self.DSAR_SLA_TIMEOUT_SECONDS
        if sla_breached:
            import logging
            logging.getLogger(__name__).warning(
                "DSAR export for student %s exceeded 30-min SLA: %.1f seconds",
                student.admission_no, duration_seconds,
            )

        from audit.models import log_event
        log_event(
            actor=request.user, action_type="EXPORT", model_name="Student",
            object_id=student.pk,
            description=(
                f"Full PDPA data export for {student.admission_no}"
                + (f" [SLA BREACH: {duration_seconds:.0f}s]" if sla_breached else "")
            ),
            request=request,
        )
        response = JsonResponse(data, json_dumps_params={"indent": 2})
        response["Content-Disposition"] = f'attachment; filename="student_{student.admission_no}_full_data.json"'
        return response


class StudentGuardianLinkView(RoleRequiredMixin, TemplateView):
    template_name = "students/guardian_link.html"
    required_permission = "students.change_student"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["student"] = get_object_or_404(Student, pk=kwargs["pk"])
        ctx["relationships"] = GuardianRelationship.choices
        return ctx

    def _invalid(self, request, pk, values, errors, mode, general=""):
        messages.error(request, _guardian_error_message(errors, general).replace(
            "parent record was not saved", "parent was not linked"))
        ctx = self.get_context_data(
            pk=pk, values=values, errors=errors, general_error=general, mode=mode,
        )
        return self.render_to_response(ctx)

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        guardian_id = (request.POST.get("guardian_id") or "").strip()
        relationship = request.POST.get("relationship", GuardianRelationship.GUARDIAN)
        is_primary = request.POST.get("is_primary") == "on"
        mode = "new" if request.POST.get("mode") == "new" else "existing"
        if guardian_id:
            mode = "existing"

        if mode == "existing":
            # Scenario 1: Link existing
            values = {"relationship": relationship, "is_primary": is_primary}
            guardian = None
            if guardian_id.isdigit():
                guardian = ParentGuardian.objects.filter(pk=guardian_id, is_archived=False).first()
            if guardian is None:
                return self._invalid(request, pk, values, {
                    "guardian_id": "Parent: search for and select a parent, "
                                   "or switch to Create New Parent.",
                }, mode)
            values["guardian"] = guardian
            try:
                with transaction.atomic():
                    _link, created = _set_guardian_link(student, guardian, relationship, is_primary)
            except (ValidationError, IntegrityError) as exc:
                detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
                return self._invalid(request, pk, values, {}, mode, general=detail)
            if created:
                messages.success(request, f"Linked existing parent: {guardian.full_name}")
            else:
                messages.success(
                    request,
                    f"{guardian.full_name} was already linked: relationship and primary contact updated.",
                )

            # FR-STU-005: Notify Finance Officer if sibling relationship confirmed
            if request.POST.get("confirm_sibling") == "1":
                self._notify_sibling_confirmation(request, student, guardian)
            return redirect("students:detail", pk=pk)

        # Scenario 2: Create new
        values, errors = _clean_guardian_post(request.POST)
        # PDPA consent is optional; it can be recorded later from Edit Parent.
        pdpa_consent_given = _clean_consent_post(request.POST, values, errors)
        portal_access = request.POST.get("portal_access") == "on"
        values.update(portal_access=portal_access, relationship=relationship, is_primary=is_primary)
        if errors:
            return self._invalid(request, pk, values, errors, mode)

        try:
            with transaction.atomic():
                guardian = ParentGuardian.objects.create(
                    full_name=values["full_name"],
                    phone=values["phone"],
                    secondary_phone=values["secondary_phone"],
                    email=values["email"],
                    address=values["address"],
                    preferred_invoice_name=values["preferred_invoice_name"],
                    preferred_language=values["preferred_language"],
                )
                if pdpa_consent_given:
                    _record_consent(guardian, values, request.user, "Consent recorded during parent link flow.")
                    guardian.save()
                _set_guardian_link(student, guardian, relationship, is_primary)
                # Portal access: create linked User account if requested
                if portal_access:
                    _created, username, delivered = grant_portal_access(guardian, request.user)
        except Exception as e:
            logger.exception("Parent create-and-link failed")
            return self._invalid(
                request, pk, values, {}, mode,
                general=f"The system could not save it ({e}). Nothing was saved; please try again.",
            )

        if portal_access:
            (messages.success if delivered else messages.warning)(
                request,
                f"Created and linked new parent {guardian.full_name} with portal login {username}; details {describe_delivery(delivered)}."
            )
        else:
            messages.success(request, f"Created and linked new parent: {guardian.full_name}")

        # FR-STU-005: Notify Finance Officer if sibling relationship confirmed
        if request.POST.get("confirm_sibling") == "1":
            self._notify_sibling_confirmation(request, student, guardian)

        return redirect("students:detail", pk=pk)

    def _notify_sibling_confirmation(self, request, student: Student, guardian: ParentGuardian) -> None:
        """Delegates to the standalone notify_sibling_confirmation function."""
        notify_sibling_confirmation(request, student, guardian)



class GuardianConsentWithdrawView(RoleRequiredMixin, View):
    """Withdraw PDPA consent for a guardian."""
    required_permission = "students.change_parentguardian"

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk)
        if not guardian.has_given_consent():
            messages.warning(request, f"{guardian.full_name} has no active consent to withdraw.")
            return redirect("students:guardian_detail", pk=pk)

        # FR-PDPA-004: Record the old version in the consent log before overwriting
        old_version = guardian.pdpa_consent_version or "v1.0"
        old_method  = guardian.pdpa_consent_method or ""
        old_consented_at = guardian.pdpa_consented_at

        # Create the withdrawn log entry with previous_version for audit trail
        PDPAConsentLog.objects.create(
            guardian=guardian,
            action=PDPAConsentLog.Action.WITHDRAWN,
            method=old_method,
            version=old_version,
            previous_version=old_version,
            actor=request.user,
            notes="Consent withdrawn by admin.",
        )

        # Now overwrite the guardian record (new consent version)
        new_version = "v1.0"
        guardian.pdpa_consent_given = False
        guardian.pdpa_consent_method = ""
        guardian.pdpa_consent_version = ""
        guardian.pdpa_consented_at = None
        guardian.save(update_fields=["pdpa_consent_given", "pdpa_consent_method", "pdpa_consent_version", "pdpa_consented_at", "updated_at"])

        messages.success(request, f"PDPA consent withdrawn for {guardian.full_name}.")
        return redirect("students:guardian_detail", pk=pk)


class GuardianPortalAccessView(RoleRequiredMixin, View):
    """Give an existing parent portal access, or send them a new temporary
    password when they already have a login."""
    required_permission = "students.change_parentguardian"

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk)
        if guardian.is_archived:
            messages.error(request, f"{guardian.full_name} is archived. Restore the parent before giving portal access.")
            return redirect("students:guardian_detail", pk=pk)
        if not (guardian.email or guardian.phone):
            messages.error(request, f"{guardian.full_name} has no email or phone to send login details to.")
            return redirect("students:guardian_detail", pk=pk)
        try:
            with transaction.atomic():
                created, username, delivered = grant_portal_access(guardian, request.user)
        except Exception as e:
            logger.exception("Parent portal access failed for guardian %s", pk)
            messages.error(request, f"Portal access was not set up ({e}). Nothing was changed; please try again.")
            return redirect("students:guardian_detail", pk=pk)

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="PARENT_PORTAL_ACCOUNT",
            model_name="ParentGuardian",
            object_id=guardian.pk,
            description=(
                f"Portal {'account created' if created else 'password reset'} for parent "
                f"{guardian.full_name} (login {username}) by {request.user.username}"
            ),
            request=request,
        )
        notify = messages.success if delivered else messages.warning
        if created:
            notify(request, f"Portal access created for {guardian.full_name}. Login {username}; details {describe_delivery(delivered)}.")
        else:
            notify(request, f"New login details for {guardian.full_name} (login {username}) {describe_delivery(delivered)}.")
        return redirect("students:guardian_detail", pk=pk)


class GuardianEditView(RoleRequiredMixin, TemplateView):
    """FR-PAR-001: Edit an existing guardian record."""
    template_name = "students/guardian_form.html"
    required_permission = "students.change_parentguardian"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        guardian = get_object_or_404(ParentGuardian, pk=kwargs["pk"])
        ctx["guardian"] = guardian
        ctx["edit_mode"] = True
        links = list(guardian.studentguardian_set.select_related("student").order_by(
            "student__first_name", "student__last_name"
        ))
        posted = kwargs.get("posted")
        for link in links:
            # Show what was submitted after a failed save, otherwise what is stored.
            if posted is not None and f"relationship_{link.pk}" in posted:
                link.form_relationship = posted.get(f"relationship_{link.pk}")
                link.form_primary = posted.get(f"primary_{link.pk}") == "on"
            else:
                link.form_relationship = link.relationship
                link.form_primary = link.is_primary
        ctx["linked_students"] = links
        ctx["relationships"] = GuardianRelationship.choices
        if "values" not in kwargs:
            ctx["values"] = {f: getattr(guardian, f) for f in GUARDIAN_TEXT_FIELDS}
            ctx["values"]["preferred_language"] = guardian.preferred_language
            ctx["values"].update(
                pdpa_consent_given=guardian.pdpa_consent_given,
                pdpa_consent_method=guardian.pdpa_consent_method,
                pdpa_consent_version=guardian.pdpa_consent_version,
            )
        return ctx

    def _invalid(self, request, pk, values, errors, general=""):
        messages.error(request, _guardian_error_message(errors, general))
        ctx = self.get_context_data(
            pk=pk, values=values, errors=errors, general_error=general, posted=request.POST,
        )
        return self.render_to_response(ctx)

    def _apply_consent(self, guardian, values, actor):
        """Turn consent on, update it, or withdraw it to match the edit form."""
        if values["pdpa_consent_given"]:
            if not guardian.has_given_consent():
                _record_consent(guardian, values, actor, "Consent recorded from Edit Parent.")
            elif (guardian.pdpa_consent_method, guardian.pdpa_consent_version) != (
                values["pdpa_consent_method"], values["pdpa_consent_version"]
            ):
                guardian.pdpa_consent_method = values["pdpa_consent_method"]
                guardian.pdpa_consent_version = values["pdpa_consent_version"]
                guardian.pdpa_consented_at = timezone.now()
                PDPAConsentLog.objects.create(
                    guardian=guardian, action=PDPAConsentLog.Action.UPDATED,
                    method=guardian.pdpa_consent_method, version=guardian.pdpa_consent_version,
                    actor=actor, notes="Consent updated from Edit Parent.",
                )
        elif guardian.pdpa_consent_given:
            PDPAConsentLog.objects.create(
                guardian=guardian, action=PDPAConsentLog.Action.WITHDRAWN,
                method=guardian.pdpa_consent_method, version=guardian.pdpa_consent_version,
                actor=actor, notes="Consent turned off from Edit Parent.",
            )
            guardian.pdpa_consent_given = False
            guardian.pdpa_consent_method = ""
            guardian.pdpa_consent_version = ""
            guardian.pdpa_consented_at = None

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk)
        values, errors = _clean_guardian_post(request.POST, exclude_pk=guardian.pk)
        # Only touch consent when the form showed the consent box, so a post
        # without it never withdraws consent by accident.
        consent_in_form = "pdpa_consent_shown" in request.POST
        if consent_in_form:
            _clean_consent_post(request.POST, values, errors)
        if errors:
            return self._invalid(request, pk, values, errors)
        full_name = values["full_name"]
        phone = values["phone"]

        # FR-PAR-001: Sibling scope  users without school-wide student access
        # can only edit guardians linked to students they have access to
        user = self.request.user
        from users.role_models import RoleConfig
        rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
        depts = rc.departments if rc and rc.departments else []
        has_school_wide_access = bool(depts) or user.role in (
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
        )
        if not has_school_wide_access and user.has_perm("students.change_parentguardian"):
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = get_teacher_assigned_classes(user)
            accessible_student_ids = set(
                Student.objects.filter(class_name__in=my_classes).values_list("pk", flat=True)
            )
            linked_student_ids = set(
                StudentGuardian.objects.filter(guardian=guardian).values_list("student_id", flat=True)
            )
            if linked_student_ids and not linked_student_ids.intersection(accessible_student_ids):
                messages.error(
                    request,
                    "You do not have access to edit this parent's record."
                )
                return redirect("students:guardian_detail", pk=pk)

        before_name = guardian.full_name
        before_phone = guardian.phone

        guardian.full_name = full_name
        guardian.phone = phone
        guardian.secondary_phone = values["secondary_phone"]
        guardian.email = values["email"]
        guardian.address = values["address"]
        guardian.preferred_invoice_name = values["preferred_invoice_name"]
        guardian.preferred_language = values["preferred_language"]
        try:
            with transaction.atomic():
                if consent_in_form:
                    self._apply_consent(guardian, values, request.user)
                guardian.save()
                # Relationship and primary contact for each linked student. Making
                # this guardian primary clears the student's other primary (FR-PAR-002).
                for link in list(guardian.studentguardian_set.select_related("student")):
                    relationship = request.POST.get(f"relationship_{link.pk}")
                    if relationship is None:
                        continue  # link added after the page was opened
                    _set_guardian_link(
                        link.student, guardian, relationship,
                        request.POST.get(f"primary_{link.pk}") == "on",
                    )
        except (ValidationError, IntegrityError) as exc:
            detail = "; ".join(getattr(exc, "messages", None) or [str(exc)])
            return self._invalid(request, pk, values, {}, general=detail)

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="GUARDIAN_UPDATED",
            model_name="ParentGuardian",
            object_id=guardian.pk,
            description=f"Parent {guardian.full_name} updated by {request.user.username}",
            before={"full_name": before_name, "phone": before_phone},
            after={"full_name": guardian.full_name, "phone": guardian.phone},
            request=request,
        )
        messages.success(request, f"Parent {guardian.full_name} updated.")
        return redirect("students:guardian_detail", pk=pk)


class GuardianDeleteView(RoleRequiredMixin, View):
    """FR-PAR-002: Delete a guardian record (no linked students or financial records)."""
    required_permission = "students.delete_parentguardian"

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk)

        linked_students = StudentGuardian.objects.filter(guardian=guardian).count()
        if linked_students > 0:
            messages.error(
                request,
                f"Cannot delete {guardian.full_name}: still linked to {linked_students} student(s). "
                "Remove all student links first."
            )
            return redirect("students:guardian_detail", pk=pk)

        from finance.models import Invoice
        if Invoice.objects.filter(student__studentguardian__guardian=guardian).exists():
            messages.error(
                request,
                f"Cannot delete {guardian.full_name}: financial records exist. "
                "Archive the parent instead."
            )
            return redirect("students:guardian_detail", pk=pk)

        GuardianArchiveView().post(request, pk)
        return redirect("students:guardian_list")


class GuardianSearchView(RoleRequiredMixin, View):
    required_permission = "students.view_parentguardian"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        if not q:
            return JsonResponse({"results": []})
        
        guardians = ParentGuardian.objects.filter(
            Q(full_name__icontains=q) | Q(phone__icontains=q)
        ).order_by("full_name")[:10]
        
        results = []
        for g in guardians:
            # FR-STU-004: Detect siblings
            sibs = g.studentguardian_set.select_related("student").values_list("student__first_name", "student__last_name", "student__class_name")
            sib_text = ", ".join([f"{s[0]} {s[1]} ({s[2]})" for s in sibs])
            
            results.append({
                "id": g.id,
                "full_name": g.full_name,
                "phone": g.phone,
                "email": g.email or "",
                "siblings": sib_text,
                "pdpa_consented": g.has_given_consent(),
            })
        
        return JsonResponse({"results": results})


class StudentSearchAPIView(RoleRequiredMixin, View):
    """AJAX endpoint for real-time student search with 2-char minimum."""
    required_permission = "students.view_student"

    def get(self, request):
        from django.urls import reverse

        q = request.GET.get("q", "").strip()
        if len(q) < 2:
            return JsonResponse({"students": [], "count": 0})
        directory = StudentListView()
        directory.request = request
        students = directory.get_queryset()[:50]
        can_manage_archive = request.user.has_perm("students.change_student")

        data = []
        for s in students:
            data.append({
                "id": s.pk,
                "first_name": s.first_name,
                "last_name": s.last_name,
                "full_name": f"{s.first_name} {s.last_name}",
                "admission_no": s.admission_no,
                "class_name": s.class_name,
                "stream_name": s.stream_name or "",
                "gender": s.gender or "",
                "status": s.status,
                "status_display": s.get_status_display(),
                "photo_url": s.photo.url if s.photo else "",
                "preferred_name": s.preferred_name or "",
                "academic_year_name": s.academic_year.name if s.academic_year else "",
                "is_archived": s.is_archived,
                "archive_url": reverse("students:restore" if s.is_archived else "students:archive", args=[s.pk]) if can_manage_archive else "",
            })

        return JsonResponse({"students": data, "count": len(data)})


class GenerateStudentIDView(RoleRequiredMixin, View):
    """Generate a student ID for an individual student."""
    required_permission = "students.change_student"

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        
        # Only generate if it's currently a placeholder or empty (though model requires it)
        # We'll allow "regenerating" if it doesn't follow the pattern or if forced
        new_id = generate_admission_number()
        old_id = student.admission_no
        Student.objects.filter(pk=student.pk).update(
            admission_no=new_id,
            updated_at=timezone.now()
        )
        
        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="ID_GENERATION",
            model_name="Student",
            object_id=student.pk,
            description=f"Generated Student ID {new_id} (replaced {old_id})",
            before_value=old_id,
            after_value=new_id,
            request=request
        )
        
        messages.success(request, f"Generated new Student ID: {new_id}")
        return redirect("students:detail", pk=pk)


class BulkGenerateStudentIDsView(RoleRequiredMixin, View):
    """Bulk generate student IDs for a class."""
    required_permission = "students.change_student"

    def post(self, request):
        class_name = request.POST.get("class_name")
        if not class_name:
            messages.error(request, "Please select a class for bulk ID generation.")
            return redirect("students:list")

        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("created_at")
        count = 0
        
        # Using a transaction to ensure sequence integrity
        from django.db import transaction
        with transaction.atomic():
            for student in students:
                # Logic: If it starts with 'TEMP' or 'PLACEHOLDER' or matches a certain pattern 
                # (or just generate for all if requested). 
                # For safety, let's only generate for those who have a certain pattern or empty
                # But here we'll follow user's direct request to "create logic to create the student ids"
                
                # Check if it's already a valid format ADM-YYYY-XXX
                import re
                valid_pattern = r"^ADM-\d{4}-\d{3}$"
                if not re.match(valid_pattern, student.admission_no):
                    old_id = student.admission_no
                    new_id = generate_admission_number()
                    Student.objects.filter(pk=student.pk).update(
                        admission_no=new_id,
                        updated_at=timezone.now()
                    )
                    
                    from audit.models import log_event
                    log_event(
                        actor=request.user,
                        action_type="ID_GENERATION",
                        model_name="Student",
                        object_id=student.pk,
                        description=f"Bulk generated ID {new_id} for class {class_name}",
                        before_value=old_id,
                        after_value=new_id,
                        request=request
                    )
                    count += 1

        if count > 0:
            messages.success(request, f"Successfully generated {count} Student IDs for {class_name}.")
        else:
            messages.info(request, f"No students in {class_name} required new IDs.")
            
        return redirect(f"/students/?class_name={class_name}")


class PrintStudentIDView(RoleRequiredMixin, DetailView):
    """Printable student ID card view."""
    template_name = "students/id_card_print.html"
    model = Student
    context_object_name = "student"
    required_permission = "students.view_student"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        from users.role_models import RoleConfig
        user = self.request.user
        rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
        depts = rc.departments if rc and rc.departments else []
        is_teacher_role = not depts and user.role not in (
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
            UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,
        )
        if is_teacher_role or user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = get_teacher_assigned_classes(user)
            if my_classes and obj.class_name not in my_classes:
                from django.http import Http404
                raise Http404("Student not found in your assigned classes.")
            elif not my_classes:
                from django.http import Http404
                raise Http404("No classes assigned to you.")
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date, timedelta
        today = date.today()
        ctx["issued_date"] = today.strftime("%d/%m/%Y")
        ctx["expiry_date"] = (today.replace(year=today.year + 1) - timedelta(days=1)).strftime("%d/%m/%Y")
        return ctx


class PrintClassIDView(RoleRequiredMixin, TemplateView):
    """Print pickup cards for all students in a class."""
    template_name = "students/id_card_print_class.html"
    required_permission = "students.view_student"

    def get(self, request, *args, **kwargs):
        class_name = kwargs.get("class_name", "").strip()
        if not class_name:
            messages.error(request, "Please specify a class.")
            return redirect("students:list")

        from users.role_models import RoleConfig
        user = request.user
        rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
        depts = rc.departments if rc and rc.departments else []
        is_teacher_role = not depts and user.role not in (
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
            UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,
        )

        students = Student.objects.filter(
            class_name=class_name, is_archived=False
        ).order_by("last_name")

        if is_teacher_role or user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = get_teacher_assigned_classes(user)
            if my_classes:
                students = students.filter(class_name__in=my_classes)
            elif not my_classes:
                messages.error(request, "No classes assigned to you.")
                return redirect("students:list")

        if not students.exists():
            messages.warning(request, f"No active students found in {class_name}.")
            return redirect(f"/students/?class_name={class_name}")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date, timedelta
        class_name = self.kwargs.get("class_name", "").strip()
        today = date.today()
        ctx["class_name"] = class_name
        ctx["students"] = Student.objects.filter(
            class_name=class_name, is_archived=False
        ).order_by("last_name")
        ctx["issued_date"] = today.strftime("%d/%m/%Y")
        ctx["expiry_date"] = (today.replace(year=today.year + 1) - timedelta(days=1)).strftime("%d/%m/%Y")
        return ctx


class PrintClassIDBulkView(RoleRequiredMixin, TemplateView):
    """Print pickup cards for all students in a class  A4 portrait, 10 per page (5+5)."""
    template_name = "students/id_card_print_bulk.html"
    required_permission = "students.view_student"

    def get(self, request, *args, **kwargs):
        class_name = kwargs.get("class_name", "").strip()
        if not class_name:
            messages.error(request, "Please specify a class.")
            return redirect("students:list")

        from users.role_models import RoleConfig
        user = request.user
        rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
        depts = rc.departments if rc and rc.departments else []
        is_teacher_role = not depts and user.role not in (
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
            UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,
        )

        students = Student.objects.filter(
            class_name=class_name, is_archived=False
        ).order_by("last_name")

        if is_teacher_role or user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = get_teacher_assigned_classes(user)
            if my_classes:
                students = students.filter(class_name__in=my_classes)
            elif not my_classes:
                messages.error(request, "No classes assigned to you.")
                return redirect("students:list")

        if not students.exists():
            messages.warning(request, f"No active students found in {class_name}.")
            return redirect(f"/students/?class_name={class_name}")

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date, timedelta
        import math
        class_name = self.kwargs.get("class_name", "").strip()
        today = date.today()
        students = list(Student.objects.filter(
            class_name=class_name, is_archived=False
        ).order_by("last_name"))

        # Group into pages of 10
        page_size = 10
        pages = [students[i:i + page_size] for i in range(0, len(students), page_size)]

        ctx["class_name"] = class_name
        ctx["students"] = students
        ctx["pages"] = pages
        ctx["total_pages"] = len(pages)
        ctx["issued_date"] = today.strftime("%d/%m/%Y")
        ctx["expiry_date"] = (today.replace(year=today.year + 1) - timedelta(days=1)).strftime("%d/%m/%Y")
        return ctx


class StudentDeleteView(StudentLifecycleMixin, RoleRequiredMixin, View):
    """
    Attempt to delete a student. If the student has associated attendance, grade,
    or financial records, the system prevents deletion and displays an error
    message telling the user to use Archive instead.
    """
    required_permission = "students.delete_student"

    def post(self, request, pk):
        student = self.get_student(request, pk)

        if student.has_protected_records():
            messages.error(
                request,
                "This student has associated records and cannot be deleted. Use Archive instead."
            )
            return redirect("students:detail", pk=pk)

        try:
            admission_no = student.admission_no
            name = str(student)
            student.delete()
            messages.success(request, f"Student {name} has been permanently deleted.")
            return redirect("students:list")
        except Exception as e:
            messages.error(
                request,
                "This student has associated records and cannot be deleted. Use Archive instead."
            )
            return redirect("students:detail", pk=pk)


class PrintLeavingCertificateView(RoleRequiredMixin, DetailView):
    """Printable leaving/transfer certificate for a student."""
    template_name = "students/leaving_certificate.html"
    model = Student
    context_object_name = "student"
    required_permission = "students.view_student"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["reason"] = self.request.GET.get("reason", "")
        return ctx


def _safe_next(request, fallback):
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(fallback)


class StudentGuardianUnlinkView(StudentLifecycleMixin, RoleRequiredMixin, View):
    """Remove the link between a student and a parent. Both records are kept."""
    required_permission = "students.change_student"

    def post(self, request, pk, guardian_pk):
        student = self.get_student(request, pk)
        link = get_object_or_404(StudentGuardian, student=student, guardian_id=guardian_pk)
        guardian = link.guardian
        link.delete()
        from audit.models import log_event
        log_event(
            actor=request.user, action_type="GUARDIAN_UNLINKED", model_name="StudentGuardian",
            object_id=student.pk,
            description=f"Parent {guardian.full_name} unlinked from student {student.admission_no}.",
            request=request,
        )
        messages.success(request, f"Unlinked {guardian.full_name} from {student.first_name} {student.last_name}.")
        return _safe_next(request, f"/students/{student.pk}/")


class GuardianStudentLinkView(StudentLifecycleMixin, RoleRequiredMixin, View):
    """Link an existing student to this parent from the parent's profile."""
    required_permission = "students.change_student"

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk, is_archived=False)
        student_id = (request.POST.get("student_id") or "").strip()
        student = self.scoped_students(request).filter(pk=student_id).first() if student_id.isdigit() else None
        if student is None:
            messages.error(request, "Search for and select a student to link.")
            return redirect("students:guardian_detail", pk=pk)
        relationship = request.POST.get("relationship", GuardianRelationship.GUARDIAN)
        is_primary = request.POST.get("is_primary") == "on"
        try:
            with transaction.atomic():
                _link, created = _set_guardian_link(student, guardian, relationship, is_primary)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, "The student was not linked: " + "; ".join(getattr(exc, "messages", None) or [str(exc)]))
            return redirect("students:guardian_detail", pk=pk)
        from audit.models import log_event
        log_event(
            actor=request.user, action_type="GUARDIAN_LINKED", model_name="StudentGuardian",
            object_id=student.pk,
            description=f"Parent {guardian.full_name} linked to student {student.admission_no}.",
            request=request,
        )
        name = f"{student.first_name} {student.last_name}"
        if created:
            messages.success(request, f"Linked {name} to {guardian.full_name}.")
        else:
            messages.success(request, f"{name} was already linked: relationship and primary contact updated.")
        return redirect("students:guardian_detail", pk=pk)


def _blocking_records(exc):
    """Human list of the record types that stopped a delete (ProtectedError)."""
    names = sorted({str(o._meta.verbose_name_plural) for o in exc.protected_objects})
    return ", ".join(names)


def _purge_student(student, actor, request):
    """Hard-delete one student and their cascading data. Returns an error
    string when other records (attendance, marks, invoices…) still point at it."""
    from django.db.models import ProtectedError
    from audit.models import log_event
    label = f"{student.admission_no} {student.first_name} {student.last_name}".strip()
    try:
        with transaction.atomic():
            # Queryset delete bypasses Student.delete()'s retention guard: this is
            # the deliberate "full delete" for duplicates and junk records.
            Student.objects.filter(pk=student.pk).delete()
    except ProtectedError as exc:
        return f"{label} (has {_blocking_records(exc)})"
    log_event(
        actor=actor, action_type="PERMANENT_DELETE", model_name="Student", object_id=student.pk,
        description=f"Student {label} permanently deleted.", request=request,
    )
    return None


def _report_purge(request, kind, deleted, skipped):
    if deleted:
        messages.success(request, f"Permanently deleted {deleted} {kind}{'' if deleted == 1 else 's'}.")
    if skipped:
        messages.error(
            request,
            "Not deleted, because other records still use them (remove those first or keep them archived): "
            + "; ".join(skipped) + ".",
        )


class StudentBulkPurgeView(StudentLifecycleMixin, RoleRequiredMixin, View):
    """Permanently delete the selected archived students."""
    required_permission = "students.delete_student"

    def post(self, request):
        ids = [i for i in request.POST.getlist("ids") if i.isdigit()]
        students = list(self.scoped_students(request).filter(pk__in=ids, is_archived=True))
        if not students:
            messages.warning(request, "Select at least one archived student to delete.")
            return redirect(f"{reverse('students:list')}?archive_status=archived")
        deleted, skipped = 0, []
        for student in students:
            error = _purge_student(student, request.user, request)
            if error:
                skipped.append(error)
            else:
                deleted += 1
        _report_purge(request, "student", deleted, skipped)
        return redirect(f"{reverse('students:list')}?archive_status=archived")


class GuardianBulkPurgeView(RoleRequiredMixin, View):
    """Permanently delete the selected archived parents, their portal accounts
    and (optionally) their students that have no other parent — used to clear
    out duplicate families."""
    required_permission = "students.delete_parentguardian"

    def post(self, request):
        from django.db.models import ProtectedError
        from audit.models import log_event
        ids = [i for i in request.POST.getlist("ids") if i.isdigit()]
        directory = GuardianListView()
        directory.request = request
        directory.include_all_archives = True
        guardians = list(directory.get_queryset().filter(pk__in=ids, is_archived=True))
        back = f"{reverse('students:guardian_list')}?archive_status=archived"
        if not guardians:
            messages.warning(request, "Select at least one archived parent to delete.")
            return redirect(back)

        with_students = request.POST.get("with_students") == "1"
        can_delete_students = request.user.has_perm("students.delete_student")
        selected = {g.pk for g in guardians}
        deleted, students_deleted, skipped = 0, 0, []
        for guardian in guardians:
            if with_students and can_delete_students:
                for link in list(guardian.studentguardian_set.select_related("student")):
                    student = link.student
                    shared = StudentGuardian.objects.filter(student=student).exclude(guardian_id__in=selected).exists()
                    if shared:
                        continue  # another parent still owns this student; just drop the link
                    error = _purge_student(student, request.user, request)
                    if error:
                        skipped.append(error)
                    else:
                        students_deleted += 1
            user = guardian.user if guardian.user_id and guardian.user.role == UserRole.PARENT else None
            try:
                with transaction.atomic():
                    ParentGuardian.objects.filter(pk=guardian.pk).delete()
            except ProtectedError as exc:
                skipped.append(f"{guardian.full_name} (has {_blocking_records(exc)})")
                continue
            if user is not None:
                try:
                    with transaction.atomic():
                        user.delete()
                except ProtectedError:
                    # Audit history references the account; keep it but locked.
                    user.is_active = False
                    user.save(update_fields=["is_active"])
            log_event(
                actor=request.user, action_type="PERMANENT_DELETE", model_name="ParentGuardian",
                object_id=guardian.pk, description=f"Parent {guardian.full_name} permanently deleted.",
                request=request,
            )
            deleted += 1
        _report_purge(request, "parent", deleted, skipped)
        if students_deleted:
            messages.success(request, f"Also deleted {students_deleted} linked student{'' if students_deleted == 1 else 's'}.")
        return redirect(back)
