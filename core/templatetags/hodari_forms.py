"""
Template tags to render form fields with Hodari design-system classes.

Usage in templates:
  {% load hodari_forms %}
  {% field form.first_name %}
  {% field form.grade placeholder="Grade 1" %}
  {% field form.notes rows=4 %}
"""
from django import template, forms
from django.forms import CheckboxInput, DateInput, Select, Textarea, TextInput, TimeInput
from django.utils.html import conditional_escape, format_html, mark_safe

register = template.Library()

# Map widget type → base CSS class
_INPUT_CLASS = "hf2-input"
_SELECT_CLASS = "hf2-select"
_TEXTAREA_CLASS = "hf2-textarea"
_CHECK_CLASS = "hf2-checkbox"


def _add_attrs(field, extra_attrs: dict):
    """Return the rendered field with extra HTML attributes merged in."""
    widget = field.field.widget
    existing = widget.attrs.copy()
    existing.update(extra_attrs)
    # Persist to widget so rendering picks them up
    bound = field.as_widget(attrs=existing)
    return bound


@register.inclusion_tag("_partials/form_field.html")
def field(bound_field, placeholder=None, rows=None, label=None, hint=None, span=None):
    """
    Render a form field with full Hodari design-system styling.
    """
    widget = bound_field.field.widget
    extra = {}

    if isinstance(widget, (TextInput, DateInput, TimeInput)):
        extra["class"] = _INPUT_CLASS
        if placeholder:
            extra["placeholder"] = placeholder
        
        # FRD-UX-001: Automatic browser-native pickers for date/time fields
        if isinstance(bound_field.field, forms.DateField) or isinstance(widget, DateInput):
            extra["type"] = "date"
            extra["class"] = _INPUT_CLASS + " hf2-date"
        elif isinstance(bound_field.field, forms.TimeField) or isinstance(widget, TimeInput):
            extra["type"] = "time"
            extra["class"] = _INPUT_CLASS + " hf2-time"
        elif isinstance(bound_field.field, forms.DateTimeField):
            extra["type"] = "datetime-local"
            extra["class"] = _INPUT_CLASS + " hf2-datetime"

    elif isinstance(widget, Select):
        extra["class"] = _SELECT_CLASS

    elif isinstance(widget, Textarea):
        extra["class"] = _TEXTAREA_CLASS
        if rows:
            extra["rows"] = str(rows)

    elif isinstance(widget, CheckboxInput):
        extra["class"] = _CHECK_CLASS

    elif isinstance(widget, forms.CheckboxSelectMultiple):
        # Container widgets don't get the standard input class
        pass

    else:
        extra["class"] = _INPUT_CLASS

    rendered = bound_field.as_widget(attrs=extra)

    return {
        "field": bound_field,
        "rendered": mark_safe(rendered),
        "label": label or bound_field.label,
        "errors": bound_field.errors,
        "help_text": hint or bound_field.help_text,
        "is_checkbox": isinstance(widget, CheckboxInput),
        "is_checkbox_select": isinstance(widget, forms.CheckboxSelectMultiple),
        "is_file": isinstance(widget, forms.FileInput),
        "span": span,  # pass 'full' to span 2 cols
    }


@register.simple_tag
def field_class(bound_field):
    """Return CSS class for a given bound field (for manual use)."""
    widget = bound_field.field.widget
    if isinstance(widget, Select):
        return _SELECT_CLASS
    elif isinstance(widget, Textarea):
        return _TEXTAREA_CLASS
    elif isinstance(widget, CheckboxInput):
        return _CHECK_CLASS
    return _INPUT_CLASS

@register.filter
def get_item(dictionary, key):
    """Template filter to get a dictionary value by key."""
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def split(value, arg):
    """Split a string by delimiter."""
    return value.split(arg)

@register.filter
def at_index(list_data, index):
    """Get item at 1-based index from list or queryset."""
    try:
        # Check if list_data is a queryset or list
        if hasattr(list_data, "__getitem__"):
            return list_data[int(index) - 1]
    except (IndexError, ValueError, TypeError):
        return None
    return None

@register.filter
def get_sub(queryset, subject):
    """Filter queryset by subject."""
    if hasattr(queryset, "filter"):
        return queryset.filter(subject=subject)
    return []

