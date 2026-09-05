import logging
import re
import time
from datetime import date
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db.models import Q, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from core.permissions import RoleRequiredMixin
from users.models import UserRole

logger = logging.getLogger(__name__)

from .models import GuardianRelationship, ParentGuardian, Student, StudentGuardian, StudentStatus, PDPAConsentLog
from .forms import StudentCreateForm, StudentPhotoUploadForm
from .services import generate_admission_number

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
    from users.role_models import RoleConfig
    rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
    if rc and rc.departments:
        return list(rc.departments)
    default = HOD_ROLE_DEPARTMENT.get(user.role)
    if default:
        return [default]
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
        qs = Student.objects.all()
        if not include_archived:
            qs = qs.filter(is_archived=False)
        if self.request.user.role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(self.request.user)
            if my_classes:
                qs = qs.filter(class_name__in=my_classes)
            else:
                qs = qs.none()

        # FR-STU-001 / NFR-SEC-004: HODs only see students in their department(s)
        hod_roles = {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}
        if self.request.user.role in hod_roles:
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
        
        # Summary Statistics — single aggregated query instead of multiple
        from django.db.models import Count
        stats = qs.aggregate(
            total=Count('id'),
            male=Count('id', filter=Q(gender='male')),
            female=Count('id', filter=Q(gender='female')),
            other=Count('id', filter=Q(gender='other')),
        )
        stats['unspecified'] = stats['total'] - stats['male'] - stats['female'] - stats['other']
        religion_counts = {item['religion']: item['count'] for item in qs.order_by().values('religion').annotate(count=Count('religion')).order_by('-count')[:4] if item['religion']}
        
        ctx["summary"] = {
            "total": stats['total'],
            "male": stats['male'],
            "female": stats['female'],
            "other": stats['other'],
            "unspecified": stats['unspecified'],
            "religions": religion_counts,
        }
        
        from academics.models import Department, GradeClass
        # Scope filter dropdowns to the user's allowed departments (DB/UI-configurable)
        hod_roles = {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}
        allowed_depts = _hod_departments(self.request.user) if self.request.user.role in hod_roles else None
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
                Student.objects.values_list("class_name", flat=True).distinct().order_by("class_name")
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
        
        # Welfare incidents
        from welfare.models import WelfareObservation
        welfare_incidents = WelfareObservation.objects.filter(student=student).select_related('submitted_by', 'reviewed_by').order_by('-observation_date')
        
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
            'present': sum(1 for a in attendance_data if a.status == 'present'),
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

        # Financial information — visible to roles with finance view permission
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
        
        # Evaluate stats BEFORE slicing the queryset — single aggregated query
        welfare_agg = WelfareObservation.objects.filter(student=student).aggregate(
            total=Count('id'),
            critical=Count('id', filter=Q(severity='critical')),
            high=Count('id', filter=Q(severity='high')),
            medium=Count('id', filter=Q(severity='medium')),
            low=Count('id', filter=Q(severity='low')),
        )
        welfare_stats = welfare_agg

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

        # Admissions documents — visible to users with admissions permission
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
            "welfare_incidents": list(welfare_incidents[:5]),
            "welfare_stats": welfare_stats,
            "discipline_incidents": list(discipline_incidents[:5]),
            "discipline_stats": discipline_incidents.aggregate(
                total=Count('id'),
                open=Count('id', filter=Q(status='pending_review') | Q(status='under_investigation')),
                resolved=Count('id', filter=Q(status='resolved')),
                critical=Count('id', filter=Q(severity='critical')),
            ),
            "academic_summary": academic_summary,
            "recent_report": recent_report,
            "all_reports": all_reports,
            "financial_info": financial_info,
            "admission_docs": admission_docs,
            "today_date": date.today().strftime("%d %B %Y"),
            "id_standard_compliant": bool(re.match(r"^ADM-\d{4}-\d{3}$", student.admission_no or "")),
            "ecd_class_names": ecd_class_names,
        })
        
        return ctx


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
                "<b>Blocked:</b> at least one guardian (name and phone) must be linked "
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
        ctx["form"] = kwargs.get("form", StudentCreateForm(instance=student))

        # Onboarding documents from linked applicant
        from admissions.models import ApplicantDocumentReceipt
        applicant = getattr(student, "from_applicant", None)
        if applicant:
            ctx["onboarding_docs"] = ApplicantDocumentReceipt.objects.filter(
                applicant=applicant
            ).order_by("document_type")

        return ctx

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        # FR-STU-001: Editing archived records requires superuser access
        if student.is_archived and not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Archived student records can only be edited by Super Admin.")
        form = StudentCreateForm(request.POST, instance=student)
        if form.is_valid():
            # FR-AUD-001: Capture all changed fields with before/after values
            changed_fields = [f for f in form.changed_data if f != "photo"]
            before = {}
            for f in changed_fields:
                val = getattr(student, f)
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                before[f] = val

            student = form.save()

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
        else:
            messages.error(request, "Please correct the errors below.")
        
        return self.render_to_response(self.get_context_data(pk=pk, form=form))


