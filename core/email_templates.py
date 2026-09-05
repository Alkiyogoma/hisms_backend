"""
Dynamic email template rendering and seeding utilities.

Templates are stored in the database (core.EmailTemplate) and admins can
edit them from Settings > Email Templates. This module provides:
  - send_dynamic_email(): render and send an email using a DB template
  - seed_default_templates(): populate DB with branded defaults
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "registration"

# ── Shared branded email wrapper ──────────────────────────────────────

_HEADER = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1.0"></head>'
    '<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,'
    "BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    '-webkit-text-size-adjust:100%">'
    '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:32px 16px">'
    '<tr><td align="center">'
    '<table width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;'
    'border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06)">'
    '<tr><td style="background:linear-gradient(135deg,#023AA5 0%,#0152c7 100%);'
    'padding:32px 24px;text-align:center">'
    '<img src="{{ site_url }}{{ static_url }}branding/hodari-logo-white.png" '
    'alt="{{ school_name }}" style="height:96px;margin-bottom:8px" />'
    '<p style="color:#C8A951;margin:0;font-size:12px;letter-spacing:1.5px;'
    'text-transform:uppercase;font-weight:500">{subtitle}</p>'
    '</td></tr>'
    '<tr><td style="padding:32px 24px">'
)

_FOOTER = (
    '</td></tr>'
    '<tr><td style="padding:20px 24px;border-top:1px solid #f0f2f5;text-align:center">'
    '<p style="color:#9ca3af;font-size:12px;margin:0 0 4px">{footer}</p>'
    '<p style="color:#9ca3af;font-size:11px;margin:0">&copy; {{ school_name }}</p>'
    '</td></tr>'
    '</table></td></tr></table>'
    '</body></html>'
)


def _brand(subtitle, body, footer="This is an automated message, please do not reply.", cta_url="", cta_text=""):
    """Build a complete branded email from a subtitle + inner body HTML."""
    parts = [_HEADER.replace("{subtitle}", subtitle), body]
    if cta_url and cta_text:
        parts.append(
            '<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 8px">'
            '<tr><td align="center">'
            f'<a href="{cta_url}" style="display:inline-block;background:#023AA5;color:#ffffff;'
            'text-decoration:none;padding:14px 0;border-radius:8px;font-size:15px;font-weight:600;'
            f'width:100%;box-sizing:border-box;text-align:center">{cta_text}</a>'
            '</td></tr></table>'
        )
    parts.append(_FOOTER.format(footer=footer))
    return "".join(parts)


def _info_card(text, color="#3b82f6", border="#bfdbfe", bg="#ffffff"):
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{bg};'
        f'border:1px solid {border};border-left:3px solid {color};border-radius:8px;margin:0 0 24px">'
        f'<tr><td style="padding:14px 16px">'
        f'<p style="color:#1e40af;font-size:13px;line-height:1.5;margin:0">{text}</p>'
        '</td></tr></table>'
    )


def _warning_card(text):
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;'
        'border:1px solid #fde68a;border-left:3px solid #f59e0b;border-radius:8px;margin:0 0 24px">'
        '<tr><td style="padding:14px 16px">'
        f'<p style="color:#92400e;font-size:13px;line-height:1.5;margin:0">{text}</p>'
        '</td></tr></table>'
    )


def _details_card(rows):
    """rows: list of (label, value) tuples."""
    cells = ""
    for label, value in rows:
        cells += (
            f'<p style="color:#6b7280;font-size:11px;margin:0 0 6px;text-transform:uppercase;'
            f'letter-spacing:1px;font-weight:600">{label}</p>'
            f'<p style="color:#023AA5;font-size:15px;font-weight:600;margin:0 0 16px;'
            f"font-family:'SF Mono',Monaco,Consolas,monospace\">{value}</p>"
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;'
        'border:1px solid #e5e7eb;border-radius:8px;margin:0 0 24px">'
        f'<tr><td style="padding:20px">{cells}</td></tr></table>'
    )


def _p(text, size="14px", color="#6b7280", mb="24px", bold=False):
    w = "font-weight:600;" if bold else ""
    return f'<p style="color:{color};font-size:{size};line-height:1.6;margin:0 0 {mb}">{text}</p>'


# ── Email sending ─────────────────────────────────────────────────────

def send_dynamic_email(template_type, to_email, context, actor=None, extra_subject=""):
    from core.models import EmailTemplate
    from communications.email_service import send_email_safe
    from django.conf import settings as django_settings

    # Resolve absolute URLs for template branding even if the caller omitted them.
    context = dict(context)
    context.setdefault("site_url", getattr(django_settings, 'SITE_URL', 'http://127.0.0.1:8000'))
    _static = getattr(django_settings, 'STATIC_URL', '/static/')
    if not _static.startswith('/'):
        _static = '/' + _static
    context.setdefault("static_url", _static)

    # Auto-resolve portal_link to an absolute URL if it's a relative path.
    _pl = context.get("portal_link", "")
    if _pl and _pl.startswith("/"):
        context["portal_link"] = context["site_url"].rstrip("/") + _pl

    try:
        tpl = EmailTemplate.objects.get(template_type=template_type)
    except EmailTemplate.DoesNotExist:
        logger.warning("EmailTemplate '%s' not found in DB. Falling back.", template_type)
        return False

    if not tpl.is_enabled:
        logger.info("EmailTemplate '%s' is disabled. Skipping.", template_type)
        return False

    subject = tpl.render_subject(context)
    if extra_subject:
        subject = f"{extra_subject} {subject}"

    html_body = tpl.render_html(context) if tpl.html_body else None
    if not html_body and tpl.plain_body:
        html_body = _plain_to_html(tpl.plain_body)
        if html_body:
            from django.template import Template, Context
            try:
                html_body = Template(html_body).render(Context(context))
            except Exception:
                pass
    plain_body = tpl.render_plain(context) if tpl.plain_body else subject

    return send_email_safe(
        to_email=to_email,
        subject=subject,
        body=plain_body,
        html_body=html_body or plain_body,
        actor=actor,
        action_type=template_type.upper(),
    )


# ── Helpers ───────────────────────────────────────────────────────────

def _read_template_file(filename):
    filepath = TEMPLATE_DIR / filename
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def _plain_to_html(plain_text):
    """Convert plain text to basic HTML for templates that have no html_body."""
    import html as html_mod
    if not plain_text:
        return ""
    escaped = html_mod.escape(plain_text)
    paragraphs = escaped.split("\n\n")
    return "<p>" + "</p><p>".join(p.replace("\n", "<br>") for p in paragraphs) + "</p>"


# ── Seed defaults ─────────────────────────────────────────────────────

_seeded = False

def seed_default_templates():
    global _seeded
    if _seeded:
        return 0
    _seeded = True

    from core.models import EmailTemplate

    defaults = [
        # ── Staff: Account Activation ──
        {
            "template_type": "activation",
            "name": "Staff Account Activation",
            "subject": "Your {{ school_name }} Account - {{ user.username }}",
            "html_file": "activation_email.html",
            "plain_body": (
                "Hello {{ user.first_name|default:user.username }},\n\n"
                "A staff account has been created for you at {{ school_name }}.\n\n"
                "Login URL: {{ login_url }}\n"
                "Username: {{ user.username }}\n"
                "Temporary Password: {{ temp_password }}\n\n"
                "Please log in and change your password immediately.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Staff: Password Reset ──
        {
            "template_type": "password_reset",
            "name": "Password Reset",
            "subject": "Password Reset - {{ school_name }}",
            "html_file": "password_reset_email.html",
            "plain_body": (
                "Hello {{ user.get_full_name|default:user.username }},\n\n"
                "You requested a password reset for your {{ school_name }} account.\n\n"
                "Click the link below to reset your password (valid for {{ expiry_hours }} hour(s)):\n"
                "{{ reset_url }}\n\n"
                "If you did not request this, please ignore this email.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Staff: Onboarding Complete ──
        {
            "template_type": "onboarding_complete",
            "name": "Onboarding Complete",
            "subject": "Onboarding Complete: {{ school_name }}",
            "html_file": "onboarding_complete_email.html",
            "plain_body": (
                "Hello {{ staff.first_name|default:staff.full_name }},\n\n"
                "Congratulations! Your onboarding process at {{ school_name }} has been completed.\n\n"
                "Employee ID: {{ staff.employee_id|default:'---' }}\n"
                "Department: {{ staff.get_department_display|default:staff.department }}\n"
                "Job Title: {{ staff.job_title|default:'---' }}\n"
                "Start Date: {{ staff.employment_start_date|date:'M d, Y'|default:'---' }}\n\n"
                "You now have full access to the School Management System.\n\n"
                "Login: {{ login_url }}\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Staff: Leave Approved ──
        {
            "template_type": "leave_approved",
            "name": "Leave Request Approved",
            "subject": "Leave Approved - {{ school_name }}",
            "html_body": _brand(
                "Leave Approved",
                _p("Dear <strong>{{ staff_name }}</strong>,", mb="8px")
                + _p("Your leave request has been <strong style='color:#16a34a'>approved</strong>.")
                + _details_card([
                    ("Leave Type", "{{ leave_type }}"),
                    ("From", "{{ start_date }}"),
                    ("To", "{{ end_date }}"),
                    ("Approved By", "{{ approved_by }}"),
                ])
                + _p("Please ensure a smooth handover before your leave begins.", mb="0"),
            ),
            "plain_body": (
                "Dear {{ staff_name }},\n\n"
                "Your leave request has been approved.\n\n"
                "Leave Type: {{ leave_type }}\n"
                "From: {{ start_date }}\n"
                "To: {{ end_date }}\n"
                "Approved by: {{ approved_by }}\n\n"
                "Please ensure a smooth handover before your leave begins.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Staff: Leave Rejected ──
        {
            "template_type": "leave_rejected",
            "name": "Leave Request Rejected",
            "subject": "Leave Request Update - {{ school_name }}",
            "html_body": _brand(
                "Leave Request Update",
                _p("Dear <strong>{{ staff_name }}</strong>,", mb="8px")
                + _p("Your leave request has been <strong style='color:#dc2626'>declined</strong>.")
                + _details_card([
                    ("Leave Type", "{{ leave_type }}"),
                    ("From", "{{ start_date }}"),
                    ("To", "{{ end_date }}"),
                    ("Reason", "{{ rejection_reason }}"),
                ])
                + _p("Please contact your supervisor if you have questions.", mb="0"),
            ),
            "plain_body": (
                "Dear {{ staff_name }},\n\n"
                "Your leave request has been declined.\n\n"
                "Leave Type: {{ leave_type }}\n"
                "From: {{ start_date }}\n"
                "To: {{ end_date }}\n"
                "Reason: {{ rejection_reason }}\n\n"
                "Please contact your supervisor if you have questions.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Parent: Portal Account ──
        {
            "template_type": "parent_portal",
            "name": "Parent Portal Account",
            "subject": "Your {{ school_name }} Parent Portal Account",
            "html_file": "parent_portal_email.html",
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "Your parent portal account has been created. You can now access your child's "
                "attendance records, report cards, invoices, and school communications.\n\n"
                "Username: {{ username }}\n"
                "Password: {{ temp_password }}\n\n"
                "IMPORTANT: You will be required to change your password on first login.\n\n"
                "Parent Portal: {{ login_url }}\n\n"
                "Thank you for choosing {{ school_name }}."
            ),
        },
        # ── Parent: Attendance Check-in ──
        {
            "template_type": "checkin_notification",
            "name": "Attendance Check-in Notification",
            "subject": "Check-in Notification - {{ school_name }}",
            "html_body": _brand(
                "Attendance Update",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("We would like to inform you that <strong>{{ student_name }}</strong> "
                     "has been <strong style='color:#16a34a'>checked in</strong> at "
                     "<strong>{{ checkin_time }}</strong>.", mb="0"),
                footer="You are receiving this because your child is registered at {{ school_name }}.",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "{{ student_name }} has been checked in at {{ checkin_time }}.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Parent: Attendance Check-out ──
        {
            "template_type": "checkout_notification",
            "name": "Attendance Check-out Notification",
            "subject": "Check-out Notification - {{ school_name }}",
            "html_body": _brand(
                "Attendance Update",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("We would like to inform you that <strong>{{ student_name }}</strong> "
                     "has been <strong style='color:#023AA5'>checked out</strong> at "
                     "<strong>{{ checkout_time }}</strong>.", mb="0"),
                footer="You are receiving this because your child is registered at {{ school_name }}.",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "{{ student_name }} has been checked out at {{ checkout_time }}.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Finance: Fee Invoice ──
        {
            "template_type": "fee_invoice",
            "name": "Fee Invoice",
            "subject": "{{ school_name }} - Invoice {{ invoice_number }}",
            "html_body": _brand(
                "Fee Invoice",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("Please find the details for invoice <strong>{{ invoice_number }}</strong> "
                     "for <strong>{{ student_name }}</strong>.")
                + _details_card([
                    ("Invoice Number", "{{ invoice_number }}"),
                    ("Amount Due", "{{ currency }} {{ amount_due }}"),
                    ("Due Date", "{{ due_date }}"),
                ])
                + _info_card("Please make payment before the due date to avoid late fees.")
                + _p("If you have already made payment, please disregard this notice.", mb="0"),
                cta_url="{{ portal_url }}", cta_text="View Invoice in Portal",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "Please find attached invoice {{ invoice_number }} for {{ student_name }}.\n\n"
                "Amount Due: {{ currency }} {{ amount_due }}\n"
                "Due Date: {{ due_date }}\n\n"
                "Please make payment before the due date.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Finance: Fee Reminder ──
        {
            "template_type": "fee_reminder",
            "name": "Fee Payment Reminder",
            "subject": "Fee Payment Reminder - {{ student_name }}",
            "html_body": _brand(
                "Payment Reminder",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("This is a friendly reminder that an outstanding fee balance remains "
                     "for <strong>{{ student_name }}</strong>.")
                + _details_card([
                    ("Outstanding Balance", "{{ currency }} {{ balance }}"),
                    ("Due Date", "{{ due_date }}"),
                ])
                + _warning_card("&#9888;&#65039; Please make payment as soon as possible to avoid disruption.")
                + _p("If you have already made payment, please disregard this notice.", mb="0"),
                cta_url="{{ portal_url }}", cta_text="View Balance in Portal",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "This is a reminder that an outstanding fee balance remains for {{ student_name }}.\n\n"
                "Balance: {{ currency }} {{ balance }}\n"
                "Due Date: {{ due_date }}\n\n"
                "Please make payment as soon as possible.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Finance: Payment Received ──
        {
            "template_type": "payment_received",
            "name": "Payment Received Confirmation",
            "subject": "Payment Received - {{ school_name }}",
            "html_body": _brand(
                "Payment Confirmed",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("We have received a payment of <strong>{{ currency }} {{ amount }}</strong> "
                     "for <strong>{{ student_name }}</strong>'s fees.")
                + _details_card([
                    ("Invoice", "{{ invoice_number }}"),
                    ("Amount Paid", "{{ currency }} {{ amount }}"),
                    ("Outstanding Balance", "{{ currency }} {{ balance }}"),
                ])
                + _p("View your receipt in the parent portal.", mb="0"),
                cta_url="{{ portal_url }}", cta_text="View Receipt in Portal",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "We have received a payment of {{ currency }} {{ amount }} for "
                "{{ student_name }}'s fees.\n\n"
                "Invoice: {{ invoice_number }}\n"
                "Amount Paid: {{ currency }} {{ amount }}\n"
                "Outstanding Balance: {{ currency }} {{ balance }}\n\n"
                "View your receipt in the parent portal.\n\n"
                "Thank you,\n{{ school_name }} Finance Office"
            ),
        },
        # ── Finance: Payment Reversal ──
        {
            "template_type": "payment_reversal",
            "name": "Payment Reversal Notice",
            "subject": "Payment Reversal - {{ school_name }}",
            "html_body": _brand(
                "Payment Reversal",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("A payment of <strong>{{ currency }} {{ amount }}</strong> on invoice "
                     "<strong>{{ invoice_number }}</strong> has been reversed.")
                + _warning_card("&#9888;&#65039; Reason: {{ reason }}")
                + _details_card([
                    ("Corrected Amount", "{{ currency }} {{ corrected_amount }}"),
                ])
                + _p("If you have questions, please contact the finance office.", mb="0"),
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "A payment of {{ currency }} {{ amount }} on invoice {{ invoice_number }} "
                "has been reversed.\n\n"
                "Reason: {{ reason }}\n"
                "A corrected entry of {{ currency }} {{ corrected_amount }} has been recorded.\n\n"
                "If you have questions, please contact the finance office.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Finance: Invoice Generated ──
        {
            "template_type": "invoice_generated",
            "name": "Invoice Generated Notification",
            "subject": "New Fee Invoice - {{ school_name }}",
            "html_body": _brand(
                "New Invoice",
                _p("Dear <strong>{{ guardian_name }}</strong>,", mb="8px")
                + _p("A new fee invoice has been generated for <strong>{{ student_name }}</strong>.")
                + _details_card([
                    ("Invoice", "{{ invoice_number }}"),
                    ("Term", "{{ term_name }}"),
                    ("Total Due", "{{ currency }} {{ total_due }}"),
                ])
                + _p("Please view the invoice in the parent portal for details.", mb="0"),
                cta_url="{{ portal_url }}", cta_text="View Invoice in Portal",
            ),
            "plain_body": (
                "Dear {{ guardian_name }},\n\n"
                "A new fee invoice has been generated for {{ student_name }}.\n\n"
                "Invoice: {{ invoice_number }}\n"
                "Term: {{ term_name }}\n"
                "Total Due: {{ currency }} {{ total_due }}\n\n"
                "Please view the invoice in the parent portal for details.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Inquiry Acknowledgement ──
        {
            "template_type": "admission_inquiry",
            "name": "Admissions Inquiry Acknowledgement",
            "subject": "We've received your inquiry for {{ child_name }} - {{ ref_number }}",
            "html_body": _brand(
                "Admissions",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for your inquiry about <strong>{{ child_name }}</strong>. "
                     "We have received your submission and our admissions team will review it shortly.")
                + _details_card([
                    ("Reference Number", "{{ ref_number }}"),
                    ("Child", "{{ child_name }}"),
                ])
                + _info_card("We will get back to you within 2 business days with next steps.")
                + _p("If you have any questions in the meantime, please do not hesitate to reach out.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for your inquiry about {{ child_name }}.\n\n"
                "Your reference number is: {{ ref_number }}\n\n"
                "Our admissions team will review your inquiry and get back to you shortly.\n\n"
                "Best regards,\n{{ school_name }} Admissions"
            ),
        },
        # ── Admissions: Meeting Scheduled (E02 — spec copy) ──
        {
            "template_type": "admission_meeting",
            "name": "Admissions Meeting Scheduled",
            "subject": "Your meeting with the Head of School — {{ reference_number|default:ref }}",
            "html_body": _brand(
                "Admissions Meeting",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for your interest in Hodari Christian School. Your meeting with our "
                     "Head of School has been confirmed.")
                + _details_card([
                    ("Date", "{{ meeting_date }}"),
                    ("Time", "{{ meeting_time }}"),
                    ("Venue", "{{ venue|default:'Hodari Christian School, main reception' }}"),
                    ("Reference", "{{ reference_number|default:ref }}"),
                ])
                + _info_card("The meeting takes about 45 minutes. Please bring your child with you, "
                             "along with their most recent school report if they are currently enrolled elsewhere.")
                + _p("If you need to change this appointment, call us on {{ admissions_phone|default:contact_phone }}.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for your interest in Hodari Christian School. Your meeting with our "
                "Head of School has been confirmed.\n\n"
                "Date: {{ meeting_date }}\n"
                "Time: {{ meeting_time }}\n"
                "Venue: {{ venue|default:'Hodari Christian School, main reception' }}\n"
                "Reference: {{ reference_number|default:ref }}\n\n"
                "The meeting takes about 45 minutes. Please bring your child with you, "
                "along with their most recent school report if they are currently enrolled elsewhere.\n\n"
                "If you need to change this appointment, call us on {{ admissions_phone|default:contact_phone }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Meeting Rescheduled (E04 — spec copy) ──
        {
            "template_type": "admission_meeting_rescheduled",
            "name": "Admissions Meeting Rescheduled",
            "subject": "Rescheduled: your meeting with the Head of School — {{ reference_number|default:ref }}",
            "html_body": _brand(
                "Meeting Rescheduled",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("We missed you at your appointment on <strong>{{ previous_meeting_date|default:previous_date }}</strong>. "
                     "Your meeting has been rescheduled.")
                + _details_card([
                    ("New date", "{{ meeting_date|default:new_date }}"),
                    ("New time", "{{ meeting_time|default:new_time }}"),
                    ("Venue", "{{ venue|default:'Hodari Christian School, main reception' }}"),
                    ("Reference", "{{ reference_number|default:ref }}"),
                ])
                + _info_card("If this time does not suit you, call us on {{ admissions_phone|default:contact_phone }} "
                             "and we will find one that does."),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "We missed you at your appointment on {{ previous_meeting_date|default:previous_date }}. "
                "Your meeting has been rescheduled.\n\n"
                "New date: {{ meeting_date|default:new_date }}\n"
                "New time: {{ meeting_time|default:new_time }}\n"
                "Venue: {{ venue|default:'Hodari Christian School, main reception' }}\n"
                "Reference: {{ reference_number|default:ref }}\n\n"
                "If this time does not suit you, call us on {{ admissions_phone|default:contact_phone }} "
                "and we will find one that does.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Assessment Logistics ──
        {
            "template_type": "admission_assessment_logistics",
            "name": "Assessment Logistics Sent",
            "subject": "What to expect on assessment day — {{ reference_number }}",
            "html_body": _brand(
                "Assessment Logistics",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Your payment has been received, thank you. Here is everything you need for "
                     "<strong>{{ child_name }}</strong>'s assessment.")
                + _details_card([
                    ("Dates", "{{ assessment_dates|default:assessment_date }}"),
                    ("Arrival time", "{{ arrival_time|default:assessment_time }}"),
                    ("Assessment starts", "{{ assessment_start_time|default:assessment_time }}"),
                    ("Collection time", "{{ collection_time|default:'After assessment' }}"),
                    ("Venue", "{{ assessment_venue|default:assessment_location }}"),
                    ("Reference", "{{ reference_number|default:ref }}"),
                ])
                + _info_card("Meals: Lunch and water are provided. Please pack a snack for the morning break.")
                + _info_card("What to bring: Two pencils, an eraser and a sharpener; a water bottle.")
                + _p("Please arrive 15 minutes early so we can settle {{ child_name }} before we begin. "
                     "Parents are welcome to wait in reception.", mb="8px")
                + _p("If anything comes up on the day, call us on {{ admissions_phone|default:contact_phone }}.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Your payment has been received. Here is everything you need for {{ child_name }}'s assessment.\n\n"
                "Dates: {{ assessment_dates|default:assessment_date }}\n"
                "Arrival time: {{ arrival_time|default:assessment_time }}\n"
                "Assessment starts: {{ assessment_start_time|default:assessment_time }}\n"
                "Collection time: {{ collection_time|default:'After assessment' }}\n"
                "Venue: {{ assessment_venue|default:assessment_location }}\n"
                "Reference: {{ reference_number|default:ref }}\n\n"
                "Meals: Lunch and water are provided. Please pack a snack for the morning break.\n\n"
                "What to bring:\n"
                "- Two pencils, an eraser and a sharpener\n"
                "- A water bottle\n\n"
                "Please arrive 15 minutes early. Parents are welcome to wait in reception.\n\n"
                "If anything comes up, call us on {{ admissions_phone|default:contact_phone }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Assessment Reminder ──
        {
            "template_type": "admission_assessment_reminder",
            "name": "Assessment Reminder (2 Days)",
            "subject": "Reminder: Assessment for {{ child_name }} in 2 days",
            "html_body": _brand(
                "Assessment Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("This is a friendly reminder that <strong>{{ child_name }}</strong>'s "
                     "assessment is in <strong>2 days</strong>.")
                + _details_card([
                    ("Date", "{{ assessment_date }}"),
                    ("Time", "{{ assessment_time }}"),
                    ("Location", "{{ assessment_location }}"),
                    ("Reference", "{{ ref }}"),
                ])
                + _warning_card("Please ensure your child arrives 15 minutes early with all required materials.")
                + _p("We look forward to seeing you.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "This is a friendly reminder that {{ child_name }}'s assessment is in 2 days.\n\n"
                "Date: {{ assessment_date }}\n"
                "Time: {{ assessment_time }}\n"
                "Location: {{ assessment_location }}\n\n"
                "Please ensure your child arrives 15 minutes early with all required materials.\n\n"
                "Reference: {{ ref }}\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Application Denied (E14) ──
        {
            "template_type": "admission_denied",
            "name": "Assessment Outcome — Not Successful (E14)",
            "subject": "Assessment outcome for {{ child_full_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Assessment Outcome",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for bringing <strong>{{ child_full_name }}</strong> to sit our "
                     "entrance assessment.")
                + _p("After reviewing the assessment, we are not able to offer "
                     "<strong>{{ child_full_name }}</strong> a place at this time. We know this is "
                     "not the news you were hoping for.")
                + _info_card("The full assessment report is attached, including the comments of "
                             "the teacher who conducted it. We hope you find it useful, whichever "
                             "school {{ child_full_name }} joins next.")
                + _p("You are welcome to apply again in a future intake. If you would like to "
                     "talk through the report, call us on {{ admissions_phone|default:admissions_email }} "
                     "and we will arrange it.", mb="8px")
                + _details_card([
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("We wish {{ child_full_name }} every success.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for bringing {{ child_full_name }} to sit our entrance assessment.\n\n"
                "After reviewing the assessment, we are not able to offer "
                "{{ child_full_name }} a place at this time. We know this is not the news "
                "you were hoping for.\n\n"
                "The full assessment report is attached, including the comments of the teacher "
                "who conducted it. We hope you find it useful, whichever school "
                "{{ child_full_name }} joins next.\n\n"
                "You are welcome to apply again in a future intake. If you would like to talk "
                "through the report, call us on {{ admissions_phone|default:admissions_email }} "
                "and we will arrange it.\n\n"
                "Reference: {{ reference_number }}\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Admission Fee Invoice ──
        {
            "template_type": "admission_fee_invoice",
            "name": "Admission Fee Invoice",
            "subject": "Admission Fee Invoice for {{ child_name }} - {{ ref }}",
            "html_body": _brand(
                "Admission Fee",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("An admission fee invoice has been generated for <strong>{{ child_name }}</strong>.")
                + _details_card([
                    ("Invoice Number", "{{ invoice_number }}"),
                    ("Amount Due", "{{ currency }} {{ amount_due }}"),
                    ("Due Date", "{{ due_date }}"),
                    ("Reference", "{{ ref }}"),
                ])
                + _info_card("Payment can be made via Bank Transfer (DTB 0225556001 or "
                             "CRDB 0150829302900) or Mobile Money ({{ contact_phone }}).")
                + _p("Send proof of payment to {{ admissions_email }} or WhatsApp {{ admissions_whatsapp }}.", mb="0"),
                footer="{{ school_name }} Finance Office",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "An admission fee invoice has been generated for {{ child_name }}.\n\n"
                "Invoice Number: {{ invoice_number }}\n"
                "Amount Due: {{ currency }} {{ amount_due }}\n"
                "Due Date: {{ due_date }}\n\n"
                "Payment can be made via:\n"
                "- Bank Transfer: DTB 0225556001 or CRDB 0150829302900\n"
                "- Mobile Money: {{ contact_phone }}\n\n"
                "Please send proof of payment to {{ admissions_email }} or WhatsApp {{ admissions_whatsapp }}.\n\n"
                "Reference: {{ ref }}\n\n"
                "Finance Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Document Received ──
        {
            "template_type": "admission_document_received",
            "name": "Document Received Confirmation",
            "subject": "Document Received - {{ child_name }} - {{ ref }}",
            "html_body": _brand(
                "Document Received",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("We have received and processed the <strong>{{ document_type }}</strong> "
                     "for <strong>{{ child_name }}</strong>'s application.")
                + _details_card([
                    ("Document Type", "{{ document_type }}"),
                    ("Reference", "{{ ref }}"),
                ])
                + _info_card("If you have any questions, please contact us at {{ admissions_email }}.")
                + _p("Thank you for your prompt submission.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "We have received and processed the {{ document_type }} for {{ child_name }}'s application.\n\n"
                "Reference: {{ ref }}\n\n"
                "If you have any questions, please contact us at {{ admissions_email }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Offer Letter (E15 — spec copy) ──
        {
            "template_type": "admission_offer_letter",
            "name": "Admission Offer Letter (E15)",
            "subject": "Congratulations — a place for {{ child_name }} at {{ school_name|default:Hodari }}",
            "html_body": _brand(
                "Offer of a Place",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Congratulations. We are delighted to offer <strong>{{ child_name }}</strong> a place "
                     "in our <strong>{{ offered_grade|default:grade }}</strong> class, commencing "
                     "{{ term_start_date|default:'the new term' }}. We trust that God will bless their "
                     "time at Hodari.")
                + _details_card([
                    ("Reference", "{{ reference_number }}"),
                    ("Grade", "{{ offered_grade|default:grade }}"),
                    ("Offer expires", "{{ offer_expiry_date|default:'14 days from this email' }}"),
                ])
                + _p("<strong>To accept the place, log in to the parent portal and complete the admission form.</strong> "
                     "The fee structure is set out inside the form.", mb="12px")
                + _details_card([
                    ("Portal", "{{ portal_link|default:'/portal/' }}"),
                    ("Username", "{{ portal_username|default:parent_email }}"),
                    ("Temporary password", "{{ portal_password|default:'Sent separately' }}"),
                ])
                + _p("You'll be asked to change your password the first time you log in. Once the form is "
                     "submitted you can generate your invoice from the portal, and the place is confirmed "
                     "when the invoice is settled in full.", mb="8px")
                + _warning_card("Please complete the form by {{ offer_expiry_date|default:'14 days' }}. "
                                "If we haven't heard from you by then we may need to release the place.")
                + _info_card("Uniform — T-shirts and sweaters are available at school, but our vendor needs "
                             "measurements to tailor shorts and trousers, so please come in for measuring "
                             "as early as you can.")
                + _info_card("Meals — lunch is included in the fees. The mid-morning meal is an optional extra.")
                + _info_card("Transport — our transport is run by Upanga Transport Company (0711 729 572), "
                             "and payments go directly to them.")
                + _info_card("Invoice — for any help with payment, contact our accountant at "
                             "{{ finance_email|default:admissions_email }}.")
                + _p("Everything is completed online, so there's nothing to print or bring in.", mb="8px")
                + _p("We look forward to having {{ child_name }} at Hodari.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Congratulations. We are delighted to offer {{ child_name }} a place in our "
                "{{ offered_grade|default:grade }} class, commencing {{ term_start_date|default:'the new term' }}.\n\n"
                "Reference: {{ reference_number }}\n"
                "Grade: {{ offered_grade|default:grade }}\n"
                "Offer expires: {{ offer_expiry_date|default:'14 days from this email' }}\n\n"
                "To accept the place, log in to the parent portal and complete the admission form.\n\n"
                "Portal: {{ portal_link|default:'/portal/' }}\n"
                "Username: {{ portal_username|default:parent_email }}\n"
                "Temporary password: {{ portal_password|default:'Sent separately' }}\n\n"
                "You'll be asked to change your password the first time you log in. Once the form is "
                "submitted you can generate your invoice from the portal, and the place is confirmed "
                "when the invoice is settled in full.\n\n"
                "Please complete the form by {{ offer_expiry_date|default:'14 days' }}. If we haven't "
                "heard from you by then we may need to release the place.\n\n"
                "Uniform — T-shirts and sweaters are available at school. Our vendor needs measurements "
                "to tailor shorts and trousers.\n"
                "Meals — lunch is included in the fees.\n"
                "Transport — run by Upanga Transport Company (0711 729 572).\n"
                "Invoice — contact our accountant at {{ finance_email|default:admissions_email }}.\n\n"
                "Everything is completed online. We look forward to having {{ child_name }} at Hodari.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Waitlist ──
        {
            "template_type": "admission_waitlist",
            "name": "Waitlist Notification",
            "subject": "Waitlist Update for {{ child_name }} - {{ ref }}",
            "html_body": _brand(
                "Waitlist Update",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for your interest in <strong>{{ school_name }}</strong> for "
                     "<strong>{{ child_name }}</strong>.")
                + _p("At this time, we do not have immediate availability in <strong>{{ grade }}</strong>. "
                     "However, we have placed {{ child_name }} on our waiting list.")
                + _details_card([
                    ("Position", "{{ waitlist_position }}"),
                    ("Reference", "{{ ref }}"),
                ])
                + _info_card("We will notify you immediately if a place becomes available.")
                + _p("Thank you for your patience.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for your interest in {{ school_name }} for {{ child_name }}.\n\n"
                "At this time, we do not have immediate availability in {{ grade }}. "
                "However, we have placed {{ child_name }} on our waiting list.\n\n"
                "Position: {{ waitlist_position }}\n"
                "Reference: {{ ref }}\n\n"
                "We will notify you immediately if a place becomes available. "
                "If you have any questions, please contact us at {{ admissions_email }}.\n\n"
                "Thank you for your patience.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Assessment Fee Reminder ──
        {
            "template_type": "admission_assessment_fee_reminder",
            "name": "Assessment Fee Reminder",
            "subject": "Reminder: assessment fee for {{ learner_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Assessment Fee Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("We have not yet received the assessment fee for <strong>{{ learner_name }}</strong>, "
                     "whose assessment is booked for <strong>{{ assessment_date }}</strong>.")
                + _details_card([
                    ("Amount Due", "{{ assessment_fee }}"),
                    ("Reference to Quote", "{{ reference_number }}"),
                ])
                + _info_card("Payment details are in our earlier email. Send your proof of payment to {{ finance_email }}.")
                + _p("If you have already paid, please ignore this message. If you would like to change the assessment "
                     "date or have any questions, call us on {{ admissions_phone }}.", mb="0"),
                footer="Admissions Office\n{{ school_name }}",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "We have not yet received the assessment fee for {{ learner_name }}, whose assessment is "
                "booked for {{ assessment_date }}.\n\n"
                "Amount due: {{ assessment_fee }}\n"
                "Reference to quote: {{ reference_number }}\n\n"
                "Payment details are in our earlier email. Send your proof of payment to {{ finance_email }}.\n\n"
                "If you have already paid, please ignore this message. If you would like to change the assessment "
                "date or have any questions, call us on {{ admissions_phone }}.\n\n"
                "Admissions Office\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Report Outstanding Reminder ──
        {
            "template_type": "admission_report_reminder",
            "name": "Assessment Report Reminder",
            "subject": "Reminder: assessment report needed for {{ learner_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Assessment Report Reminder",
                _p("Dear <strong>{{ teacher_name }}</strong>,", mb="8px")
                + _p("The assessment for <strong>{{ learner_name }}</strong> has been marked complete. "
                     "Please complete the assessment report.")
                + _details_card([
                    ("Learner", "{{ learner_name }}"),
                    ("Grade Assessed For", "{{ intended_grade }}"),
                    ("Assessment Date", "{{ assessment_date }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("Complete the report here: <a href='{{ report_link }}' style='color:#023AA5'>{{ report_link }}</a>", mb="8px")
                + _p("Your report goes to the Head of School for review, so please submit it within 48 hours.", mb="0"),
                footer="{{ school_name }}",
            ),
            "plain_body": (
                "Dear {{ teacher_name }},\n\n"
                "The assessment for {{ learner_name }} has been marked complete. Please complete the "
                "assessment report.\n\n"
                "Learner: {{ learner_name }}\n"
                "Grade assessed for: {{ intended_grade }}\n"
                "Assessment date: {{ assessment_date }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Complete the report here: {{ report_link }}\n\n"
                "Your report goes to the Head of School for review, so please submit it within 48 hours.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Form Submission Reminder ──
        {
            "template_type": "admission_form_reminder",
            "name": "Admission Form Reminder",
            "subject": "Reminder: complete the admission form for {{ learner_name }}",
            "html_body": _brand(
                "Admission Form Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("<strong>{{ learner_name }}</strong>'s place at Hodari Christian School is still waiting for you. "
                     "The admission form has not yet been completed.")
                + _info_card("Please complete it by {{ offer_expiry_date }}.")
                + _details_card([
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("Complete the form here: <a href='{{ portal_link }}' style='color:#023AA5'>{{ portal_link }}</a>", mb="8px")
                + _p("If you are having trouble logging in, or your plans have changed, call us on {{ admissions_phone }}.", mb="0"),
                footer="Admissions Office\n{{ school_name }}",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "{{ learner_name }}'s place at Hodari Christian School is still waiting for you. "
                "The admission form has not yet been completed.\n\n"
                "Please complete it by {{ offer_expiry_date }}: {{ portal_link }}\n\n"
                "Reference: {{ reference_number }}\n\n"
                "If you are having trouble logging in, or your plans have changed, call us on {{ admissions_phone }}.\n\n"
                "Admissions Office\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Invoice Payment Reminder ──
        {
            "template_type": "admission_invoice_reminder",
            "name": "Admission Invoice Reminder",
            "subject": "Reminder: admission invoice for {{ learner_name }} — {{ invoice_number }}",
            "html_body": _brand(
                "Admission Invoice Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for completing <strong>{{ learner_name }}</strong>'s admission form. "
                     "The admission invoice is still outstanding.")
                + _details_card([
                    ("Invoice Number", "{{ invoice_number }}"),
                    ("Amount Due", "{{ invoice_amount }}"),
                    ("Reference to Quote", "{{ reference_number }}"),
                ])
                + _info_card("You can view and download the invoice in the parent portal.")
                + _p("View invoice: <a href='{{ portal_link }}' style='color:#023AA5'>{{ portal_link }}</a>", mb="8px")
                + _p("Enrolment is completed once payment is received in full. If you would like to discuss the "
                     "payment, call us on {{ finance_phone }}.", mb="0"),
                footer="Admissions Office\n{{ school_name }}",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for completing {{ learner_name }}'s admission form. The admission invoice is "
                "still outstanding.\n\n"
                "Invoice number: {{ invoice_number }}\n"
                "Amount due: {{ invoice_amount }}\n"
                "Reference to quote: {{ reference_number }}\n\n"
                "You can view and download the invoice in the parent portal: {{ portal_link }}\n\n"
                "Enrolment is completed once payment is received in full. If you would like to discuss the "
                "payment, call us on {{ finance_phone }}.\n\n"
                "Admissions Office\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Declined at Meeting (E05 — spec copy) ──
        {
            "template_type": "admission_declined_at_meeting",
            "name": "Declined at Meeting",
            "subject": "Your application to Hodari Christian School — {{ reference_number }}",
            "html_body": _brand(
                "Application Update",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("Thank you for meeting with us and for considering Hodari Christian School for "
                     "<strong>{{ child_name }}</strong>.")
                + _p("After our conversation, we are not able to take this application forward at this time. "
                     "We appreciate the time you gave us and wish {{ child_name }} every success.")
                + _info_card("You are welcome to enquire again in a future intake.")
                + _p("Reference: {{ reference_number }}", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "Thank you for meeting with us and for considering Hodari Christian School for {{ child_name }}.\n\n"
                "After our conversation, we are not able to take this application forward at this time. "
                "We appreciate the time you gave us and wish {{ child_name }} every success.\n\n"
                "You are welcome to enquire again in a future intake.\n\n"
                "Reference: {{ reference_number }}\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Assessment Scheduled + Fee Due (E06) ──
        {
            "template_type": "admission_assessment_fee_invoice",
            "name": "Assessment Scheduled + Fee Due",
            "subject": "Assessment for {{ child_name }} scheduled — {{ reference_number }}",
            "html_body": _brand(
                "Assessment & Fee Due",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("An assessment has been scheduled for <strong>{{ child_name }}</strong>. "
                     "Please arrange payment of the assessment fee before the assessment date.")
                + _details_card([
                    ("Learner", "{{ child_name }}"),
                    ("Grade enquired for", "{{ grade }}"),
                    ("Assessment date", "{{ assessment_date }}"),
                    ("Assessment time", "{{ assessment_time }}"),
                    ("Duration", "{{ assessment_duration|default:'~2 hours' }}"),
                    ("Scope", "{{ assessment_scope|default:'English, Mathematics, and interview' }}"),
                    ("Assessment fee", "{{ assessment_fee }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _info_card("Bank details: {{ bank_details|default:'See earlier correspondence' }}.")
                + _info_card("Send your proof of payment to {{ finance_email }} or WhatsApp {{ whatsapp_number }}.")
                + _info_card("Meals: {{ meals|default:'Please ensure your child has had breakfast and carries a packed snack.' }}")
                + _p("If you have any questions, call us on {{ admissions_phone }}.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "An assessment has been scheduled for {{ child_name }}.\n\n"
                "Learner: {{ child_name }}\n"
                "Grade: {{ grade }}\n"
                "Assessment date: {{ assessment_date }}\n"
                "Assessment time: {{ assessment_time }}\n"
                "Duration: {{ assessment_duration|default:'~2 hours' }}\n"
                "Scope: {{ assessment_scope|default:'English, Mathematics, and interview' }}\n"
                "Assessment fee: {{ assessment_fee }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Bank details: {{ bank_details|default:'See earlier correspondence' }}.\n"
                "Send proof of payment to {{ finance_email }} or WhatsApp {{ whatsapp_number }}.\n\n"
                "Meals: {{ meals|default:'Please ensure your child has had breakfast and carries a packed snack.' }}\n\n"
                "If you have any questions, call us on {{ admissions_phone }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Report Submitted → HOS (E12 — spec copy) ──
        {
            "template_type": "admission_report_submitted_hos",
            "name": "Assessment Report Submitted (Notify HOS)",
            "subject": "Review needed: {{ child_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Assessment Report",
                _p("The assessment report for <strong>{{ child_name }}</strong> has been submitted by "
                   "<strong>{{ teacher_name }}</strong>.")
                + _details_card([
                    ("Learner", "{{ child_name }}"),
                    ("Grade assessed for", "{{ intended_grade|default:grade }}"),
                    ("Assessment date", "{{ assessment_date }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("Add your comments and record the outcome: "
                     "<a href='{{ review_link }}' style='color:#023AA5'>{{ review_link }}</a>", mb="0"),
                footer="{{ school_name }}",
            ),
            "plain_body": (
                "The assessment report for {{ child_name }} has been submitted by {{ teacher_name }}.\n\n"
                "Learner: {{ child_name }}\n"
                "Grade assessed for: {{ intended_grade|default:grade }}\n"
                "Assessment date: {{ assessment_date }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Add your comments and record the outcome: {{ review_link }}\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Welcome / Enrolment Confirmed (E21 — spec copy) ──
        {
            "template_type": "admission_welcome",
            "name": "Welcome / Enrolment Confirmed",
            "subject": "Welcome to {{ school_name }} — {{ child_name }}",
            "html_body": _brand(
                "Welcome!",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("We are delighted to confirm that <strong>{{ child_name }}</strong> "
                     "is now enrolled at <strong>{{ school_name }}</strong>.")
                + _details_card([
                    ("Student Number", "{{ admission_no }}"),
                    ("Grade", "{{ grade }}"),
                    ("Class", "{{ class_name|default:'TBC' }}"),
                    ("Academic Year", "{{ academic_year }}"),
                    ("Term start date", "{{ term_start_date|default:'See school calendar' }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _info_card("Your parent portal account is now active. Log in at {{ portal_link|default:'/accounts/login/' }} "
                             "to view attendance, report cards, invoices, and school communications.")
                + _info_card("Uniform: {{ uniform_notes|default:'Ensure correct uniform is worn from day one.' }}")
                + _info_card("Meals: {{ meals|default:'School meals available — see fee schedule.' }}")
                + _info_card("Transport: {{ transport_notes|default:'Transport available for qualifying zones.' }}")
                + _p("We look forward to welcoming {{ child_name }} on the first day of school!", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "We are delighted to confirm that {{ child_name }} is now enrolled at {{ school_name }}.\n\n"
                "Student Number: {{ admission_no }}\n"
                "Grade: {{ grade }}\n"
                "Class: {{ class_name|default:'TBC' }}\n"
                "Academic Year: {{ academic_year }}\n"
                "Term start date: {{ term_start_date|default:'See school calendar' }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Your parent portal account is now active.\n"
                "Log in at {{ portal_link|default:'/accounts/login/' }} to view attendance, "
                "report cards, invoices, and school communications.\n\n"
                "Uniform: {{ uniform_notes|default:'Ensure correct uniform is worn from day one.' }}\n"
                "Meals: {{ meals|default:'School meals available — see fee schedule.' }}\n"
                "Transport: {{ transport_notes|default:'Transport available for qualifying zones.' }}\n\n"
                "We look forward to welcoming {{ child_name }} on the first day of school!\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Meeting Reminder 48h (E-MEET-48) ──
        {
            "template_type": "admission_meeting_reminder_48h",
            "name": "Meeting Reminder (48 Hours)",
            "subject": "Reminder: Meeting in 2 days — {{ child_name }}",
            "html_body": _brand(
                "Meeting Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("This is a reminder that your meeting with the Head of School for "
                     "<strong>{{ child_name }}</strong>'s admission is in <strong>2 days</strong>.")
                + _details_card([
                    ("Date", "{{ meeting_date }}"),
                    ("Time", "{{ meeting_time }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _warning_card("Please arrive 10 minutes early. Bring your child's school reports and any relevant documents.")
                + _p("If you need to reschedule, call us on {{ admissions_phone }}.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "This is a reminder that your meeting with the Head of School for "
                "{{ child_name }}'s admission is in 2 days.\n\n"
                "Date: {{ meeting_date }}\n"
                "Time: {{ meeting_time }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Please arrive 10 minutes early. If you need to reschedule, "
                "call us on {{ admissions_phone }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: Meeting Reminder 24h (E-MEET-24) ──
        {
            "template_type": "admission_meeting_reminder_24h",
            "name": "Meeting Reminder (24 Hours)",
            "subject": "Reminder: Meeting tomorrow — {{ child_name }}",
            "html_body": _brand(
                "Meeting Reminder",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("This is a reminder that your meeting with the Head of School for "
                     "<strong>{{ child_name }}</strong>'s admission is <strong>tomorrow</strong>.")
                + _details_card([
                    ("Date", "{{ meeting_date }}"),
                    ("Time", "{{ meeting_time }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _warning_card("Please arrive 10 minutes early. Bring your child's school reports and any relevant documents.")
                + _p("If you need to reschedule, call us on {{ admissions_phone }}.", mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "This is a reminder that your meeting with the Head of School for "
                "{{ child_name }}'s admission is tomorrow.\n\n"
                "Date: {{ meeting_date }}\n"
                "Time: {{ meeting_time }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Please arrive 10 minutes early. If you need to reschedule, "
                "call us on {{ admissions_phone }}.\n\n"
                "Admissions Office\n{{ school_name }}"
            ),
        },
        # ── Admissions: HOS Review Reminder 72h (E13) ──
        {
            "template_type": "admission_hos_review_reminder",
            "name": "HOS Review Reminder (72 Hours)",
            "subject": "Reminder: Assessment report awaiting review — {{ child_name }}",
            "html_body": _brand(
                "Review Reminder",
                _p("Dear <strong>{{ hos_name }}</strong>,", mb="8px")
                + _p("An assessment report for <strong>{{ child_name }}</strong> has been "
                     "awaiting your review for <strong>72 hours</strong>.")
                + _details_card([
                    ("Learner", "{{ child_name }}"),
                    ("Grade", "{{ grade }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("Review the report here: <a href='{{ review_link }}' style='color:#023AA5'>{{ review_link }}</a>", mb="0"),
                footer="{{ school_name }}",
            ),
            "plain_body": (
                "Dear {{ hos_name }},\n\n"
                "An assessment report for {{ child_name }} has been awaiting your review for 72 hours.\n\n"
                "Learner: {{ child_name }}\n"
                "Grade: {{ grade }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Review the report here: {{ review_link }}\n\n"
                "{{ school_name }}"
            ),
        },
        # ── Admissions: Payment Receipt (E20-RECEIPT) ──
        {
            "template_type": "admission_payment_receipt",
            "name": "Payment Receipt",
            "subject": "Payment Received — {{ child_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Payment Received",
                _p("Dear <strong>{{ parent_name }}</strong>,", mb="8px")
                + _p("We have received your payment for <strong>{{ child_name }}</strong>'s admission.")
                + _details_card([
                    ("Amount Paid", "{{ amount_paid }}"),
                    ("Payment Method", "{{ payment_method }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _info_card("No further action is required at this time.")
                + _p("Thank you for your prompt payment.", mb="0"),
                footer="{{ school_name }} Finance Office",
            ),
            "plain_body": (
                "Dear {{ parent_name }},\n\n"
                "We have received your payment for {{ child_name }}'s admission.\n\n"
                "Amount Paid: {{ amount_paid }}\n"
                "Payment Method: {{ payment_method }}\n"
                "Reference: {{ reference_number }}\n\n"
                "Thank you for your prompt payment.\n\n"
                "Finance Office\n{{ school_name }}"
            ),
        },
        # ── System: OTP Code ──
        {
            "template_type": "otp_code",
            "name": "OTP Code",
            "subject": "OTP Code - {{ school_name }}",
            "html_body": _brand(
                "Verification Code",
                _p("Your one-time password (OTP) is:", mb="4px", color="#374151")
                + _details_card([("OTP Code", "{{ otp_code }}")])
                + _warning_card("This code expires in {{ expiry_minutes }} minutes. Do not share this code with anyone.")
                + _p("If you did not request this code, please ignore this email.", mb="0"),
            ),
            "plain_body": (
                "Your one-time password (OTP) is: {{ otp_code }}\n\n"
                "This code expires in {{ expiry_minutes }} minutes.\n\n"
                "{{ school_name }}"
            ),
        },
        # ── System: Broadcast Message ──
        {
            "template_type": "broadcast",
            "name": "Broadcast Message",
            "subject": "{{ subject }}",
            "html_body": _brand(
                "{{ subject }}",
                _p("{{ message }}", size="15px", color="#374151", mb="0"),
                footer="This is an automated message from {{ school_name }}.",
            ),
            "plain_body": "{{ message }}",
        },
        # ── E01: New inquiry received → admissions group + HOS ──
        {
            "template_type": "admission_new_inquiry",
            "name": "New Inquiry Received (E01)",
            "subject": "New inquiry: {{ child_name }}, Grade {{ grade }} — {{ reference_number }}",
            "html_body": _brand(
                "New Inquiry",
                _p("A new admission inquiry has been submitted.", mb="12px")
                + _details_card([
                    ("Reference", "{{ reference_number }}"),
                    ("Learner", "{{ child_name }}"),
                    ("Grade enquired for", "{{ grade }}"),
                    ("Parent or guardian", "{{ parent_name }}"),
                    ("Phone", "{{ parent_phone }}"),
                    ("Email", "{{ parent_email|default:'Not provided' }}"),
                    ("Meeting date requested", "{{ proposed_meeting_date|default:'To be confirmed' }} at {{ proposed_meeting_time|default:'To be confirmed' }}"),
                ])
                + _p('<a href="{{ application_link }}" style="display:inline-block;padding:10px 20px;background:#1B3A8F;color:#fff;text-decoration:none;border-radius:6px;font-weight:600">Review application</a>', mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "A new admission inquiry has been submitted.\n\n"
                "Reference: {{ reference_number }}\n"
                "Learner: {{ child_name }}\n"
                "Grade enquired for: {{ grade }}\n"
                "Parent or guardian: {{ parent_name }}\n"
                "Phone: {{ parent_phone }}\n"
                "Email: {{ parent_email|default:'Not provided' }}\n\n"
                "Meeting date requested: {{ proposed_meeting_date|default:'To be confirmed' }} at "
                "{{ proposed_meeting_time|default:'To be confirmed' }}\n\n"
                "Review: {{ application_link }}\n\n"
                "{{ school_name }} Admissions"
            ),
        },
        # ── E17: Form outstanding 14d → admissions group ──
        {
            "template_type": "admission_form_outstanding",
            "name": "Form Outstanding 14 Days (E17)",
            "subject": "Form outstanding: {{ child_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Admission Form Outstanding",
                _p("The admission form for <strong>{{ child_name }}</strong> has not been completed "
                   "within 14 days of the offer.", mb="12px")
                + _details_card([
                    ("Learner", "{{ child_name }}"),
                    ("Grade", "{{ grade }}"),
                    ("Parent", "{{ parent_name }}"),
                    ("Phone", "{{ parent_phone }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("The admin office should follow up with the family to determine whether "
                     "the place should be released.", mb="8px")
                + _p('<a href="{{ application_link }}" style="display:inline-block;padding:10px 20px;background:#1B3A8F;color:#fff;text-decoration:none;border-radius:6px;font-weight:600">View application</a>', mb="0"),
                footer="{{ school_name }} Admissions",
            ),
            "plain_body": (
                "The admission form for {{ child_name }} has not been completed within 14 days.\n\n"
                "Learner: {{ child_name }}\n"
                "Grade: {{ grade }}\n"
                "Parent: {{ parent_name }}\n"
                "Phone: {{ parent_phone }}\n"
                "Reference: {{ reference_number }}\n\n"
                "The admin office should follow up with the family.\n\n"
                "View: {{ application_link }}\n\n"
                "{{ school_name }} Admissions"
            ),
        },
        # ── E20: Invoice outstanding 30d → admissions + finance ──
        {
            "template_type": "admission_invoice_outstanding_30d",
            "name": "Invoice Outstanding 30 Days (E20)",
            "subject": "Invoice outstanding 30 days: {{ child_name }} — {{ reference_number }}",
            "html_body": _brand(
                "Invoice Outstanding — 30 Days",
                _p("The admission invoice for <strong>{{ child_name }}</strong> has been outstanding "
                   "for 30 days.", mb="12px")
                + _details_card([
                    ("Learner", "{{ child_name }}"),
                    ("Grade", "{{ grade }}"),
                    ("Parent", "{{ parent_name }}"),
                    ("Phone", "{{ parent_phone }}"),
                    ("Invoice", "{{ invoice_number }}"),
                    ("Reference", "{{ reference_number }}"),
                ])
                + _p("The admin office should contact the family. If payment is not expected, "
                     "consider releasing the place.", mb="8px")
                + _p('<a href="{{ application_link }}" style="display:inline-block;padding:10px 20px;background:#1B3A8F;color:#fff;text-decoration:none;border-radius:6px;font-weight:600">View application</a>', mb="0"),
                footer="{{ school_name }} Finance & Admissions",
            ),
            "plain_body": (
                "The admission invoice for {{ child_name }} has been outstanding for 30 days.\n\n"
                "Learner: {{ child_name }}\n"
                "Grade: {{ grade }}\n"
                "Parent: {{ parent_name }}\n"
                "Phone: {{ parent_phone }}\n"
                "Invoice: {{ invoice_number }}\n"
                "Reference: {{ reference_number }}\n\n"
                "The admin office should contact the family.\n\n"
                "View: {{ application_link }}\n\n"
                "{{ school_name }} Finance & Admissions"
            ),
        },
    ]

    created_count = 0
    updated_count = 0
    for tpl_data in defaults:
        html_file = tpl_data.pop("html_file", None)
        if html_file and "html_body" not in tpl_data:
            tpl_data["html_body"] = _read_template_file(html_file)

        tpl, created = EmailTemplate.objects.get_or_create(
            template_type=tpl_data["template_type"],
            defaults={**tpl_data, "is_default": True, "is_enabled": True},
        )
        if created:
            created_count += 1
        else:
            new_html = tpl_data.get("html_body", "")
            if new_html and tpl.html_body != new_html:
                tpl.html_body = new_html
                tpl.save(update_fields=["html_body"])
                updated_count += 1
            elif not tpl.html_body and tpl.plain_body:
                tpl.html_body = _plain_to_html(tpl.plain_body)
                tpl.save(update_fields=["html_body"])
                updated_count += 1

    if updated_count:
        logger.info("Updated html_body for %d existing templates.", updated_count)
    return created_count
