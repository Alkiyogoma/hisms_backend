"""
Shared department-scoping mixin for welfare and discipline modules.
"""
from users.models import UserRole


class DepartmentScopedMixin:
    """Mixin that provides department-scoping for welfare views.

    Set ``department = "ecd"`` or ``department = "primary"`` via
    ``as_view(department=...)`` in urls.py.  When *None* (the default)
    the department is inferred from the logged-in user's role.
    """
    department = None  # overridden by as_view(department=...)

    def _get_department(self):
        from academics.models import Department
        dept = self.department
        if dept == "primary":
            return Department.PRIMARY
        if dept == "ecd":
            return Department.ECD
        # Fallback: infer from user role
        user = self.request.user
        if user.role == UserRole.ECD_HOD:
            return Department.ECD
        if user.role == UserRole.PRIMARY_HOD:
            return Department.PRIMARY
        if user.role == UserRole.LOWER_SECONDARY_HOD:
            return Department.LOWER_SECONDARY
        # For teachers and other roles, infer from StaffProfile department
        if user.role == UserRole.TEACHER:
            from hr.models import StaffProfile
            profile = StaffProfile.objects.filter(user=user).values_list("department", flat=True).first()
            if profile == "PRIMARY":
                return Department.PRIMARY
            if profile == "LOWER_SECONDARY":
                return Department.LOWER_SECONDARY
            return Department.ECD
        return Department.ECD

    def _get_non_ecd_classes(self):
        """Return sorted list of Primary + Secondary class names (no ECD)."""
        from academics.models import Department, GradeClass
        return sorted(
            GradeClass.objects.exclude(department=Department.ECD)
            .values_list("name", flat=True)
            .distinct()
        )

    def _get_dept_classes(self, department):
        """Return sorted list of class names for a specific department."""
        from academics.models import GradeClass
        return sorted(
            GradeClass.objects.filter(department=department)
            .values_list("name", flat=True)
            .distinct()
        )

    def _check_teacher_department(self):
        """Raise PermissionDenied if a TEACHER accesses a department they don't belong to."""
        from django.core.exceptions import PermissionDenied
        user = self.request.user
        if user.role != UserRole.TEACHER:
            return
        from core.teacher_context import is_ecd_teacher
        from academics.models import Department
        view_dept = self._get_department()
        if view_dept == Department.ECD and not is_ecd_teacher(user):
            raise PermissionDenied("You do not have access to ECD welfare.")
        if view_dept == Department.PRIMARY and is_ecd_teacher(user):
            raise PermissionDenied("You do not have access to Primary welfare.")
