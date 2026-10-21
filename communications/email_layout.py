"""
Branded HTML layout for every email the system sends.

Alerts and notifications are written as plain text all over the codebase
(``dispatch_notification(title=..., message=...)``). This module turns that
text into a school-branded, mobile-friendly HTML email so that nothing leaves
the system as an unstyled wall of text:

  - ``render_branded_email()`` builds the HTML version.
  - ``render_plain_email()`` builds a tidy plain-text version with signature.
  - ``looks_like_html()`` tells whether a caller already supplied real HTML.

Message text is formatted, not just escaped:
  - blank lines separate paragraphs, single newlines are kept as line breaks;
  - blocks of ``Label: value`` lines become a details table;
  - lines starting with ``-``, ``*`` or ``•`` become a bullet list;
  - a trailing ``View details: <url>`` style line becomes a button;
  - bare URLs become links.

Email clients ignore <style> blocks and most modern CSS, so everything is
table-based with inline styles.
"""

import html
import re
from datetime import date

from django.conf import settings

# ── Palette ───────────────────────────────────────────────────────────
NAVY = "#0B2559"
BLUE = "#023AA5"
GOLD = "#C8A951"
INK = "#1F2937"
MUTED = "#6B7280"
LINE = "#E5E7EB"
CANVAS = "#EEF1F6"
SOFT = "#F7F8FB"

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',Times,serif"

# Section label shown above the title, by the first segment of the link.
_SECTION_BY_PATH = {
    "admissions": "Admissions",
    "attendance": "Attendance",
    "finance": "Finance",
    "academics": "Academics",
    "students": "Student Records",
    "welfare": "Student Welfare",
    "discipline": "Student Conduct",
    "hr": "Human Resources",
    "events": "School Events",
    "ptc": "Parent–Teacher Conference",
    "timetable": "Timetable",
    "reports": "Reports",
    "tasks": "Tasks",
    "communications": "Announcements",
    "parent": "Parent Portal",
    "parent-portal": "Parent Portal",
    "parent_portal": "Parent Portal",
}

_CTA_LINE = re.compile(
    r"^\s*(view|open|review|see|click|access|log ?in|sign ?in|pay|complete|track)[^:\n]{0,40}:\s*(\S+)\s*$",
    re.IGNORECASE,
)
_KV_LINE = re.compile(r"^\s*([A-Za-z][^:\n]{0,40}?)\s*:\s+(\S.*)$")
_BULLET_LINE = re.compile(r"^\s*(?:[-*•–])\s+(.*)$")
_URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]])")
_HTML_TAG = re.compile(r"<\s*(html|body|table|div|p|br|a|strong|b|em|span|h[1-6]|ul|ol|li|img)\b", re.IGNORECASE)


# ── Branding ──────────────────────────────────────────────────────────

def school_branding():
    """Name, contacts and absolute URLs used in email headers and footers."""
    from core.context_processors import BRANDING_DEFAULTS as d

    site_url = getattr(settings, "SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    static_url = getattr(settings, "STATIC_URL", "/static/")
    if not static_url.startswith(("/", "http")):
        static_url = "/" + static_url
    if not static_url.startswith("http"):
        static_url = site_url + static_url
    data = {
        "school_name": d["school_name"],
        "tagline": d["tagline"],
        "address": d["address"],
        "email": d["email"],
        "phone": d["phone"],
        "website": d["website"],
        "site_url": site_url,
        "logo_url": static_url.rstrip("/") + "/branding/hodari-logo-white.png",
    }
    try:
        from core.models import SchoolSettings
        s = SchoolSettings.get_settings()
        data["school_name"] = s.school_name or data["school_name"]
        if s.login_tagline:
            data["tagline"] = s.login_tagline
    except Exception:
        pass
    return data


def absolute_url(link, site_url=None):
    if not link:
        return ""
    if link.startswith(("http://", "https://", "mailto:")):
        return link
    site_url = (site_url or school_branding()["site_url"]).rstrip("/")
    return site_url + ("" if link.startswith("/") else "/") + link


def section_for_link(link):
    """'Attendance' for '/attendance/...', or '' when the path isn't known."""
    if not link:
        return ""
    path = re.sub(r"^https?://[^/]+", "", link).strip("/")
    first = path.split("/", 1)[0].lower()
    return _SECTION_BY_PATH.get(first, "")


def looks_like_html(text):
    return bool(text) and bool(_HTML_TAG.search(text))


# ── Message → HTML ────────────────────────────────────────────────────

def _inline(text):
    """Escape a line and turn bare URLs into links."""
    escaped = html.escape(text)
    return _URL.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:{BLUE};text-decoration:underline">{m.group(1)}</a>',
        escaped,
    )


