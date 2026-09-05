import logging
import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import UpdateView, TemplateView, View
import json

from academics.ecd_utils import build_ecd_report_context, ecd_template_type_from_class_name, grade_class_names_for_department
from academics.forms import LessonPlanForm, LessonPlanReviewForm, ExamScoreFilterForm, SubjectForm
from academics.models import (
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

logger = logging.getLogger(__name__)
from academics.grading_utils import (
    compute_grade_with_gaps, get_grade_from_score, get_grade_label, get_full_grade_display,
    is_pass, is_at_risk, is_critical
)


class MigrationTriggerView(LoginRequiredMixin, View):
    """Temporary view to trigger migrations when terminal is restricted."""
    def get(self, request):
        if not request.user.is_superuser:
            return HttpResponse("Unauthorized", status=403)
        if not settings.DEBUG:
            return HttpResponse("Migration endpoint is only available in DEBUG mode.", status=403)
        try:
            call_command('makemigrations', 'hr', '--noinput')
            call_command('makemigrations', 'attendance', '--noinput')
            call_command('makemigrations', 'academics', '--noinput')
            call_command('migrate', '--noinput')
            return HttpResponse("Migrations successfully executed.")
        except Exception as e:
            return HttpResponse(f"Error: {e}")


class RoomListView(RoleRequiredMixin, View):
    """List all rooms and handle the Add-new-room form."""
    template_name = "academics/rooms.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "academics.view_room"

    def get(self, request, *args, **kwargs):
        from academics.models import Room
        return render(request, self.template_name, {
            "rooms": Room.objects.all(),
            "edit_room": None,
        })

    def post(self, request, *args, **kwargs):
        from academics.models import Room
        name = request.POST.get("name", "").strip()
        building = request.POST.get("building", "").strip()
        capacity = request.POST.get("capacity") or None
        is_active = request.POST.get("is_active", "1") == "1"

        if not name:
            messages.error(request, "Room name is required.")
            return redirect("academics:rooms")

        _, created = Room.objects.get_or_create(
            name=name,
            defaults={"building": building, "capacity": capacity, "is_active": is_active}
        )
        if created:
            messages.success(request, f'Room "{name}" added successfully.')
        else:
            messages.warning(request, f'A room named "{name}" already exists.')
        return redirect("academics:rooms")


class RoomEditView(RoleRequiredMixin, View):
    """Edit an existing room."""
    template_name = "academics/rooms.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER]
    required_permission = "academics.change_room"

    def get(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        return render(request, self.template_name, {
            "rooms": Room.objects.all(),
            "edit_room": room,
        })

    def post(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Room name is required.")
            return redirect("academics:room_edit", pk=pk)

        room.name = name
        room.building = request.POST.get("building", "").strip()
        room.capacity = request.POST.get("capacity") or None
        room.is_active = request.POST.get("is_active", "1") == "1"
        room.save()
        messages.success(request, f'Room "{room.name}" updated.')
        return redirect("academics:rooms")


class RoomDeleteView(RoleRequiredMixin, View):
    """Delete a room (POST only)."""
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "academics.delete_room"

    def post(self, request, pk):
        from academics.models import Room
        room = get_object_or_404(Room, pk=pk)
        name = room.name
        # Check it's not in use by any timetable slot
        if room.timetable_slots.exists():
            messages.error(request, f'"{name}" is assigned to timetable slots and cannot be deleted. Remove those slots first.')
        else:
            room.delete()
            messages.success(request, f'Room "{name}" deleted.')
        return redirect("academics:rooms")


class DuplicateCheckView(RoleRequiredMixin, LoginRequiredMixin, View):
    """AJAX endpoint: check uploaded file hashes against existing attachments.
    
    POST with JSON body: {"hashes": [{"hash": "abc123...", "filename": "file.pdf"}, ...]}
    Returns: {"results": [{"hash": "abc123...", "filename": "file.pdf", "duplicate": true, "existing_name": "file.pdf"}, ...]}
    """
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    # Proxy permission: view_importlog does not exist; use view_lessonplanattachment as closest match
    required_permission = "academics.view_lessonplanattachment"

    def post(self, request):
        import json
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        raw_hashes = data.get("hashes", [])
        hashes = [h for h in raw_hashes if isinstance(h, dict) and "hash" in h and "filename" in h]
        if not hashes:
            return JsonResponse({"results": []})

        # Get all existing attachment hashes for the current lesson plan
        # or all attachments if no lesson_plan_id provided
        lesson_plan_id = data.get("lesson_plan_id")
        if lesson_plan_id:
            try:
                lesson_plan_id = int(lesson_plan_id)
            except (TypeError, ValueError):
                lesson_plan_id = None
        from academics.models import LessonPlanAttachment
        
        if lesson_plan_id:
            existing = LessonPlanAttachment.objects.filter(lesson_plan_id=lesson_plan_id)
        else:
            existing = LessonPlanAttachment.objects.all()

        # Compute hashes of existing files
        from academics.validators import _compute_file_hash
        existing_hashes = {}
        for att in existing:
            try:
                h = _compute_file_hash(att.file.path)
                existing_hashes[h] = att.filename
            except (AttributeError, ValueError, OSError):
                # Can't compute hash — skip
                continue

        results = []
        incoming_map = {item["hash"]: item["filename"] for item in hashes if "hash" in item}
        
        for h, fname in incoming_map.items():
            is_dup = h in existing_hashes
            results.append({
                "hash": h,
                "filename": fname,
                "duplicate": is_dup,
                "existing_name": existing_hashes.get(h, ""),
            })

        return JsonResponse({"results": results})


class GradeClassListView(RoleRequiredMixin, View):
    template_name = "academics/classes.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.ADMIN_OFFICER]
    required_permission = "academics.view_gradeclass"

    def get(self, request, *args, **kwargs):
        from academics.models import GradeClass, Department
        classes = GradeClass.objects.all().order_by("department", "name")
        # OP 7.2: Department-scope for HOD roles
        HOD_DEPT_MAP = {
            UserRole.PRIMARY_HOD: Department.PRIMARY,
            UserRole.ECD_HOD: Department.ECD,
            UserRole.LOWER_SECONDARY_HOD: Department.LOWER_SECONDARY,
        }
        if request.user.role in HOD_DEPT_MAP:
            classes = classes.filter(department=HOD_DEPT_MAP[request.user.role])
        return render(request, self.template_name, {"classes": classes, "edit_class": None, "departments": Department.choices})

    def post(self, request, *args, **kwargs):
        from academics.models import GradeClass, Department
        # Permission-driven (FRD OP 8.1): mirrors the gradeclass permissions
        # configured in the Role Permission UI.
        if not request.user.has_perm("academics.add_gradeclass"):
            messages.error(request, "You do not have permission to create new classes.")
            return redirect("academics:class_management")
        name = request.POST.get("name", "").strip()
        department = request.POST.get("department", "").strip()
        max_capacity_raw = request.POST.get("max_capacity", "").strip()
        if not max_capacity_raw:
            messages.error(request, "Class capacity is required. Enter a positive whole number.")
            return redirect("academics:class_management")
        try:
            max_capacity = int(max_capacity_raw)
            if max_capacity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Class capacity must be a positive whole number.")
            return redirect("academics:class_management")
        is_exit_grade = request.POST.get("is_exit_grade") == "on"
        try:
            from django.db.models import Max
            next_sort = (GradeClass.objects.aggregate(m=Max("sort_order"))["m"] or 0) + 1
            gc = GradeClass(
                name=name, department=department, max_capacity=max_capacity,
                sort_order=next_sort, is_exit_grade=is_exit_grade,
            )
            gc.full_clean()
            gc.save()
            # FRD OP 8.1: Seed ClassCapacity for current academic year
            from academics.models import ClassCapacity, AcademicYear
            current_year = AcademicYear.objects.filter(is_current=True).first()
            if current_year:
                ClassCapacity.objects.get_or_create(
                    grade_class=gc, academic_year=current_year,
                    defaults={"max_capacity": max_capacity}
                )
            messages.success(request, f'Class "{name}" added successfully.')
        except ValidationError as e:
            msg = e.message if hasattr(e, 'message') else str(e)
            messages.error(request, msg)
        except Exception as e:
            messages.error(request, f"Error creating class: {e}")
        return redirect("academics:class_management")


class GradeClassEditView(RoleRequiredMixin, View):
    template_name = "academics/classes.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "academics.change_gradeclass"

    def get(self, request, pk):
        from academics.models import GradeClass, Department
        gc = get_object_or_404(GradeClass, pk=pk)
        return render(request, self.template_name, {"classes": GradeClass.objects.all().order_by("department", "name"), "edit_class": gc, "departments": Department.choices})

    def post(self, request, pk):
        from academics.models import GradeClass
        from students.models import Student
        gc = get_object_or_404(GradeClass, pk=pk)
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Class name is required.")
            return redirect("academics:class_edit", pk=pk)

        # OP 7.3: Block renaming or department change when students are enrolled
        new_dept = request.POST.get("department", gc.department).strip()
        has_students = Student.objects.filter(class_name=gc.name, is_archived=False).exists()
        if has_students:
            if name != gc.name:
                messages.error(
                    request,
                    f'Cannot rename "{gc.name}" — it has enrolled students. '
                    f'Reassign students first.'
                )
                return redirect("academics:class_edit", pk=pk)
            if new_dept != gc.department:
                messages.error(
                    request,
                    f'Cannot change department of "{gc.name}" — it has enrolled students. '
                    f'Reassign students first.'
                )
                return redirect("academics:class_edit", pk=pk)

        gc.name = name
        gc.department = new_dept
        max_capacity_raw = request.POST.get("max_capacity", "").strip()
        if not max_capacity_raw:
            messages.error(request, "Class capacity is required. Enter a positive whole number.")
            return redirect("academics:class_edit", pk=pk)
        try:
            gc.max_capacity = int(max_capacity_raw)
            if gc.max_capacity <= 0:
                raise ValueError
        except (TypeError, ValueError):
            messages.error(request, "Class capacity must be a positive whole number.")
            return redirect("academics:class_edit", pk=pk)
        try:
            gc.sort_order = int(request.POST.get("sort_order", gc.sort_order))
        except (TypeError, ValueError):
            logger.warning("Invalid sort_order value for class %s", gc.pk)
        gc.is_exit_grade = request.POST.get("is_exit_grade") == "on"
        gc.save()
        # FRD OP 8.1: Also update ClassCapacity for current academic year
        from academics.models import ClassCapacity, AcademicYear
        current_year = AcademicYear.objects.filter(is_current=True).first()
        if current_year:
            cc, created = ClassCapacity.objects.get_or_create(
                grade_class=gc, academic_year=current_year,
                defaults={"max_capacity": gc.max_capacity}
            )
            if not created:
                cc.max_capacity = gc.max_capacity
                cc.save(update_fields=["max_capacity"])
        messages.success(request, f'Class "{gc.name}" updated.')
        return redirect("academics:class_management")


class GradeClassDeleteView(RoleRequiredMixin, View):
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "academics.delete_gradeclass"

    def post(self, request, pk):
        from academics.models import GradeClass, TimetableEntry
        gc = get_object_or_404(GradeClass, pk=pk)
        name = gc.name
        from students.models import Student
        from attendance.models import AttendanceEntry
        from academics.models import ExamScore, ReportCard, LessonPlan

        reasons = []

        student_count = Student.objects.filter(class_name=name, is_archived=False).count()
        if student_count > 0:
            reasons.append(f'{student_count} active student(s)')

        timetable_count = TimetableEntry.objects.filter(class_name=name).count()
        if timetable_count > 0:
            reasons.append(f'{timetable_count} timetable slot(s)')

        attendance_count = AttendanceEntry.objects.filter(student__class_name=name).count()
        if attendance_count > 0:
            reasons.append(f'{attendance_count} attendance record(s)')

        grade_count = ExamScore.objects.filter(student__class_name=name).count()
        if grade_count > 0:
            reasons.append(f'{grade_count} exam score(s)')

        report_count = ReportCard.objects.filter(student__class_name=name).count()
        if report_count > 0:
            reasons.append(f'{report_count} report card(s)')

        lesson_count = LessonPlan.objects.filter(class_name=name).count()
        if lesson_count > 0:
            reasons.append(f'{lesson_count} lesson plan(s)')

        if reasons:
            messages.error(
                request,
                f'Cannot delete "{name}" — associated records exist: '
                f'{"; ".join(reasons)}. Remove all linked records first.'
            )
        else:
            gc.delete()
            messages.success(request, f'Class "{name}" deleted.')
        return redirect("academics:class_management")

class SubjectListView(RoleRequiredMixin, TemplateView):
    template_name = "academics/subjects.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.TEACHER]
    required_permission = "academics.view_subject"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # FRD OP 9.2: All roles, scoped to own grades
        user = self.request.user
        selected_dept = self.request.GET.get("department", "").strip()

        if user.role == UserRole.TEACHER:
            # Teachers see only subjects for grades they teach
            from timetable.models import TimetableSlot
            taught_grades = list(TimetableSlot.objects.filter(teacher=user).values_list("class_name", flat=True).distinct())
            subjects_qs = Subject.objects.filter(classes__name__in=taught_grades, is_active=True).order_by("name").distinct()
        else:
            subjects_qs = Subject.objects.all().order_by("name")

        if selected_dept:
            subjects_qs = [s for s in subjects_qs if selected_dept in (s.departments or [])]

        ctx["subjects"] = subjects_qs
        ctx["form"] = SubjectForm()
        # Mapping for JS filtering on the form
        ctx["class_depts"] = {str(gc.id): gc.department for gc in GradeClass.objects.all()}
        ctx["selected_dept"] = selected_dept
        ctx["dept_choices"] = Department.choices
        ctx["all_classes"] = GradeClass.objects.all().order_by("name")
        return ctx

    def post(self, request):
        # FRD OP 9.1: Only Super Admin can create/update subjects
        if request.user.role != UserRole.SUPER_ADMIN:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": "Only Super Admin can create subjects."}, status=403)
            messages.error(request, "Only Super Admin can create subjects.")
            return redirect("academics:subjects")
        form = SubjectForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except Exception as exc:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({"error": str(exc)}, status=400)
                messages.error(request, f"Failed to save subject: {exc}")
                return redirect("academics:subjects")
            messages.success(request, "Subject created successfully.")
            return redirect("academics:subjects")
        
        # AJAX mode: return error
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type.startswith("multipart"):
            from django.http import JsonResponse
            error_msg = "Validation failed. Please check the form fields."
            if form.errors.get("departments"):
                error_msg = "Please select at least one department."
            elif form.errors.get("__all__"):
                error_msg = form.errors["__all__"][0]
            return JsonResponse({"error": error_msg}, status=400)

        ctx = self.get_context_data()
        ctx["form"] = form
        return render(request, self.template_name, ctx)


class SubjectEditView(RoleRequiredMixin, UpdateView):
    model = Subject
    form_class = SubjectForm
    template_name = "academics/subjects.html"
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "academics.change_subject"
    success_url = "/academics/subjects/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["subjects"] = Subject.objects.all().order_by("name")
        ctx["edit_subject"] = self.get_object()
        # Mapping for JS filtering
        ctx["class_depts"] = {str(gc.id): gc.department for gc in GradeClass.objects.all()}
        ctx["dept_choices"] = Department.choices
        ctx["selected_dept"] = self.request.GET.get("department", "").strip()
        ctx["all_classes"] = GradeClass.objects.all().order_by("name")
        return ctx

    def get(self, request, *args, **kwargs):
        subject = self.get_object()
        return JsonResponse({
            "id": subject.pk,
            "name": subject.name,
            "code": subject.code or "",
            "color": subject.color or "#023AA5",
            "departments": subject.departments or [],
            "classes": list(subject.classes.values_list("id", flat=True)),
            "is_active": subject.is_active,
        })

    def form_valid(self, form):
        messages.success(self.request, "Subject updated successfully.")
        response = super().form_valid(form)
        # AJAX mode: return JSON success
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest" or self.request.content_type.startswith("multipart"):
            from django.http import JsonResponse
            return JsonResponse({"success": True, "subject_id": self.object.pk})
        return response

    def form_invalid(self, form):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest" or self.request.content_type.startswith("multipart"):
            from django.http import JsonResponse
            error_msg = "Validation failed. Please check the form fields."
            if form.errors.get("departments"):
                error_msg = "Please select at least one department."
            elif form.errors.get("__all__"):
                error_msg = form.errors["__all__"][0]
            return JsonResponse({"error": error_msg}, status=400)
        return super().form_invalid(form)


class SubjectDeleteView(RoleRequiredMixin, View):
    # FRD OP 9.3: Only Super Admin can delete subjects
    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "academics.delete_subject"

    def post(self, request, pk):
        if request.user.role != UserRole.SUPER_ADMIN:
            raise PermissionDenied()
        subject = get_object_or_404(Subject, pk=pk)
        name = subject.name

        # FRD OP 9.3: Check for dependencies before deletion
        from academics.models import LessonPlan, TimetableEntry, ExamScore
        deps = []
        lp_count = LessonPlan.objects.filter(subject_name__iexact=name).count()
        if lp_count > 0:
            deps.append(f"{lp_count} lesson plan(s)")
        te_count = TimetableEntry.objects.filter(subject=subject).count()
        if te_count > 0:
            deps.append(f"{te_count} timetable slot(s)")
        es_count = ExamScore.objects.filter(subject_name__iexact=name).count()
        if es_count > 0:
            deps.append(f"{es_count} exam score(s)")

        if deps:
            messages.error(
                request,
                f'Cannot delete "{name}" — associated records exist: '
                f'{"; ".join(deps)}. Remove all linked records first.'
            )
            return redirect("academics:subjects")

        subject.delete()
        messages.success(request, f"Subject '{name}' deleted.")
        return redirect("academics:subjects")


class SubjectClassesAPIView(RoleRequiredMixin, View):
    """AJAX: return subjects assigned to a specific class via Subject.classes M2M.
    Permission-driven via academics.view_subject (matches SubjectListView).
    """
    required_permission = "academics.view_subject"

    def get(self, request, *args, **kwargs):
        class_name = request.GET.get("class_name", "").strip()
        if not class_name:
            return JsonResponse({"subjects": []})
        gc = GradeClass.objects.filter(name__iexact=class_name).first()
        if not gc:
            return JsonResponse({"subjects": []})
        subjects = list(
            gc.subjects.filter(is_active=True).order_by("name").values_list("name", flat=True)
        )
        return JsonResponse({"class_name": class_name, "subjects": subjects})


class SubjectsPerGradeView(RoleRequiredMixin, TemplateView):
    """Grade-centric view: shows which subjects are configured for each grade."""
    template_name = "academics/subjects_per_grade.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "academics.view_subject"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        classes = GradeClass.objects.all().order_by("department", "sort_order", "name")
        grade_data = []
        for gc in classes:
            subjects = gc.subjects.order_by("-is_active", "name")
            grade_data.append({
                "grade": gc,
                "subjects": subjects,
                "subject_count": subjects.count(),
            })
        ctx["grade_data"] = grade_data
        ctx["departments"] = Department.choices
        return ctx


class ECDDomainConfigListView(RoleRequiredMixin, TemplateView):
    """Admin UI for managing ECD domain competencies per class type."""
    template_name = "academics/ecd_domain_config.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ECD_HOD]
    required_permission = "academics.view_ecddomainconfig"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from academics.models import ECDDomainConfig
        configs = ECDDomainConfig.objects.all().order_by("class_type", "sort_order")
        by_type = {}
        for c in configs:
            by_type.setdefault(c.class_type, []).append(c)
        # Pass structured data for template
        ctx["ecd_sections"] = [
            {"class_type": ct, "label": label, "domains": by_type.get(ct, [])}
            for ct, label in ECDDomainConfig.CLASS_TYPE_CHOICES
        ]
        return ctx

    def post(self, request, *args, **kwargs):
        from academics.models import ECDDomainConfig
        action = request.POST.get("action")

        if action == "add_domain":
            class_type = request.POST.get("class_type", "").strip()
            domain_name = request.POST.get("domain_name", "").strip()
            competencies_raw = request.POST.get("competencies", "").strip()
            competencies = [c.strip() for c in competencies_raw.split("\n") if c.strip()]
            if not class_type or not domain_name:
                messages.error(request, "Class type and domain name are required.")
                return redirect("academics:ecd_domain_config")
            existing = ECDDomainConfig.objects.filter(class_type=class_type, domain_name=domain_name).first()
            if existing:
                existing.competencies = competencies
                existing.save()
                messages.success(request, f'Updated "{domain_name}" for {dict(ECDDomainConfig.CLASS_TYPE_CHOICES).get(class_type, class_type)}.')
            else:
                max_order = ECDDomainConfig.objects.filter(class_type=class_type).count()
                ECDDomainConfig.objects.create(
                    class_type=class_type,
                    domain_name=domain_name,
                    competencies=competencies,
                    sort_order=max_order,
                )
                messages.success(request, f'Added "{domain_name}" for {dict(ECDDomainConfig.CLASS_TYPE_CHOICES).get(class_type, class_type)}.')

        elif action == "delete_domain":
            pk = request.POST.get("domain_id")
            if pk:
                ECDDomainConfig.objects.filter(pk=pk).delete()
                messages.success(request, "Domain deleted.")

        elif action == "update_competencies":
            domain_id = request.POST.get("domain_id")
            competencies_raw = request.POST.get("competencies", "").strip()
            competencies = [c.strip() for c in competencies_raw.split("\n") if c.strip()]
            try:
                obj = ECDDomainConfig.objects.get(pk=domain_id)
                obj.competencies = competencies
                obj.save()
                messages.success(request, f'Updated competencies for "{obj.domain_name}".')
            except ECDDomainConfig.DoesNotExist:
                messages.error(request, "Domain not found.")

        return redirect("academics:ecd_domain_config")
