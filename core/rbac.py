from __future__ import annotations

from dataclasses import dataclass

from users.models import UserRole


class ModuleKey:
    DASHBOARD = "dashboard"
    ADMISSIONS = "admissions"
    LESSON_PLANS = "lesson_plans"
    WELFARE = "welfare"
    TIMETABLE = "timetable"
    REPORTS = "reports"
    ATTENDANCE = "attendance"
    STAFF_ATTENDANCE = "staff_attendance"   # FRD Section 2.2
    USER_MANAGEMENT = "user_management"     # FRD Section 2.2 / OP3.3
    FINANCE = "finance"
    COMMUNICATION = "communication"
    CALENDAR = "calendar"                  # FRD Section 12 / FR-CAL-001
    AUDIT_LOG = "audit_log"
    STUDENTS = "students"                  # FRD Section 4.1
    ROOMS = "rooms"
    GRADING = "grading"                    # Student grading and results
    SUBJECTS = "subjects"
    TASKS = "tasks"
    CLASS_MANAGEMENT = "class_management"
    PTC = "ptc"
    SETTINGS = "settings"
    BEHAVIOUR = "behaviour"
    PRIMARY_WELFARE = "primary_welfare"


# FRD Section 2.2: permission matrix (Full/View/-).
# We treat "View" and "Full" as visible in nav; "-" as hidden.
# CALENDAR visibility follows FR-CAL-005 (all staff roles see the calendar link).
FRD_ROLE_MODULE_VISIBILITY: dict[str, set[str]] = {
    UserRole.SUPER_ADMIN: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.LESSON_PLANS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.WELFARE,
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,
        ModuleKey.FINANCE,
        ModuleKey.COMMUNICATION,
        ModuleKey.CALENDAR,
        ModuleKey.AUDIT_LOG,
        ModuleKey.ROOMS,
        ModuleKey.GRADING,
        ModuleKey.SUBJECTS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
        ModuleKey.PTC,
            ModuleKey.SETTINGS,
    },
    UserRole.HEAD_OF_SCHOOL: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.LESSON_PLANS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.WELFARE,
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,
        ModuleKey.FINANCE,
        ModuleKey.COMMUNICATION,
        ModuleKey.CALENDAR,
        ModuleKey.AUDIT_LOG,
        ModuleKey.ROOMS,
        ModuleKey.GRADING,
        ModuleKey.SUBJECTS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
        ModuleKey.PTC,
            ModuleKey.SETTINGS,
    },
    UserRole.PRIMARY_HOD: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.LESSON_PLANS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,
        ModuleKey.CALENDAR,
        ModuleKey.COMMUNICATION,   # FRD 2.2: Primary HOD = View
        ModuleKey.ROOMS,
        ModuleKey.SUBJECTS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
        ModuleKey.PTC,
    },
    UserRole.ECD_HOD: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,
        ModuleKey.CALENDAR,
        ModuleKey.COMMUNICATION,   # FRD 2.2: ECD HOD = View
        ModuleKey.ROOMS,
        ModuleKey.SUBJECTS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
    },
    UserRole.LOWER_SECONDARY_HOD: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.LESSON_PLANS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,
        ModuleKey.CALENDAR,
        ModuleKey.COMMUNICATION,
        ModuleKey.ROOMS,
        ModuleKey.SUBJECTS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
        ModuleKey.PTC,
    },
    UserRole.ADMIN_OFFICER: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.ADMISSIONS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.ATTENDANCE,
        ModuleKey.STAFF_ATTENDANCE,   # FRD: Admin = Full
        ModuleKey.COMMUNICATION,
        ModuleKey.CALENDAR,           # FRD FR-CAL-001: Admin creates/edits events
        ModuleKey.ROOMS,
        ModuleKey.TASKS,
        ModuleKey.CLASS_MANAGEMENT,
            ModuleKey.SETTINGS,
    },
    UserRole.FINANCE_OFFICER: {
        ModuleKey.DASHBOARD,
        ModuleKey.ADMISSIONS,
        ModuleKey.FINANCE,
        ModuleKey.COMMUNICATION,
        ModuleKey.CALENDAR,
        ModuleKey.AUDIT_LOG,
        ModuleKey.TASKS,
            ModuleKey.SETTINGS,
    },
    UserRole.TEACHER: {
        ModuleKey.DASHBOARD,
        ModuleKey.STUDENTS,
        ModuleKey.LESSON_PLANS,
        ModuleKey.BEHAVIOUR,
        ModuleKey.WELFARE,  # ECD-only enforcement happens at view/nav level
        ModuleKey.PRIMARY_WELFARE,
        ModuleKey.TIMETABLE,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.CALENDAR,
        ModuleKey.COMMUNICATION,
        ModuleKey.GRADING,
        ModuleKey.TASKS,
        ModuleKey.PTC,
    },
    UserRole.PARENT: {
        ModuleKey.DASHBOARD,
        ModuleKey.REPORTS,
        ModuleKey.ATTENDANCE,
        ModuleKey.FINANCE,
        ModuleKey.TASKS,
        ModuleKey.COMMUNICATION,
        ModuleKey.GRADING,
        ModuleKey.CALENDAR,
        ModuleKey.WELFARE,
    },
}


