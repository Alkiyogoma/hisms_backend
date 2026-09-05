import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, TemplateView, View
import json

from academics.ecd_utils import build_ecd_report_context, ecd_template_type_from_class_name, grade_class_names_for_department
from academics.forms import LessonPlanForm, LessonPlanReviewForm, ExamScoreFilterForm, SubjectForm, TermForm
from academics.utils import get_current_term
from academics.models import (
    AcademicYear,
    Department,
    ECDEvaluation,
    ExamScore,
    ExamType,
    LessonPlan,
    LessonPlanAttachment,
    LessonPlanStatus,
    GradeClass,
    ProgressionConfig,
    ProgressionCase,
    ProgressionStatus,
    ProgressionOutcome,
    PromotionRun,
    ReportCard,
    ReportCardStatus,
    Term,
    GradeClass,
    get_exam_weights,
    get_active_exam_types,
    get_active_ecd_templates,
    ABCPaceProgress,
    ABCScripture,
    ABCReadingProgramme,
    ABCGeneralAssignment,
    ABCInternalExam,
    Subject,
)
from academics.services import generate_class_reports, sign_off_report, calculate_progression_cases
from audit.models import log_event
from students.models import Student, StudentStatus, EnrollmentHistory
from users.models import UserRole
from core.permissions import RoleRequiredMixin
from core.teacher_context import get_teacher_assigned_classes, get_teacher_assigned_classes_from_tca, is_ecd_teacher
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


