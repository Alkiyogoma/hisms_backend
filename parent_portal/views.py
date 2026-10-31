"""
Parent portal (v1): Home, learner progress and the school calendar.

Parents see only their own active children (parent_portal.services.children_for);
every page, endpoint and download looks the learner up through it, so changing
an id in the URL gives a 404. Marks come from the report card calculation and
attendance from the approved attendance rules, both via parent_portal.services.
Welfare, discipline and finance are not part of v1.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from core.permissions import RoleRequiredMixin
from parent_portal import services
from students.models import StudentGuardian, ParentGuardian, GuardianRelationship
from users.models import User, UserRole


def _get_parent_guardian(user):
    """Return the ParentGuardian instance linked to this user, or None."""
    return ParentGuardian.objects.filter(user=user).first()


def _get_children(user):
    """All active learners linked to this parent."""
    return services.children_for(user)


def _base_ctx(user):
    """Shared context for the parent pages that still use the staff layout."""
    return {
        "initials": (user.first_name[0] + user.last_name[0]).upper() if user.first_name and user.last_name else user.username[:2].upper(),
        "today_display": timezone.localtime().strftime("%A, %B %d, %Y"),
        "greeting": services.greeting(),
    }


class ParentOnlyMixin(RoleRequiredMixin):
    """Parent portal pages are Parent/Guardian-role pages.

    Super Admin is also allowed (for testing/support purposes); they have no
    linked learners, so they see the empty states.
    """

    allowed_roles = [UserRole.PARENT, UserRole.SUPER_ADMIN]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role not in (UserRole.PARENT, UserRole.SUPER_ADMIN):
            raise PermissionDenied("Parent portal access is restricted to the Parent role.")
        return super().dispatch(request, *args, **kwargs)


def _valid_relationship(value):
    """Return a valid GuardianRelationship value, falling back to OTHER."""
    valid = {val for val, _ in GuardianRelationship.choices}
    return value if value in valid else GuardianRelationship.OTHER


def _portal_ctx(request, active):
    from core.models import SchoolSettings
    settings_obj = SchoolSettings.get_settings()
    return {
        "portal_nav": active,
        "school_name": settings_obj.school_name or "Hodari Christian School",
        "school_phone": settings_obj.get_parent_contact_phone(),
    }


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

class ParentDashboardView(ParentOnlyMixin, TemplateView):
    """Home: greeting, current term context and one card per learner. No marks."""
    template_name = "parent_portal/v1/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        user = self.request.user
        children = list(services.children_for(user))
        term = services.current_term()
        guardian = _get_parent_guardian(user)
        name = (user.first_name or (guardian.full_name.split()[0] if guardian and guardian.full_name else "")
                or user.get_username())
        today_map = services.today_statuses(children, today)
        ctx.update(_portal_ctx(self.request, "home"))
        ctx.update({
            "today": today,
            "greeting": services.greeting(),
            "parent_name": name,
            "term_info": services.term_context(term, today),
            "learners": [{"student": c, "today": today_map[c.pk]} for c in children],
        })
        return ctx


RING_CIRCUMFERENCE = 251.33  # 2 * pi * r for the r=40 ring in includes/ring.html


def _ring(value):
    """SVG ring geometry for a percentage (None draws an empty ring)."""
    pct = max(0.0, min(100.0, float(value))) if value is not None else 0.0
    return {"value": value, "dash": RING_CIRCUMFERENCE,
            "offset": round(RING_CIRCUMFERENCE * (1 - pct / 100), 2)}


# ---------------------------------------------------------------------------
# Learner progress
# ---------------------------------------------------------------------------

class ParentLearnerProgressView(ParentOnlyMixin, TemplateView):
    """One learner: rings, today, assessment charts, subject marks, published
    report cards and attendance."""
    template_name = "parent_portal/v1/progress.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        student = services.child_or_404(self.request.user, kwargs["student_id"])
        term = services.current_term()
        academic = services.academic_progress(student, term, today)
        month = services.parse_month(self.request.GET.get("month"), today)
        ctx.update(_portal_ctx(self.request, "progress"))
        ctx.update({
            "today": today,
            "student": student,
            "term": term,
            "academic": academic,
            "average_ring": _ring(academic["average"] if academic else None),
            "attendance_ring": _ring(services.term_attendance_rate(student, term, today)),
            "today_info": services.today_status(student, today),
            "month": services.month_attendance(student, month, today),
            "reports": services.published_reports(student),
        })
        return ctx


# ---------------------------------------------------------------------------
# Report cards (published only)
# ---------------------------------------------------------------------------

class ParentReportCardView(ParentOnlyMixin, TemplateView):
    """A published report card of the parent's own child, in the school's
    report layout."""

    def get_template_names(self):
        return ["academics/ecd_report_preview.html" if self.report.is_ecd_report
                else "academics/report_card_print.html"]

    def get(self, request, *args, **kwargs):
        self.report = services.published_report_or_404(request.user, kwargs["student_id"], kwargs["report_id"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from academics.views.reports import _build_report_card_context
        ctx = super().get_context_data(**kwargs)
        ctx.update(_build_report_card_context(self.report))
        ctx["back_url"] = reverse("parent_portal:progress", args=[self.report.student_id]) + "#report-cards"
        ctx["download_url"] = reverse("parent_portal:report_download",
                                      args=[self.report.student_id, self.report.pk])
        return ctx


class ParentReportCardDownloadView(ParentOnlyMixin, View):
    """PDF of a published report card of the parent's own child."""

    def get(self, request, *args, **kwargs):
        from academics.views.reports import render_report_pdf_response
        report = services.published_report_or_404(request.user, kwargs["student_id"], kwargs["report_id"])
        return render_report_pdf_response(request, report)