@register.filter
def to_int(value):
    """Convert value to integer."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


@register.filter
def format_parent_notes(value):
    """Parse parent_form JSON notes and return formatted HTML."""
    import json
    if not value:
        return ""
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return mark_safe(f'<div style="white-space:pre-wrap">{conditional_escape(str(value))}</div>')

    via = data.get("submitted_via", "")

    if via in ("public_inquiry", "staff_form"):
        html = []
        label_map = {
            "public_inquiry": "Public Inquiry",
            "staff_form": "Staff Inquiry",
        }
        html.append(f'<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:var(--hodari-blue)">{label_map.get(via, "Inquiry")}</div>')
        fields = [
            ("Channel", data.get("submitted_via", "").replace("_", " ").title()),
            ("Gender", data.get("gender")),
            ("Current school", data.get("current_school")),
            ("Current grade", data.get("current_grade")),
        ]
        for label, val in fields:
            if val:
                html.append(f'<div style="display:flex;gap:8px;font-size:12.5px;margin-bottom:3px"><span style="color:var(--text-muted);min-width:110px">{label}</span><span>{conditional_escape(str(val))}</span></div>')
        notes = data.get("inquiry_notes")
        if notes:
            html.append(f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)"><div style="font-weight:600;font-size:12px;margin-bottom:4px;color:var(--text-primary)">Notes</div><div style="font-size:12.5px;color:var(--text-secondary);white-space:pre-wrap">{conditional_escape(notes)}</div></div>')
        return mark_safe("\n".join(html))

    if via not in ("parent_form", "parent_portal_wizard"):
        return mark_safe(f'<div style="white-space:pre-wrap">{conditional_escape(str(value))}</div>')

    html = []

    g = data.get("guardian") or {}
    if not g:
        guardians = data.get("guardians")
        if isinstance(guardians, list) and guardians:
            g = guardians[0]
    if g:
        html.append('<div style="margin-bottom:14px">')
        html.append('<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:var(--hodari-blue)">Parent / Guardian</div>')
        fields = [("Name", g.get("name")), ("Phone", g.get("phone")), ("Email", g.get("email")),
                  ("Relationship", g.get("rel")), ("National ID", g.get("nid")),
                  ("Address", g.get("address")), ("Occupation", g.get("occupation")),
                  ("Religion", g.get("religion")), ("Church", g.get("church"))]
        for label, val in fields:
            if val:
                html.append(f'<div style="display:flex;gap:8px;font-size:12.5px;margin-bottom:3px"><span style="color:var(--text-muted);min-width:90px">{label}</span><span>{conditional_escape(val)}</span></div>')
        html.append('</div>')

    children = data.get("children") or data.get("students") or []
    for i, c in enumerate(children):
        html.append('<div style="margin-bottom:14px">')
        html.append(f'<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:var(--hodari-blue)">Student {i+1}: {conditional_escape(c.get("name", ""))}</div>')
        fields = [("Grade", c.get("grade")), ("DOB", c.get("dob")), ("Gender", c.get("gender")),
                  ("Nationality", c.get("nationality")), ("Previous school", c.get("prevSchool")),
                  ("Languages", c.get("languages")), ("Health", c.get("health")),
                  ("Allergies", c.get("allergies")), ("Meds", c.get("meds")),
                  ("Hospital", c.get("hospital")), ("Breakfast", "Yes" if c.get("breakfast") else "No"),
                  ("STEM", "Yes" if c.get("stem") else "No")]
        for label, val in fields:
            if val:
                html.append(f'<div style="display:flex;gap:8px;font-size:12.5px;margin-bottom:3px"><span style="color:var(--text-muted);min-width:90px">{label}</span><span>{conditional_escape(str(val))}</span></div>')
        html.append('</div>')

    emergency = data.get("emergency", [])
    active_emergency = [e for e in emergency if e.get("name")]
    if active_emergency:
        html.append('<div style="margin-bottom:14px">')
        html.append('<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:var(--hodari-blue)">Emergency Contacts</div>')
        for e in active_emergency:
            html.append(f'<div style="font-size:12.5px;margin-bottom:3px">{conditional_escape(e.get("name", ""))} ({conditional_escape(e.get("rel", ""))}) — {conditional_escape(e.get("phone", ""))}</div>')
        html.append('</div>')

    extras = []
    additional = data.get("additional", {}) or {}
    if data.get("pledge") or additional.get("pledge"): extras.append(("Pledge", data.get("pledge") or additional.get("pledge")))
    if data.get("expelled") or additional.get("expelled"): extras.append(("Expelled", data.get("expelled") or additional.get("expelled")))
    if data.get("why") or additional.get("why"): extras.append(("Why Hodari", data.get("why") or additional.get("why")))
    if data.get("heardFrom") or additional.get("heardFrom"): extras.append(("Heard from", data.get("heardFrom") or additional.get("heardFrom")))
    if data.get("signature"): extras.append(("Signed by", data["signature"]))
    if extras:
        html.append('<div style="margin-bottom:14px">')
        html.append('<div style="font-weight:700;font-size:13px;margin-bottom:6px;color:var(--hodari-blue)">Additional Info</div>')
        for label, val in extras:
            html.append(f'<div style="display:flex;gap:8px;font-size:12.5px;margin-bottom:3px"><span style="color:var(--text-muted);min-width:90px">{label}</span><span>{conditional_escape(val)}</span></div>')
        html.append('</div>')

    consent = data.get("consent", {})
    if consent:
        consents = []
        if consent.get("core"): consents.append("Data processing")
        if consent.get("media"): consents.append("Child photos/video")
        if consent.get("mediaParent"): consents.append("Parent photos/video")
        if consents:
            html.append(f'<div style="font-size:12.5px;color:var(--text-muted)">Consent: {", ".join(consents)}</div>')

    return mark_safe("\n".join(html)) if html else f'<div style="white-space:pre-wrap">{conditional_escape(str(value))}</div>'


@register.filter(name="dict_get")
def dict_get(d, key):
    """Get a value from a dict by dynamic key: {{ mydict|dict_get:trait_key }}"""
    if isinstance(d, dict):
        return d.get(key, "")
    return ""


MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


@register.filter(name="month_name")
def month_name(value):
    """Convert month number (1-12) to name: {{ 8|month_name }} -> August"""
    try:
        return MONTH_NAMES[int(value)]
    except (ValueError, IndexError):
        return ""


@register.filter
def has_parent_submission(value):
    """Check if notes JSON was submitted by a parent via the portal."""
    import json
    if not value:
        return False
    try:
        data = json.loads(value)
        return data.get("submitted_via") in ("parent_portal_wizard", "parent_form")
    except (json.JSONDecodeError, TypeError):
        return False
