"""

RBAC mixins for class-based views — FRD NFR-SEC-004.

Usage:

    class MyView(RoleRequiredMixin, LoginRequiredMixin, View):

        required_permission = "students.view_student"

"""

from django.contrib.auth.mixins import LoginRequiredMixin

from django.core.exceptions import PermissionDenied

from django.contrib.auth.decorators import user_passes_test

from django.shortcuts import redirect

from users.models import UserRole



# Parents hitting admin URLs get redirected here instead of 403.

_PARENT_REDIRECT_MAP = {

    "/attendance/":                "/parent/attendance/",

    "/finance/":                   "/parent/invoices/",

    "/communications/":            "/parent/communications/",

    "/communications/broadcasts/": "/parent/communications/",

    "/academics/primary-assessment/": "/academics/progression/parent/",

    "/academics/ecd-assessment/":  "/academics/progression/parent/",

    "/welfare/ecd/incidents/":     "/parent/welfare/",

    "/welfare/primary/incidents/": "/parent/welfare/",

}





class RoleRequiredMixin(LoginRequiredMixin):

    """

    Mixin that restricts a view to users with specific permissions.

    Set `required_permission` on the view class.

    Roles are never hardcoded — access is determined by Django permissions

    configured via the Role Management UI by the admin.

    Super Admin is always allowed.

    Parents are redirected to their portal equivalents.

    """

    allowed_roles: list[str] = []

    required_permission: str | None = None

    required_permissions_any: list[str] | None = None



    def dispatch(self, request, *args, **kwargs):

        if not request.user.is_authenticated:

            return super().dispatch(request, *args, **kwargs)



        if request.user.role == UserRole.SUPER_ADMIN:

            return super().dispatch(request, *args, **kwargs)



        has_permission_requirement = bool(

            self.required_permission or self.required_permissions_any

        )



        # Permission-based check takes priority when the view declares one.

        # Any role holding the permission is granted access (no hard-coded roles).

        if has_permission_requirement:

            if self.required_permissions_any:

                if not any(request.user.has_perm(p) for p in self.required_permissions_any):

                    from audit.models import log_event

                    log_event(

                        actor=request.user,

                        action_type="PERMISSION_DENIED",

                        model_name="AccessLog",

                        object_id=0,

                        description=(

                            f"User {request.user} (role={request.user.role}) denied access to "

                            f"{request.method} {request.path} — missing one of: "

                            f"{', '.join(self.required_permissions_any)}"

                        ),

                        request=request,

                    )

                    raise PermissionDenied(

                        "You do not have any of the required permissions for this page."

                    )

            elif self.required_permission and not request.user.has_perm(self.required_permission):

                from audit.models import log_event

                log_event(

                    actor=request.user,

                    action_type="PERMISSION_DENIED",

                    model_name="AccessLog",

                    object_id=0,

                    description=(

                        f"User {request.user} (role={request.user.role}) denied access to "

                        f"{request.method} {request.path} — missing perm: {self.required_permission}"

                    ),

                    request=request,

                )

                raise PermissionDenied(

                    f"You do not have the required permission ({self.required_permission}) for this page."

                )

            return super().dispatch(request, *args, **kwargs)



        # Role-based fallback (only for views with no permission requirement).

        if self.allowed_roles and request.user.role not in self.allowed_roles:

            # Custom roles fall through to permission-based check below

            if not request.user.role.startswith("custom_"):

                # Redirect parents to their portal instead of 403

                if request.user.role == UserRole.PARENT:

                    path = request.path

                    for admin_path, parent_path in _PARENT_REDIRECT_MAP.items():

                        if path.startswith(admin_path):

                            return redirect(parent_path)

                    return redirect("/parent/")



                from audit.models import log_event

                log_event(

                    actor=request.user,

                    action_type="ROLE_ACCESS_DENIED",

                    model_name="AccessLog",

                    object_id=0,

                    description=(

                        f"User {request.user} (role={request.user.role}) denied access to "

                        f"{request.method} {request.path}"

                    ),

                    request=request,

                )

                raise PermissionDenied(

                    f"Your role ({request.user.role}) is not authorised to access this page."

                )



        return super().dispatch(request, *args, **kwargs)





def role_required(*allowed_roles: str):

    """

    Decorator for function-based views.

    Usage: @role_required(UserRole.ADMIN_OFFICER, UserRole.SUPER_ADMIN)

    """

    def check(user):

        return user.is_authenticated and user.role in allowed_roles



    return user_passes_test(check)





def assert_role(user, *allowed_roles: str) -> None:

    """Raise PermissionDenied if user.role not in allowed_roles."""

    if not (user.is_authenticated and user.role in allowed_roles):

        raise PermissionDenied("You do not have permission for this action.")





#  DRF Permission Classes — FRD NFR-SEC-004

# These enforce permission-based access on REST API endpoints.

# Roles are never hardcoded — access is determined by Django permissions

# configured via the Role Management UI by the admin.

from rest_framework.permissions import BasePermission



class IsAdminOrSuperAdmin(BasePermission):

    """Allow users with applicant management permission — FRD Section 5, 12."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("admissions.add_applicant")

        )



class IsFinanceOfficer(BasePermission):

    """Allow users with invoice permission — FRD Section 10."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("finance.add_invoice")

        )



class IsTeacherOrAbove(BasePermission):

    """Allow users with lesson plan permission — FRD Sections 6, 8, 9."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("academics.add_lessonplan")

        )



class IsHODOrAbove(BasePermission):

    """Allow users with lesson plan review permission — FRD Sections 8.2, 9.6."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("academics.can_review_lessonplan")

        )



class IsHOSOrAbove(BasePermission):

    """Allow users with report card sign-off permission — FRD Sections 5.6, 9.5, 14.1."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("academics.change_reportcard")

        )



class IsSuperAdminOnly(BasePermission):

    """Allow superuser only — FRD Section 14.8."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and request.user.is_superuser



class IsECDTeacherOrAbove(BasePermission):

    """Allow users with ECD evaluation permission — FRD Section 13."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("academics.add_ecdevaluation")

        )



class IsParentUser(BasePermission):

    """Allow users with view student permission (parent portal scope) — FRD Section 14.7."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and request.user.has_perm("students.view_student")



class IsAttendanceOperator(BasePermission):

    """Allow users with attendance entry permission — FRD Section 6."""

    def has_permission(self, request, view):

        return request.user.is_authenticated and (

            request.user.is_superuser

            or request.user.has_perm("attendance.add_attendanceentry")

        )
