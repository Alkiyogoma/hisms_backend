"""

Custom login view implementing FRD NFR-SEC-005:

- 5 consecutive failures → lock account for 15 minutes

"""

from django.conf import settings

from django.contrib import messages

from django.contrib.auth import authenticate, login, logout as auth_logout

from django.contrib.auth.forms import AuthenticationForm

from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordChangeDoneView

from django.core.exceptions import PermissionDenied

from django.http import JsonResponse

from django.shortcuts import redirect, get_object_or_404

from django.urls import reverse_lazy

from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from django.views import View

from django.views.generic import ListView, TemplateView, CreateView, UpdateView

from django.contrib.auth.mixins import LoginRequiredMixin

from core.permissions import RoleRequiredMixin

from users.models import User, UserRole





def _sync_user_role_group(user):

    """Sync the user to the correct auth Group matching their role."""

    from django.contrib.auth.models import Group

    ROLE_GROUP_MAP = {

        UserRole.SUPER_ADMIN: "role_super_admin",

        UserRole.HEAD_OF_SCHOOL: "role_head_of_school",

        UserRole.ADMIN_OFFICER: "role_admin_officer",

        UserRole.FINANCE_OFFICER: "role_finance_officer",

        UserRole.PRIMARY_HOD: "role_primary_hod",

        UserRole.ECD_HOD: "role_ecd_hod",

        UserRole.LOWER_SECONDARY_HOD: "role_lower_secondary_hod",

        UserRole.TEACHER: "role_teacher",

        UserRole.PARENT: "role_parent",

    }

    target_group = ROLE_GROUP_MAP.get(user.role)

    # Handle custom roles (e.g. "custom_12" → "role_custom_12")

    if target_group is None and user.role and user.role.startswith("custom_"):

        target_group = f"role_{user.role}"

    for grp in Group.objects.filter(name__startswith="role_"):

        grp.user_set.remove(user)

    if target_group:

        grp, _ = Group.objects.get_or_create(name=target_group)

        grp.user_set.add(user)





class HISMSAuthenticationForm(AuthenticationForm):

    """FRD: custom login error message."""

    error_messages = {

        **AuthenticationForm.error_messages,

        'invalid_login': "Email or password incorrect",

    }





class HISMSLoginView(LoginView):

    """

    Override Django's LoginView to enforce:

    1. Login lockout after N failed attempts

    2. Redirect based on user role after success

    """

    template_name = "registration/login.html"

    authentication_form = HISMSAuthenticationForm



    def get(self, request, *args, **kwargs):

        return super().get(request, *args, **kwargs)



    def form_valid(self, form):

        """Called when credentials are correct."""

        user = form.get_user()

        # Check lockout first

        if user.is_locked_out:

            remaining = int((user.locked_until - timezone.now()).total_seconds() / 60) + 1

            form.add_error(

                None,

                f"Account locked. Try again after {remaining} minute(s)."

            )

            return self.form_invalid(form)

        # FR-STF-003: Auto-deactivate departed staff before granting session

        if user.departure_date and user.departure_date <= timezone.now().date():

            user.is_active = False

            user.save(update_fields=["is_active"])

            from audit.models import log_event as _audit_log

            _audit_log(

                actor=user,

                action_type="ACCOUNT_DEACTIVATED",

                model_name="User",

                object_id=user.pk,

                description=(

                    f"Auto-deactivated on login: departure date "

                    f"{user.departure_date} has passed."

                ),

                request=self.request,

            )

            form.errors.pop("__all__", None)

            form.add_error(None, "Your account has been deactivated. Contact your administrator.")

            return self.form_invalid(form)

        user.clear_failed_logins()

        login(self.request, user)

        # Audit log successful login

        from audit.models import log_event as _audit_log

        _audit_log(

            actor=user,

            action_type="LOGIN",

            model_name="User",

            object_id=user.pk,

            description=f"Successful login by {user.get_full_name() or user.username}",

            request=self.request,

        )

        # FR-STAFF-004: Cache role in session so it persists until logout

        from users.middleware import SESSION_ROLE_KEY

        self.request.session[SESSION_ROLE_KEY] = user.role

        # FR-STAFF-003: force password change on first login

        if user.must_change_password:

            return redirect("users:password_change")

        return redirect(self.get_success_url())

    def form_invalid(self, form):

        """Called when credentials are wrong OR lockout above."""

        username = form.data.get("username", "")

        if username:

            from users.models import User

            try:

                user = User.objects.get(username=username)

                # FRD: deactivated accounts must not be counted toward lockout

                if not user.is_active:

                    form.errors.pop('__all__', None)  # Clear wrong-password error

                    form.add_error(

                        None,

                        "Your account is not active. Contact your administrator."

                    )

                    return super().form_invalid(form)

                if not user.is_locked_out:

                    max_attempts = getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)

                    lockout_seconds = getattr(settings, "LOGIN_LOCKOUT_DURATION", 900)

                    user.record_failed_login(max_attempts=max_attempts, lockout_seconds=lockout_seconds)

                    # Audit log failed login attempt

                    from audit.models import log_event as _audit_log

                    _audit_log(

                        actor=user,

                        action_type="FAILED_LOGIN",

                        model_name="User",

                        object_id=user.pk,

                        description=(

                            f"Failed login attempt for {user.username} "

                            f"(attempt {user.failed_login_attempts}/{max_attempts})"

                        ),

                        request=self.request,

                    )

                    if user.is_locked_out:

                        # Clear the wrong-password error so only lockout message shows

                        form.errors.pop('__all__', None)

                        form.add_error(

                            None,

                            "Account locked. Try again after 15 minutes."

                        )

                        # Audit log account lockout

                        from audit.models import log_event as _audit_log

                        _audit_log(

                            actor=user,

                            action_type="ACCOUNT_LOCKED",

                            model_name="User",

                            object_id=user.pk,

                            description=(

                                f"Account locked for {user.username} after "

                                f"{max_attempts} consecutive failed login attempts"

                            ),

                            request=self.request,

                        )

                        # FR-AUTH-005: Alert Super Admin about account lockout

                        self._alert_super_admin_lockout(user)

            except User.DoesNotExist:

                pass  # Don't reveal existence

        # Move non-field credential errors to password field for inline display

        if form.errors.get('__all__'):

            pw_errors = form.errors.pop('__all__')

            if 'password' in form.errors:

                form.errors['password'].extend(pw_errors)

            else:

                form.errors['password'] = pw_errors

        return super().form_invalid(form)



    # FR-AUTH-005: Alert Super Admin when an account is locked

    def _alert_super_admin_lockout(self, locked_user):

        """Send in-app notification to all Super Admins about a locked account."""

        try:

            from communications.email_service import dispatch_notification



            super_admins = User.objects.filter(

                role=UserRole.SUPER_ADMIN, is_active=True

            )

            for admin in super_admins:

                dispatch_notification(

                    user=admin,

                    title="Account Locked: Security Alert",

                    message=(

                        f"Account '{locked_user.username}' has been locked for "

                        f"15 minutes after 5 consecutive failed login attempts. "

                        f"User: {locked_user.get_full_name() or locked_user.username} "

                        f"({locked_user.role}). Time: {timezone.now().strftime('%Y-%m-%d %H:%M')}"

                    ),

                    link="/users/",

                    actor=None,

                )

        except Exception as exc:

            logger.warning("Failed to send lockout alert to Super Admin: %s", exc)

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next") or ""
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={self.request.get_host()}):
            return next_url
        return settings.LOGIN_REDIRECT_URL or "/"


