from datetime import date as _date


def globals(request):
    """Inject user_initials, today_date, unread notification count, and recent notifications into every template."""
    user = getattr(request, "user", None)
    initials = "HX"
    unread_notifs = 0
    recent_notifications = []
    is_ecd_teacher = False
    if user and user.is_authenticated:
        full = user.get_full_name()
        if full:
            parts = full.split()
            initials = (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else parts[0][:2].upper()
        else:
            initials = user.username[:2].upper()
        try:
            from communications.models import Notification
            from users.models import UserRole
            recent_notifications = list(Notification.objects.filter(recipient=user).order_by("is_read", "-created_at")[:15])
            unread_notifs = sum(1 for n in recent_notifications if not n.is_read)
            
            # ECD teacher flag for template role-based visibility (cached per-request)
            if user.role == UserRole.TEACHER:
                from core.teacher_context import is_ecd_teacher as _check_ecd
                is_ecd_teacher = bool(getattr(request, '_cached_is_ecd', None) or _check_ecd(user))
                request._cached_is_ecd = is_ecd_teacher
        except Exception:
            pass
    from django.utils import timezone
    today = timezone.now().date()
    return {
        "user_initials": initials,
        "today_date": today.strftime("%d %B %Y"),
        "today": today,
        "unread_notifs": unread_notifs,
        "recent_notifications": recent_notifications,
        "is_ecd_teacher": is_ecd_teacher,
    }


def branding(request):
    defaults = {
        "school_name": "Hodari Christian School",
        "tagline": "The courage to stand out",
        "address": "House No. 14, Nova Road, Kawe Beach, P.O. Box 1114, Dar es Salaam, Tanzania",
        "email": "hello@hodari.ac.tz",
        "phone": "+255 767 016 940 / +255 743 791 828",
        "website": "www.hodari.ac.tz",
        "login_heading": "Welcome back to\n{school_name}",
        "login_description": "Manage admissions, academics, finance, and school operations from one secure dashboard.",
        "login_feature1": "Role-based access for all staff",
        "login_feature2": "Immutable audit log on all changes",
        "login_feature3": "Finance, HR, and academics in one place",
        "login_right_heading": "Sign in to your account",
        "login_right_subtitle": "Choose your login type and enter your credentials.",
    }
    try:
        from core.models import SchoolSettings
        settings_obj = SchoolSettings.get_settings()
        school_name = settings_obj.school_name or defaults["school_name"]
        data = {
            "school_name": school_name,
            "tagline": settings_obj.login_tagline if settings_obj.login_tagline is not None else defaults["tagline"],
            "address": defaults["address"],
            "email": defaults["email"],
            "phone": defaults["phone"],
            "website": defaults["website"],
            "login_hero_image": settings_obj.login_hero_image,
            "login_staff_hero_image": settings_obj.login_staff_hero_image,
            "login_parent_hero_image": settings_obj.login_parent_hero_image,
            "login_logo": settings_obj.login_logo,
            "login_heading": ((settings_obj.login_heading if settings_obj.login_heading is not None else defaults["login_heading"]).replace("{school_name}", school_name)),
            "login_description": settings_obj.login_description if settings_obj.login_description is not None else defaults["login_description"],
            "login_feature1": settings_obj.login_feature1 if settings_obj.login_feature1 is not None else defaults["login_feature1"],
            "login_feature2": settings_obj.login_feature2 if settings_obj.login_feature2 is not None else defaults["login_feature2"],
            "login_feature3": settings_obj.login_feature3 if settings_obj.login_feature3 is not None else defaults["login_feature3"],
            "login_right_heading": settings_obj.login_right_heading if settings_obj.login_right_heading is not None else defaults["login_right_heading"],
            "login_right_subtitle": settings_obj.login_right_subtitle if settings_obj.login_right_subtitle is not None else defaults["login_right_subtitle"],
        }
    except Exception:
        data = {
            **defaults,
            "login_hero_image": None,
            "login_logo": None,
            "login_heading": defaults["login_heading"].replace("{school_name}", defaults["school_name"]),
        }
    return {"brand": data}


def navigation(request):
    from core.rbac import ModuleKey, NavItem, MODULE_PERMISSIONS
    from core.teacher_context import is_ecd_teacher as _is_ecd_teacher
    from users.models import UserRole
    import dataclasses

    user = getattr(request, "user", None)
    role = getattr(user, "role", None)
    path = getattr(request, "path", "") or ""
    items: list[NavItem] = []

    # ECD teacher flag (cached per-request) — used only for welfare link routing.
    is_ecd_teacher = False
    if user and user.is_authenticated and role == UserRole.TEACHER:
        is_ecd_teacher = bool(getattr(request, '_cached_is_ecd', None) or _is_ecd_teacher(user))
        request._cached_is_ecd = is_ecd_teacher

    # DB-driven sidebar: a module is revealed if the user holds ANY of its
    # MODULE_PERMISSIONS (from their role group / user permissions). Removing a
    # permission in Role Management hides the module; assigning one surfaces it.
    # Super Admin holds a wildcard ("*") and sees every module.
    perms_set: set[str] = set()
    granted_modules: set[str] = set()
    if user and user.is_authenticated:
        try:
            perms_set = user.get_effective_permissions()
            if "*" in perms_set:
                granted_modules = set(MODULE_PERMISSIONS)
            else:
                granted_modules = {
                    mod for mod, perm_list in MODULE_PERMISSIONS.items()
                    if any(p in perms_set for p in perm_list)
                }
        except Exception:
            perms_set = set()
            granted_modules = set()

    def add(item: NavItem):
        # Parent items are handled separately
        if item.key.startswith("parent_"):
            if _is_parent:
                items.append(item)
            return

        # Dashboard is universal for authenticated users (no permission maps to it).
        if item.key == ModuleKey.DASHBOARD:
            if user and user.is_authenticated:
                items.append(item)
            return

        # Everything else is permission-driven: no permission → no link.
        if item.key not in granted_modules:
            return

        # Dynamic routing (permission-gated) — ECD vs Primary vs Lower Secondary assessment
        if item.key == ModuleKey.GRADING:
            if role == UserRole.TEACHER:
                if is_ecd_teacher:
                    item = dataclasses.replace(item, href="/academics/ecd-assessment/")
                else:
                    # Check if teacher has Lower Secondary classes
                    from academics.ecd_utils import grade_class_names_for_department
                    from academics.models import Department
                    from timetable.models import TimetableSlot
                    teacher_classes = set(TimetableSlot.objects.filter(teacher=user).values_list("class_name", flat=True))
                    ls_names = grade_class_names_for_department(Department.LOWER_SECONDARY)
                    has_ls = bool(teacher_classes & ls_names)
                    if has_ls:
                        item = dataclasses.replace(item, href="/academics/lower-secondary-assessment/")
                    else:
                        item = dataclasses.replace(item, href="/academics/primary-assessment/")
            elif role == UserRole.ECD_HOD:
                item = dataclasses.replace(item, href="/academics/ecd-assessment/")
            elif role == UserRole.LOWER_SECONDARY_HOD:
                item = dataclasses.replace(item, href="/academics/lower-secondary-assessment/")
            else:
                item = dataclasses.replace(item, href="/academics/primary-assessment/")

        # Dynamic routing — parent/teacher/admin report entry points
        if item.key == ModuleKey.REPORTS:
            if _is_parent:
                item = dataclasses.replace(item, href="/academics/reports/parent/")
            elif role == UserRole.TEACHER:
                item = dataclasses.replace(item, href="/academics/reports/list/")
            else:
                item = dataclasses.replace(item, href="/academics/reports/")

        items.append(item)

    # ── Parent portal: override URLs to parent-specific views ──
    _is_parent = (role == UserRole.PARENT)

    add(NavItem(key=ModuleKey.DASHBOARD,        label="Dashboard",        href="/parent/" if _is_parent else "/",                       section="MAIN",       icon="grid",     enabled=True))
    if _is_parent:
        add(NavItem(key="parent_children",      label="My Children",      href="/parent/children/",                                       section="MAIN",       icon="users",    enabled=True))
        add(NavItem(key="parent_grades",        label="Grades",           href="/parent/grades/",                                         section="ACADEMIC",   icon="report",   enabled=True))
        add(NavItem(key="parent_admission",     label="Admission Form",   href="/parent/admission-form/",                                 section="OPERATIONS",icon="doc",      enabled=True))
    # Task badge — use task_pending_count from tasks.context_processors (runs first)
    _task_badge = str(request.task_pending_count) if getattr(request, 'task_pending_count', 0) else None
    add(NavItem(key=ModuleKey.TASKS,            label="Tasks",            href="/tasks/",                 section="MAIN",       icon="check",    enabled=True, badge=_task_badge))
    # FR-ADM-007: Finance Officers are excluded from the admissions module — they only see fee status on individual applicants.
    if role != UserRole.FINANCE_OFFICER and not _is_parent:
        add(NavItem(key=ModuleKey.ADMISSIONS,       label="Admissions",       href="/admissions/",            section="MAIN",       icon="user",     enabled=True))
    if not _is_parent:
        add(NavItem(key=ModuleKey.STUDENTS,         label="Students",         href="/students/",              section="MAIN",       icon="users",    enabled=True))
    if not is_ecd_teacher:
        add(NavItem(key=ModuleKey.LESSON_PLANS,     label="Lesson plans",     href="/academics/lesson-plans/",section="ACADEMIC",   icon="doc",      enabled=True))
    add(NavItem(key=ModuleKey.BEHAVIOUR,        label="Discipline",        href="/behaviour/",             section="ACADEMIC",   icon="badge",    enabled=True))

    # Welfare — visibility is permission-driven (welfare.view_welfareobservation);
    # only the ECD/Primary link routing is contextual (teacher track / department head).
    if ModuleKey.WELFARE in granted_modules:
        if role in {UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER} or is_ecd_teacher:
            add(NavItem(key=ModuleKey.WELFARE,          label="ECD Welfare",       href="/welfare/ecd/incidents/", section="ACADEMIC",   icon="pin",      enabled=True))
        if role in {UserRole.PRIMARY_HOD, UserRole.LOWER_SECONDARY_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER} or (role == UserRole.TEACHER and not is_ecd_teacher):
            add(NavItem(key=ModuleKey.PRIMARY_WELFARE,  label="Primary Welfare" if role != UserRole.TEACHER else "Welfare",   href="/welfare/primary/incidents/", section="ACADEMIC",   icon="pin",      enabled=True))
        if _is_parent:
            add(NavItem(key=ModuleKey.WELFARE,          label="Welfare",           href="/parent/welfare/",         section="ACADEMIC",   icon="pin",      enabled=True))
    add(NavItem(key=ModuleKey.TIMETABLE,        label="Timetable",        href="/timetable/",             section="ACADEMIC",   icon="calendar", enabled=True))
    add(NavItem(key=ModuleKey.SUBJECTS,         label="Subjects",         href="/academics/subjects/",    section="ACADEMIC",   icon="doc",      enabled=True))
    add(NavItem(key=ModuleKey.REPORTS,          label="Reports",          href="/academics/reports/",     section="ACADEMIC",   icon="report",   enabled=True))
    add(NavItem(key=ModuleKey.GRADING,         label="Progress",       href="/academics/ecd-assessment/",               section="ACADEMIC",   icon="badge",    enabled=True))
    add(NavItem(key=ModuleKey.PTC,             label="PTC Reports",    href="/ptc/",                    section="ACADEMIC",   icon="report",   enabled=True))
    add(NavItem(key=ModuleKey.ATTENDANCE,       label="Attendance",       href="/parent/attendance/" if _is_parent else "/attendance/",            section="OPERATIONS", icon="check",    enabled=True))
    # User Management — permission-driven: any role granted user-management
    # permissions (e.g. users.view_user ticked in Role Management) sees the
    # full user list. TEACHER and PARENT hold users.view_user only for their
    # own account, so they never see this module.
    _show_user_mgmt = (
        ModuleKey.USER_MANAGEMENT in granted_modules
        and role not in {UserRole.TEACHER, UserRole.PARENT}
    )
    if _show_user_mgmt:
        add(NavItem(key=ModuleKey.USER_MANAGEMENT, label="User Management", href="/accounts/list/", section="OPERATIONS", icon="users", enabled=True))
    # Staff Attendance — shown for staff roles holding staff-attendance
    # permission (HODs etc.). Roles already seeing User Management stay on the
    # user-list page, preserving the FRD HOS/Admin layout.
    if ModuleKey.STAFF_ATTENDANCE in granted_modules and not _show_user_mgmt and role not in {UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD}:
        add(NavItem(key=ModuleKey.STAFF_ATTENDANCE, label="Staff Attendance", href="/attendance/staff/", section="OPERATIONS", icon="users", enabled=True))
    add(NavItem(key=ModuleKey.FINANCE,          label="Finance",          href="/parent/invoices/" if _is_parent else "/finance/",               section="OPERATIONS", icon="card",     enabled=True))
    add(NavItem(key=ModuleKey.COMMUNICATION,    label="Communication",    href="/parent/communications/" if _is_parent else "/communications/",        section="OPERATIONS", icon="chat",     enabled=True))
    add(NavItem(key=ModuleKey.CALENDAR,         label="Calendar",        href="/parent/calendar/" if _is_parent else "/events/",                section="OPERATIONS", icon="calendar", enabled=True))
    add(NavItem(key=ModuleKey.SETTINGS, label="Settings", href="/settings/", section="SYSTEM", icon="settings", enabled=True))
    add(NavItem(key=ModuleKey.AUDIT_LOG,        label="Audit log",        href="/audit/logs/",            section="SYSTEM",     icon="clock",    enabled=True))

    def with_active(item: NavItem) -> dict:
        href = item.href
        active = False
        if href == "/":
            active = path == "/"
        elif href and href != "#":
            active = path.startswith(href)
        return {
            "key": item.key,
            "label": item.label,
            "href": href,
            "section": item.section,
            "icon": item.icon,
            "enabled": item.enabled,
            "badge": item.badge,
            "active": active,
        }

    grouped: dict[str, list[dict]] = {"MAIN": [], "ACADEMIC": [], "OPERATIONS": [], "SYSTEM": []}
    for item in items:
        grouped.setdefault(item.section, []).append(with_active(item))

    return {"nav": grouped}