class StudentArchiveView(RoleRequiredMixin, View):
    required_permission = "students.change_student"

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        if student.is_archived:
            messages.warning(request, "Student is already archived.")
        else:
            student.is_archived = True
            student.archived_at = timezone.now()
            student.archived_by = request.user
            student.status = StudentStatus.WITHDRAWN
            student.save(update_fields=["is_archived", "archived_at", "archived_by", "status"])

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

        return redirect("students:detail", pk=pk)


class StudentRestoreView(RoleRequiredMixin, View):
    """Restore an archived student back to active status."""
    required_permission = "students.change_student"

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)

        if not student.is_archived:
            messages.warning(request, "Student is not archived.")
            return redirect("students:detail", pk=pk)

        from academics.models import GradeClass, get_class_capacity
        from students.models import StudentStatus

        target_class = GradeClass.objects.filter(name=student.class_name).first()
        if not target_class:
            messages.error(
                request,
                f"Cannot restore {student}: class '{student.class_name}' no longer exists. "
                f"Update the student's class first.",
            )
            return redirect("students:detail", pk=pk)

        if target_class.is_exit_grade:
            messages.error(
                request,
                f"Cannot restore {student} into exit grade '{student.class_name}'. "
                f"Exit grades cannot accept restored students.",
            )
            return redirect("students:detail", pk=pk)

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
                return redirect("students:detail", pk=pk)

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

        return redirect("students:detail", pk=pk)


class GuardianListView(RoleRequiredMixin, ListView):
    template_name = "students/guardian_list.html"
    context_object_name = "guardians"
    paginate_by = 30
    required_permission = "students.view_parentguardian"

    def get_queryset(self):
        qs = ParentGuardian.objects.filter(is_archived=False).order_by("full_name")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q))
        # Dynamic scoping: teachers see only guardians linked to their class students
        user = self.request.user
        from users.role_models import RoleConfig
        rc = RoleConfig.objects.filter(role=user.role, is_active=True).first()
        depts = rc.departments if rc and rc.departments else []
        is_teacher_role = not depts and user.role not in (
            UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL,
            UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,
        )
        if is_teacher_role or user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = get_teacher_assigned_classes(user)
            if my_classes:
                my_student_ids = Student.objects.filter(
                    class_name__in=my_classes, is_archived=False
                ).values_list("pk", flat=True)
                qs = qs.filter(studentguardian__student_id__in=my_student_ids).distinct()
            else:
                qs = qs.none()
        elif user.role in {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}:
            my_depts = _hod_departments(user)
            if my_depts:
                from academics.models import GradeClass
                dept_class_names = GradeClass.objects.filter(
                    department__in=my_depts
                ).values_list("name", flat=True)
                dept_student_ids = Student.objects.filter(
                    class_name__in=dept_class_names, is_archived=False
                ).values_list("pk", flat=True)
                qs = qs.filter(studentguardian__student_id__in=dept_student_ids).distinct()
        return qs.select_related('user').prefetch_related('studentguardian_set__student')