class HISMSLoginAPIView(View):
    """API endpoint: accepts JSON login (email + password) for tabbed login form.
    Returns JSON with redirect URL or error."""

    def post(self, request):
        import json
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid request body."}, status=400)

        email = (body.get("email") or "").strip()
        password = body.get("password", "")
        login_type = body.get("type", "staff")  # "staff" or "parent"

        if not email or not password:
            return JsonResponse({"ok": False, "error": "Email and password are required."}, status=400)

        from users.models import User
        try:
            user_obj = User.objects.get(email=email)
        except User.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Email or password incorrect."}, status=401)

        # Role gate
        if login_type == "parent" and user_obj.role != UserRole.PARENT:
            return JsonResponse({"ok": False, "error": "This account is not a parent account. Please use Staff Login."}, status=403)
        if login_type == "staff" and user_obj.role == UserRole.PARENT:
            return JsonResponse({"ok": False, "error": "This account is a parent account. Please use Parent Login."}, status=403)

        # Check lockout
        if user_obj.is_locked_out:
            remaining = int((user_obj.locked_until - timezone.now()).total_seconds() / 60) + 1
            return JsonResponse({"ok": False, "error": f"Account locked. Try again after {remaining} minute(s)."}, status=423)

        # Check active
        if not user_obj.is_active:
            return JsonResponse({"ok": False, "error": "Your account is not active. Contact your administrator."}, status=403)

        # Check departure
        if user_obj.departure_date and user_obj.departure_date <= timezone.now().date():
            user_obj.is_active = False
            user_obj.save(update_fields=["is_active"])
            from audit.models import log_event as _audit_log
            _audit_log(
                actor=user_obj, action_type="ACCOUNT_DEACTIVATED", model_name="User",
                object_id=user_obj.pk,
                description=f"Auto-deactivated on login: departure date {user_obj.departure_date} has passed.",
                request=request,
            )
            return JsonResponse({"ok": False, "error": "Your account has been deactivated. Contact your administrator."}, status=403)

        # Authenticate
        authed = authenticate(request, username=user_obj.username, password=password)
        if authed is None:
            # Record failed attempt
            max_attempts = getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
            lockout_seconds = getattr(settings, "LOGIN_LOCKOUT_DURATION", 900)
            user_obj.record_failed_login(max_attempts=max_attempts, lockout_seconds=lockout_seconds)
            from audit.models import log_event as _audit_log
            _audit_log(
                actor=user_obj, action_type="FAILED_LOGIN", model_name="User",
                object_id=user_obj.pk,
                description=f"Failed login attempt for {user_obj.username} (attempt {user_obj.failed_login_attempts}/{max_attempts})",
                request=request,
            )
            if user_obj.is_locked_out:
                _audit_log(
                    actor=user_obj, action_type="ACCOUNT_LOCKED", model_name="User",
                    object_id=user_obj.pk,
                    description=f"Account locked for {user_obj.username} after {max_attempts} consecutive failed login attempts",
                    request=request,
                )
                from users.views import HISMSLoginView
                HISMSLoginView()._alert_super_admin_lockout(user_obj)
            return JsonResponse({"ok": False, "error": "Email or password incorrect."}, status=401)

        # Success
        user_obj.clear_failed_logins()
        login(request, authed)
        from audit.models import log_event as _audit_log
        _audit_log(
            actor=authed, action_type="LOGIN", model_name="User",
            object_id=authed.pk,
            description=f"Successful login by {authed.get_full_name() or authed.username}",
            request=request,
        )
        from users.middleware import SESSION_ROLE_KEY
        request.session[SESSION_ROLE_KEY] = authed.role

        if authed.must_change_password:
            return JsonResponse({"ok": True, "redirect": "/accounts/password-change/"})

        next_url = body.get("next") or request.POST.get("next") or request.GET.get("next") or "/"
        if authed.role == UserRole.PARENT and next_url in ("/", ""):
            next_url = "/parent/"
        elif not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = settings.LOGIN_REDIRECT_URL or "/"
        return JsonResponse({"ok": True, "redirect": next_url})


class StartImpersonationView(RoleRequiredMixin, View):

    """FR-DASH-008: Super Admin 'View As' — start impersonation.

    NOTE: The ImpersonationMiddleware swaps request.user to the target user

    *before* this view runs, so we must use request.real_user for the

    permission check (the real admin).

    """

    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]

    required_permission = "users.change_user"



    def dispatch(self, request, *args, **kwargs):

        real = getattr(request, "real_user", request.user)

        if not real.is_authenticated:

            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())

        # Super Admin bypass (matches RoleRequiredMixin.dispatch pattern)

        if real.role == UserRole.SUPER_ADMIN:

            return super(RoleRequiredMixin, self).dispatch(request, *args, **kwargs)

        if real.role not in self.allowed_roles:

            raise PermissionDenied()

        if self.required_permission and not real.has_perm(self.required_permission):

            raise PermissionDenied()

        return super(RoleRequiredMixin, self).dispatch(request, *args, **kwargs)



    def get(self, request, user_id):

        """FR-DASH-008: Allow GET for direct URL navigation (e.g. browser address bar)."""

        target_user = get_object_or_404(User, pk=user_id)

        request.session["impersonate_user_id"] = target_user.pk

        messages.success(request, f"Now viewing as {target_user.get_full_name() or target_user.username}.")

        return redirect("/")



    def post(self, request, user_id):

        target_user = get_object_or_404(User, pk=user_id)

        request.session["impersonate_user_id"] = target_user.pk

        messages.success(request, f"Now viewing as {target_user.get_full_name() or target_user.username}.")

        return redirect("/")



