from django import forms
from core.models import SchoolSettings


def _style_fields(form):
    for name, field in form.fields.items():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs["class"] = "hf-checkbox"
        elif isinstance(field.widget, (forms.Select, forms.SelectMultiple)):
            field.widget.attrs["class"] = "hf-select mt-1"
        else:
            field.widget.attrs["class"] = "hf-input mt-1"


class SchoolSettingsSystemForm(forms.ModelForm):
    class Meta:
        model = SchoolSettings
        fields = [
            "school_name",
            "attendance_threshold_warn",
            "attendance_threshold_critical",
            "admission_fee",
            "assessment_fee",
            "enable_online_inquiry",
            "send_absentee_sms",
            "sms_sender_id",
            "pass_mark",
            "enable_auto_report_generation",
            "pwa_domain",
            "pwa_version",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)


class SchoolSettingsMessagingForm(forms.ModelForm):
    class Meta:
        model = SchoolSettings
        fields = [
            "email_backend",
            "email_host",
            "email_port",
            "email_use_tls",
            "email_host_user",
            "email_host_password",
            "default_from_email",
            "whatsapp_sender_id",
            "admissions_phone",
            "admissions_email",
            "admissions_whatsapp",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)


class SchoolSettingsForm(forms.ModelForm):
    class Meta:
        model = SchoolSettings
        fields = [
            "school_name",
            "attendance_threshold_warn",
            "attendance_threshold_critical",
            "admission_fee",
            "enable_online_inquiry",
            "send_absentee_sms",
            "sms_sender_id",
            "pass_mark",
            "enable_auto_report_generation",
            # Email
            "email_backend",
            "email_host",
            "email_port",
            "email_use_tls",
            "email_host_user",
            "email_host_password",
            "default_from_email",
            # WhatsApp
            "whatsapp_sender_id",
            # PWA
            "pwa_domain",
            "pwa_version",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self)