class GuardianDetailView(RoleRequiredMixin, DetailView):
    template_name = "students/guardian_detail.html"
    model = ParentGuardian
    context_object_name = "guardian"
    required_permission = "students.view_parentguardian"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        linked = self.object.studentguardian_set.select_related("student")
        ctx["linked_students"] = linked

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


class GuardianCreateView(RoleRequiredMixin, TemplateView):
    template_name = "students/guardian_form.html"
    required_permission = "students.add_parentguardian"

    def post(self, request):
        # FR-PDPA-002: PDPA consent must be recorded before creating a guardian
        pdpa_consent_given = request.POST.get("pdpa_consent_given") == "on"
        pdpa_consent_method = request.POST.get("pdpa_consent_method", "").strip()
        pdpa_consent_version = request.POST.get("pdpa_consent_version", "").strip()

        if not pdpa_consent_given or not pdpa_consent_method:
            messages.error(
                request,
                "PDPA consent must be recorded before this record can be saved."
            )
            return redirect("students:guardian_create")
        # FR-PAR-004: the version of the consent statement presented is mandatory
        if not pdpa_consent_version:
            messages.error(
                request,
                "The version of the PDPA consent statement must be recorded with the consent."
            )
            return redirect("students:guardian_create")

        # FR-PAR-001: Phone is required; student link is optional
        phone = request.POST.get("phone", "").strip()
        student_id = request.POST.get("student_id")
        if not phone:
            messages.error(request, "A primary phone number is required.")
            return redirect("students:guardian_create")

        student = None
        if student_id:
            try:
                student = Student.objects.get(pk=student_id)
            except Student.DoesNotExist:
                messages.warning(request, "Selected student not found — guardian created without link.")


        try:
            guardian = ParentGuardian.objects.create(
                full_name=request.POST.get("full_name", "").strip(),
                phone=phone,
                secondary_phone=request.POST.get("secondary_phone", "").strip(),
                email=request.POST.get("email", "").strip(),
                address=request.POST.get("address", "").strip(),
                preferred_invoice_name=request.POST.get("preferred_invoice_name", "").strip(),
                preferred_language=request.POST.get("preferred_language", "en"),
                pdpa_consent_given=True,
                pdpa_consent_method=pdpa_consent_method,
                pdpa_consent_version=pdpa_consent_version,
                pdpa_consented_at=timezone.now(),
            )
            PDPAConsentLog.objects.create(
                guardian=guardian, action=PDPAConsentLog.Action.GIVEN,
                method=pdpa_consent_method, version=pdpa_consent_version,
                actor=request.user,
                notes="Consent recorded during guardian creation.",
            )
            # Link student only if one was selected
            if student:
                relationship = request.POST.get("relationship", GuardianRelationship.GUARDIAN)
                is_primary_flag = request.POST.get("is_primary") == "on"
                StudentGuardian.objects.create(
                    student=student,
                    guardian=guardian,
                    relationship=relationship,
                    is_primary=is_primary_flag,
                )
            # Portal access: create linked User account if requested
            portal_access = request.POST.get("portal_access") == "on"
            if portal_access:
                self._create_parent_user(guardian, request.user)
                messages.success(
                    request,
                    f"Guardian {guardian.full_name} created — portal account created. Credentials sent via notification."
                )
            else:
                messages.success(request, f"Guardian {guardian.full_name} created.")

            # Tracker 12.1: audit guardian creation
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="GUARDIAN_CREATED",
                model_name="ParentGuardian",
                object_id=guardian.pk,
                description=(
                    f"Guardian {guardian.full_name} (phone {guardian.phone}) created by "
                    f"{request.user.get_full_name() or request.user.username}"
                ),
                request=request,
            )
            return redirect("students:guardian_detail", pk=guardian.pk)
        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect("students:guardian_create")

    def _create_parent_user(self, guardian, actor):
        """Create a linked User account with role=PARENT for portal access."""
        from users.models import User, UserRole
        from django.contrib.auth.models import Group
        from django.utils.crypto import get_random_string
        from django.template.loader import render_to_string
        from communications.email_service import send_parent_notification
        from core.models import SchoolSettings

        phone_digits = "".join(filter(str.isdigit, guardian.phone or ""))
        base_username = f"parent_{phone_digits[:12]}" if phone_digits else f"parent_{guardian.pk}"
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        raw_password = get_random_string(length=10)
        name_parts = (guardian.full_name or "").strip().split(maxsplit=1)
        parent_first = name_parts[0] if name_parts else ""
        parent_last = name_parts[1] if len(name_parts) > 1 else ""

        parent_user = User.objects.create(
            username=username,
            email=guardian.email or "",
            role=UserRole.PARENT,
            first_name=parent_first,
            last_name=parent_last,
            must_change_password=True,
        )
        parent_user.set_password(raw_password)
        parent_user.save()

        grp, _ = Group.objects.get_or_create(name="role_parent")
        grp.user_set.add(parent_user)

        guardian.user = parent_user
        guardian.save(update_fields=["user", "updated_at"])

        school_name = SchoolSettings.get_settings().school_name or "Hodari School"
        login_url = getattr(settings, 'SITE_URL', 'http://localhost:8000') + '/parent/admission-form/'

        creds_message = (
            f"Dear {guardian.full_name},\n\n"
            f"Your parent portal account has been created. You can now access your child's "
            f"attendance records, report cards, invoices, and school communications.\n\n"
            f"Email: {guardian.email}\n"
            f"Password: {raw_password}\n\n"
            f"IMPORTANT: You will be required to change your password on first login.\n\n"
            f"Parent Portal: {login_url}\n\n"
            f"Thank you for choosing {school_name}."
        )

        tpl_context = {
            "guardian_name": guardian.full_name,
            "email": guardian.email,
            "username": username,
            "temp_password": raw_password,
            "login_url": login_url,
            "school_name": school_name,
            "site_url": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
            "static_url": getattr(settings, 'STATIC_URL', '/static/'),
            "portal_link": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000') + '/parent/admission-form/',
        }

        # Try dynamic DB template first
        from core.email_templates import send_dynamic_email
        db_sent = send_dynamic_email(
            template_type="parent_portal",
            to_email=guardian.email,
            context=tpl_context,
            actor=actor,
        )

        if not db_sent:
            html_body = render_to_string("registration/parent_portal_email.html", tpl_context)

            send_parent_notification(
                guardian=guardian,
                title="Your Parent Portal Account Has Been Created",
                message=creds_message,
                html_body=html_body,
                actor=actor,
                action_type="PARENT_PORTAL_ACCOUNT",
            )