class StopImpersonationView(RoleRequiredMixin, View):

    """FR-DASH-008: Stop impersonation and return to real user."""

    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]

    required_permission = "users.change_user"



    def dispatch(self, request, *args, **kwargs):

        real = getattr(request, "real_user", request.user)

        if not real.is_authenticated:

            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())

        # Super Admin bypass (matches RoleRequiredMixin.dispatch pattern)

        if real.role == UserRole.SUPER_ADMIN:

            return super(RoleRequiredMixin, self).dispatch(request, *args, **kwargs)

        if real.role not in self.allowed_roles:

            raise PermissionDenied()

        if self.required_permission and not real.has_perm(self.required_permission):

            raise PermissionDenied()

        return super(RoleRequiredMixin, self).dispatch(request, *args, **kwargs)



    def post(self, request):

        if "impersonate_user_id" in request.session:

            # FR-DASH-008: Log impersonation end before clearing

            try:

                from core.utils import get_client_ip

                from audit.models import AuditLog

                real_user = getattr(request, "real_user", request.user)

                target_username = request.user.get_full_name() or request.user.username

                AuditLog.objects.create(

                    actor=real_user,

                    action_type="IMPERSONATION_ENDED",

                    model_name="User",

                    object_id=str(request.user.pk),

                    description=(

                        f"Super Admin '{real_user.username}' ended View-As session "

                        f"as '{target_username}' ({request.user.get_role_display()})."

                    ),

                    ip_address=get_client_ip(request),

                )

            except Exception:

                pass

            request.session.pop("impersonate_user_id")

            request.session.pop("_impersonation_logged", None)

            messages.success(request, "View-As mode ended.")

        return redirect("/")







class UserListView(RoleRequiredMixin, ListView):

    template_name = "users/user_list.html"

    model = User

    context_object_name = "users"

    allowed_roles = [

        UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL,

        UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD,

    ]

    paginate_by = 50



    def get_queryset(self):

        qs = User.objects.select_related("staff_profile").exclude(role=UserRole.PARENT)

        q = self.request.GET.get("q", "").strip()

        dept = self.request.GET.get("department", "")

        status = self.request.GET.get("status", "")

        staff_cat = self.request.GET.get("staff_category", "")

        onboarding = self.request.GET.get("onboarding", "")



        # FRD: HODs can only see users in their own department

        hod_roles = {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}

        if self.request.user.role in hod_roles:

            my_dept = getattr(

                getattr(self.request.user, "staff_profile", None), "department", ""

            )

            if my_dept:

                qs = qs.filter(staff_profile__department=my_dept)



        if q:

            from django.db.models import Q

            qs = qs.filter(

                Q(username__icontains=q) |

                Q(first_name__icontains=q) |

                Q(last_name__icontains=q) |

                Q(email__icontains=q) |

                Q(staff_profile__job_title__icontains=q) |

                Q(staff_profile__employee_id__icontains=q)

            )

        if dept:

            qs = qs.filter(staff_profile__department=dept)

        if status == "active":

            qs = qs.filter(is_active=True)

        elif status == "inactive":

            qs = qs.filter(is_active=False)

        elif status == "expiring":

            from datetime import timedelta

            from django.utils import timezone as _tz

            today = _tz.now().date()

            qs = qs.filter(

                staff_profile__is_active=True,

                staff_profile__contract_end_date__isnull=False,

                staff_profile__contract_end_date__gte=today,

                staff_profile__contract_end_date__lte=today + timedelta(days=60),

            )

        if staff_cat:

            qs = qs.filter(staff_profile__staff_category=staff_cat)

        if onboarding == "pending":

            qs = qs.filter(staff_profile__is_active=True, staff_profile__onboarding_completed=False).exclude(staff_profile__onboarding_step=0)

        elif onboarding == "not_started":

            qs = qs.filter(staff_profile__is_active=True, staff_profile__onboarding_step=0)



        return qs.order_by("-is_active", "username")



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        from academics.models import Department

        from hr.models import StaffProfile, TeacherClassAssignment

        from datetime import timedelta

        from django.utils import timezone as _tz

        from academics.utils import get_current_term



        ctx["department_choices"] = Department.choices

        today = _tz.now().date()

        ctx["expiring_contracts"] = StaffProfile.objects.filter(

            is_active=True, contract_end_date__isnull=False,

            contract_end_date__gte=today,

            contract_end_date__lte=today + timedelta(days=60),

        ).order_by("contract_end_date")[:10]



        # Build assigned-classes lookup for each user in the queryset

        term = get_current_term()

        user_ids = [u.pk for u in ctx["users"]]

        user_assignments: dict[int, list[dict]] = {}

        if term and user_ids:

            tcqs = (

                TeacherClassAssignment.objects

                .filter(teacher__user_id__in=user_ids, term=term)

                .select_related("grade_class", "teacher__user")

            )

            for tca in tcqs:

                uid = tca.teacher.user_id

                user_assignments.setdefault(uid, []).append({

                    "class_name": tca.grade_class.name,

                    "is_class_teacher": tca.is_class_teacher,

                    "subjects_taught": tca.subjects_taught or [],

                })

        ctx["user_assignments"] = user_assignments

        return ctx





class UserProfileView(RoleRequiredMixin, TemplateView):

    allowed_roles = [

        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,

        UserRole.ECD_HOD, UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,

        UserRole.TEACHER, UserRole.PARENT]

    template_name = "users/profile.html"



    def get(self, request, *args, **kwargs):

        from hr.models import StaffProfile

        staff = StaffProfile.objects.filter(user=request.user).first()

        if staff:

            return redirect("hr:staff_detail", pk=staff.pk)

        return super().get(request, *args, **kwargs)



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        u = self.request.user

        ctx["profile_user"] = u

        ctx["is_own_profile"] = True

        ctx["is_admin_viewing"] = False

        ctx["user_initials"] = (u.first_name[0] if u.first_name else "") + (u.last_name[0] if u.last_name else u.username[0])

        if u.role == UserRole.PARENT:

            from students.models import ParentGuardian, StudentGuardian, Student, StudentStatus

            guardian = ParentGuardian.objects.filter(user=u).first()

            if guardian:

                ctx["linked_children"] = Student.objects.filter(

                    guardians=guardian, status=StudentStatus.ACTIVE

                )

                ctx["guardian_profile"] = guardian

            else:

                ctx["linked_children"] = Student.objects.none()

                ctx["guardian_profile"] = None

        return ctx



    def post(self, request, *args, **kwargs):

        if "profile_picture" in request.FILES:

            from academics.validators import validate_attachment_file, _compute_file_hash

            from users.models import User

            pic = request.FILES["profile_picture"]

            try:

                validate_attachment_file(pic, area="profile_photos")



                # Duplicate photo detection — compare hash with other users

                pic.seek(0)

                new_hash = _compute_file_hash(pic)

                pic.seek(0)



                other_users = User.objects.filter(

                    profile_picture__isnull=False

                ).exclude(pk=request.user.pk)

                for u in other_users:

                    try:

                        if _compute_file_hash(u.profile_picture.path) == new_hash:

                            messages.warning(

                                request,

                                f"This photo is already used by {u.get_full_name() or u.username}. "

                                "Please upload a different photo."

                            )

                            return redirect("users:profile")

                    except (AttributeError, ValueError, OSError):

                        continue



                user = request.user

                user.profile_picture = pic

                user.save(update_fields=["profile_picture"])

                messages.success(request, "Profile picture updated successfully.")

            except ValidationError as e:

                msg = e.message if hasattr(e, 'message') else str(e)

                messages.error(request, f"Photo rejected: {msg}")

        return redirect("users:profile")





