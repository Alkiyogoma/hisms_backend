"""
RoleConfig model and default permission mappings per FRD.

Super Admin manages role configurations through the Role Management page.
Each system role has a default set of permissions derived from the FRD
Master Tracker, Acceptance Criteria, and CRUD Matrix documents.
"""

from django.db import models
from django.conf import settings

from users.models import UserRole


class RoleConfig(models.Model):
    """
    Extends a system role with configurable metadata and default permissions.
    One row per system role (super_admin, head_of_school, etc.).
    """
    role = models.CharField(
        max_length=40,
        choices=UserRole.choices,
        unique=True,
        help_text="System role identifier from UserRole TextChoices.",
    )
    label = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    departments = models.JSONField(
        default=list,
        blank=True,
        help_text='List of department keys, e.g. ["ECD","PRIMARY"].',
    )
    icon_color = models.CharField(
        max_length=7,
        default="#6B7280",
        help_text="Hex colour for the role badge.",
    )
    is_system = models.BooleanField(
        default=True,
        help_text="System roles cannot be deleted.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive roles are hidden from user assignment.",
    )
    permissions = models.ManyToManyField(
        "auth.Permission",
        blank=True,
        related_name="role_configs",
        help_text="Default Django permissions for this role.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role"]

    def __str__(self) -> str:
        return self.label


# ──────────────────────────────────────────────────────────────
# Default metadata per system role (matches ROLE_CONFIG in views)
# ──────────────────────────────────────────────────────────────

DEFAULT_ROLE_CONFIGS = {
    UserRole.SUPER_ADMIN: {
        "label": "Super Admin",
        "description": "Full system access. Can manage all modules, users, and settings.",
        "departments": ["ECD", "PRIMARY", "LOWER_SECONDARY", "ADMINISTRATION"],
        "icon_color": "#7C3AED",
    },
    UserRole.HEAD_OF_SCHOOL: {
        "label": "Head of School (HOS)",
        "description": "School-wide oversight. Can view all departments and sign off reports.",
        "departments": ["ECD", "PRIMARY", "LOWER_SECONDARY", "ADMINISTRATION"],
        "icon_color": "#2563EB",
    },
    UserRole.PRIMARY_HOD: {
        "label": "Primary HOD",
        "description": "Head of Department for Primary. Manages Primary teachers and classes.",
        "departments": ["PRIMARY"],
        "icon_color": "#059669",
    },
    UserRole.ECD_HOD: {
        "label": "ECD HOD",
        "description": "Head of Department for ECD. Manages ECD teachers and classes.",
        "departments": ["ECD"],
        "icon_color": "#D97706",
    },
    UserRole.LOWER_SECONDARY_HOD: {
        "label": "Lower Secondary HOD",
        "description": "Head of Department for Lower Secondary. Manages Lower Secondary teachers.",
        "departments": ["LOWER_SECONDARY"],
        "icon_color": "#DC2626",
    },
    UserRole.ADMIN_OFFICER: {
        "label": "Admin Officer",
        "description": "Operational admin. Manages admissions, enrolment, staff records, and communications.",
        "departments": ["ADMINISTRATION"],
        "icon_color": "#0891B2",
    },
    UserRole.FINANCE_OFFICER: {
        "label": "Finance Officer",
        "description": "Manages invoicing, payments, fees, and financial reports.",
        "departments": ["ADMINISTRATION"],
        "icon_color": "#BE185D",
    },
    UserRole.TEACHER: {
        "label": "Teacher",
        "description": "Class/subject teacher. Manages attendance, grades, and lesson plans for assigned classes.",
        "departments": [],
        "icon_color": "#4F46E5",
    },
    UserRole.PARENT: {
        "label": "Parent / Guardian",
        "description": "Parent portal access. Views their children's academic and welfare information.",
        "departments": [],
        "icon_color": "#6B7280",
    },
}


# ──────────────────────────────────────────────────────────────
# Default permissions per role (FRD-aligned)
# ──────────────────────────────────────────────────────────────
# Maps role → list of Django permission codenames.
# Managed via seed_roles management command.
# Based on FRD Master Tracker, Acceptance Criteria, and CRUD Matrix.

