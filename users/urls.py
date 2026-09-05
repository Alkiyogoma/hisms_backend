from django.urls import path
from users.views import (
    HISMSLoginView, HISMSLogoutView, HISMSLoginAPIView, StartImpersonationView, StopImpersonationView,
    UserListView, UserProfileView, HISMSPasswordChangeView,
    HISMSPasswordChangeDoneView, UserCreateView, UserUpdateView,
    PasswordResetRequestView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
    ForceLogoutView, ReactivateUserView, ParentProfileEditView,
    AssignmentConflictCheckView,
)
from users.assignment_meta import StaffAssignmentMetaView

from users.role_management_views import (
    RoleListView, RoleDetailView, RolePermissionUpdateView,
    RoleUserCountAPIView, RoleCreateView, RoleUpdateView, RoleDeleteView,
    RoleToggleActiveView, RoleUserAssignView, RoleUserRemoveView,
    UserPermissionOverrideView, UserExtraRolesView,
)

app_name = "users"

urlpatterns = [
    path("login/", HISMSLoginView.as_view(), name="login"),
    path("login/api/", HISMSLoginAPIView.as_view(), name="login_api"),
    path("logout/", HISMSLogoutView.as_view(), name="logout"),
    path("list/", UserListView.as_view(), name="user_list"),
    path("impersonate/start/<int:user_id>/", StartImpersonationView.as_view(), name="impersonate_start"),
    path("impersonate/stop/", StopImpersonationView.as_view(), name="impersonate_stop"),
    path("profile/", UserProfileView.as_view(), name="profile"),
    path("profile/edit/", ParentProfileEditView.as_view(), name="parent_profile_edit"),
    path("password-change/", HISMSPasswordChangeView.as_view(), name="password_change"),
    path("password-change/done/", HISMSPasswordChangeDoneView.as_view(), name="password_change_done"),
    path("create/", UserCreateView.as_view(), name="user_create"),
    path("edit/<int:pk>/", UserUpdateView.as_view(), name="user_edit"),
    path("assignment-conflict-check/", AssignmentConflictCheckView.as_view(), name="assignment_conflict_check"),
    path("staff-assignment-meta/", StaffAssignmentMetaView.as_view(), name="staff_assignment_meta"),
    path("force-logout/<int:user_id>/", ForceLogoutView.as_view(), name="force_logout"),
    path("reactivate/<int:user_id>/", ReactivateUserView.as_view(), name="reactivate_user"),

    # FRD AUTH-RESET-001: Password Reset Flow
    path("password-reset/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset/done/", PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset/<str:uidb64>/<str:token>/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # Role Management (FRD OP3.3)
    path("roles/", RoleListView.as_view(), name="role_list"),
    path("roles/create/", RoleCreateView.as_view(), name="role_create"),
    path("roles/<int:role_pk>/edit/", RoleUpdateView.as_view(), name="role_edit"),
    path("roles/<int:role_pk>/delete/", RoleDeleteView.as_view(), name="role_delete"),
    path("roles/<int:role_pk>/toggle-active/", RoleToggleActiveView.as_view(), name="role_toggle_active"),
    path("roles/<int:role_pk>/assign-user/", RoleUserAssignView.as_view(), name="role_assign_user"),
    path("roles/<int:role_pk>/remove-user/", RoleUserRemoveView.as_view(), name="role_remove_user"),
    path("roles/<str:role>/", RoleDetailView.as_view(), name="role_detail"),
    path("roles/<str:role>/permissions/update/", RolePermissionUpdateView.as_view(), name="role_permission_update"),
    path("roles/<str:role>/user-count/", RoleUserCountAPIView.as_view(), name="role_user_count"),
    # Per-User Permission Overrides & Extra Roles
    path("users/<int:user_id>/permissions/", UserPermissionOverrideView.as_view(), name="user_permission_override"),
    path("users/<int:user_id>/extra-roles/", UserExtraRolesView.as_view(), name="user_extra_roles"),
]