# ---------------------------------------------------------------------------
# School calendar (school-wide, no learner data)
# ---------------------------------------------------------------------------

class ParentCalendarView(ParentOnlyMixin, TemplateView):
    template_name = "parent_portal/v1/calendar.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        ctx.update(_portal_ctx(self.request, "calendar"))
        ctx.update({"today": today, "event_groups": services.school_events(today)})
        return ctx


# ---------------------------------------------------------------------------
# Earlier portal pages: kept as redirects so old links and emails still work.
# ---------------------------------------------------------------------------

class LegacyRedirectView(ParentOnlyMixin, View):
    """Send an old portal URL to its v1 page. A learner id goes through the
    ownership check on the progress page itself."""

    def get(self, request, *args, **kwargs):
        student_id = kwargs.get("student_id") or request.GET.get("student")
        if student_id and str(student_id).isdigit():
            return redirect("parent_portal:progress", student_id=int(student_id))
        return redirect("parent_portal:dashboard")



# ---------------------------------------------------------------------------
# Guardians linked to a child
# ---------------------------------------------------------------------------

class ParentGuardianListView(ParentOnlyMixin, TemplateView):
    """List all guardians linked to a specific child."""
    allowed_roles = [UserRole.PARENT, UserRole.SUPER_ADMIN]
    template_name = "parent_portal/child_guardians.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_base_ctx(self.request.user))

        student_id = self.kwargs["student_id"]
        children = _get_children(self.request.user)
        student = children.filter(id=student_id).first()
        if not student:
            ctx["error"] = "Student not found or not linked to your account."
            ctx["children"] = children
            return ctx

        ctx["student"] = student
        ctx["guardian_links"] = StudentGuardian.objects.filter(
            student=student
        ).select_related("guardian").order_by("-is_primary", "guardian__full_name")
        return ctx