ROLE_DEFAULT_PERMISSIONS = {
    # ── SUPER ADMIN ──────────────────────────────────────────
    # Full system access: ALL 448 permissions on all modules.
    UserRole.SUPER_ADMIN: [
        "add_abcgeneralassignment", "change_abcgeneralassignment", "delete_abcgeneralassignment", "view_abcgeneralassignment",
        "add_abcinternalexam", "change_abcinternalexam", "delete_abcinternalexam", "view_abcinternalexam",
        "add_abcpaceprogress", "change_abcpaceprogress", "delete_abcpaceprogress", "view_abcpaceprogress",
        "add_abcreadingprogramme", "change_abcreadingprogramme", "delete_abcreadingprogramme", "view_abcreadingprogramme",
        "add_abcscripture", "change_abcscripture", "delete_abcscripture", "view_abcscripture",
        "add_academicyear", "change_academicyear", "delete_academicyear", "view_academicyear",
        "add_cambridgecheckpointscore", "change_cambridgecheckpointscore", "delete_cambridgecheckpointscore", "view_cambridgecheckpointscore",
        "add_ecddomainconfig", "change_ecddomainconfig", "delete_ecddomainconfig", "view_ecddomainconfig",
        "add_ecdevaluation", "change_ecdevaluation", "delete_ecdevaluation", "view_ecdevaluation",
        "add_ecdtemplateconfiguration", "change_ecdtemplateconfiguration", "delete_ecdtemplateconfiguration", "view_ecdtemplateconfiguration",
        "add_examscore", "change_examscore", "delete_examscore", "view_examscore",
        "add_examtypeconfiguration", "change_examtypeconfiguration", "delete_examtypeconfiguration", "view_examtypeconfiguration",
        "add_classcapacity", "change_classcapacity", "delete_classcapacity", "view_classcapacity",
        "add_gradeclass", "change_gradeclass", "delete_gradeclass", "view_gradeclass",
        "add_lessonplan", "change_lessonplan", "delete_lessonplan", "view_lessonplan",
        "add_lessonplanattachment", "change_lessonplanattachment", "delete_lessonplanattachment", "view_lessonplanattachment",
        "can_review_lessonplan", "view_all_lessonplans",
        "add_progressioncase", "change_progressioncase", "delete_progressioncase", "view_progressioncase",
        "can_review_progression", "can_decide_progression",
        "add_progressionconfig", "change_progressionconfig", "delete_progressionconfig", "view_progressionconfig",
        "add_promotionrun", "change_promotionrun", "delete_promotionrun", "view_promotionrun",
        "add_reportcard", "change_reportcard", "delete_reportcard", "view_reportcard",
        "add_room", "change_room", "delete_room", "view_room",
        "add_subject", "change_subject", "delete_subject", "view_subject",
        "add_term", "change_term", "delete_term", "view_term",
        "add_timetableentry", "change_timetableentry", "delete_timetableentry", "view_timetableentry",
        "add_logentry", "change_logentry", "delete_logentry", "view_logentry",
        "add_admissiongrade", "change_admissiongrade", "delete_admissiongrade", "view_admissiongrade",
        "add_applicant", "change_applicant", "delete_applicant", "view_applicant",
        "add_applicantdocumentreceipt", "change_applicantdocumentreceipt", "delete_applicantdocumentreceipt", "view_applicantdocumentreceipt",
        "add_applicantinternalnote", "change_applicantinternalnote", "delete_applicantinternalnote", "view_applicantinternalnote",
        "add_applicanttimelineentry", "change_applicanttimelineentry", "delete_applicanttimelineentry", "view_applicanttimelineentry",
        "add_assessmentschedule", "change_assessmentschedule", "delete_assessmentschedule", "view_assessmentschedule",
        "add_meetingschedule", "change_meetingschedule", "delete_meetingschedule", "view_meetingschedule",
        "add_enrolmentchecklist", "change_enrolmentchecklist", "delete_enrolmentchecklist", "view_enrolmentchecklist",
        "add_attendanceentry", "change_attendanceentry", "delete_attendanceentry", "view_attendanceentry",
        "add_message", "change_message", "delete_message", "view_message",
        "add_notificationlog", "change_notificationlog", "delete_notificationlog", "view_notificationlog",
        "add_otpcode", "change_otpcode", "delete_otpcode", "view_otpcode",
        "add_staffattendanceentry", "change_staffattendanceentry", "delete_staffattendanceentry", "view_staffattendanceentry",
        "add_auditlog", "change_auditlog", "delete_auditlog", "view_auditlog",
        "add_dsarrequest", "change_dsarrequest", "delete_dsarrequest", "view_dsarrequest",
        "add_group", "change_group", "delete_group", "view_group",
        "add_permission", "change_permission", "delete_permission", "view_permission",
        "add_broadcast", "change_broadcast", "delete_broadcast", "view_broadcast",
        "add_emailsendlog", "change_emailsendlog", "delete_emailsendlog", "view_emailsendlog",
        "add_notification", "change_notification", "delete_notification", "view_notification",
        "add_phoneotp", "change_phoneotp", "delete_phoneotp", "view_phoneotp",
        "add_weeklyfocus", "change_weeklyfocus", "delete_weeklyfocus", "view_weeklyfocus",
        "add_contenttype", "change_contenttype", "delete_contenttype", "view_contenttype",
        "add_archiveretentionpolicy", "change_archiveretentionpolicy", "delete_archiveretentionpolicy", "view_archiveretentionpolicy",
        "add_mediasettings", "change_mediasettings", "delete_mediasettings", "view_mediasettings",
        "add_schoolsettings", "change_schoolsettings", "delete_schoolsettings", "view_schoolsettings",
        "add_emailtemplate", "change_emailtemplate", "delete_emailtemplate", "view_emailtemplate",
        "add_lessonplandeadline", "change_lessonplandeadline", "delete_lessonplandeadline", "view_lessonplandeadline",
        "add_disciplineincident", "change_disciplineincident", "delete_disciplineincident", "view_disciplineincident",
        "add_clockedschedule", "change_clockedschedule", "delete_clockedschedule", "view_clockedschedule",
        "add_crontabschedule", "change_crontabschedule", "delete_crontabschedule", "view_crontabschedule",
        "add_intervalschedule", "change_intervalschedule", "delete_intervalschedule", "view_intervalschedule",
        "add_periodictask", "change_periodictask", "delete_periodictask", "view_periodictask",
        "add_periodictasks", "change_periodictasks", "delete_periodictasks", "view_periodictasks",
        "add_solarschedule", "change_solarschedule", "delete_solarschedule", "view_solarschedule",
        "add_calendarevent", "change_calendarevent", "delete_calendarevent", "view_calendarevent",
        "add_eventacknowledgement", "change_eventacknowledgement", "delete_eventacknowledgement", "view_eventacknowledgement",
        "add_budget", "change_budget", "delete_budget", "view_budget",
        "add_concession", "change_concession", "delete_concession", "view_concession",
        "add_expense", "change_expense", "delete_expense", "view_expense",
        "add_feestructure", "change_feestructure", "delete_feestructure", "view_feestructure",
        "add_feestructureitem", "change_feestructureitem", "delete_feestructureitem", "view_feestructureitem",
        "add_financeconfig", "change_financeconfig", "delete_financeconfig", "view_financeconfig",
        "add_financeperiod", "change_financeperiod", "delete_financeperiod", "view_financeperiod",
        "add_invoice", "change_invoice", "delete_invoice", "view_invoice",
        "add_invoicelineitem", "change_invoicelineitem", "delete_invoicelineitem", "view_invoicelineitem",
        "add_openingbalance", "change_openingbalance", "delete_openingbalance", "view_openingbalance",
        "add_payment", "change_payment", "delete_payment", "view_payment",
        "add_recurringexpense", "change_recurringexpense", "delete_recurringexpense", "view_recurringexpense",
        "add_reminderconfiguration", "change_reminderconfiguration", "delete_reminderconfiguration", "view_reminderconfiguration",
        "add_unmatchedpayment", "change_unmatchedpayment", "delete_unmatchedpayment", "view_unmatchedpayment",
        "add_inductionchecklistcompletion", "change_inductionchecklistcompletion", "delete_inductionchecklistcompletion", "view_inductionchecklistcompletion",
        "add_leaveallocation", "change_leaveallocation", "delete_leaveallocation", "view_leaveallocation",
        "add_leaverequest", "change_leaverequest", "delete_leaverequest", "view_leaverequest",
        "add_offboardingrecord", "change_offboardingrecord", "delete_offboardingrecord", "view_offboardingrecord",
        "add_onboardingchecklistitem", "change_onboardingchecklistitem", "delete_onboardingchecklistitem", "view_onboardingchecklistitem",
        "add_payetaxband", "change_payetaxband", "delete_payetaxband", "view_payetaxband",
        "add_payrollconfig", "change_payrollconfig", "delete_payrollconfig", "view_payrollconfig",
        "add_payrollentry", "change_payrollentry", "delete_payrollentry", "view_payrollentry",
        "add_payrollrun", "change_payrollrun", "delete_payrollrun", "view_payrollrun",
        "add_staffdocument", "change_staffdocument", "delete_staffdocument", "view_staffdocument",
        "add_staffonboardingprogress", "change_staffonboardingprogress", "delete_staffonboardingprogress", "view_staffonboardingprogress",
        "add_staffprofile", "change_staffprofile", "delete_staffprofile", "view_staffprofile",
        "add_statutoryfiling", "change_statutoryfiling", "delete_statutoryfiling", "view_statutoryfiling",
        "add_rolloverconflict", "change_rolloverconflict", "delete_rolloverconflict", "view_rolloverconflict",
        "add_teacherclassassignment", "change_teacherclassassignment", "delete_teacherclassassignment", "view_teacherclassassignment",
        "add_enrichmentgrade", "change_enrichmentgrade", "delete_enrichmentgrade", "view_enrichmentgrade",
        "add_enrichmentsubject", "change_enrichmentsubject", "delete_enrichmentsubject", "view_enrichmentsubject",
        "manage_enrichment_config",
        "add_learnerattributeratingentry", "change_learnerattributeratingentry", "delete_learnerattributeratingentry", "view_learnerattributeratingentry",
        "add_ptcdatechangelog", "change_ptcdatechangelog", "delete_ptcdatechangelog", "view_ptcdatechangelog",
        "add_ptcgenerationlog", "change_ptcgenerationlog", "delete_ptcgenerationlog", "view_ptcgenerationlog",
        "add_ptcprogresstrendoverride", "change_ptcprogresstrendoverride", "delete_ptcprogresstrendoverride", "view_ptcprogresstrendoverride",
        "add_ptcsubjectcomment", "change_ptcsubjectcomment", "delete_ptcsubjectcomment", "view_ptcsubjectcomment",
        "add_ptcwindow", "change_ptcwindow", "delete_ptcwindow", "view_ptcwindow",
        "add_session", "change_session", "delete_session", "view_session",
        "add_enrollmenthistory", "change_enrollmenthistory", "delete_enrollmenthistory", "view_enrollmenthistory",
        "add_laravelparent", "change_laravelparent", "delete_laravelparent", "view_laravelparent",
        "add_parentguardian", "change_parentguardian", "delete_parentguardian", "view_parentguardian",
        "add_pdpaconsentlog", "change_pdpaconsentlog", "delete_pdpaconsentlog", "view_pdpaconsentlog",
        "add_student", "change_student", "delete_student", "view_student",
        "add_studentguardian", "change_studentguardian", "delete_studentguardian", "view_studentguardian",
        "add_studentlaravelparent", "change_studentlaravelparent", "delete_studentlaravelparent", "view_studentlaravelparent",
        "add_studentsibling", "change_studentsibling", "delete_studentsibling", "view_studentsibling",
        "add_task", "change_task", "delete_task", "view_task",
        "view_department_tasks",
        "add_taskcomment", "change_taskcomment", "delete_taskcomment", "view_taskcomment",
        "add_taskhistory", "change_taskhistory", "delete_taskhistory", "view_taskhistory",
        "add_tasknotificationlog", "change_tasknotificationlog", "delete_tasknotificationlog", "view_tasknotificationlog",
        "add_tasktemplate", "change_tasktemplate", "delete_tasktemplate", "view_tasktemplate",
        "add_ecdbreakconfig", "change_ecdbreakconfig", "delete_ecdbreakconfig", "view_ecdbreakconfig",
        "add_timetableslot", "change_timetableslot", "delete_timetableslot", "view_timetableslot",
        "add_roleconfig", "change_roleconfig", "delete_roleconfig", "view_roleconfig",
        "add_user", "change_user", "delete_user", "view_user",
        "add_welfareacknowledgment", "change_welfareacknowledgment", "delete_welfareacknowledgment", "view_welfareacknowledgment",
        "add_welfareobservation", "change_welfareobservation", "delete_welfareobservation", "view_welfareobservation",
        # Custom admission permissions
        "transition_applicant_status", "confirm_assessment_fee", "reverse_assessment_fee",
        "submit_hos_review", "edit_admission_note", "delete_admission_note",
        "schedule_assessment", "submit_assessment_result", "send_assessment_logistics",
        "toggle_admission_document", "complete_enrolment", "upload_applicant_photo",
        "view_assessment_calendar",
        # Custom review permissions
        "can_review_incident", "can_review_lessonplan", "can_review_observation",
    ],

    # ── HEAD OF SCHOOL ───────────────────────────────────────
    # School-wide read + sign-off + status updates. No direct data creation.
    UserRole.HEAD_OF_SCHOOL: [
        # Review approvals — HOD sign-off
        "can_review_incident", "can_review_lessonplan", "can_review_observation",
        # Academics — read all, sign off reports
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "view_lessonplan", "view_lessonplanattachment",
        "view_examscore", "view_examtypeconfiguration",
        "change_reportcard", "view_reportcard",
        "view_cambridgecheckpointscore",
        "view_ecdevaluation", "view_ecdtemplateconfiguration",
        "add_reportcard",
        "view_progressioncase", "view_progressionconfig",
        "can_review_progression", "can_decide_progression",
        "view_room", "view_timetableentry",
        # Students — full management
        "view_student", "add_student", "change_student",
        "view_studentguardian", "view_studentsibling",
        "view_parentguardian", "add_parentguardian", "change_parentguardian",
        "view_laravelparent", "view_studentlaravelparent",
        "view_enrollmenthistory", "view_pdpaconsentlog",
        # Attendance — read school-wide
        "view_attendanceentry", "view_staffattendanceentry",
        "view_message", "view_notificationlog",
        # HR — read only
        "view_staffprofile", "view_teacherclassassignment",
        "view_staffdocument", "view_staffonboardingprogress",
        "view_onboardingchecklistitem", "view_inductionchecklistcompletion",
        "view_offboardingrecord", "view_leaverequest", "view_leaveallocation",
        "view_statutoryfiling",
        # Finance — read summaries
        "view_feestructure", "view_feestructureitem",
        "view_invoice", "view_invoicelineitem",
        "view_payment", "view_openingbalance",
        "view_financeconfig", "view_financeperiod",
        "view_budget", "view_expense",
        # Admissions — read + add internal notes + transition status
        "view_applicant", "view_applicantdocumentreceipt",
        "add_applicantinternalnote", "change_applicantinternalnote", "view_applicantinternalnote",
        "view_applicanttimelineentry",
        "view_assessmentschedule", "view_admissiongrade",
        "view_meetingschedule",
        "add_applicant", "view_enrolmentchecklist",
        "transition_applicant_status",
        "edit_admission_note",
        "view_assessment_calendar",
        "change_applicant",
        "schedule_assessment",
        "confirm_assessment_fee",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "upload_applicant_photo",
        "submit_assessment_result",
        "submit_hos_review",
        "reverse_assessment_fee",
        # Welfare — read + HOD notes + status
        "view_welfareobservation", "add_welfareobservation",
        "change_welfareobservation", "view_welfareacknowledgment",
        # Discipline — read + status
        "view_disciplineincident", "change_disciplineincident",
        # Events — read
        "view_calendarevent", "view_eventacknowledgement",
        # Tasks — read
        "view_task", "view_taskcomment", "view_taskhistory",
        "view_department_tasks",
        # Communications — read
        "view_broadcast", "view_notification",
        "view_emailsendlog", "add_weeklyfocus", "change_weeklyfocus", "view_weeklyfocus",
        # PTC — read
        "view_ptcwindow", "view_ptcsubjectcomment",
        "view_learnerattributeratingentry", "view_enrichmentgrade",
        "view_enrichmentsubject", "view_ptcgenerationlog",
        # Timetable — read
        "view_timetableslot", "view_ecdbreakconfig",
        # Core — read settings
        "view_schoolsettings", "change_schoolsettings", "view_mediasettings",
        "view_emailtemplate", "view_lessonplandeadline",
        # Audit — read
        "view_auditlog", "view_dsarrequest",
        # Users — read
        "view_user", "view_group",
    ],

    # ── PRIMARY HOD ──────────────────────────────────────────
    # Department-scoped: Primary classes/teachers. Approve grades, lesson plans.
    UserRole.PRIMARY_HOD: [
        # Review approvals — HOD sign-off
        "can_review_incident", "can_review_lessonplan", "can_review_observation",
        # Academics — read own dept, approve lesson plans + grades
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "add_lessonplan", "change_lessonplan", "view_lessonplan", "view_lessonplanattachment",
        "view_examscore", "change_examscore", "view_examtypeconfiguration",
        "change_reportcard", "view_reportcard",
        "view_progressioncase", "can_review_progression",
        "view_room", "view_timetableentry",
        # Students — read Primary only
        "view_student", "view_studentguardian", "view_studentsibling",
        "view_parentguardian", "view_enrollmenthistory",
        # Admissions — read + HOD review + transition status
        "view_applicant",
        "submit_hos_review",
        "edit_admission_note",
        "transition_applicant_status",
        "view_applicantinternalnote",
        "add_applicantinternalnote",
        "view_assessment_calendar",
        "change_applicant",
        "schedule_assessment",
        "submit_assessment_result",
        "confirm_assessment_fee",
        "reverse_assessment_fee",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "upload_applicant_photo",
        # Attendance — read own dept
        "view_attendanceentry", "view_staffattendanceentry",
        # HR — read own dept
        "view_staffprofile", "view_teacherclassassignment",
        # Welfare — HOD welfare notes + status
        "add_welfareobservation", "change_welfareobservation", "view_welfareobservation",
        # Discipline — create + read + update (own dept)
        "add_disciplineincident", "change_disciplineincident", "view_disciplineincident",
        # Events — read
        "view_calendarevent",
        # Tasks — read
        "view_task",
        "view_department_tasks",
        # Communications — read + weekly focus submit/edit
        "view_broadcast", "view_weeklyfocus",
        "add_weeklyfocus", "change_weeklyfocus",
        # PTC — read dept completion
        "view_ptcsubjectcomment", "view_learnerattributeratingentry",
        "view_enrichmentgrade",
        "view_ptcwindow", "view_ptcgenerationlog",
        # Timetable — read + create/edit slots
        "view_timetableslot", "add_timetableslot", "change_timetableslot",
        # Admissions — meeting schedule
        "view_meetingschedule",
        # Core — deadline config
        "view_lessonplandeadline",
        # Audit — read
        "view_auditlog",
    ],

    # ── ECD HOD ──────────────────────────────────────────────
    # ECD-specific: ECD evaluations, welfare notes, behaviour.
    UserRole.ECD_HOD: [
        # Review approvals — HOD sign-off
        "can_review_incident", "can_review_lessonplan", "can_review_observation",
        # Academics — ECD specific
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "add_ecdevaluation", "change_ecdevaluation", "view_ecdevaluation",
        "view_ecdtemplateconfiguration",
        "view_progressioncase", "can_review_progression",
        "view_examscore", "change_examscore", "view_examtypeconfiguration",
        "add_abcgeneralassignment", "change_abcgeneralassignment", "view_abcgeneralassignment",
        "add_abcinternalexam", "change_abcinternalexam", "view_abcinternalexam",
        "add_abcpaceprogress", "change_abcpaceprogress", "view_abcpaceprogress",
        "add_abcreadingprogramme", "change_abcreadingprogramme", "view_abcreadingprogramme",
        "add_abcscripture", "change_abcscripture", "view_abcscripture",
        "view_room", "view_timetableentry",
        # Reports — read + ECD entry (FRD module visibility)
        "change_reportcard", "view_reportcard",
        # Students — read ECD only
        "view_student", "view_studentguardian", "view_studentsibling",
        "view_parentguardian", "view_enrollmenthistory",
        # Admissions — read + HOD review + transition status
        "view_applicant",
        "submit_hos_review",
        "edit_admission_note",
        "transition_applicant_status",
        "view_applicantinternalnote",
        "add_applicantinternalnote",
        "view_assessment_calendar",
        "change_applicant",
        "schedule_assessment",
        "submit_assessment_result",
        "confirm_assessment_fee",
        "reverse_assessment_fee",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "upload_applicant_photo",
        # Attendance — read own dept
        "view_attendanceentry", "view_staffattendanceentry",
        # HR — read own dept
        "view_staffprofile", "view_teacherclassassignment",
        # Welfare — HOD welfare notes + status
        "add_welfareobservation", "change_welfareobservation", "view_welfareobservation",
        # Discipline — create + read + update (own dept)
        "add_disciplineincident", "change_disciplineincident", "view_disciplineincident",
        # Events — read
        "view_calendarevent",
        # Tasks — read
        "view_task",
        "view_department_tasks",
        # Communications — read + weekly focus submit/edit
        "view_broadcast", "view_weeklyfocus",
        "add_weeklyfocus", "change_weeklyfocus",
        # PTC — read dept completion
        "view_learnerattributeratingentry", "view_enrichmentgrade",
        # Timetable — read + create/edit slots + break config
        "view_timetableslot", "add_timetableslot", "change_timetableslot",
        "view_ecdbreakconfig", "change_ecdbreakconfig",
        # Admissions — meeting schedule
        "view_meetingschedule",
        # Core — deadline config
        "view_lessonplandeadline",
        # Audit — read
        "view_auditlog",
    ],

    # ── LOWER SECONDARY HOD ─────────────────────────────────
    # Department-scoped: Lower Secondary classes/teachers.
    UserRole.LOWER_SECONDARY_HOD: [
        # Review approvals — HOD sign-off
        "can_review_lessonplan", "can_review_observation",
        "can_review_incident",
        # Academics — read own dept, approve lesson plans + grades
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "add_lessonplan", "change_lessonplan", "view_lessonplan", "view_lessonplanattachment",
        "view_examscore", "change_examscore", "view_examtypeconfiguration",
        "change_reportcard", "view_reportcard",
        "view_cambridgecheckpointscore",
        "view_progressioncase", "can_review_progression",
        "view_room", "view_timetableentry",
        # Students — read Lower Secondary only
        "view_student", "view_studentguardian", "view_studentsibling",
        "view_parentguardian", "view_enrollmenthistory",
        # Admissions — full HOD review pipeline
        "view_applicant",
        "submit_hos_review",
        "edit_admission_note",
        "transition_applicant_status",
        "view_applicantinternalnote",
        "add_applicantinternalnote",
        "view_assessment_calendar",
        "change_applicant",
        "schedule_assessment",
        "submit_assessment_result",
        "confirm_assessment_fee",
        "reverse_assessment_fee",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "upload_applicant_photo",
        # Attendance — read own dept
        "view_attendanceentry", "view_staffattendanceentry",
        # HR — read own dept
        "view_staffprofile", "view_teacherclassassignment",
        # Welfare — read
        "view_welfareobservation",
        # Discipline — create + read + update + review (own dept)
        "add_disciplineincident", "change_disciplineincident", "view_disciplineincident",
        # Events — read
        "view_calendarevent",
        # Tasks — read
        "view_task",
        "view_department_tasks",
        # Communications — read + weekly focus submit/edit
        "view_broadcast", "view_weeklyfocus",
        "add_weeklyfocus", "change_weeklyfocus",
        # PTC — read dept completion
        "view_ptcsubjectcomment", "view_learnerattributeratingentry",
        "view_enrichmentgrade",
        "view_ptcwindow", "view_ptcgenerationlog",
        # Timetable — read + create/edit slots
        "view_timetableslot", "add_timetableslot", "change_timetableslot",
        # Admissions — meeting schedule
        "view_meetingschedule",
        # Core — deadline config
        "view_lessonplandeadline",
        # Audit — read
        "view_auditlog",
    ],

    # ── ADMIN OFFICER ────────────────────────────────────────
    # Admissions, enrolment, staff records, comms, events, timetable.
    UserRole.ADMIN_OFFICER: [
        # Academics — read
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "view_examscore",
        # Progression — execute promotion runs
        "add_progressioncase", "change_progressioncase", "view_progressioncase",
        "view_progressionconfig",
        # Students — full CRUD
        "add_student", "change_student", "view_student",
        "add_studentguardian", "change_studentguardian", "view_studentguardian",
        "add_studentsibling", "change_studentsibling", "view_studentsibling",
        "add_parentguardian", "change_parentguardian", "view_parentguardian",
        "add_laravelparent", "change_laravelparent", "view_laravelparent",
        "add_studentlaravelparent", "change_studentlaravelparent", "view_studentlaravelparent",
        "add_enrollmenthistory", "change_enrollmenthistory", "view_enrollmenthistory",
        "add_pdpaconsentlog", "change_pdpaconsentlog", "view_pdpaconsentlog",
        # Attendance — excused absences + corrections
        "add_attendanceentry", "change_attendanceentry", "view_attendanceentry",
        "view_staffattendanceentry",
        "add_message", "view_message",
        "view_notificationlog",
        # HR — staff records + assignments
        "view_staffprofile",
        "add_teacherclassassignment", "change_teacherclassassignment", "view_teacherclassassignment",
        "add_staffdocument", "change_staffdocument", "view_staffdocument",
        "add_staffonboardingprogress", "change_staffonboardingprogress", "view_staffonboardingprogress",
        "add_onboardingchecklistitem", "change_onboardingchecklistitem", "view_onboardingchecklistitem",
        "view_offboardingrecord", "view_leaverequest", "view_leaveallocation",
        # Finance — read assessment fees only
        "view_invoice", "view_invoicelineitem",
        # Admissions — full CRUD + actions
        "add_applicant", "change_applicant", "view_applicant",
        "add_applicantdocumentreceipt", "change_applicantdocumentreceipt", "view_applicantdocumentreceipt",
        "add_applicantinternalnote", "change_applicantinternalnote", "view_applicantinternalnote",
        "add_applicanttimelineentry", "change_applicanttimelineentry", "view_applicanttimelineentry",
        "add_assessmentschedule", "change_assessmentschedule", "view_assessmentschedule",
        "add_meetingschedule", "change_meetingschedule", "view_meetingschedule",
        "add_admissiongrade", "change_admissiongrade", "view_admissiongrade",
        "add_enrolmentchecklist", "change_enrolmentchecklist", "view_enrolmentchecklist",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "schedule_assessment",
        "upload_applicant_photo",
        "edit_admission_note",
        "transition_applicant_status",
        "view_assessment_calendar",
        "submit_hos_review",
        "reverse_assessment_fee",
        "submit_assessment_result",
        # Events — full CRUD
        "add_calendarevent", "change_calendarevent", "delete_calendarevent", "view_calendarevent",
        "add_eventacknowledgement", "change_eventacknowledgement", "view_eventacknowledgement",
        # Communications — full CRUD
        "add_broadcast", "change_broadcast", "view_broadcast",
        "add_notification", "change_notification", "view_notification",
        "add_emailsendlog", "view_emailsendlog",
        "add_weeklyfocus", "change_weeklyfocus", "view_weeklyfocus",
        # Timetable — full CRUD
        "add_timetableslot", "change_timetableslot", "delete_timetableslot", "view_timetableslot",
        "add_ecdbreakconfig", "change_ecdbreakconfig", "view_ecdbreakconfig",
        "add_timetableentry", "change_timetableentry", "delete_timetableentry", "view_timetableentry",
        "view_room",
        # Behaviour — create + read + update
        "add_disciplineincident", "change_disciplineincident", "view_disciplineincident",
        # Primary Welfare — read
        "view_welfareobservation",
        # Core — read + bulk import writes
        "view_schoolsettings", "change_schoolsettings",
        "add_lessonplandeadline", "change_lessonplandeadline", "view_lessonplandeadline",
        # PTC dates
        "add_ptcwindow", "change_ptcwindow", "view_ptcwindow",
        "add_ptcdatechangelog", "view_ptcdatechangelog",
        # Users — limited (read + create staff)
        "view_user",
        # Tasks — read (FRD module visibility)
        "view_task",
        # Audit — read
        "view_auditlog",
    ],

    # ── FINANCE OFFICER ──────────────────────────────────────
    # Finance module: fees, invoices, payments, reconciliation.
    UserRole.FINANCE_OFFICER: [
        # Academics — read
        "view_academicyear", "view_term",
        # Students — read guardian financial info
        "view_student", "view_studentguardian", "view_studentsibling",
        "view_parentguardian",
        # Finance — full CRUD
        "add_feestructure", "change_feestructure", "view_feestructure",
        "add_feestructureitem", "change_feestructureitem", "view_feestructureitem",
        "add_invoice", "change_invoice", "view_invoice",
        "add_invoicelineitem", "change_invoicelineitem", "view_invoicelineitem",
        "add_payment", "change_payment", "view_payment",
        "add_openingbalance", "change_openingbalance", "view_openingbalance",
        "add_unmatchedpayment", "change_unmatchedpayment", "view_unmatchedpayment",
        "add_financeconfig", "change_financeconfig", "view_financeconfig",
        "add_financeperiod", "change_financeperiod", "view_financeperiod",
        "add_budget", "change_budget", "view_budget",
        "add_expense", "change_expense", "view_expense",
        "add_recurringexpense", "change_recurringexpense", "view_recurringexpense",
        "add_reminderconfiguration", "change_reminderconfiguration", "view_reminderconfiguration",
        "add_concession", "change_concession", "view_concession",
        # Admissions — read assessment fees + confirm fee
        "view_applicant", "view_assessmentschedule", "view_meetingschedule",
        "confirm_assessment_fee",
        "reverse_assessment_fee",
        "transition_applicant_status",
        "view_assessment_calendar",
        "view_applicantinternalnote",
        "submit_hos_review",
        "send_assessment_logistics",
        "toggle_admission_document",
        "complete_enrolment",
        "submit_assessment_result",
        "upload_applicant_photo",
        "change_applicant",
        "schedule_assessment",
        # Tasks — read
        "view_task",
        # Shared read modules (FRD matrix visibility)
        "view_schoolsettings", "view_broadcast", "view_calendarevent", "view_auditlog",
    ],

    # ── TEACHER ──────────────────────────────────────────────
    # Class/subject scoped: attendance, grades, lesson plans, behaviour, welfare.
    UserRole.TEACHER: [
        # Academics — own class/subject
        "view_academicyear", "view_term", "view_subject", "view_gradeclass",
        "view_progressioncase",
        "add_lessonplan", "change_lessonplan", "view_lessonplan",
        "add_lessonplanattachment", "change_lessonplanattachment", "delete_lessonplanattachment", "view_lessonplanattachment",
        "view_lessonplandeadline",
        "add_examscore", "change_examscore", "view_examscore",
        "add_reportcard", "change_reportcard", "view_reportcard",
        "add_cambridgecheckpointscore", "change_cambridgecheckpointscore", "view_cambridgecheckpointscore",
        "add_ecdevaluation", "change_ecdevaluation", "view_ecdevaluation",
        "add_abcgeneralassignment", "change_abcgeneralassignment", "view_abcgeneralassignment",
        "add_abcinternalexam", "change_abcinternalexam", "view_abcinternalexam",
        "add_abcpaceprogress", "change_abcpaceprogress", "view_abcpaceprogress",
        "add_abcreadingprogramme", "change_abcreadingprogramme", "view_abcreadingprogramme",
        "add_abcscripture", "change_abcscripture", "view_abcscripture",
        # Students — own class
        "view_student", "view_studentguardian", "view_parentguardian",
        "view_studentsibling", "view_enrollmenthistory",
        # Admissions — submit assessment result (facilitating teacher only)
        "submit_assessment_result",
        # Attendance — own class
        "add_attendanceentry", "change_attendanceentry", "view_attendanceentry",
        # Welfare — create observation (ECD) + view own
        "add_welfareobservation", "view_welfareobservation",
        "view_welfareacknowledgment",
        # Discipline — create + read own
        "add_disciplineincident", "change_disciplineincident", "view_disciplineincident",
        # Events — read
        "view_calendarevent",
        # Tasks — read
        "view_task", "view_taskcomment",
        # Communications — read
        "view_broadcast", "view_weeklyfocus",
        "add_weeklyfocus",
        # PTC — own class/subject
        "add_ptcsubjectcomment", "change_ptcsubjectcomment", "view_ptcsubjectcomment",
        "add_learnerattributeratingentry", "change_learnerattributeratingentry", "view_learnerattributeratingentry",
        "add_enrichmentgrade", "change_enrichmentgrade", "view_enrichmentgrade",
        "view_enrichmentsubject",
        # Timetable — read own
        "view_timetableentry", "view_timetableslot",
        # Note: view_user intentionally excluded — Teachers should NOT access
        # /accounts/list/ (User Management). Own-profile viewing is handled
        # separately via the profile endpoint.
    ],

    # ── PARENT ───────────────────────────────────────────────
    # Read-only portal: own children's data. No write operations.
    UserRole.PARENT: [
        # Students — own child (read only)
        "view_student", "view_studentguardian", "view_studentsibling",
        "view_parentguardian",
        "change_studentguardian",
        # Attendance — own child
        "view_attendanceentry",
        # Finance — own child invoices/payments
        "view_invoice", "view_invoicelineitem", "view_payment",
        # Academics — own child reports
        "view_reportcard", "view_cambridgecheckpointscore",
        "view_progressioncase",
        "view_ecdevaluation", "view_examscore",
        # Welfare — own child
        "view_welfareobservation",
        "view_welfareacknowledgment", "change_welfareacknowledgment",
        # Discipline — own child incident confirmation
        "change_disciplineincident",
        # Events — read
        "view_calendarevent",
        # Communications — read
        "view_weeklyfocus", "view_broadcast",
        # Tasks — own child related
        "view_task",
        # PTC — own child
        "view_ptcsubjectcomment", "view_learnerattributeratingentry",
        "view_enrichmentgrade",
        # PDPA consent
        "view_pdpaconsentlog",
        # Note: view_user intentionally excluded — Parents should NOT access
        # /accounts/list/ (User Management). Own-profile viewing is handled
        # separately via the profile endpoint.
    ],
}