class StudentDataExportView(RoleRequiredMixin, View):
    """FR-PDPA-003: Full data export within 30 min for DSAR compliance."""
    required_permission = "students.view_student"

    # 30-minute SLA timeout in seconds
    DSAR_SLA_TIMEOUT_SECONDS = 30 * 60

    def get(self, request, pk):
        student = get_object_or_404(Student, pk=pk)

        # NFR-PDPA-003: Enforce 30-minute SLA — log start and warn if exceeded
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
                    "observation_date", "concern_type", "severity", "description", "status"
                )
            )
        except Exception:
            logger.exception("Failed to load welfare records for student %s", student.pk)

        discipline = []
        try:
            from discipline.models import DisciplineIncident
            discipline = list(
                DisciplineIncident.objects.filter(student=student).values(
                    "created_at", "incident_type", "severity", "description", "status"
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
                    "term__name", "subject_name", "exam_type__name", "score", "status"
                )
            )
        except Exception:
            logger.exception("Failed to load exam scores for student %s", student.pk)

        report_cards = []
        try:
            from academics.models import ReportCard
            report_cards = list(
                ReportCard.objects.filter(student=student).values(
                    "term__name", "overall_average", "grade", "status", "published_at"
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

        # NFR-PDPA-003: SLA enforcement — check if export exceeded 30 minutes
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

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)
        guardian_id = request.POST.get("guardian_id")
        relationship = request.POST.get("relationship", GuardianRelationship.GUARDIAN)
        is_primary = request.POST.get("is_primary") == "on"

        if guardian_id:
            # Scenario 1: Link existing
            guardian = get_object_or_404(ParentGuardian, pk=guardian_id)
            if not guardian.has_given_consent():
                messages.error(
                    request,
                    "PDPA consent must be recorded before this record can be saved."
                )
                return redirect("students:guardian_link", pk=pk)
            StudentGuardian.objects.get_or_create(
                student=student, 
                guardian=guardian,
                defaults={"relationship": relationship, "is_primary": is_primary}
            )
            messages.success(request, f"Linked existing guardian: {guardian.full_name}")

            # FR-STU-005: Notify Finance Officer if sibling relationship confirmed
            if request.POST.get("confirm_sibling") == "1":
                self._notify_sibling_confirmation(request, student, guardian)

        else:
            # Scenario 2: Create new
            full_name = request.POST.get("full_name", "").strip()
            phone = request.POST.get("phone", "").strip()
            if not full_name or not phone:
                messages.error(request, "Name and Phone are required for new guardians.")
                return self.get(request, pk=pk)

            # FR-PDPA-002: PDPA consent must be recorded before creating/linking a guardian
            pdpa_consent_given = request.POST.get("pdpa_consent_given") == "on"
            pdpa_consent_method = request.POST.get("pdpa_consent_method", "").strip()
            pdpa_consent_version = request.POST.get("pdpa_consent_version", "").strip()
            if not pdpa_consent_given or not pdpa_consent_method:
                messages.error(
                    request,
                    "PDPA consent must be recorded before this record can be saved."
                )
                return redirect("students:guardian_link", pk=pk)
            # FR-PAR-004: the version of the consent statement presented is mandatory
            if not pdpa_consent_version:
                messages.error(
                    request,
                    "The version of the PDPA consent statement must be recorded with the consent."
                )
                return redirect("students:guardian_link", pk=pk)
            
            guardian = ParentGuardian.objects.create(
                full_name=full_name,
                phone=phone,
                secondary_phone=request.POST.get("secondary_phone", "").strip(),
                email=request.POST.get("email", "").strip(),
                address=request.POST.get("address", "").strip(),
                preferred_invoice_name=request.POST.get("preferred_invoice_name", "").strip(),
                pdpa_consent_given=True,
                pdpa_consent_method=pdpa_consent_method,
                pdpa_consent_version=pdpa_consent_version,
                pdpa_consented_at=timezone.now(),
            )
            PDPAConsentLog.objects.create(
                guardian=guardian, action=PDPAConsentLog.Action.GIVEN,
                method=pdpa_consent_method, version=pdpa_consent_version,
                actor=request.user,
                notes="Consent recorded during guardian link flow.",
            )
            StudentGuardian.objects.create(
                student=student,
                guardian=guardian,
                relationship=relationship,
                is_primary=is_primary
            )

            # Portal access: create linked User account if requested
            portal_access = request.POST.get("portal_access") == "on"
            if portal_access:
                self._create_parent_user(guardian, request.user)
                messages.success(
                    request,
                    f"Created and linked new guardian: {guardian.full_name} — portal account created. Credentials sent via notification."
                )
            else:
                messages.success(request, f"Created and linked new guardian: {guardian.full_name}")

            # FR-STU-005: Notify Finance Officer if sibling relationship confirmed
            if request.POST.get("confirm_sibling") == "1":
                self._notify_sibling_confirmation(request, student, guardian)

        return redirect("students:detail", pk=pk)

    def _notify_sibling_confirmation(self, request, student: Student, guardian: ParentGuardian) -> None:
        """Delegates to the standalone notify_sibling_confirmation function."""
        notify_sibling_confirmation(request, student, guardian)

    def _create_parent_user(self, guardian: ParentGuardian, actor) -> None:
        """Create a linked User account with role=PARENT for portal access."""
        from users.models import User
        from django.contrib.auth.models import Group
        from django.utils.crypto import get_random_string
        from django.template.loader import render_to_string
        from communications.email_service import send_parent_notification
        from core.models import SchoolSettings

        phone_digits = "".join(filter(str.isdigit, guardian.phone or ""))
        base_username = f"parent_{phone_digits[:12]}" if phone_digits else f"parent_{guardian.pk}"
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        raw_password = get_random_string(length=10)
        name_parts = (guardian.full_name or "").strip().split(maxsplit=1)
        parent_first = name_parts[0] if name_parts else ""
        parent_last = name_parts[1] if len(name_parts) > 1 else ""

        parent_user = User.objects.create(
            username=username,
            email=guardian.email or "",
            role=UserRole.PARENT,
            first_name=parent_first,
            last_name=parent_last,
            must_change_password=True,
        )
        parent_user.set_password(raw_password)
        parent_user.save()

        grp, _ = Group.objects.get_or_create(name="role_parent")
        grp.user_set.add(parent_user)

        guardian.user = parent_user
        guardian.save(update_fields=["user", "updated_at"])

        school_name = SchoolSettings.get_settings().school_name or "Hodari School"
        login_url = getattr(settings, 'SITE_URL', 'http://localhost:8000') + '/parent/admission-form/'

        creds_message = (
            f"Dear {guardian.full_name},\n\n"
            f"Your parent portal account has been created. You can now access your child's "
            f"attendance records, report cards, invoices, and school communications.\n\n"
            f"Email: {guardian.email}\n"
            f"Password: {raw_password}\n\n"
            f"IMPORTANT: You will be required to change your password on first login.\n\n"
            f"Parent Portal: {login_url}\n\n"
            f"Thank you for choosing {school_name}."
        )

        tpl_context = {
            "guardian_name": guardian.full_name,
            "email": guardian.email,
            "username": username,
            "temp_password": raw_password,
            "login_url": login_url,
            "school_name": school_name,
            "site_url": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),
            "static_url": getattr(settings, 'STATIC_URL', '/static/'),
            "portal_link": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000') + '/parent/admission-form/',
        }

        # Try dynamic DB template first
        from core.email_templates import send_dynamic_email
        db_sent = send_dynamic_email(
            template_type="parent_portal",
            to_email=guardian.email,
            context=tpl_context,
            actor=actor,
        )

        if not db_sent:
            html_body = render_to_string("registration/parent_portal_email.html", tpl_context)

            send_parent_notification(
                guardian=guardian,
                title="Your Parent Portal Account Has Been Created",
                message=creds_message,
                html_body=html_body,
                actor=actor,
                action_type="PARENT_PORTAL_ACCOUNT",
            )


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