class HISMSPasswordChangeView(RoleRequiredMixin, PasswordChangeView):

    allowed_roles = [

        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,

        UserRole.ECD_HOD, UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,

        UserRole.TEACHER, UserRole.PARENT]

    template_name = "registration/password_change_form.html"

    success_url = reverse_lazy("users:password_change_done")



    def form_valid(self, form):

        messages.success(self.request, "Your password has been successfully updated.")

        # FR-STAFF-003: clear must_change_password after successful change

        if self.request.user.must_change_password:

            self.request.user.must_change_password = False

            self.request.user.save(update_fields=["must_change_password"])

        # FRD: invalidate all OTHER active sessions for this user

        from django.contrib.sessions.models import Session

        user_id = str(self.request.user.pk)

        current_session_key = self.request.session.session_key

        for session in Session.objects.filter(expire_date__gte=timezone.now()):

            try:

                data = session.get_decoded()

                if data.get("_auth_user_id") == user_id and session.session_key != current_session_key:

                    session.delete()

            except Exception:

                pass

        return super().form_valid(form)





class HISMSPasswordChangeDoneView(RoleRequiredMixin, PasswordChangeDoneView):

    allowed_roles = [

        UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD,

        UserRole.ECD_HOD, UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER,

        UserRole.TEACHER, UserRole.PARENT]

    template_name = "registration/password_change_done.html"



from users.forms import UserCreateForm, UserUpdateForm

from django.views.generic import CreateView, UpdateView



class _UserFormContextMixin:

    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        import json

        from academics.models import GradeClass, Subject, Department, Term, AcademicYear

        from academics.utils import get_current_term

        # Class → department mapping for JS department filtering

        class_depts = {}

        for gc in GradeClass.objects.all():

            class_depts[str(gc.id)] = gc.department

        ctx["class_depts_json"] = json.dumps(class_depts)

        ctx["class_depts_dict"] = class_depts

        # Build list of (id, name, department) for template rendering

        from academics.models import GradeClass as _GC

        ctx["class_items_list"] = [

            {"id": str(gc.id), "name": gc.name, "dept": gc.department}

            for gc in _GC.objects.all().order_by("sort_order", "name")

        ]

        # Department → subjects mapping for JS subject filtering

        all_subjects = list(Subject.objects.filter(is_active=True).values("name", "departments"))

        dept_subjects = {}

        dept_choices = [c[0] for c in Department.choices if c[0] != "ADMINISTRATION"]

        for dept in dept_choices:

            dept_subjects[dept] = sorted(

                s["name"] for s in all_subjects if dept in (s["departments"] or [])

            )

        ctx["dept_subjects_json"] = json.dumps(dept_subjects)

        # Class → subjects mapping for JS subject filtering by checked classes

        class_subjects = {}

        for gc in GradeClass.objects.all():

            class_subjects[str(gc.id)] = sorted(

                gc.subjects.filter(is_active=True).values_list("name", flat=True)

            )

        ctx["class_subjects_json"] = json.dumps(class_subjects)

        # Subject → color mapping for table grid

        subject_colors = {}

        for s in Subject.objects.filter(is_active=True):

            subject_colors[s.name] = s.color

        ctx["subject_colors_json"] = json.dumps(subject_colors)

        # Flat list of all active subjects with name + color for template table

        ctx["all_subjects_list"] = [

            {"name": s.name, "color": s.color}

            for s in Subject.objects.filter(is_active=True).order_by("name")

        ]



        # --- Multi-term context ---

        from academics.utils import get_current_academic_year

        current_academic_year = get_current_academic_year()

        current_term = get_current_term()

        if current_academic_year:

            terms_qs = Term.objects.filter(

                academic_year=current_academic_year

            ).order_by("start_date")

        else:

            terms_qs = Term.objects.all().order_by("-end_date")[:10]

        ctx["available_terms"] = list(terms_qs)

        ctx["current_term"] = current_term

        # Current term IDs as JSON for JS default selection

        ctx["current_term_ids_json"] = json.dumps(

            [current_term.pk] if current_term else []

        )

        # Staff contract dates for JS warning display

        profile = None

        if self.request.resolver_match and self.request.resolver_match.url_name == "user_edit":

            user_obj = self.get_object()

            profile = getattr(user_obj, "staff_profile", None)

        ctx["staff_contract"] = {

            "start": profile.employment_start_date.isoformat() if profile and profile.employment_start_date else None,

            "end": profile.contract_end_date.isoformat() if profile and profile.contract_end_date else None,

        }

        # Existing assignments per term for conflict detection

        if profile:

            existing = {}

            assigned_term_ids = set()

            for tca in profile.assignments.select_related("term", "grade_class").all():

                tid = str(tca.term_id)

                assigned_term_ids.add(tca.term_id)

                if tid not in existing:

                    existing[tid] = []

                existing[tid].append({

                    "class_id": str(tca.grade_class_id),

                    "class_name": tca.grade_class.name,

                    "subjects": tca.subjects_taught or [],

                    "is_class_teacher": tca.is_class_teacher,

                })

            ctx["existing_assignments_json"] = json.dumps(existing)

            ctx["assigned_term_ids"] = list(assigned_term_ids)

            # Per-class subjects for the current term so the grid pre-selects only assigned subjects
            current_cls_subjects = {}
            ct = get_current_term()
            if ct and str(ct.pk) in existing:
                for a in existing[str(ct.pk)]:
                    if a.get("subjects"):
                        current_cls_subjects[a["class_id"]] = a["subjects"]
            ctx["initial_class_subjects_json"] = json.dumps(current_cls_subjects)

        else:

            ctx["existing_assignments_json"] = json.dumps({})

            ctx["assigned_term_ids"] = []

            ctx["initial_class_subjects_json"] = json.dumps({})



        # Role → departments mapping from RoleConfig (source of truth)

        from users.role_models import RoleConfig

        role_depts = {}

        for rc in RoleConfig.objects.filter(is_active=True):

            role_depts[rc.role] = rc.departments

        ctx["role_depts_json"] = json.dumps(role_depts)



        return ctx