class ECDEvaluationEntryView(RoleRequiredMixin, TemplateView):
    template_name = "academics/ecd_evaluation_entry.html"
    allowed_roles = [UserRole.TEACHER, UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ECD_HOD]
    required_permission = "academics.change_reportcard"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["academics_tab"] = "ecd_evaluations"

        # UAT ECD-008: Pre-School formal assessment blocked in Term 2
        current_term = get_current_term()
        ctx["pre_school_term2_blocked"] = False
        if current_term and "Term 2" in (current_term.name or ""):
            ctx["pre_school_term2_blocked"] = True
            ctx["pre_school_term2_message"] = "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."

        return ctx

    # FRD FR-ACAD-ECD: Granular domain competencies per class type
    # Each entry: (section_header, [list of competency strings])
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
                "Honesty & integrity", "Respect for authority",
                "Kindness to peers", "Self-discipline",
                "Attitude toward learning",
            ]),
        ],
    }

    @staticmethod
    def _load_domain_groups():
        """Load domain groups from ECDDomainConfig model, falling back to hardcoded."""
        try:
            from academics.models import ECDDomainConfig
            configs = ECDDomainConfig.objects.all().order_by("class_type", "sort_order")
            if configs.exists():
                result = {}
                for c in configs:
                    result.setdefault(c.class_type, []).append(
                        (c.domain_name, c.competencies or [])
                    )
                return result
        except Exception:
            pass
        return ECDEvaluationEntryView.DOMAIN_GROUPS

    # Flat domain list derived from domain groups (used for DB keys)
    @classmethod
    def get_flat_domains(cls, template_type):
        groups = cls._load_domain_groups().get(template_type, [])
        return [item for _, items in groups for item in items]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = (self.request.GET.get("class_name") or "").strip()
        template_type = (self.request.GET.get("template_type") or "").strip()

        # ECD tabs derived dynamically from GradeClass (FRD 2.3: ECD department)
        from academics.models import GradeClass, Department
        ecd_classes = (
            GradeClass.objects.filter(department=Department.ECD)
            .order_by("sort_order", "name")
        )
        _type_hints = [
            ("pre_k", ["pre-k", "pre k", "prek", "kindergarten"]),
            ("kindergarten", ["kg", "kindergarten"]),
            ("pre_school", ["pre-school", "pre school", "preschool"]),
            ("abc", ["abc"]),
        ]
        ECD_TABS = []
        for gc in ecd_classes:
            name = gc.name.lower()
            template_type = "pre_k"
            for _tt, hints in _type_hints:
                if any(h in name for h in hints):
                    template_type = _tt
                    break
            ECD_TABS.append({"label": gc.name, "class_name": gc.name, "template_type": template_type})
        if not ECD_TABS:
            ECD_TABS = [
                {"label": "Pre-Kindergarten", "class_name": "Pre-K",          "template_type": "pre_k"},
                {"label": "Kindergarten",     "class_name": "KG",             "template_type": "kindergarten"},
                {"label": "Pre-School (RR)",  "class_name": "Pre-School",     "template_type": "pre_school"},
                {"label": "ABC Class",        "class_name": "ABC",            "template_type": "abc"},
            ]
        if self.request.user.role == UserRole.TEACHER:
            assigned_classes = get_teacher_assigned_classes(self.request.user)
            ctx["ecd_tabs"] = [t for t in ECD_TABS if t["class_name"] in assigned_classes]
        else:
            ctx["ecd_tabs"] = ECD_TABS

        # Auto-detect current term via date-based resolution (FR-CAL-003)
        from academics.utils import get_current_term
        auto_term = get_current_term()
        ctx["current_term"] = auto_term

        # If no class_name in URL, default to first available tab
        if not class_name and ctx["ecd_tabs"]:
            class_name = ctx["ecd_tabs"][0]["class_name"]
            template_type = ctx["ecd_tabs"][0]["template_type"]

        # Auto-resolve template_type from class name if missing
        if class_name and not template_type:
            _tmap = {t["class_name"]: t["template_type"] for t in ECD_TABS}
            template_type = _tmap.get(class_name, "pre_k")

        ctx["selected_class"] = class_name
        ctx["selected_template"] = template_type
        term = auto_term
        ctx["selected_term"] = term

        if not term or not class_name or not template_type:
            return ctx

        # Security: Ensure teacher is allowed to see this class
        if self.request.user.role == UserRole.TEACHER:
            from timetable.models import TimetableSlot
            if not TimetableSlot.objects.filter(teacher=self.request.user, class_name=class_name).exists():
                messages.error(self.request, "You are not authorized to evaluate this class.")
                return ctx

        students = Student.objects.filter(class_name=class_name, is_archived=False).order_by("last_name", "first_name")

        # Only generate missing report cards once
        if not ReportCard.objects.filter(term=term, student__in=students).exists():
            from academics.services import generate_class_reports
            generate_class_reports(term.id, class_name, self.request.user)

        report_cards = ReportCard.objects.filter(term=term, student__in=students)
        for rc in report_cards:
            if not rc.is_ecd_report or rc.ecd_template_type != template_type:
                rc.is_ecd_report = True
                rc.ecd_template_type = template_type
                rc.save(update_fields=["is_ecd_report", "ecd_template_type"])

        domains = self.get_flat_domains(template_type)
        domain_groups = self._load_domain_groups().get(template_type, [])

        # Fetch existing evaluations
        evaluations = ECDEvaluation.objects.filter(report_card__in=report_cards)
        eval_map = {}  # (report_card_id, domain) -> rating
        for ev in evaluations:
            eval_map[(ev.report_card_id, ev.domain)] = ev.rating

        # Welfare linkage: flag students with open high/critical welfare observations
        from welfare.models import WelfareObservation, WelfareSeverity
        welfare_flags = {}
        if term:
            open_welfare = WelfareObservation.objects.filter(
                student__in=students,
                severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
            ).exclude(hod_status="resolved").select_related("student")
            for obs in open_welfare:
                welfare_flags.setdefault(obs.student_id, []).append(obs)

        ctx["students_data"] = []
        for student in students:
            rc = report_cards.filter(student=student).first()
            if not rc:
                continue

            # Build grouped evaluation structure for template
            student_groups = []
            all_rated = True
            for section_title, section_items in domain_groups:
                section_evals = []
                for domain in section_items:
                    rating = eval_map.get((rc.id, domain), "")
                    if not rating:
                        all_rated = False
                    section_evals.append({"domain": domain, "rating": rating})
                student_groups.append({"section": section_title, "items": section_evals})

            # Also flat list for compatibility
            flat_evals = [{"domain": d, "rating": eval_map.get((rc.id, d), "")} for d in domains]

            # ABC Specific Data
            abc_data = {}
            if template_type == "abc":
                abc_data = {
                    "pace_progress": rc.abc_pace_progress.all().order_by("subject", "pace_no"),
                    "scripture": {s.quarter: s.verse for s in rc.abc_scripture.all()},
                    "reading": {r.quarter: r for r in rc.abc_reading.all()},
                    "assignments": {a.quarter: a for a in rc.abc_assignments.all()},
                }

            ctx["students_data"].append({
                "student": student,
                "report_card": rc,
                "evaluations": flat_evals,
                "domain_groups": student_groups,
                "abc": abc_data,
                "is_complete": all_rated and len(rc.teacher_comments or "") >= 50,
                "welfare_flags": welfare_flags.get(student.id, []),
            })

        ctx["domains"] = domains
        ctx["domain_groups"] = domain_groups

        return ctx

    def post(self, request, *args, **kwargs):
        term_id = request.POST.get("term")
        class_name = request.POST.get("class_name")
        template_type = request.POST.get("template_type")
        
        if not (term_id and class_name and template_type):
            messages.error(request, "Missing selection parameters.")
            return redirect("academics:ecd_evaluations_entry")

        # Security: Enforce own classes only for teachers
        if request.user.role == UserRole.TEACHER:
            from timetable.models import TimetableSlot
            if not TimetableSlot.objects.filter(teacher=request.user, class_name=class_name).exists():
                messages.error(request, "Access denied: This class is not assigned to you.")
                return redirect("academics:ecd_evaluations_entry")
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if template_type == "pre_school":
            term_obj = Term.objects.get(pk=term_id) if term_id else None
            if term_obj and "Term 2" in term_obj.name:
                messages.error(request, "Pre-School assessment is only conducted in Term 1 and Term 3 per school policy.")
                return redirect("academics:ecd_evaluations_entry")

        domains = self.get_flat_domains(template_type)
        students = Student.objects.filter(class_name=class_name, is_archived=False)
        report_cards = ReportCard.objects.filter(term_id=term_id, student__in=students)

        updated_count = 0
        for rc in report_cards:
            if rc.status != ReportCardStatus.DRAFT:
                if request.user.role != UserRole.SUPER_ADMIN:
                    continue

            # Teacher comments
            comment = request.POST.get(f"comment_{rc.id}")
            if comment is not None and comment.strip() != rc.teacher_comments:
                rc.teacher_comments = comment
                rc.save(update_fields=["teacher_comments"])
                from audit.models import log_event
                log_event(
                    actor=request.user,
                    action_type="ECD_COMMENT_SAVED",
                    model_name="ReportCard",
                    object_id=rc.pk,
                    description=f"ECD teacher comment saved for {rc.student.admission_no} in {class_name}",
                    request=request,
                )

            for domain in domains:
                rating = request.POST.get(f"eval_{rc.id}_{domain}")
                if rating:
                    if rating not in ["E", "G", "S", "N"]:
                        messages.error(request, f"Invalid rating '{rating}' for domain '{domain}'. Allowed: E, G, S, N.")
                        continue
                    ECDEvaluation.objects.update_or_create(
                        report_card=rc,
                        domain=domain,
                        defaults={"rating": rating}
                    )

            # ABC Extra Data Saving
            if template_type == "abc":
                from academics.models import ABCPaceProgress, ABCScripture, ABCReadingProgramme, ABCGeneralAssignment
                
                # 1. PACE Progress
                # Expected format: pace_subject_rcid_idx, pace_no_rcid_idx, etc.
                # Simplified for this specific implementation: we'll handle subjects Math, English, Word Building, Science, Social Studies
                subjects = ["Math", "English", "Word Building", "Science", "Social Studies"]
                for sub in subjects:
                    for i in range(1, 5): # Up to 4 PACEs per subject
                        pace_no = request.POST.get(f"pace_no_{rc.id}_{sub}_{i}")
                        if pace_no:
                            ABCPaceProgress.objects.update_or_create(
                                report_card=rc, subject=sub, pace_no=pace_no,
                                defaults={
                                    "sticker_no": request.POST.get(f"pace_stk_{rc.id}_{sub}_{i}", ""),
                                    "status": request.POST.get(f"pace_status_{rc.id}_{sub}_{i}", "in_progress"),
                                    "supervisor_score": request.POST.get(f"pace_s_{rc.id}_{sub}_{i}") or None,
                                    "moderator_score": request.POST.get(f"pace_m_{rc.id}_{sub}_{i}") or None,
                                    "date_completed": request.POST.get(f"pace_date_{rc.id}_{sub}_{i}", ""),
                                }
                            )

                # 2. Scripture, Reading, Assignments (Terms 1-3 — FR-CAL-002)
                for q in range(1, 4):
                    verse = request.POST.get(f"abc_verse_{rc.id}_{q}")
                    if verse is not None:
                        ABCScripture.objects.update_or_create(report_card=rc, quarter=q, defaults={"verse": verse})
                    
                    item_name = request.POST.get(f"abc_assign_item_{rc.id}_{q}")
                    if item_name is not None:
                        ABCGeneralAssignment.objects.update_or_create(
                            report_card=rc, quarter=q, 
                            defaults={
                                "item_name": item_name,
                                "score": request.POST.get(f"abc_assign_score_{rc.id}_{q}") or None
                            }
                        )
                    
                    wpm = request.POST.get(f"abc_read_wpm_{rc.id}_{q}")
                    if wpm is not None:
                        ABCReadingProgramme.objects.update_or_create(
                            report_card=rc, quarter=q,
                            defaults={
                                "wpm": wpm or None,
                                "percentage": request.POST.get(f"abc_read_pct_{rc.id}_{q}") or None,
                                "comprehension_score": request.POST.get(f"abc_read_comp_{rc.id}_{q}") or None,
                            }
                        )

            updated_count += 1

        messages.success(request, f"Saved evaluations for {updated_count} students.")
        return redirect(f"{request.path}?term={term_id}&class_name={class_name}&template_type={template_type}")