class ParentGuardianLinkView(ParentOnlyMixin, TemplateView):
    """Link an additional guardian (next of kin) to a child."""
    allowed_roles = [UserRole.PARENT, UserRole.SUPER_ADMIN]
    template_name = "parent_portal/child_guardian_link.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_base_ctx(self.request.user))

        student_id = self.kwargs["student_id"]
        children = _get_children(self.request.user)
        student = children.filter(id=student_id).first()
        if not student:
            ctx["error"] = "Student not found or not linked to your account."
            ctx["children"] = children
            return ctx

        ctx["student"] = student
        ctx["relationship_choices"] = GuardianRelationship.choices
        return ctx

    def post(self, request, *args, **kwargs):
        student_id = self.kwargs["student_id"]
        children = _get_children(request.user)
        student = children.filter(id=student_id).first()
        if not student:
            messages.error(request, "Student not found.")
            return redirect("parent_portal:dashboard")

        action = request.POST.get("action")

        if action == "request_guardian":
            guardian_name = request.POST.get("guardian_name", "").strip()
            guardian_phone = request.POST.get("guardian_phone", "").strip()
            relationship = _valid_relationship(request.POST.get("relationship", "other"))
            notes = request.POST.get("notes", "").strip()

            if not guardian_name or not guardian_phone:
                messages.error(request, "Parent name and phone are required.")
                return redirect("parent_portal:guardian_link", student_id=student_id)

            from users.models import User, UserRole
            from communications.models import Notification

            admin_users = User.objects.filter(role__in=[UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER], is_active=True)
            parent_name = request.user.get_full_name() or request.user.username
            rel_display = dict(GuardianRelationship.choices).get(relationship, relationship)

            notif_title = "Parent Link Request"
            notif_body = (
                f"{parent_name} has requested a new parent be created and linked to "
                f"{student.first_name} {student.last_name}.\n\n"
                f"Parent Name: {guardian_name}\n"
                f"Phone: {guardian_phone}\n"
                f"Relationship: {rel_display}\n"
            )
            if notes:
                notif_body += f"Notes: {notes}\n"
            notif_body += f"\nPlease create the parent record and link them to the student."

            for admin in admin_users:
                Notification.objects.create(
                    recipient=admin,
                    category="system",
                    title=notif_title,
                    body=notif_body,
                    link=f"/students/guardians/",
                )

            messages.success(request, f"Request sent to the school office. They will create and link {guardian_name} shortly.")
            return redirect("parent_portal:guardian_list", student_id=student_id)

        guardian_id = request.POST.get("guardian_id")
        relationship = _valid_relationship(request.POST.get("relationship", "other"))

        if not guardian_id:
            messages.error(request, "Please select a parent to link.")
            return redirect("parent_portal:guardian_link", student_id=student_id)

        # IDOR guard: only allow linking guardians already associated with the
        # requesting parent's own family (their children or their own record),
        # never an arbitrary ParentGuardian PK from the whole system.
        scoped_pks = set(
            StudentGuardian.objects.filter(student__in=children).values_list("guardian_id", flat=True)
        )
        parent_guardian = _get_parent_guardian(request.user)
        if parent_guardian:
            scoped_pks.add(parent_guardian.pk)
        guardian = ParentGuardian.objects.filter(pk=guardian_id, pk__in=scoped_pks).first()
        if not guardian:
            messages.error(request, "Parent not found or not linked to your family.")
            return redirect("parent_portal:guardian_link", student_id=student_id)

        if StudentGuardian.objects.filter(student=student, guardian=guardian).exists():
            messages.warning(request, f"{guardian.full_name} is already linked to {student.first_name}.")
            return redirect("parent_portal:guardian_list", student_id=student_id)

        StudentGuardian.objects.create(
            student=student,
            guardian=guardian,
            relationship=relationship,
            is_primary=False,
        )
        messages.success(request, f"{guardian.full_name} linked to {student.first_name} as {relationship}.")
        return redirect("parent_portal:guardian_list", student_id=student_id)


class ParentGuardianUnlinkView(ParentOnlyMixin, View):
    """Remove a guardian link from a child. Parents cannot remove the primary guardian."""
    allowed_roles = [UserRole.PARENT, UserRole.SUPER_ADMIN]

    def post(self, request, *args, **kwargs):
        student_id = self.kwargs["student_id"]
        sg_pk = self.kwargs["sg_pk"]
        children = _get_children(request.user)
        student = children.filter(id=student_id).first()
        if not student:
            messages.error(request, "Student not found.")
            return redirect("parent_portal:dashboard")

        sg = get_object_or_404(StudentGuardian, pk=sg_pk, student=student)

        if sg.is_primary:
            messages.error(request, "Cannot remove the primary parent.")
            return redirect("parent_portal:guardian_list", student_id=student_id)

        guardian_name = sg.guardian.full_name
        sg.delete()
        messages.success(request, f"{guardian_name} unlinked from {student.first_name}.")
        return redirect("parent_portal:guardian_list", student_id=student_id)