class UserCreateView(_UserFormContextMixin, RoleRequiredMixin, CreateView):

    model = User

    template_name = "users/user_form.html"

    form_class = UserCreateForm

    allowed_roles = [UserRole.SUPER_ADMIN]

    required_permission = "users.add_user"



    def get_success_url(self):

        return f"/accounts/edit/{self.object.pk}/?saved=1#tab-assignments"



    def get_form_kwargs(self):

        kwargs = super().get_form_kwargs()

        kwargs["request"] = self.request

        kwargs["skip"] = self.request.POST.get("action") == "skip"

        return kwargs



    def form_invalid(self, form):

        for field, errors in form.errors.items():

            label = field if field != "__all__" else "Form"

            for err in errors:

                messages.error(self.request, f"{label}: {err}")

        return super().form_invalid(form)



    def form_valid(self, form):

        is_skip = form.skip

        try:

            response = super().form_valid(form)

        except Exception as e:

            import logging

            logging.getLogger("users.views").error(f"super().form_valid failed: {e}")

            from django.http import HttpResponseRedirect

            response = HttpResponseRedirect(f"/accounts/edit/{form.instance.pk}/?saved=1#tab-assignments")

        try:

            _sync_user_role_group(form.instance)

        except Exception as e:

            import logging

            logging.getLogger("users.views").error(f"_sync_user_role_group failed: {e}")

        try:

            from audit.models import log_event as _audit_log

            _audit_log(

                actor=self.request.user,

                action_type="USER_CREATED",

                model_name="User",

                object_id=form.instance.pk,

                description=(

                    f"{self.request.user.get_full_name() or self.request.user.username} "

                    f"created user {form.instance.get_full_name() or form.instance.username} "

                    f"(role={form.instance.get_role_display()})"

                ),

                request=self.request,

            )

        except Exception as e:

            import logging

            logging.getLogger("users.views").error(f"audit_log failed: {e}")

        # FRD OP 10.1: Notify if activation email fails

        try:

            if getattr(form, '_email_sent', False):

                messages.success(

                    self.request,

                    f"Account created for {form.instance.get_full_name() or form.instance.username}. "

                    f"Activation email sent to {form.instance.email}."

                )

            else:

                messages.warning(

                    self.request,

                    f"Account created for {form.instance.get_full_name() or form.instance.username}, "

                    f"but activation email could not be sent to {form.instance.email}. "

                    f"Please check SMTP configuration or contact the user manually."

                )

        except Exception as e:

            import logging

            logging.getLogger("users.views").error(f"messages failed: {e}")

        if is_skip:

            try:

                from hr.models import StaffProfile, StaffOnboardingProgress

                profile = StaffProfile.objects.filter(user=form.instance).first()

                if profile:

                    StaffOnboardingProgress.objects.get_or_create(staff=profile)

                    return redirect("hr:onboarding_start", pk=profile.pk)

            except Exception as e:

                import logging

                logging.getLogger("users.views").error(f"onboarding redirect failed: {e}")

        return response





class UserUpdateView(_UserFormContextMixin, RoleRequiredMixin, UpdateView):

    model = User

    template_name = "users/user_form.html"

    form_class = UserUpdateForm

    allowed_roles = [UserRole.SUPER_ADMIN]

    required_permission = "users.change_user"



    def get_success_url(self):

        return f"/accounts/edit/{self.object.pk}/?saved=1#tab-assignments"



    def form_invalid(self, form):

        for field, errors in form.errors.items():

            label = field if field != "__all__" else "Form"

            for err in errors:

                messages.error(self.request, f"{label}: {err}")

        return super().form_invalid(form)



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        import json

        from hr.models import TeacherClassAssignment

        from academics.utils import get_current_term

        # Permission-driven: anyone who can edit a user (users.change_user) can reset their password

        ctx["can_reset_password"] = self.request.user.has_perm("users.change_user")

        # Class → class teacher name mapping for assistant class teacher display

        ct_term = get_current_term()

        class_teachers = {}

        assistant_teachers = {}

        if ct_term:

            ctas = TeacherClassAssignment.objects.filter(

                term=ct_term, is_class_teacher=True

            ).select_related("teacher__user")

            for tca in ctas:

                class_teachers[str(tca.grade_class_id)] = tca.teacher.full_name

            # Assistant class teacher: only explicitly assigned assistants

            asst_tcas = TeacherClassAssignment.objects.filter(

                term=ct_term, is_assistant_class_teacher=True

            ).select_related("teacher__user")

            for tca in asst_tcas:

                cid = str(tca.grade_class_id)

                assistant_teachers.setdefault(cid, []).append(tca.teacher.full_name)

        ctx["class_teachers_json"] = json.dumps(class_teachers)

        ctx["assistant_class_teachers_json"] = json.dumps(assistant_teachers)

        return ctx



    def form_valid(self, form):

        import logging

        _log = logging.getLogger("users.views")

        try:

            # Detect role change and active-status change for ANY user.

            # NOTE: ModelForm._post_clean() mutates form.instance during is_valid(),

            # so form.instance.role/is_active already reflect the NEW values here.

            # Read the ORIGINAL values from the database instead, otherwise the

            # role-change guard (2.3) never fires and deactivation (2.4) is missed.

            from users.models import User as _UserModel

            orig = None

            if form.instance.pk:

                orig = _UserModel.objects.filter(pk=form.instance.pk).first()

            old_role = orig.role if orig else form.instance.role

            new_role = form.cleaned_data.get("role")

            role_changed = (

                form.instance.pk is not None

                and old_role != new_role

            )

            was_active = orig.is_active if orig else form.instance.is_active

            new_active = form.cleaned_data.get("is_active", was_active)

            deactivated = was_active and not new_active



            # OP 2.3: Require a reason when changing a user's role

            if role_changed:

                role_reason = self.request.POST.get("role_change_reason", "").strip()

                if not role_reason:

                    form.add_error(

                        None,

                        "A reason is required when changing a user's role. "

                        "Please provide a reason for this role change.",

                    )

                    return self.form_invalid(form)



            # Must pop from cleaned_data so form.save() won't process it either
            can_reset = self.request.user.has_perm("users.change_user")
            if not can_reset:

                form.cleaned_data.pop("new_password", None)

            new_password = form.cleaned_data.get("new_password", "")

            password_changed = bool(new_password)



            response = super().form_valid(form)



            # Sync role group membership after save

            _sync_user_role_group(form.instance)



            # NFR-SEC: Invalidate sessions when account is deactivated

            if deactivated and form.instance.pk != self.request.user.pk:

                self._invalidate_user_sessions(form.instance.pk)

                from audit.models import log_event as _audit_log

                _audit_log(

                    actor=self.request.user,

                    action_type="ACCOUNT_DEACTIVATED",

                    model_name="User",

                    object_id=form.instance.pk,

                    description=(

                        f"Account deactivated for "

                        f"{form.instance.get_full_name() or form.instance.username}. "

                        f"Sessions invalidated."

                    ),

                    request=self.request,

                )

                messages.warning(

                    self.request,

                    f"Account for {form.instance.get_full_name() or form.instance.username} "

                    "has been deactivated. All active sessions have been terminated."

                )



            # Handle admin password reset (after save so we know it persisted)

            if password_changed:

                from audit.models import log_event

                log_event(

                    actor=self.request.user,

                    action_type="PASSWORD_RESET",

                    model_name="User",

                    object_id=form.instance.pk,

                    description=(

                        f"Admin {self.request.user.get_full_name() or self.request.user.username} "

                        f"reset password for {form.instance.get_full_name() or form.instance.username}",

                    ),

                    request=self.request,

                )

                # Always invalidate sessions and force password change on next login

                form.instance.must_change_password = True

                form.instance.save(update_fields=["must_change_password"])

                editing_self_pw = form.instance.pk == self.request.user.pk

                if not editing_self_pw:

                    self._invalidate_user_sessions(form.instance.pk)

                messages.success(

                    self.request,

                    f"Password changed for {form.instance.get_full_name() or form.instance.username}."

                )



            if role_changed:

                from audit.models import log_event as _audit_log

                role_labels = dict(UserRole.choices)

                role_reason = self.request.POST.get("role_change_reason", "").strip()

                desc = (

                    f"Role changed from {role_labels.get(old_role, old_role)} "

                    f"to {role_labels.get(new_role, new_role)} for "

                    f"{form.instance.get_full_name() or form.instance.username}"

                )

                if role_reason:

                    desc += f". Reason: {role_reason}"

                _audit_log(

                    actor=self.request.user,

                    action_type="ROLE_CHANGE",

                    model_name="User",

                    object_id=form.instance.pk,

                    description=desc,

                    before_value=old_role,

                    after_value=new_role,

                    request=self.request,

                )



                target_user = form.instance

                editing_self = target_user.pk == self.request.user.pk



                # 1. Update session cache if editing self

                if editing_self:

                    from users.middleware import SESSION_ROLE_KEY

                    self.request.session[SESSION_ROLE_KEY] = new_role



                # 2. Auto-sync departments to match the new role

                self._sync_departments_for_role(target_user, new_role)



                # 3. Invalidate sessions for other users only (skip self to

                #    avoid logging the admin out mid-request)

                if not editing_self:

                    self._invalidate_user_sessions(target_user.pk)



                # 4. Send in-app notification to the affected user (skip self)

                if not editing_self:

                    self._notify_role_changed(target_user, old_role, new_role)



                # 5. Warning message for the admin

                if editing_self:

                    messages.success(

                        self.request,

                        f"Your role has been updated to {target_user.get_role_display()}. "

                        "Refresh the page to see the changes."

                    )

                else:

                    messages.warning(

                        self.request,

                        f"Role updated to {target_user.get_role_display()} for "

                        f"{target_user.get_full_name() or target_user.username}. "

                        "The user has been signed out and must log in again."

                    )



            return response

        except Exception:

            _log.exception("UserUpdateView.form_valid failed for pk=%s", self.kwargs.get("pk"))

            raise



    # ------------------------------------------------------------------

    # Role change helpers

    # ------------------------------------------------------------------



    @staticmethod

    def _sync_departments_for_role(user, new_role):

        """Auto-sync StaffProfile departments to match the new role.



        Roles with fixed departments are auto-set. Teachers keep their

        existing department assignments unchanged.

        """

        from academics.models import Department



        ROLE_DEPT_DEFAULTS = {

            UserRole.SUPER_ADMIN: [

                Department.ECD, Department.PRIMARY,

                Department.LOWER_SECONDARY, Department.ADMINISTRATION,

            ],

            UserRole.HEAD_OF_SCHOOL: [

                Department.ECD, Department.PRIMARY,

                Department.LOWER_SECONDARY, Department.ADMINISTRATION,

            ],

            UserRole.PRIMARY_HOD: [Department.PRIMARY],

            UserRole.ECD_HOD: [Department.ECD],

            UserRole.LOWER_SECONDARY_HOD: [Department.LOWER_SECONDARY],

            UserRole.ADMIN_OFFICER: [Department.ADMINISTRATION],

            UserRole.FINANCE_OFFICER: [Department.ADMINISTRATION],

        }



        new_depts = ROLE_DEPT_DEFAULTS.get(new_role)

        if new_depts is None:

            return  # Teachers and parents keep existing departments



        if not hasattr(user, 'staff_profile'):

            return

        profile = user.staff_profile



        profile.departments = new_depts

        if new_depts:

            profile.department = new_depts[0]

        profile.save(update_fields=["departments", "department", "updated_at"])



    @staticmethod

    def _invalidate_user_sessions(user_id):

        """Invalidate active sessions for a user to force re-login."""

        from django.contrib.sessions.models import Session

        from django.utils import timezone



        for session in Session.objects.filter(expire_date__gte=timezone.now()):

            try:

                data = session.get_decoded()

                if data.get("_auth_user_id") == str(user_id):

                    session.delete()

            except Exception:

                pass



    @staticmethod

    def _notify_role_changed(user, old_role, new_role):

        """Send in-app notification when a user's role is changed by an admin."""

        from communications.email_service import dispatch_notification



        role_labels = dict(UserRole.choices)

        old_label = role_labels.get(old_role, old_role)

        new_label = role_labels.get(new_role, new_role)



        dispatch_notification(

            user=user,

            title="Your Role Has Been Updated",

            message=(

                f"Your system role has been changed from {old_label} to {new_label}. "

                "Please sign in again to access the updated interface."

            ),

            link="/accounts/login/",

        )