def _export_students_for_user(user):
    """Scope academic CSV export by role (Primary HOD / ECD HOD cannot export other departments)."""
    from students.models import Student

    qs = Student.objects.filter(is_archived=False).order_by("class_name", "last_name")
    role = getattr(user, "role", None)
    if role in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL):
        return qs
    if role == UserRole.PRIMARY_HOD:
        names = grade_class_names_for_department(Department.PRIMARY)
        return qs.filter(class_name__in=names)
    if role == UserRole.ECD_HOD:
        names = grade_class_names_for_department(Department.ECD)
        return qs.filter(class_name__in=names)
    return Student.objects.none()


def _ecd_api_class_allowed(request, class_name: str) -> bool:
    if not (class_name or "").strip():
        return False
    role = request.user.role
    if role in (UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
        return True
    if role == UserRole.TEACHER:
        from timetable.models import TimetableSlot

        if TimetableSlot.objects.filter(teacher=request.user, class_name=class_name).exists():
            return True
        from core.teacher_context import get_teacher_assigned_classes_from_tca
        return class_name in get_teacher_assigned_classes_from_tca(request.user)
    if role == UserRole.ECD_HOD:
        return class_name in grade_class_names_for_department(Department.ECD)
    return False


def _ecd_api_student_allowed(request, student: Student) -> bool:
    return _ecd_api_class_allowed(request, student.class_name)


class ECDStudentsAPIView(RoleRequiredMixin, View):
    """API endpoint to get students for a specific ECD class"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "students.view_student"

    def get(self, request):
        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        students = (
            Student.objects.filter(class_name=class_name, is_archived=False)
            .order_by("last_name", "first_name")
            .values("id", "first_name", "last_name", "admission_no", "date_of_birth")
        )

        return JsonResponse({"students": list(students)})


class ECDEvaluationAPIView(RoleRequiredMixin, View):
    """API endpoint for ECD evaluation CRUD operations"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def get(self, request, student_id=None):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        tpl = ecd_template_type_from_class_name(student.class_name)
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if tpl == "pre_school":
            current_term_chk = get_current_term()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = get_current_term()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        report_card, created = ReportCard.objects.get_or_create(
            student=student,
            term=term,
            defaults={
                "is_ecd_report": True,
                "ecd_template_type": tpl,
                "generated_by": request.user,
                "status": ReportCardStatus.DRAFT,
            },
        )
        if report_card.ecd_template_type != tpl:
            report_card.is_ecd_report = True
            report_card.ecd_template_type = tpl
            report_card.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])

        report_card.populate_attendance_summary()

        evaluations = ECDEvaluation.objects.filter(report_card=report_card)
        eval_data = {ev.domain: ev.rating for ev in evaluations}

        domains = ECDEvaluationEntryView.get_flat_domains(tpl)

        response_data = {
            "student": {
                "id": student.id,
                "name": f"{student.first_name} {student.last_name}",
                "class_name": student.class_name,
                "admission_no": student.admission_no,
            },
            "report_card_id": report_card.id,
            "ecd_template_type": tpl,
            "evaluations": eval_data,
            "domains": domains,
            "teacher_comments": report_card.teacher_comments or "",
            "ecd_remarks": report_card.ecd_remarks or "",
            "status": report_card.status,            "is_locked": report_card.status != ReportCardStatus.DRAFT,
            "is_term_locked": term.is_locked,
        }

        # Add ABC specific data
        if tpl == 'abc':
            paces = ABCPaceProgress.objects.filter(report_card=report_card)
            response_data['abc_pace_progress'] = [
                {
                    "subject": p.subject,
                    "pace_no": p.pace_no,
                    "sticker_no": p.sticker_no,
                    "status": p.status,
                    "supervisor_score": float(p.supervisor_score) if p.supervisor_score else None,
                    "moderator_score": float(p.moderator_score) if p.moderator_score else None,
                    "date_completed": p.date_completed
                } for p in paces
            ]
            
            scripture = ABCScripture.objects.filter(report_card=report_card)
            response_data['abc_scripture'] = {s.quarter: s.verse for s in scripture}
            
            reading = ABCReadingProgramme.objects.filter(report_card=report_card)
            response_data['abc_reading'] = {
                r.quarter: {
                    "wpm": r.wpm,
                    "percentage": float(r.percentage) if r.percentage else None,
                    "comprehension_score": float(r.comprehension_score) if r.comprehension_score else None
                } for r in reading
            }
            
            assignments = ABCGeneralAssignment.objects.filter(report_card=report_card)
            response_data['abc_assignments'] = {
                a.quarter: {
                    "item_name": a.item_name,
                    "score": float(a.score) if a.score else None
                } for a in assignments
            }

            # FR-ACAD-013: ABC Class Term 3 internal exam
            internal_exams = ABCInternalExam.objects.filter(report_card=report_card)
            response_data['abc_internal_exams'] = {
                e.subject: e.rating for e in internal_exams
            }

        return JsonResponse(response_data)

    def post(self, request, student_id=None):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        tpl = ecd_template_type_from_class_name(student.class_name)
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if tpl == "pre_school":
            current_term_chk = get_current_term()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = get_current_term()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        if term.is_locked:
            return JsonResponse(
                {"error": "This term is locked and cannot accept score entry."},
                status=403,
            )

        report_card, created = ReportCard.objects.get_or_create(
            student=student,
            term=term,
            defaults={
                "is_ecd_report": True,
                "ecd_template_type": tpl,
                "generated_by": request.user,
                "status": ReportCardStatus.DRAFT,
            },
        )
        if report_card.ecd_template_type != tpl:
            report_card.is_ecd_report = True
            report_card.ecd_template_type = tpl
            report_card.save(update_fields=["is_ecd_report", "ecd_template_type", "updated_at"])

        report_card.populate_attendance_summary()

        # UAT ECD-009: Lock evaluations when report is not in DRAFT status
        # SA can bypass lock for corrections after HOD review
        if report_card.status != ReportCardStatus.DRAFT:
            if request.user.role != UserRole.SUPER_ADMIN:
                return JsonResponse(
                    {"error": "This report has already been submitted and is locked for editing."},
                    status=403,
                )

        from django.db import transaction
        with transaction.atomic():
            evaluations = data.get("evaluations", {})
        for domain, rating in evaluations.items():
            if rating not in ["E", "G", "S", "N"]:
                return JsonResponse(
                    {"error": f"Invalid rating '{rating}' for domain '{domain}'. Allowed: E, G, S, N."},
                    status=400,
                )
            ECDEvaluation.objects.update_or_create(
                    report_card=report_card,
                    domain=domain,
                    defaults={"rating": rating},
                )

        # Handle ABC specific data in POST
        if tpl == 'abc':
            pace_data = data.get("abc_pace_progress", [])
            for p in pace_data:
                ABCPaceProgress.objects.update_or_create(
                    report_card=report_card,
                    subject=p.get("subject"),
                    pace_no=p.get("pace_no"),
                    defaults={
                        "sticker_no": p.get("sticker_no", ""),
                        "status": p.get("status", "in_progress"),
                        "supervisor_score": p.get("supervisor_score"),
                        "moderator_score": p.get("moderator_score"),
                        "date_completed": p.get("date_completed", "")
                    }
                )
            
            scripture_data = data.get("abc_scripture", {})
            for q, v in scripture_data.items():
                if v:
                    ABCScripture.objects.update_or_create(
                        report_card=report_card,
                        quarter=int(q),
                        defaults={"verse": v}
                    )
            
            reading_data = data.get("abc_reading", {})
            for q, rd in reading_data.items():
                ABCReadingProgramme.objects.update_or_create(
                    report_card=report_card,
                    quarter=int(q),
                    defaults={
                        "wpm": rd.get("wpm"),
                        "percentage": rd.get("percentage"),
                        "comprehension_score": rd.get("comprehension_score")
                    }
                )
            
            assignment_data = data.get("abc_assignments", {})
            for q, ad in assignment_data.items():
                ABCGeneralAssignment.objects.update_or_create(
                    report_card=report_card,
                    quarter=int(q),
                    defaults={
                        "item_name": ad.get("item_name", ""),
                        "score": ad.get("score")
                    }
                )

            # FR-ACAD-013: ABC Class Term 3 internal exam — only save in Term 3
            internal_exam_data = data.get("abc_internal_exams", {})
            if internal_exam_data and term and "Term 3" in (term.name or ""):
                for subject, rating in internal_exam_data.items():
                    if rating in ["E", "G", "S", "N"]:
                        ABCInternalExam.objects.update_or_create(
                            report_card=report_card,
                            subject=subject,
                            defaults={"rating": rating},
                        )

        teacher_comments = data.get("teacher_comments", "")
        if teacher_comments.strip() and len(teacher_comments.strip()) < 50:
            return JsonResponse(
                {"error": "Teacher comment must be at least 50 characters."},
                status=400,
            )
        if len(teacher_comments.strip()) >= 50:
            report_card.teacher_comments = teacher_comments

        ecd_remarks = (data.get("overall_remarks") or "").strip()
        if ecd_remarks:
            report_card.ecd_remarks = ecd_remarks[:50]

        if teacher_comments or ecd_remarks:
            report_card.save(update_fields=["teacher_comments", "ecd_remarks"])
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="ECD_COMMENT_SAVED",
                model_name="ReportCard",
                object_id=report_card.pk,
                description=f"ECD teacher comment saved for {report_card.student.admission_no}",
                request=request,
            )

        return JsonResponse({"success": True, "message": "Evaluations saved successfully"})