# ── Parent Admission Form + Invoice Generation (Spec Step 10) ──────────────

class ParentAdmissionFormView(ParentOnlyMixin, TemplateView):
    """Parent fills admission form after receiving an offer (step 10).

    Accessible when applicant status is ADMITTED or CONDITIONAL.
    On submit, transitions applicant to FORM_SUBMITTED.
    """
    template_name = "parent_portal/admission_form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in (UserRole.PARENT, UserRole.SUPER_ADMIN):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def _get_applicant(self):
        """Find the applicant linked to this parent by email match."""
        from admissions.models import Applicant, ApplicantStatus
        user_email = (self.request.user.email or "").strip().lower()
        if not user_email:
            return None
        return Applicant.objects.filter(
            parent_email__iexact=user_email,
            status__in=[
                ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL,
                ApplicantStatus.FORM_SUBMITTED, ApplicantStatus.INVOICE_GENERATED,
                ApplicantStatus.INVOICE_PAID, ApplicantStatus.ENROLLED,
            ],
        ).order_by("-created_at").first()

    def get_context_data(self, **kwargs):
        import json as _json
        from admissions.models import ApplicantStatus
        ctx = super().get_context_data(**kwargs)
        ctx.update(_base_ctx(self.request.user))
        applicant = self._get_applicant()
        if not applicant:
            ctx["no_applicant"] = True
            return ctx
        ctx["applicant"] = applicant
        from admissions.fees import form_fee_context
        ctx.update(form_fee_context(applicant))
        if applicant.status in (ApplicantStatus.FORM_SUBMITTED, ApplicantStatus.INVOICE_GENERATED,
                                ApplicantStatus.INVOICE_PAID, ApplicantStatus.ENROLLED):
            ctx["form_submitted"] = True
        try:
            ctx["saved_form_data"] = _json.loads(applicant.notes or "{}")
        except (_json.JSONDecodeError, ValueError):
            ctx["saved_form_data"] = {}
        return ctx

    def post(self, request, *args, **kwargs):
        import json
        from django.http import JsonResponse
        from admissions.models import Applicant, ApplicantStatus
        from admissions.services import transition_applicant_status
        from django.contrib import messages as django_messages

        applicant = self._get_applicant()
        if not applicant:
            if request.headers.get("Accept") == "application/json" or request.POST.get("action") == "submit_admission":
                return JsonResponse({"ok": False, "error": "No active admission application found for your account."}, status=404)
            django_messages.error(request, "No active admission application found for your account.")
            return redirect("parent_portal:dashboard")

        if applicant.status not in [ApplicantStatus.ADMITTED, ApplicantStatus.CONDITIONAL]:
            if request.POST.get("action") == "submit_admission":
                return JsonResponse({"ok": False, "error": "This application is no longer at the admission form stage."}, status=400)
            django_messages.warning(request, "This application is no longer at the admission form stage.")
            return redirect("parent_portal:dashboard")

        action = request.POST.get("action", "")

        if action == "submit_admission":
            raw = request.POST.get("data", "{}")
            try:
                form_data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return JsonResponse({"ok": False, "error": "Invalid form data."}, status=400)

            notes_data = {
                "submitted_via": "parent_portal_wizard",
                "children": form_data.get("children", []),
                "guardians": form_data.get("guardians", []),
                "emergency": form_data.get("emergency", []),
                "siblings": form_data.get("siblings", ""),
                "heardFrom": form_data.get("heardFrom", ""),
                "pledge": form_data.get("pledge", ""),
                "expelled": form_data.get("expelled", ""),
                "why": form_data.get("why", ""),
                "summary": form_data.get("summary", ""),
                "ack": form_data.get("ack", {}),
                "consent": form_data.get("consent", {}),
                "signature": form_data.get("signature", ""),
                "signDate": form_data.get("signDate", ""),
            }

            applicant.notes = json.dumps(notes_data)
            applicant.save(update_fields=["notes", "updated_at"])

            _file_map = {
                "birth": "birth_certificate",
                "clearance": "clearance_form",
            }
            from admissions.models import ApplicantDocumentReceipt
            from admissions.services import ensure_default_documents
            ensure_default_documents(applicant)
            for field_key, doc_type in _file_map.items():
                uploaded = request.FILES.get("file_" + field_key)
                if uploaded:
                    from academics.validators import validate_attachment_file
                    try:
                        validate_attachment_file(uploaded, area="admission_documents")
                    except Exception:
                        pass
                    receipt, created = ApplicantDocumentReceipt.objects.get_or_create(
                        applicant=applicant, document_type=doc_type,
                        defaults={"is_received": True, "received_by": request.user},
                    )
                    if not created:
                        receipt.is_received = True
                        receipt.received_by = request.user
                    receipt.file = uploaded
                    receipt.received_at = timezone.now()
                    receipt.save(update_fields=["file", "is_received", "received_by", "received_at"])

            admission_form_receipt, _ = ApplicantDocumentReceipt.objects.get_or_create(
                applicant=applicant, document_type="admission_form",
                defaults={"is_received": True, "received_by": request.user, "received_at": timezone.now()},
            )
            if not admission_form_receipt.is_received:
                admission_form_receipt.is_received = True
                admission_form_receipt.received_by = request.user
                admission_form_receipt.received_at = timezone.now()
                admission_form_receipt.save(update_fields=["is_received", "received_by", "received_at"])

            try:
                from users.models import User as _User, UserRole as _UR
                _system_actor = _User.objects.filter(role=_UR.SUPER_ADMIN, is_active=True).first() or request.user
                transition_applicant_status(
                    applicant=applicant,
                    to_status=ApplicantStatus.FORM_SUBMITTED,
                    actor=_system_actor,
                    reason="Parent completed admission form online.",
                )
            except Exception:
                pass

            from admissions.fees import admission_invoice_for, invoice_summary
            return JsonResponse({
                "ok": True,
                "ref": applicant.reference_number or f"ADM-{applicant.id:04d}",
                "invoice": invoice_summary(admission_invoice_for(applicant)),
                "message": "Admission form submitted successfully.",
            })

        pdpa_consent = request.POST.get("pdpa_consent") == "on"
        code_of_conduct = request.POST.get("code_of_conduct") == "on"

        if not pdpa_consent:
            django_messages.error(request, "You must provide data consent to proceed.")
            return self.get(request, *args, **kwargs)

        if not code_of_conduct:
            django_messages.error(request, "You must accept the code of conduct to proceed.")
            return self.get(request, *args, **kwargs)

        notes_data = {
            "submitted_via": "parent_portal_form",
            "student": {
                "name": request.POST.get("student_name", ""),
                "dob": request.POST.get("student_dob", ""),
                "nationality": request.POST.get("student_nationality", ""),
                "religion": request.POST.get("student_religion", ""),
                "languages": request.POST.get("student_languages", ""),
            },
            "health": {
                "state": request.POST.get("health_state", ""),
                "allergies": request.POST.get("allergies", ""),
                "medications": request.POST.get("medications", ""),
                "special_needs": request.POST.get("special_needs", ""),
                "hospital": request.POST.get("hospital", ""),
                "physician": request.POST.get("physician", ""),
                "insurance": request.POST.get("insurance", ""),
            },
            "emergency": [
                {"name": request.POST.get("emergency1_name", ""), "rel": request.POST.get("emergency1_rel", ""), "phone": request.POST.get("emergency1_phone", "")},
                {"name": request.POST.get("emergency2_name", ""), "rel": request.POST.get("emergency2_rel", ""), "phone": request.POST.get("emergency2_phone", "")},
            ],
            "uniform": {
                "size": request.POST.get("uniform_size", ""),
                "measurements": request.POST.get("uniform_measurements", ""),
            },
            "meal_choice": request.POST.get("meal_choice", "pack"),
            "transport_needed": request.POST.get("transport_needed", "no"),
            "consent": {
                "pdpa": True,
                "media": request.POST.get("media_consent") == "on",
                "code_of_conduct": True,
            },
        }

        applicant.notes = json.dumps(notes_data)
        applicant.save(update_fields=["notes", "updated_at"])

        try:
            transition_applicant_status(
                applicant=applicant,
                to_status=ApplicantStatus.FORM_SUBMITTED,
                actor=request.user,
                reason="Parent completed admission form online.",
            )
            django_messages.success(request, "Admission form submitted successfully. You can now generate your invoice.")
        except Exception as e:
            django_messages.error(request, f"Error submitting form: {e}")

        return redirect("parent_portal:admission_form")


