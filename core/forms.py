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

    SMTP = "django.core.mail.backends.smtp.EmailBackend"
    OFF = "django.core.mail.backends.console.EmailBackend"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = (self.instance.email_backend or "") if self.instance else ""
        self.fields["email_backend"] = forms.ChoiceField(
            label="Email sending",
            choices=[(self.SMTP, "On: send real email through SMTP"),
                     (self.OFF, "Off: don't send (messages only appear in the server log)")],
            initial=self.SMTP if "smtp" in current.lower() else self.OFF,
        )
        # Never echo the stored password back into the page; blank keeps it.
        self.fields["email_host_password"].widget = forms.PasswordInput(
            render_value=False, attrs={"autocomplete": "new-password",
                                       "placeholder": "Saved. Leave blank to keep it" if self.instance and self.instance.email_host_password else ""})
        self.fields["email_host_password"].required = False
        _style_fields(self)

    def clean_email_host_password(self):
        pw = self.cleaned_data.get("email_host_password") or ""
        return pw or (self.instance.email_host_password if self.instance else "")

    def clean(self):
        data = super().clean()
        if data.get("email_backend") == self.SMTP:
            for f, label in (("email_host", "SMTP server"), ("email_host_user", "SMTP username")):
                if not data.get(f):
                    self.add_error(f, f"{label} is required to send email.")
            if not data.get("email_host_password"):
                self.add_error("email_host_password", "SMTP password is required to send email.")
        return data


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