class ECDSubmissionAPIView(RoleRequiredMixin, View):
    """API endpoint for submitting ECD evaluations for HOD review"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.change_reportcard"

    def post(self, request, student_id=None):

        if not student_id:
            return JsonResponse({"error": "Student ID required"}, status=400)

        student = get_object_or_404(Student, id=student_id)

        if not _ecd_api_student_allowed(request, student):
            return JsonResponse({"error": "Access denied for this student"}, status=403)

        tpl = ecd_template_type_from_class_name(student.class_name)
        # UAT ECD-008: Pre-School formal assessment only in Term 1 and Term 3
        if tpl == "pre_school":
            current_term_chk = get_current_term()
            if current_term_chk and "Term 2" in current_term_chk.name:
                return JsonResponse(
                    {"error": "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."},
                    status=403,
                )

        term = get_current_term()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        report_card = get_object_or_404(ReportCard, student=student, term=term)
        report_card.populate_attendance_summary()

        if report_card.status == ReportCardStatus.PENDING_SIGN_OFF:
            return JsonResponse(
                {"error": "This report has already been submitted for review."},
                status=400,
            )
        if report_card.status == ReportCardStatus.PUBLISHED:
            return JsonResponse(
                {"error": "This report has already been published and cannot be re-submitted."},
                status=400,
            )

        tpl = ecd_template_type_from_class_name(student.class_name) or report_card.ecd_template_type
        if not tpl:
            return JsonResponse({"error": "Student is not in a recognised ECD class"}, status=400)

        if not report_card.teacher_comments or len(report_card.teacher_comments.strip()) < 50:
            return JsonResponse({"error": "Teacher comments must be at least 50 characters long"}, status=400)

        required_domains = ECDEvaluationEntryView.get_flat_domains(tpl)
        existing_evaluations = ECDEvaluation.objects.filter(report_card=report_card).values_list(
            "domain", flat=True
        )

        missing_domains = set(required_domains) - set(existing_evaluations)
        if missing_domains:
            return JsonResponse(
                {"error": f'Missing evaluations for domains: {", ".join(missing_domains)}'},
                status=400,
            )

        report_card.status = ReportCardStatus.PENDING_SIGN_OFF
        report_card.comments_submitted = True
        report_card.save(update_fields=["status", "comments_submitted", "updated_at"])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="ECD_SUBMITTED",
            model_name="ReportCard",
            object_id=report_card.pk,
            description=f"ECD evaluations submitted for {student.admission_no} ({student.class_name})",
            request=request
        )

        # FRD: Notify ECD HOD when evaluations are submitted for review
        try:
            from communications.email_service import dispatch_notification
            from users.models import User
            hod_roles = [UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL]
            hods = User.objects.filter(role__in=hod_roles, is_active=True).distinct()
            for hod in hods:
                dispatch_notification(
                    user=hod,
                    title="ECD evaluations submitted for review",
                    message=(
                        f"{request.user.get_full_name()} has submitted ECD evaluations for "
                        f"{student.first_name} {student.last_name} ({student.class_name}). "
                        f"Please review and approve."
                    ),
                    link="/academics/ecd-assessment/?class_name=" + student.class_name,
                    actor=request.user,
                )
        except Exception:
            pass  # Never block submission on notification failure

        return JsonResponse({"success": True, "message": "Report submitted for HOD review"})


class ECDAssessmentView(RoleRequiredMixin, TemplateView):
    """View for ECD assessment entry page (class from query string; default Pre-Kindergarten)."""

    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get_template_names(self):
        class_name = (self.request.GET.get("class_name") or "Pre-K").strip()
        tpl = ecd_template_type_from_class_name(class_name) or "pre_k"
        return [f"academics/ecd_assessment_{tpl}.html"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        class_name = (self.request.GET.get("class_name") or "Pre-K").strip()
        tpl = ecd_template_type_from_class_name(class_name) or "pre_k"

        # FRD 2.3: ECD tabs derived dynamically from GradeClass registry
        from academics.models import GradeClass, Department
        ecd_classes = (
            GradeClass.objects.filter(department=Department.ECD)
            .order_by("sort_order", "name")
        )
        if ecd_classes.exists():
            ECD_TABS = [{"display_name": gc.name, "class_name": gc.name} for gc in ecd_classes]
        else:
            ECD_TABS = [
                {"display_name": "Pre-Kindergarten", "class_name": "Pre-K"},
                {"display_name": "Kindergarten",     "class_name": "KG"},
                {"display_name": "Pre-School",       "class_name": "Pre-School"},
                {"display_name": "ABC Class",        "class_name": "ABC"},
            ]

        if self.request.user.role == UserRole.TEACHER:
            assigned_tca = get_teacher_assigned_classes_from_tca(self.request.user)
            assigned_class_names = set(assigned_tca.keys())
            available_tabs = [tab for tab in ECD_TABS if tab["class_name"] in assigned_class_names]
            ctx["ecd_tabs"] = available_tabs

            if not available_tabs:
                ctx["no_class_assigned"] = True
            elif class_name not in {tab["class_name"] for tab in available_tabs}:
                class_name = available_tabs[0]["class_name"]
                tpl = ecd_template_type_from_class_name(class_name) or "pre_k"
        else:
            ctx["ecd_tabs"] = ECD_TABS

        # Current term for display and term gating (FR-CAL-002: 3 terms)
        current_term = get_current_term()
        ctx["current_term"] = current_term
        if current_term:
            try:
                term_num = int(current_term.name.replace("Term ", ""))
            except (ValueError, AttributeError):
                term_num = 1
        else:
            term_num = 1
        ctx["current_term_num"] = term_num

        ctx["ecd_class_name"] = class_name
        ctx["ecd_template_type"] = tpl
        ctx["ecd_class_label"] = class_name
        ctx["page_title"] = f"ECD Reports — {class_name}"
        ctx["domain_groups"] = ECDEvaluationEntryView._load_domain_groups().get(tpl, [])

        # Pre-K field groups (label, field_id) matching Hodari_09a mockup
        ctx["pre_k_fields_nc"] = [
            ("Recognises numbers 1–10", "n1"), ("Recites numbers 1–10", "n2"),
            ("Understands size (big / small)", "n3"), ("Identifies colours learned this term", "n4"),
            ("Identifies shapes learned this term", "n5"), ("Sorting shapes & colours", "n6"),
        ]
        ctx["pre_k_fields_cs"] = [
            ("Knows first name", "c1"), ("Responds to direct questions", "c2"),
            ("Expresses needs clearly", "c3"), ("Responds well within a group", "c4"),
            ("Repeats sentences", "c5"),
        ]
        ctx["pre_k_fields_ms"] = [
            ("Can tear paper", "m1"), ("Holds & uses paint brush, pencil, crayon", "m2"),
            ("Can thread beads", "m3"), ("Holds spoon and eats without help", "m4"),
            ("Can jump up and down", "m5"), ("Can throw a ball", "m6"),
            ("Can kick a ball", "m7"), ("Participates in music and movement", "m8"),
        ]
        ctx["pre_k_fields_se"] = [
            ("Enjoys school activities", "s1"), ("Plays and shares well with others", "s2"),
            ("Listens and follows instructions", "s3"),
        ]
        ctx["pre_k_fields_sw"] = [
            ("Getting in the water", "sw1"), ("Hand & leg movement", "sw2"),
            ("Attitude", "sw3"), ("Swimming attendance", "sw4"),
        ]

        # KG sections (Hodari_09b)
        ctx["kg_sections"] = [
            ("Numeracy", [
                ("Number counting 1–100", "kn1"), ("Number formation 0–9", "kn2"),
                ("Number sequence 1–40", "kn3"), ("Number recognition 1–40", "kn4"),
                ("Number value", "kn5"), ("Number tracing", "kn6"),
                ("Basic shapes", "kn7"), ("Problem solving", "kn8"),
                ("Can put together puzzles (critical thinking)", "kn9"),
                ("Ability to do a maze (critical thinking)", "kn10"),
                ("Basic addition", "kn11"), ("Basic subtraction", "kn12"),
            ]),
            ("Reading", [
                ("Recognise, compare & distinguish sounds", "kr1"),
                ("Listening to stories", "kr2"),
                ("Vocabulary, grammar & pronunciation", "kr3"),
                ("Songs and rhymes", "kr4"), ("Comprehension", "kr5"),
                ("Ability to sit still and listen to recall", "kr6"),
                ("Reading / blending two to three sounds", "kr7"),
            ]),
            ("Writing", [
                ("Sound and number formation", "kw1"),
                ("Handwriting neatness and pencil grip", "kw2"),
                ("Writing first name", "kw3"), ("Writing last name", "kw4"),
            ]),
        ]
        ctx["kg_general_sections"] = [
            ("Personal", [
                ("Completes work timely", "gp1"), ("Shows initiative and creativity", "gp2"),
                ("Attentive to direction", "gp3"), ("Works well independently", "gp4"),
                ("Exhibits self-confidence", "gp5"), ("Portrays independence", "gp6"),
                ("Exhibits self-control", "gp7"), ("Considerate of others", "gp8"),
                ("Responds well to correction", "gp9"), ("Cleanliness", "gp10"),
                ("Food appetite", "gp11"),
            ]),
            ("Physical Development", [
                ("Swimming", "ph1"), ("Catch and throw", "ph2"),
                ("Balancing", "ph3"), ("Running", "ph4"),
                ("Kicking", "ph5"), ("Jumping jacks", "ph6"),
            ]),
            ("Artistry", [
                ("Good hand and eye coordination to perform a task", "ar1"),
                ("Participates in music and dance", "ar2"),
                ("Fine motor grip", "ar3"), ("Shows creativity in crafts", "ar4"),
            ]),
        ]

        # Pre-School sections (Hodari_09c mockup labels) — 4-point scale
        ctx["pre_school_sections"] = [
            ("Numeracy", [
                ("Counting 1–10: counting", "ps_n1"),
                ("Counting 1–10: recognition", "ps_n2"),
                ("Counting 1–10: matching", "ps_n3"),
                ("Shapes recognition", "ps_sc1"),
                ("Shapes association", "ps_sc2"),
                ("Colour recognition", "ps_cl1"),
                ("Colour association", "ps_cl2"),
            ]),
            ("Pre-Writing", [
                ("colouring, painting, moulding", "ps_pw1"),
                ("eye-hand coordination", "ps_pw2"),
            ]),
            ("School Readiness", [
                ("Readiness to participate in class activities", "ps_sr1"),
                ("Attention span", "ps_sr2"),
                ("Grade level maturity", "ps_sr3"),
                ("Able to eat on their own", "ps_sr4"),
                ("Able to comprehend and follow instructions", "ps_sr5"),
                ("Able to communicate in comprehensible speech", "ps_sr6"),
                ("Potty trained", "ps_sr7"),
            ]),
            ("Social Skills", [
                ("Willing to share", "ps_ss1"),
                ("Plays well and safely with others", "ps_ss2"),
                ("Attitude towards discipline and correction", "ps_ss3"),
            ]),
        ]

        # ABC sections (FR-ACAD-011)
        ctx["abc_pace_subjects"] = ["Mathematics", "English", "Science", "Social Studies", "Word Building", "Scripture"]
        ctx["abc_fields_assignments"] = [
            ("Completes assignments on time", "abc_a1"),
            ("Quality of homework", "abc_a2"),
            ("Independent work habits", "abc_a3"),
        ]
        ctx["abc_fields_character"] = [
            ("Honesty & integrity", "abc_c1"),
            ("Respect for authority", "abc_c2"),
            ("Kindness to peers", "abc_c3"),
            ("Self-discipline", "abc_c4"),
            ("Attitude toward learning", "abc_c5"),
        ]
        ctx["current_term"] = get_current_term()
        from django.utils.timezone import localdate
        ctx["today_display"] = localdate().strftime("%b %d, %Y").lstrip("0").replace(" 0", " ")
        ctx["academics_tab"] = "ecd_evaluations"

        # UAT ECD-008: Pre-School formal assessment blocked in Term 2
        ecd_current_term = ctx.get("current_term")
        ctx["pre_school_term2_blocked"] = False
        if ecd_current_term and "Term 2" in (ecd_current_term.name or ""):
            ctx["pre_school_term2_blocked"] = True
            ctx["pre_school_term2_message"] = "Pre-School formal assessment is only conducted in Term 1 and Term 3 per school policy."

        # Welfare linkage: flag students with open high/critical welfare observations
        from welfare.models import WelfareObservation, WelfareSeverity
        welfare_flags = {}
        students_in_class = Student.objects.filter(class_name=class_name, is_archived=False)
        open_welfare = WelfareObservation.objects.filter(
            student__in=students_in_class,
            severity__in=[WelfareSeverity.HIGH, WelfareSeverity.CRITICAL],
        ).exclude(hod_status="resolved").select_related("student")
        for obs in open_welfare:
            welfare_flags.setdefault(obs.student_id, []).append(obs)
        ctx["welfare_flags"] = welfare_flags

        return ctx



class ECDProgressAPIView(RoleRequiredMixin, View):
    """API endpoint to get ECD evaluation progress for a class"""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "academics.view_reportcard"

    def get(self, request):
        if request.user.role not in [
            UserRole.TEACHER,
            UserRole.ECD_HOD,
            UserRole.HEAD_OF_SCHOOL,
            UserRole.SUPER_ADMIN,
        ]:
            return JsonResponse({"error": "Permission denied"}, status=403)

        class_name = request.GET.get("class_name")
        if not class_name:
            return JsonResponse({"error": "Class name required"}, status=400)

        if not _ecd_api_class_allowed(request, class_name):
            return JsonResponse({"error": "Access denied for this class"}, status=403)

        tpl = ecd_template_type_from_class_name(class_name)
        if not tpl:
            return JsonResponse({"error": "Not an ECD class"}, status=400)

        term = get_current_term()
        if not term:
            return JsonResponse({"error": "No active term found"}, status=400)

        students = Student.objects.filter(class_name=class_name, is_archived=False)
        total_students = students.count()

        if total_students == 0:
            return JsonResponse({"progress": {"total": 0, "completed": 0, "percentage": 0}})

        completed_reports = ReportCard.objects.filter(
            student__in=students,
            term=term,
            is_ecd_report=True,
            ecd_template_type=tpl,
            status__in=[ReportCardStatus.PENDING_SIGN_OFF, ReportCardStatus.PUBLISHED],
        ).count()

        percentage = round((completed_reports / total_students) * 100, 1) if total_students > 0 else 0

        return JsonResponse(
            {
                "progress": {
                    "total": total_students,
                    "completed": completed_reports,
                    "percentage": percentage,
                }
            }
        )