# ========================================================================

# Assignment Conflict Check (AJAX)

# ========================================================================



class AssignmentConflictCheckView(RoleRequiredMixin, View):

    """AJAX endpoint: check contract + subject-slot conflicts for multi-term assignment."""

    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "hr.change_teacherclassassignment"



    def post(self, request):

        import json

        from hr.models import StaffProfile

        from hr.utils import check_assignment_conflicts_bulk



        try:

            data = json.loads(request.body)

        except (json.JSONDecodeError, TypeError):

            return JsonResponse({"warnings": [], "error": "Invalid JSON"}, status=400)



        teacher_id = data.get("teacher_id")

        term_ids = data.get("term_ids", [])

        class_ids = data.get("class_ids", [])

        per_class_subjects = data.get("per_class_subjects", {})



        if not teacher_id or not term_ids or not class_ids:

            return JsonResponse({"warnings": []})



        try:

            profile = StaffProfile.objects.get(pk=teacher_id)

        except StaffProfile.DoesNotExist:

            return JsonResponse({"warnings": [], "error": "Staff profile not found"}, status=404)



        warnings = check_assignment_conflicts_bulk(

            teacher_profile=profile,

            term_ids=term_ids,

            class_ids=class_ids,

            per_class_subjects=per_class_subjects,

        )



        return JsonResponse({"warnings": warnings})





# ========================================================================

# FRD AUTH-RESET-001: Password Reset Flow

# ========================================================================

import re

import logging



from django.contrib.auth.tokens import default_token_generator

from django.core.mail import send_mail

from django.template.loader import render_to_string

from django.utils.encoding import force_bytes, force_str

from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from django.views.generic import FormView

from django import forms



logger = logging.getLogger(__name__)





class HISMSLogoutView(View):

    """

    Custom logout that accepts GET requests (for testing compatibility).

    Django's built-in LogoutView in v4+ requires POST by default, which

    breaks headless/script-based logout used in headed browser tests.

    """

    def get(self, request, *args, **kwargs):

        # Audit log before logout (while we still have the user)

        if request.user.is_authenticated:

            from audit.models import log_event as _audit_log

            _audit_log(

                actor=request.user,

                action_type="LOGOUT",

                model_name="User",

                object_id=request.user.pk,

                description=f"User {request.user.get_full_name() or request.user.username} logged out",

                request=request,

            )

        auth_logout(request)

        messages.success(request, "You have been logged out.")

        return redirect(settings.LOGOUT_REDIRECT_URL or "/accounts/login/")



    def post(self, request, *args, **kwargs):

        if request.user.is_authenticated:

            from audit.models import log_event as _audit_log

            _audit_log(

                actor=request.user,

                action_type="LOGOUT",

                model_name="User",

                object_id=request.user.pk,

                description=f"User {request.user.get_full_name() or request.user.username} logged out",

                request=request,

            )

        auth_logout(request)

        messages.success(request, "You have been logged out.")

        return redirect(settings.LOGOUT_REDIRECT_URL or "/accounts/login/")