class ParentGenerateInvoiceView(ParentOnlyMixin, View):
    """Generate admission invoice after parent submits the form (step 10).

    Creates the ADM- invoice and fires E18 to finance.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role not in (UserRole.PARENT, UserRole.SUPER_ADMIN):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def _get_applicant(self):
        from admissions.models import Applicant, ApplicantStatus
        user_email = (self.request.user.email or "").strip().lower()
        if not user_email:
            return None
        return Applicant.objects.filter(
            parent_email__iexact=user_email,
            status=ApplicantStatus.FORM_SUBMITTED,
        ).order_by("-created_at").first()

    def post(self, request, *args, **kwargs):
        from admissions.services import generate_admission_invoice
        from communications.email_service import send_email_safe
        from core.email_templates import send_dynamic_email
        from core.models import SchoolSettings
        from django.contrib import messages as django_messages

        applicant = self._get_applicant()
        if not applicant:
            django_messages.error(request, "No admission form found that is ready for invoicing.")
            return redirect("parent_portal:dashboard")

        # Parents cannot move an applicant through the pipeline themselves, so the
        # status change is recorded against a system (super admin) actor.
        sys_actor = User.objects.filter(role=UserRole.SUPER_ADMIN, is_active=True).first() or request.user
        invoice, created = generate_admission_invoice(
            applicant=applicant, actor=sys_actor, reason="generated by parent",
        )
        if not created:
            django_messages.info(request, f"Invoice {invoice.invoice_number} already exists. Amount: TZS {invoice.total_due:,.0f}")
            return redirect("parent_portal:dashboard")

        # E18: invoice-raised email to Finance (the service sends the in-app notification).
        settings = SchoolSettings.get_settings()
        inv_no = invoice.invoice_number
        total_due = invoice.total_due
        for fo in User.objects.filter(role=UserRole.FINANCE_OFFICER, is_active=True).exclude(email=""):
            e18_context = {
                "guardian_name": applicant.parent_full_name,
                "student_name": applicant.child_full_name,
                "invoice_number": inv_no,
                "total_due": f"{total_due:,.0f}",
                "currency": "TZS",
                "term_name": "Admissions",
                "portal_url": request.build_absolute_uri("/parent/"),
                "school_name": settings.school_name or "Hodari Christian School",
            }
            if not send_dynamic_email(template_type="invoice_generated", to_email=fo.email, context=e18_context):
                send_email_safe(
                    to_email=fo.email,
                    subject=f"Invoice raised: {applicant.child_full_name} - {applicant.reference_number}",
                    body=(
                        f"An admission invoice has been generated and payment is expected.\n\n"
                        f"Student: {applicant.child_full_name}\n"
                        f"Grade: {applicant.grade_applying_for}\n"
                        f"Parent: {applicant.parent_full_name}\n"
                        f"Invoice number: {inv_no}\n"
                        f"Amount: TZS {total_due:,.0f}\n"
                        f"Reference: {applicant.reference_number}\n\n"
                        f"Enrolment cannot proceed until this invoice is settled in full.\n\n"
                        f"View the application: /admissions/applicant/{applicant.pk}/"
                    ),
                )

        django_messages.success(
            request,
            f"Invoice {inv_no} generated. Amount: TZS {total_due:,.0f}. "
            f"Due by {invoice.due_date.strftime('%d %B %Y')}. "
            f"Please pay to confirm enrolment."
        )
        return redirect("parent_portal:dashboard")
