"""Shared staff profile and class-assignment logic for user create/update forms."""

from django.utils import timezone

from academics.models import Department, GradeClass, Term
from hr.models import StaffCategory, StaffProfile, TeacherClassAssignment


def _subjects_for_class(grade_class, selected_subjects):
    if not selected_subjects:
        return []
    valid = set(
        grade_class.subjects.filter(is_active=True).values_list("name", flat=True)
    )
    return [s for s in selected_subjects if s in valid]


def sync_staff_profile(user, *, departments=None, staff_category, contract_end_date=None,
                        job_title="", employee_id="", employment_type="permanent",
                        employment_start_date=None, probation_end_date=None,
                        confirmation_date=None,
                        date_of_birth=None, gender="", marital_status="",
                        contact_phone="", residential_address="",
                        nationality="", years_of_experience=0, highest_qualification="",
                        national_id_number="",
                        # Emergency contacts
                        emergency_contact_name="", emergency_contact_phone="",
                        emergency_contact_relationship="",
                        # Salary & banking
                        basic_salary=0, housing_allowance=0, transport_allowance=0,
                        medical_allowance=0, other_allowances=0,
                        bank_name="", bank_account_number="", bank_branch="",
                        # Statutory
                        nssf_number="", tin_number="", nhif_number=""):
    """Create or update StaffProfile from user form data."""
    departments = departments or []

    existing_profile = getattr(user, "staff_profile", None)
    if not departments and existing_profile:
        departments = existing_profile.departments or (
            [existing_profile.department] if existing_profile.department else []
        )

    primary_dept = departments[0] if departments else ""

    # Auto-generate employee_id if empty (use user PK to avoid race conditions)
    if not employee_id:
        employee_id = f"STAFF-{user.pk:03d}"

    defaults = {
        "full_name": f"{user.first_name} {user.last_name}".strip() or user.username,
        "department": primary_dept,
        "departments": departments,
        "staff_category": staff_category or StaffCategory.TEACHING,
        "contact_email": user.email,
        "is_active": user.is_active,
        "employment_start_date": (
            employment_start_date
            or (user.staff_profile.employment_start_date
                if getattr(user, "staff_profile", None)
                and user.staff_profile.employment_start_date
                else timezone.now().date())
        ),
        "contract_end_date": contract_end_date,
        "job_title": job_title,
        "employee_id": employee_id,
        "employment_type": employment_type or "permanent",
        "date_of_birth": date_of_birth,
        "gender": gender,
        "marital_status": marital_status,
        "contact_phone": contact_phone,
        "residential_address": residential_address,
        "nationality": nationality,
        "years_of_experience": years_of_experience or 0,
        "highest_qualification": highest_qualification,
        # Emergency contacts
        "emergency_contact_name": emergency_contact_name,
        "emergency_contact_phone": emergency_contact_phone,
        "emergency_contact_relationship": emergency_contact_relationship,
        # Salary & banking
        "basic_salary": basic_salary or 0,
        "housing_allowance": housing_allowance or 0,
        "transport_allowance": transport_allowance or 0,
        "medical_allowance": medical_allowance or 0,
        "other_allowances": other_allowances or 0,
        "bank_name": bank_name,
        "bank_account_number": bank_account_number,
        "bank_branch": bank_branch,
        # Statutory
        "nssf_number": nssf_number,
        "tin_number": tin_number,
        "nhif_number": nhif_number,
        "national_id_number": national_id_number,
    }
    if probation_end_date:
        defaults["probation_end_date"] = probation_end_date
    if confirmation_date:
        defaults["confirmation_date"] = confirmation_date

    profile, _ = StaffProfile.objects.update_or_create(
        user=user,
        defaults=defaults,
    )
    return profile


def sync_teacher_assignments(profile, *, class_ids, subjects_taught, repeat_across_terms=False):
    """Sync term class assignments; subjects are scoped per class."""
    if not profile or not profile.is_teaching_staff:
        TeacherClassAssignment.objects.filter(teacher=profile).delete()
        return

    term = Term.objects.filter(is_locked=False).order_by("-start_date").first()
    if not term:
        return

    TeacherClassAssignment.objects.filter(teacher=profile, term=term).delete()
    if not class_ids:
        return

    for cid in class_ids:
        gc = GradeClass.objects.get(pk=cid)
        class_subjects = _subjects_for_class(gc, subjects_taught or [])
        TeacherClassAssignment.objects.create(
            teacher=profile,
            term=term,
            grade_class=gc,
            subjects_taught=class_subjects,
            is_assistant_class_teacher=False,
            repeat_across_terms=repeat_across_terms,
        )


def apply_django_admin_flags(user, *, role):
    """Super Admin accounts need Django admin (is_staff); only Super Admin may be superuser."""
    from users.models import UserRole

    if role == UserRole.SUPER_ADMIN:
        user.is_staff = True
    return user