def _paragraph(lines):
    body = "<br>".join(_inline(l.strip()) for l in lines)
    return (
        f'<p style="margin:0 0 18px;color:{INK};font-family:{SANS};font-size:15px;line-height:1.65">'
        f"{body}</p>"
    )


def _bullets(items):
    rows = "".join(
        '<tr>'
        f'<td width="18" valign="top" style="padding:0 0 8px;color:{GOLD};font-size:15px;line-height:1.6">&#9670;</td>'
        f'<td style="padding:0 0 8px;color:{INK};font-family:{SANS};font-size:15px;line-height:1.6">{_inline(i)}</td>'
        '</tr>'
        for i in items
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px">{rows}</table>'


def details_table(rows):
    """rows: [(label, value_html), ...]  values must already be escaped."""
    trs = []
    for i, (label, value) in enumerate(rows):
        border = f"border-top:1px solid {LINE};" if i else ""
        trs.append(
            "<tr>"
            f'<td valign="top" style="{border}padding:12px 16px;width:38%;color:{MUTED};font-family:{SANS};'
            f'font-size:12px;letter-spacing:.6px;text-transform:uppercase;font-weight:600">{label}</td>'
            f'<td valign="top" style="{border}padding:12px 16px;color:{INK};font-family:{SANS};'
            f'font-size:15px;font-weight:600;line-height:1.5">{value}</td>'
            "</tr>"
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:0 0 22px;background:{SOFT};border:1px solid {LINE};border-left:3px solid {GOLD};'
        f'border-radius:8px;border-collapse:separate">{"".join(trs)}</table>'
    )


def button(url, text):
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 26px">'
        f'<tr><td align="center" bgcolor="{BLUE}" style="border-radius:8px;background:{BLUE}">'
        f'<a href="{html.escape(url, quote=True)}" target="_blank" style="display:inline-block;padding:14px 30px;'
        f'font-family:{SANS};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;'
        f'border-radius:8px;letter-spacing:.2px">{html.escape(text)} &rarr;</a>'
        "</td></tr></table>"
    )


def format_message_html(message, site_url=None):
    """Return (body_html, cta_url) for a plain-text message.

    A last line like ``View details: /attendance/`` is lifted out as the
    button link instead of being shown as text.
    """
    lines = (message or "").replace("\r\n", "\n").strip().split("\n")
    cta_url = ""
    if lines:
        m = _CTA_LINE.match(lines[-1])
        if m and (m.group(2).startswith(("http://", "https://", "/"))):
            cta_url = absolute_url(m.group(2), site_url)
            lines = lines[:-1]

    blocks, current = [], []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    out = []
    for block in blocks:
        kv = [_KV_LINE.match(l) for l in block]
        bullets = [_BULLET_LINE.match(l) for l in block]
        if all(bullets):
            out.append(_bullets([b.group(1) for b in bullets]))
        elif len(block) > 1 and all(kv):
            out.append(details_table([(html.escape(k.group(1)), _inline(k.group(2))) for k in kv]))
        elif len(block) > 1 and not kv[0] and all(kv[1:]):
            # "Intro sentence:" followed by Label: value lines.
            out.append(_paragraph(block[:1]))
            out.append(details_table([(html.escape(k.group(1)), _inline(k.group(2))) for k in kv[1:]]))
        elif len(block) > 1 and not bullets[0] and all(bullets[1:]):
            out.append(_paragraph(block[:1]))
            out.append(_bullets([b.group(1) for b in bullets[1:]]))
        else:
            out.append(_paragraph(block))
    return "".join(out), cta_url


# ── Full documents ────────────────────────────────────────────────────

def wrap_html(inner_html, *, title="", section="", preheader="", footer_note="", brand=None,
              signature=True, year=None):
    """Put already-built HTML inside the school's header and footer."""
    b = brand or school_branding()
    school = html.escape(b["school_name"])
    year = year or date.today().year
    eyebrow_html = (
        f'<div style="margin:0 0 10px;font-family:{SANS};font-size:11px;font-weight:700;letter-spacing:1.8px;'
        f'text-transform:uppercase;color:{GOLD}">{html.escape(section)}</div>'
        if section else ""
    )
    title_html = (
        f'<h1 style="margin:0 0 22px;font-family:{SERIF};font-size:26px;line-height:1.3;'
        f'font-weight:normal;color:{NAVY}">{html.escape(title)}</h1>'
        if title else ""
    )
    note = html.escape(
        footer_note
        or f"You are receiving this email because you have an account with {b['school_name']}. "
        "This is an automated message: please do not reply directly."
    ).replace("\n", "<br>")
    signature_html = (
        f'<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:0 36px 32px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="border-top:1px solid {LINE};padding-top:20px;font-family:{SANS};font-size:14px;line-height:1.6;color:{MUTED}">'
        f'Warm regards,<br><span style="font-family:{SERIF};font-size:16px;color:{NAVY}">{school}</span>'
        f'</td></tr></table></td></tr>'
        if signature else
        '<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:0 0 20px;font-size:0;line-height:0">&nbsp;</td></tr>'
    )
    website = html.escape(b["website"])
    website_href = website if website.startswith("http") else f"https://{website}"
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{html.escape(title or b["school_name"])}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};font-family:{SANS};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">{html.escape(preheader)}&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{CANVAS}" style="background:{CANVAS}">
<tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px">