class ForceLogoutView(RoleRequiredMixin, View):

    """

    SA-only: terminate another user's active sessions.

    FRD NFR-SEC-005 — SA can force any user to re-login.

    """

    allowed_roles = [UserRole.SUPER_ADMIN]
    required_permission = "users.change_user"



    def post(self, request, user_id):

        target_user = get_object_or_404(User, pk=user_id)

        if target_user.pk == request.user.pk:

            messages.error(request, "You cannot force-terminate your own session.")

            return redirect("users:user_list")



        from django.contrib.sessions.models import Session

        from django.utils import timezone as _tz

        terminated = 0

        for session in Session.objects.filter(expire_date__gte=_tz.now()):

            try:

                data = session.get_decoded()

                if data.get("_auth_user_id") == str(target_user.pk):

                    session.delete()

                    terminated += 1

            except Exception:

                pass



        from audit.models import log_event

        log_event(

            actor=request.user,

            action_type="FORCE_LOGOUT",

            model_name="User",

            object_id=target_user.pk,

            description=(

                f"Super Admin {request.user.get_full_name() or request.user.username} "

                f"force-terminated {terminated} active session(s) for "

                f"{target_user.get_full_name() or target_user.username}"

            ),

            request=request,

        )

        messages.success(

            request,

            f"Terminated {terminated} active session(s) for "

            f"{target_user.get_full_name() or target_user.username}."

        )

        return redirect("users:user_list")





class ReactivateUserView(RoleRequiredMixin, View):

    """

    SA-only: reactivate a deactivated user account.

    Requires a reason for audit trail (FRD: "Reactivation without logging reason" must be prevented).

    """

    allowed_roles = [UserRole.SUPER_ADMIN]

    required_permission = "users.change_user"



    def post(self, request, user_id):

        target_user = get_object_or_404(User, pk=user_id)

        if target_user.is_active:

            messages.info(request, "This account is already active.")

            return redirect("users:user_list")



        reason = request.POST.get("reason", "").strip()

        if not reason:

            messages.error(request, "A reason is required to reactivate an account.")

            return redirect("users:user_list")



        target_user.is_active = True

        target_user.departure_date = None

        target_user.departure_reason = ""

        target_user.save(update_fields=["is_active", "departure_date", "departure_reason"])



        from audit.models import log_event

        log_event(

            actor=request.user,

            action_type="REACTIVATION",

            model_name="User",

            object_id=target_user.pk,

            description=(

                f"Super Admin {request.user.get_full_name() or request.user.username} "

                f"reactivated account for {target_user.get_full_name() or target_user.username}. "

                f"Reason: {reason}"

            ),

            request=request,

        )

        messages.success(

            request,

            f"Account for {target_user.get_full_name() or target_user.username} has been reactivated."

        )

        return redirect("users:user_list")





# --- FRD: "a password reset form requiring a new password and confirmation

#     field" with custom validation (8+ chars, must contain a number).

# ========================================================================



class PasswordResetRequestForm(forms.Form):

    """FRD: user enters their registered email address."""

    email = forms.EmailField(

        label="Email address",

        max_length=254,

        widget=forms.EmailInput(attrs={

            "class": "hf2-input",

            "placeholder": "you@hodari.ac.tz",

            "autocomplete": "email",

            "autofocus": True,

        }),

    )





class SetNewPasswordForm(forms.Form):

    """

    FRD: new password + confirmation.

    Validation: >= 8 chars AND contains at least one digit.

    """

    new_password = forms.CharField(

        label="New password",

        widget=forms.PasswordInput(attrs={

            "class": "hf2-input",

            "placeholder": "Enter new password",

            "autocomplete": "new-password",

        }),

    )

    confirm_password = forms.CharField(

        label="Confirm new password",

        widget=forms.PasswordInput(attrs={

            "class": "hf2-input",

            "placeholder": "Re-enter new password",

            "autocomplete": "new-password",

        }),

    )



    def clean_new_password(self):

        pw = self.cleaned_data.get("new_password", "")

        errors = []

        if len(pw) < 8:

            errors.append("Password must be at least 8 characters long.")

        if not re.search(r"\d", pw):

            errors.append("Password must contain at least one number.")

        if errors:

            raise forms.ValidationError(" ".join(errors))

        return pw



    def clean(self):

        cleaned = super().clean()

        pw = cleaned.get("new_password")

        cpw = cleaned.get("confirm_password")

        if pw and cpw and pw != cpw:

            self.add_error("confirm_password", "Passwords do not match.")

        return cleaned





class PasswordResetRequestView(FormView):

    """

    FRD: "a user clicks 'Forgot password?' on the login screen WHEN they

    enter their registered email address and submit THEN a reset link is

    sent to that email within 60 seconds. The screen displays: 'If that

    email is registered, you will receive a reset link.' (Message shown

    regardless of whether email exists - security best practice.)"

    """

    template_name = "registration/forgot_password.html"

    form_class = PasswordResetRequestForm

    success_url = reverse_lazy("users:password_reset_done")



    def form_valid(self, form):

        from core.throttles import rate_limit_or_429

        email = form.cleaned_data["email"].strip().lower()



        def _email_key(req):

            return email



        blocked = rate_limit_or_429(self.request, "password_reset", limit=3, period=300, key_func=_email_key)

        if blocked:

            return blocked



        # Security: always show the same message regardless of email existence

        try:

            user = User.objects.get(email__iexact=email)

            # Generate token

            uid = urlsafe_base64_encode(force_bytes(user.pk))

            token = default_token_generator.make_token(user)



            # Build reset URL

            reset_url = self.request.build_absolute_uri(

                reverse_lazy("users:password_reset_confirm", kwargs={

                    "uidb64": uid, "token": token,

                })

            )



            # FRD: email sent within 60 seconds

            subject = f"Password Reset - {getattr(settings, 'SCHOOL_NAME', 'Hodari SMS')}"

            context = {

                "user": user,

                "reset_url": reset_url,

                "expiry_hours": 1,

                "school_name": getattr(settings, 'SCHOOL_NAME', 'Hodari Christian School'),

                "site_url": getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000'),

                "static_url": getattr(settings, 'STATIC_URL', '/static/'),

            }

            # Try dynamic DB template first
            from core.email_templates import send_dynamic_email
            db_sent = send_dynamic_email(
                template_type="password_reset",
                to_email=user.email,
                context=context,
                actor=user,
            )

            if not db_sent:
                html_message = render_to_string(

                    "registration/password_reset_email.html", context

                )

                plain_message = (

                    f"Hello {user.get_full_name() or user.username},\n\n"

                    f"You requested a password reset for your Hodari SMS account.\n\n"

                    f"Click the link below to reset your password (valid for 1 hour):\n"

                    f"{reset_url}\n\n"

                    f"If you did not request this, please ignore this email.\n\n"

                    f"Hodari Christian School"

                )

                from communications.email_service import send_email_safe

                send_email_safe(

                    to_email=user.email,

                    subject=subject,

                    body=plain_message,

                    html_body=html_message,

                    actor=user,

                    action_type="PASSWORD_RESET",

                )

            from audit.models import log_event as _audit_log

            _audit_log(

                actor=user,

                action_type="PASSWORD_RESET_REQUEST",

                model_name="User",

                object_id=user.pk,

                description=f"Password reset requested for {user.username}",

                request=self.request,

            )

            logger.info("Password reset email sent to %s", email)

        except User.DoesNotExist:

            # FRD: message shown regardless - security best practice

            logger.info("Password reset requested for non-existent email: %s", email)

        except Exception as exc:

            logger.error("Password reset email failed: %s", exc)

            from audit.models import log_event

            log_event(

                actor=None,

                action_type="EMAIL_FAILED",

                model_name="User",

                object_id="",

                description=f"Password reset email failed for {email}. Error: {exc}",

            )



        return super().form_valid(form)