def can_see_module(role: str | None, module: str, user=None) -> bool:
    """Permission-driven module visibility.

    If a user object is provided, checks whether the user holds ANY of the
    permissions listed in MODULE_PERMISSIONS for the module.  This makes
    sidebar visibility fully dynamic — admin configures permissions via the
    Role Management UI and the nav updates automatically.

    Falls back to the legacy FRD_ROLE_MODULE_VISIBILITY mapping when no user
    is supplied (backwards compatibility).
    """
    if not role:
        return False
    # Custom roles: always visible — permission-based check at view level gates access
    if role.startswith("custom_"):
        return True
    # Permission-driven path (preferred)
    if user is not None:
        perms = MODULE_PERMISSIONS.get(module, [])
        return any(user.has_perm(p) for p in perms)
    # Legacy fallback
    return module in FRD_ROLE_MODULE_VISIBILITY.get(role, set())


# Mapping from module keys to the permission that controls backend access.
# Used by the navigation context processor to hide sidebar links when the
# user lacks the permission the backend view enforces via required_permission.
MODULE_REQUIRED_PERMISSIONS: dict[str, str] = {
    ModuleKey.STUDENTS: "students.view_student",
    ModuleKey.FINANCE: "finance.view_invoice",
    ModuleKey.ATTENDANCE: "attendance.view_attendanceentry",
    ModuleKey.ADMISSIONS: "admissions.view_applicant",
    ModuleKey.LESSON_PLANS: "academics.view_lessonplan",
    ModuleKey.GRADING: "academics.view_examscore",
    ModuleKey.REPORTS: "academics.view_reportcard",
    ModuleKey.STAFF_ATTENDANCE: "attendance.view_staffattendanceentry",
    ModuleKey.COMMUNICATION: "communications.view_broadcast",
    ModuleKey.WELFARE: "welfare.view_welfareobservation",
    ModuleKey.BEHAVIOUR: "discipline.view_disciplineincident",
    ModuleKey.PTC: "ptc.view_ptcsubjectcomment",
    ModuleKey.CALENDAR: "events.view_calendarevent",
    ModuleKey.TASKS: "tasks.view_task",
    ModuleKey.ROOMS: "academics.view_room",
    ModuleKey.SUBJECTS: "academics.view_subject",
    ModuleKey.CLASS_MANAGEMENT: "academics.view_gradeclass",
    ModuleKey.AUDIT_LOG: "audit.view_auditlog",
    ModuleKey.SETTINGS: "core.view_schoolsettings",
    ModuleKey.PRIMARY_WELFARE: "welfare.view_welfareobservation",
    ModuleKey.TIMETABLE: "timetable.view_timetableslot",
}


# Permission-driven sidebar visibility. A module is revealed if the user holds
# ANY of these permissions (additive). This powers the navigation context
# processor: a role granted a single finance.* permission sees the Finance
# module, while page-level access is still enforced per-view via
# required_permission.
MODULE_PERMISSIONS: dict[str, list[str]] = {
    ModuleKey.STUDENTS: [
        "students.view_student", "students.add_student",
        "students.change_student", "students.delete_student",
    ],
    ModuleKey.FINANCE: [
        "finance.view_invoice", "finance.add_invoice",
        "finance.change_invoice", "finance.delete_invoice",
        "finance.view_payment", "finance.add_payment",
        "finance.change_payment", "finance.delete_payment",
        "finance.view_receipt", "finance.add_receipt",
        "finance.change_receipt", "finance.delete_receipt",
        "finance.view_budget", "finance.add_budget",
        "finance.change_budget", "finance.delete_budget",
        "finance.view_expense", "finance.add_expense",
        "finance.change_expense", "finance.delete_expense",
    ],
    ModuleKey.ATTENDANCE: [
        "attendance.view_attendanceentry", "attendance.add_attendanceentry",
        "attendance.change_attendanceentry", "attendance.delete_attendanceentry",
    ],
    ModuleKey.ADMISSIONS: [
        "admissions.view_applicant", "admissions.add_applicant",
        "admissions.change_applicant", "admissions.delete_applicant",
    ],
    ModuleKey.LESSON_PLANS: ["academics.view_lessonplan"],
    ModuleKey.GRADING: ["academics.view_examscore"],
    ModuleKey.REPORTS: ["academics.view_reportcard"],
    ModuleKey.STAFF_ATTENDANCE: ["attendance.view_staffattendanceentry"],
    ModuleKey.USER_MANAGEMENT: [
        "users.view_user", "users.add_user",
        "users.change_user", "users.delete_user",
        "auth.view_group",
    ],
    ModuleKey.COMMUNICATION: [
        "communications.view_broadcast", "communications.add_broadcast",
    ],
    ModuleKey.WELFARE: ["welfare.view_welfareobservation"],
    ModuleKey.BEHAVIOUR: ["discipline.view_disciplineincident"],
    ModuleKey.PTC: ["ptc.view_ptcsubjectcomment"],
    ModuleKey.CALENDAR: ["events.view_calendarevent"],
    ModuleKey.TASKS: ["tasks.view_task"],
    ModuleKey.ROOMS: ["academics.view_room"],
    ModuleKey.SUBJECTS: ["academics.view_subject"],
    ModuleKey.CLASS_MANAGEMENT: ["academics.view_gradeclass"],
    ModuleKey.AUDIT_LOG: ["audit.view_auditlog"],
    ModuleKey.SETTINGS: ["core.view_schoolsettings"],
    ModuleKey.PRIMARY_WELFARE: ["welfare.view_welfareobservation"],
    ModuleKey.TIMETABLE: ["timetable.view_timetableslot"],
}


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str
    section: str
    icon: str
    enabled: bool = True
    badge: str | None = None