<tr><td bgcolor="{NAVY}" style="background:{NAVY};background-image:linear-gradient(135deg,{NAVY} 0%,{BLUE} 100%);border-radius:14px 14px 0 0;padding:34px 32px 28px;text-align:center">
  <img src="{html.escape(b["logo_url"], quote=True)}" alt="{school}" height="84" style="display:block;margin:0 auto 14px;height:84px;width:auto;border:0;outline:none">
  <div style="font-family:{SERIF};font-size:21px;line-height:1.3;color:#ffffff;letter-spacing:.3px">{school}</div>
  <div style="margin-top:6px;font-family:{SANS};font-size:11px;letter-spacing:2.4px;text-transform:uppercase;color:{GOLD}">{html.escape(b["tagline"])}</div>
</td></tr>
<tr><td height="4" bgcolor="{GOLD}" style="background:{GOLD};font-size:0;line-height:0">&nbsp;</td></tr>

<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:36px 36px 12px;font-family:{SANS};color:{INK}">
  {eyebrow_html}
  {title_html}
  {inner_html}
</td></tr>

{signature_html}

<tr><td bgcolor="{SOFT}" style="background:{SOFT};border-radius:0 0 14px 14px;border-top:1px solid {LINE};padding:24px 32px;text-align:center;font-family:{SANS}">
  <div style="font-size:13px;font-weight:600;color:{NAVY};margin-bottom:6px">{school}</div>
  <div style="font-size:12px;line-height:1.6;color:{MUTED}">{html.escape(b["address"])}</div>
  <div style="font-size:12px;line-height:1.6;color:{MUTED};margin-top:4px">
    {html.escape(b["phone"])} &nbsp;&middot;&nbsp;
    <a href="mailto:{html.escape(b["email"], quote=True)}" style="color:{BLUE};text-decoration:none">{html.escape(b["email"])}</a> &nbsp;&middot;&nbsp;
    <a href="{website_href}" style="color:{BLUE};text-decoration:none">{website}</a>
  </div>
  <div style="font-size:11px;line-height:1.6;color:#9CA3AF;margin-top:14px">{note}</div>
  <div style="font-size:11px;color:#9CA3AF;margin-top:6px">&copy; {year} {school}. All rights reserved.</div>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _first_name(recipient):
    if recipient is None:
        return ""
    if isinstance(recipient, str):
        return recipient.strip()
    name = (getattr(recipient, "first_name", "") or "").strip()
    if not name and hasattr(recipient, "get_full_name"):
        name = (recipient.get_full_name() or "").strip()
    return name


def render_branded_email(title, message, link=None, *, recipient=None, section=None,
                         cta_text="View details", footer_note=""):
    """Full branded HTML email for a plain-text ``message``.

    ``recipient`` may be a User (its first name is used) or a name string.
    ``link`` may be relative (``/attendance/``); it is made absolute.
    """
    b = school_branding()
    body_html, cta_from_text = format_message_html(message, b["site_url"])
    cta_url = absolute_url(link, b["site_url"]) if link else cta_from_text
    name = _first_name(recipient)
    greeting = (
        f'<p style="margin:0 0 18px;color:{INK};font-family:{SANS};font-size:15px;line-height:1.65">'
        f"Dear {html.escape(name)},</p>"
        if name else ""
    )
    inner = greeting + body_html + (button(cta_url, cta_text) if cta_url else "")
    preheader = " ".join((message or "").split())[:140]
    return wrap_html(
        inner,
        title=title,
        section=section or section_for_link(link or cta_url) or "Notification",
        preheader=preheader,
        footer_note=footer_note,
        brand=b,
    )


def render_plain_email(message, link=None, *, recipient=None, cta_text="View details"):
    """Plain-text alternative with greeting, absolute link and signature."""
    b = school_branding()
    parts = []
    name = _first_name(recipient)
    if name:
        parts.append(f"Dear {name},")
    parts.append((message or "").strip())
    if link:
        parts.append(f"{cta_text}: {absolute_url(link, b['site_url'])}")
    parts.append(f"Warm regards,\n{b['school_name']}")
    parts.append(f"{b['address']}\n{b['phone']} | {b['email']} | {b['website']}")
    return "\n\n".join(p for p in parts if p)