class PasswordResetDoneView(TemplateView):

    """

    FRD: screen displays 'If that email is registered, you will receive

    a reset link.'

    """

    template_name = "registration/password_reset_done.html"





class PasswordResetConfirmView(FormView):

    """

    FRD: "a user receives a valid reset link and clicks it within 1 hour

    WHEN they open the link THEN they see a password reset form requiring

    a new password and confirmation field."



    FRD: "a user clicks a reset link more than 1 hour after it was issued,

    or a link that has already been used WHEN the link is opened THEN an

    error is displayed: 'This reset link has expired. Request a new one.'

    with a link back to the forgot password screen."



    FRD: "a valid new password is submitted WHEN the change is saved THEN

    the password is updated, all active sessions for that user are

    invalidated, and the user is redirected to the login screen with:

    'Password updated. Please log in.'"

    """

    template_name = "registration/password_reset_confirm.html"

    form_class = SetNewPasswordForm

    success_url = reverse_lazy("users:password_reset_complete")



    def dispatch(self, request, *args, **kwargs):

        self.valid_token = False

        self.user_obj = None

        uidb64 = kwargs.get("uidb64", "")

        token = kwargs.get("token", "")



        try:

            uid = force_str(urlsafe_base64_decode(uidb64))

            self.user_obj = User.objects.get(pk=uid)

        except (TypeError, ValueError, OverflowError, User.DoesNotExist):

            self.user_obj = None



        if self.user_obj is not None and default_token_generator.check_token(

            self.user_obj, token

        ):

            self.valid_token = True

        else:

            self.valid_token = False



        return super().dispatch(request, *args, **kwargs)



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["validlink"] = self.valid_token

        ctx["uidb64"] = self.kwargs.get("uidb64", "")

        ctx["token"] = self.kwargs.get("token", "")

        return ctx



    def form_valid(self, form):

        if not self.valid_token:

            return redirect("users:password_reset_request")



        new_password = form.cleaned_data["new_password"]



        # Set the new password

        self.user_obj.set_password(new_password)

        self.user_obj.save(update_fields=["password"])



        # FRD: invalidate ALL active sessions for this user

        from django.contrib.sessions.models import Session

        sessions = Session.objects.all()

        sessions_deleted = 0

        for session in sessions:

            try:

                data = session.get_decoded()

                if data.get("_auth_user_id") == str(self.user_obj.pk):

                    session.delete()

                    sessions_deleted += 1

            except Exception:

                pass

        logger.info(

            "Password reset complete for %s. %d session(s) invalidated.",

            self.user_obj.username, sessions_deleted,

        )



        return super().form_valid(form)





class PasswordResetCompleteView(TemplateView):

    """

    FRD: "the user is redirected to the login screen with:

    'Password updated. Please log in.'"

    """

    template_name = "registration/password_reset_complete.html"





class ParentProfileEditView(RoleRequiredMixin, TemplateView):

    """Allow parents to edit their profile details. Sends notification to admin on sensitive changes."""

    template_name = "users/parent_profile_edit.html"

    login_url = "/accounts/login/"

    allowed_roles = [UserRole.PARENT]

    required_permission = "users.change_user"



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        ctx["profile_user"] = self.request.user

        from students.models import ParentGuardian

        ctx["guardian"] = ParentGuardian.objects.filter(user=self.request.user).first()

        return ctx



    def post(self, request, *args, **kwargs):

        user = request.user

        old_first = user.first_name

        old_last = user.last_name

        old_email = user.email



        # Update User fields

        user.first_name = request.POST.get("first_name", user.first_name)

        user.last_name = request.POST.get("last_name", user.last_name)

        user.email = request.POST.get("email", user.email)

        user.save(update_fields=["first_name", "last_name", "email"])



        # Update ParentGuardian fields

        from students.models import ParentGuardian

        guardian, _ = ParentGuardian.objects.get_or_create(user=user)

        old_phone = guardian.phone

        old_secondary = guardian.secondary_phone

        old_address = guardian.address

        new_phone = request.POST.get("phone_number", guardian.phone)

        guardian.phone = new_phone or guardian.phone

        guardian.secondary_phone = request.POST.get("secondary_phone", guardian.secondary_phone)

        guardian.address = request.POST.get("address", guardian.address)

        guardian.full_name = user.get_full_name() or guardian.full_name

        guardian.email = user.email or guardian.email

        guardian.save(update_fields=["phone", "secondary_phone", "address", "full_name", "email"])



        # Check if sensitive fields changed

        changes = []

        if user.first_name != old_first or user.last_name != old_last:

            changes.append(f"Name changed to {user.get_full_name()}")

        if guardian.phone != old_phone:

            changes.append(f"Phone changed to {guardian.phone}")

        if user.email != old_email:

            changes.append(f"Email changed to {user.email}")

        if guardian.secondary_phone != old_secondary:

            changes.append(f"Backup phone changed to {guardian.secondary_phone}")

        if guardian.address != old_address:

            changes.append("Address changed")



        if changes:

            from communications.models import Notification

            admin_users = User.objects.filter(

                role__in=[UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER],

                is_active=True

            )

            for admin in admin_users:

                Notification.objects.create(

                    recipient=admin,

                    title="Parent Profile Updated",

                    body=f"{old_first} {old_last} updated their profile: {'; '.join(changes)}",

                    category="info",

                    link="/accounts/list/",

                )



        messages.success(request, "Profile updated successfully.")

        return redirect("users:profile")