class GuardianEditView(RoleRequiredMixin, TemplateView):
    """FR-PAR-001: Edit an existing guardian record."""
    template_name = "students/guardian_form.html"
    required_permission = "students.change_parentguardian"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        guardian = get_object_or_404(ParentGuardian, pk=kwargs["pk"])
        ctx["guardian"] = guardian
        ctx["edit_mode"] = True
        ctx["linked_students"] = guardian.studentguardian_set.select_related("student")
        return ctx

    def post(self, request, pk):
        guardian = get_object_or_404(ParentGuardian, pk=pk)
        full_name = request.POST.get("full_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        if not full_name or not phone:
            messages.error(request, "Name and phone are required.")
            return redirect("students:guardian_edit", pk=pk)

        # FR-PAR-001: Sibling scope — users without school-wide student access
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
                    "You do not have access to edit this guardian's record."
                )
                return redirect("students:guardian_detail", pk=pk)

        before_name = guardian.full_name
        before_phone = guardian.phone

        guardian.full_name = full_name
        guardian.phone = phone
        guardian.secondary_phone = request.POST.get("secondary_phone", "").strip()
        guardian.email = request.POST.get("email", "").strip()
        guardian.address = request.POST.get("address", "").strip()
        guardian.preferred_invoice_name = request.POST.get("preferred_invoice_name", "").strip()
        lang = request.POST.get("preferred_language", "en")
        if lang in ("en", "sw"):
            guardian.preferred_language = lang
        guardian.save()

        # FR-PAR-002: Primary contact replacement — if is_primary changed, clear other primaries
        new_is_primary = request.POST.get("is_primary") == "on"
        student_id = request.POST.get("scope_student_id")
        if student_id and new_is_primary:
            StudentGuardian.objects.filter(
                student_id=student_id, is_primary=True
            ).exclude(guardian=guardian).update(is_primary=False)
            StudentGuardian.objects.filter(
                student=student_id, guardian=guardian
            ).update(is_primary=True)

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="GUARDIAN_UPDATED",
            model_name="ParentGuardian",
            object_id=guardian.pk,
            description=f"Guardian {guardian.full_name} updated by {request.user.username}",
            before={"full_name": before_name, "phone": before_phone},
            after={"full_name": guardian.full_name, "phone": guardian.phone},
            request=request,
        )
        messages.success(request, f"Guardian {guardian.full_name} updated.")
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
                "Archive the guardian instead."
            )
            return redirect("students:guardian_detail", pk=pk)

        name = guardian.full_name
        guardian.is_archived = True
        guardian.archived_at = timezone.now()
        guardian.archived_by = request.user
        guardian.save(update_fields=["is_archived", "archived_at", "archived_by", "updated_at"])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="GUARDIAN_DELETED",
            model_name="ParentGuardian",
            object_id=guardian.pk,
            description=f"Guardian {name} archived by {request.user.username}",
            request=request,
        )
        messages.success(request, f"Guardian {name} has been removed.")
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
        from core.teacher_context import get_teacher_assigned_classes

        q = request.GET.get("q", "").strip()
        include_archived = request.GET.get("include_archived", "") == "1"
        if len(q) < 2:
            return JsonResponse({"students": [], "count": 0})

        filters = Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(admission_no__icontains=q)
        if not include_archived:
            filters = filters & Q(is_archived=False)

        base_qs = Student.objects.filter(filters)
        if request.user.role == UserRole.TEACHER:
            my_classes = get_teacher_assigned_classes(request.user)
            if my_classes:
                base_qs = base_qs.filter(class_name__in=my_classes)
            else:
                return JsonResponse({"students": [], "count": 0})

        students = base_qs.select_related("academic_year").order_by("class_name", "last_name")[:50]

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
        student.admission_no = new_id
        student.save(update_fields=["admission_no", "updated_at"])
        
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
                    student.admission_no = new_id
                    student.save(update_fields=["admission_no", "updated_at"])
                    
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
        # Sibling info might be useful for identification? (Optional)
        return ctx


class StudentDeleteView(RoleRequiredMixin, View):
    """
    Attempt to delete a student. If the student has associated attendance, grade,
    or financial records, the system prevents deletion and displays an error
    message telling the user to use Archive instead.
    """
    required_permission = "students.delete_student"

    def post(self, request, pk):
        student = get_object_or_404(Student, pk=pk)

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