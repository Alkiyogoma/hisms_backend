import csv
import io
import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import models
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView
from django.utils import timezone

from audit.models import AuditLog, log_event
from core.permissions import RoleRequiredMixin
from students.models import Student, ParentGuardian
from users.models import UserRole

logger = logging.getLogger(__name__)


class CspReportView(View):
    """
    CSP violation report collector — FRD NFR-SEC-001.

    Receives POST reports from browsers when Content-Security-Policy is
    violated. Logs them for security monitoring without blocking the user.
    Accessible to anyone (no auth required) so browser reports can arrive
    from any page including login.
    """

    def get(self, request):
        """Redirect GET requests to the audit logs page."""
        return redirect("audit:logs")

    def post(self, request):
        try:
            report = json.loads(request.body)
            csp_report = report.get("csp-report", report)

            logger.warning(
                "CSP Violation: blocked-uri=%s | violated-directive=%s | "
                "source-file=%s | line=%s | document-uri=%s",
                csp_report.get("blocked-uri", "N/A"),
                csp_report.get("violated-directive", "N/A"),
                csp_report.get("source-file", "N/A"),
                csp_report.get("line-number", "N/A"),
                csp_report.get("document-uri", "N/A"),
            )

            # Write to audit log for security team review
            try:
                log_event(
                    actor=None,
                    action_type="CSP_VIOLATION",
                    model_name="CSPReport",
                    description=json.dumps(csp_report, default=str)[:2000],
                )
            except Exception as log_exc:
                logger.warning("Failed to log CSP report to audit: %s", log_exc)

        except json.JSONDecodeError:
            logger.warning("Received invalid CSP report body")
        except Exception as exc:
            logger.error("CSP report processing error: %s", exc)

        # Always return 204 No Content — browsers don't expect a response
        return HttpResponse(status=204)


class CspReportsListView(RoleRequiredMixin, TemplateView):
    """Listing page for CSP violation reports pulled from the audit log."""
    template_name = "audit/csp_reports.html"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL]
    required_permission = "audit.view_auditlog"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["audit_tab"] = "csp"
        ctx["reports"] = AuditLog.objects.filter(
            action_type="CSP_VIOLATION",
            model_name="CSPReport",
        ).order_by("-created_at")[:200]
        return ctx


class AuditLogsListView(RoleRequiredMixin, TemplateView):
    """Full audit log view with filtering — FRD NFR-PDPA-006.
    
    Supports:
    - Date range filtering (date_from, date_to)
    - Actor/user filtering (actor_id)
    - Action type filtering (action_type)
    - Model name filtering (model_name)
    - Search by description text (q)
    - Role-scoped visibility: Admin Officer sees admissions/comms only,
      Finance Officer sees financial only, others see all.
    - CSV export of filtered results
    """
    template_name = "audit/logs.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.FINANCE_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "audit.view_auditlog"

    ROLE_MODEL_SCOPES = {
        "admin_officer": [
            "Applicant", "ApplicantDocumentReceipt", "AssessmentSchedule",
            "EnrolmentChecklist", "Broadcast", "Notification", "ParentGuardian",
            "CambridgeCheckpointScore",
        ],
        "finance_officer": [
            "Invoice", "Payment", "FeeStructure", "UnmatchedPayment",
            "InvoiceLineItem", "FeeReminderConfig",
        ],
    }

    def get(self, request, *args, **kwargs):
        """Handle export before any template rendering."""
        export_fmt = request.GET.get("export")
        if export_fmt:
            logs = self._get_filtered_logs()
            user = request.user
            if export_fmt == "csv":
                return self._export_csv(logs, user)
            if export_fmt == "pdf":
                return self._export_pdf(logs, user)
        return super().get(request, *args, **kwargs)

    def _get_filtered_logs(self):
        """Build the filtered queryset (shared by view + exports)."""
        user = self.request.user
        logs = AuditLog.objects.select_related("actor").order_by("-created_at")

        # Role-scoped visibility
        role_key = user.role.lower() if user.role else ""
        scoped_models = self.ROLE_MODEL_SCOPES.get(role_key)
        is_unrestricted = role_key in ("super_admin", "head_of_school")
        if scoped_models and not is_unrestricted:
            logs = logs.filter(model_name__in=scoped_models)

        q = self.request.GET.get("q", "").strip()
        date_from = self.request.GET.get("date_from", "")
        date_to = self.request.GET.get("date_to", "")
        actor_id = self.request.GET.get("actor_id", "")
        action_type = self.request.GET.get("action_type", "")
        model_name = self.request.GET.get("model_name", "")

        if q:
            logs = logs.filter(
                models.Q(description__icontains=q)
                | models.Q(actor__username__icontains=q)
                | models.Q(model_name__icontains=q)
                | models.Q(action_type__icontains=q)
            )
        if date_from:
            logs = logs.filter(created_at__date__gte=date_from)
        if date_to:
            logs = logs.filter(created_at__date__lte=date_to)
        if actor_id:
            logs = logs.filter(actor_id=actor_id)
        if action_type:
            logs = logs.filter(action_type=action_type)
        if model_name:
            logs = logs.filter(model_name=model_name)

        return logs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        logs = self._get_filtered_logs()

        # ── Pagination ──────────────────────────────────────────────────
        paginator = Paginator(logs, 50)
        page_number = self.request.GET.get("page")
        page_obj = paginator.get_page(page_number)
        ctx["logs"] = page_obj
        ctx["page_obj"] = page_obj
        ctx["paginator"] = paginator
        ctx["is_paginated"] = page_obj.has_other_pages()

        # ── Filter context ─────────────────────────────────────────────────
        ctx["filter_q"] = self.request.GET.get("q", "").strip()
        ctx["filter_date_from"] = self.request.GET.get("date_from", "")
        ctx["filter_date_to"] = self.request.GET.get("date_to", "")
        ctx["filter_actor_id"] = self.request.GET.get("actor_id", "")
        ctx["filter_action_type"] = self.request.GET.get("action_type", "")
        ctx["filter_model_name"] = self.request.GET.get("model_name", "")
        ctx["audit_tab"] = "logs"

        # Dropdown options — scoped to role
        role_key = user.role.lower() if user.role else ""
        scoped_models = self.ROLE_MODEL_SCOPES.get(role_key)
        is_unrestricted = role_key in ("super_admin", "head_of_school")
        base_qs = logs if scoped_models and not is_unrestricted else AuditLog.objects.all()
        from users.models import User
        ctx["actors"] = (
            base_qs.values_list("actor_id", "actor__username")
            .distinct()
            .exclude(actor_id__isnull=True)
            .order_by("actor__username")
        )
        ctx["action_types"] = (
            base_qs.values_list("action_type", flat=True)
            .distinct()
            .order_by("action_type")
        )
        ctx["model_names"] = (
            base_qs.values_list("model_name", flat=True)
            .distinct()
            .exclude(model_name__isnull=True)
            .order_by("model_name")
        )

        return ctx

    def _export_csv(self, logs, user):
        """Export filtered audit logs as CSV — FR-AUD-005."""
        log_event(
            actor=user,
            action_type="AUDIT_EXPORT",
            model_name="AuditLog",
            description=f"Audit log CSV export by {user.username}",
            request=self.request,
        )
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Timestamp", "Actor", "Action", "Module", "Description", "IP Address"])
        for log in logs[:5000]:
            writer.writerow([
                log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
                log.actor.get_username() if log.actor else "System",
                log.action_type,
                log.model_name,
                log.description or "",
                log.ip_address or "",
            ])
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="audit_log_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        return response

    def _export_pdf(self, logs, user):
        """Export filtered audit logs as PDF — FR-AUD-005."""
        log_event(
            actor=user,
            action_type="AUDIT_EXPORT",
            model_name="AuditLog",
            description=f"Audit log PDF export by {user.username}",
            request=self.request,
        )
        rows = []
        for log in logs[:5000]:
            rows.append({
                "timestamp": log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
                "actor": log.actor.get_username() if log.actor else "System",
                "action": log.action_type,
                "module": log.model_name,
                "description": (log.description or "")[:200],
                "ip": log.ip_address or "",
            })
        html_string = _build_audit_pdf_html(rows, user)
        try:
            from weasyprint import HTML
            pdf_bytes = HTML(string=html_string).write_pdf()
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="audit_log_{timezone.now().strftime("%Y%m%d_%H%M%S")}.pdf"'
            return response
        except (ImportError, OSError):
            return HttpResponse(
                "PDF generation requires WeasyPrint with system dependencies (GTK). Please use CSV export instead.",
                status=503,
            )


def _build_audit_pdf_html(rows, user):
    """Build an HTML document for PDF rendering of audit logs."""
    now = timezone.now().strftime("%d %B %Y, %H:%M")
    scope_label = {
        "admin_officer": "Admissions and Communications",
        "finance_officer": "Financial",
        "head_of_school": "Full Log",
        "super_admin": "Full Log",
    }.get(user.role.lower() if user.role else "", "Scoped")
    table_rows = ""
    for r in rows:
        table_rows += f"""<tr>
            <td>{r['timestamp']}</td>
            <td>{r['actor']}</td>
            <td>{r['action']}</td>
            <td>{r['module']}</td>
            <td>{r['description']}</td>
            <td>{r['ip']}</td>
        </tr>"""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body {{ font-family: sans-serif; font-size: 10px; color: #1E293B; margin: 20px; }}
  h1 {{ font-size: 16px; margin-bottom: 4px; }}
  .meta {{ font-size: 10px; color: #64748B; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: #F1F5F9; padding: 6px 8px; text-align: left; font-size: 9px;
        font-weight: 700; color: #475569; border-bottom: 2px solid #E2E8F0; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #F1F5F9; font-size: 9px; }}
  tr:nth-child(even) td {{ background: #FAFAFA; }}
</style></head>
<body>
  <h1>Audit Log Export</h1>
  <div class="meta">Generated: {now} &middot; By: {user.username} &middot; Scope: {scope_label} &middot; Records: {len(rows)}</div>
  <table>
    <thead><tr>
      <th>Timestamp</th><th>Actor</th><th>Action</th>
      <th>Module</th><th>Description</th><th>IP</th>
    </tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</body></html>"""


class PersonalDataExportView(RoleRequiredMixin, TemplateView):
    """
    FRD NFR-PDPA-003: Super Admin and HOS can produce a complete export of personal
    data for a named individual within 30 minutes.

    GET  -> renders the search form
    POST -> performs the search or triggers the export
    GET  ?action=export&type=student&id=123 -> downloads the JSON export
    """

    template_name = "audit/dsar_export.html"
    login_url = "/accounts/login/"
    allowed_roles = [UserRole.HEAD_OF_SCHOOL]
    required_permission = "audit.view_dsarrequest"

    def _check_permission(self, request):
        return request.user.role in (UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL)

    def get(self, request, *args, **kwargs):
        if not self._check_permission(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        action = request.GET.get("action", "")

        # ── Export download ──────────────────────────────────────────────
        if action == "export":
            return self._handle_export(request)

        # ── Search API (for HTMX live search) ───────────────────────────
        if action == "search":
            return self._handle_search(request)

        # ── Render search page ───────────────────────────────────────────
        ctx = {
            "today_display": timezone.now().date().strftime("%A, %d %B %Y"),
            "audit_tab": "dsar",
        }
        return self.render_to_response(ctx)

    def post(self, request, *args, **kwargs):
        if not self._check_permission(request):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        action = request.POST.get("action", "")

        if action == "search":
            return self._handle_search(request)

        if action == "export":
            return self._handle_export(request)

        return JsonResponse({"error": "Unknown action"}, status=400)

    # ── Search handler ───────────────────────────────────────────────────

    def _handle_search(self, request):
        query = (request.GET.get("q") or request.POST.get("q") or "").strip()
        search_type = request.GET.get("type") or request.POST.get("type", "student")

        if len(query) < 2:
            return JsonResponse({"results": []})

        from audit.services.dsar_export import search_students, search_guardians

        if search_type == "guardian":
            results = search_guardians(query)
            data = [
                {
                    "id": g.id,
                    "label": f"{g.full_name} — {g.phone}",
                    "detail": g.email or "",
                    "type": "guardian",
                }
                for g in results
            ]
        else:
            results = search_students(query)
            data = [
                {
                    "id": s.id,
                    "label": f"{s.first_name} {s.last_name} ({s.admission_no})",
                    "detail": f"{s.class_name} — {s.status}",
                    "type": "student",
                }
                for s in results
            ]

        return JsonResponse({"results": data})

    # ── Export handler ───────────────────────────────────────────────────

    def _handle_export(self, request):
        from django.utils import timezone as _tz
        from audit.models import DSARRequest, DSARRequestStatus, DSAR_EXPECTED_MODULES

        target_id = request.GET.get("id") or request.POST.get("id")
        target_type = request.GET.get("type") or request.POST.get("type", "student")
        export_format = (
            request.GET.get("format")
            or request.POST.get("format", "json")
        ).lower()

        if not target_id:
            return JsonResponse({"error": "Missing id parameter"}, status=400)

        if export_format not in ("json", "csv", "xlsx"):
            return JsonResponse({"error": "Invalid format. Use json, csv, or xlsx."}, status=400)

        # Create DSAR request record for audit trail
        dsar = DSARRequest.objects.create(
            requested_by=request.user,
            target_type=target_type,
            target_id=target_id,
            target_label="",
            export_format=export_format,
            status=DSARRequestStatus.IN_PROGRESS,
            started_at=_tz.now(),
            modules_expected=DSAR_EXPECTED_MODULES,
            ip_address=get_client_ip(request),
        )

        try:
            from audit.services.dsar_export import (
                export_student_data,
                export_guardian_data,
                build_dsar_sections,
                _rows_to_csv,
                _rows_to_xlsx_bytes,
            )

            if target_type == "guardian":
                target = get_object_or_404(ParentGuardian, pk=target_id)
                data = export_guardian_data(target)
                base = f"dsar_export_guardian_{target_id}"
                label = target.full_name
            else:
                target = get_object_or_404(Student, pk=target_id)
                data = export_student_data(target)
                base = f"dsar_export_student_{target_id}"
                label = target.get_full_name()

            # Update DSAR request with target label
            dsar.target_label = label

            # ── NFR-PDPA-003: Validate module coverage ──────────────────
            exported_modules = list(data.keys())
            # Map export keys to expected module names
            module_mapping = {
                "personal_info": "identity",
                "medical_info": "medical",
                "guardians": "guardians",
                "siblings": "siblings",
                "attendance": "attendance",
                "finances": "finances",
                "academics": "academics",
                "discipline": "discipline",
                "welfare": "welfare",
                "admissions": "admissions",
                "audit_trail": "audit_trail",
                "notifications": "notifications",
                "pdpa_consent": "pdpa_consent",
            }
            included_modules = []
            missing_modules = []
            for export_key, module_name in module_mapping.items():
                if export_key in data and data[export_key]:
                    included_modules.append(module_name)
                else:
                    missing_modules.append(module_name)

            dsar.modules_included = included_modules

            if missing_modules:
                logger.warning(
                    "DSAR export for %s#%s missing modules: %s",
                    target_type, target_id, missing_modules,
                )

            # ── Log the export event for audit trail ─────────────────────
            log_event(
                actor=request.user,
                action_type="DSAR_EXPORT",
                model_name=target_type.title(),
                object_id=str(target_id),
                description=(
                    f"DSAR export ({export_format.upper()}) produced for "
                    f"{target_type} '{label}' (id={target_id}) by "
                    f"{request.user.username}. "
                    f"Modules included: {len(included_modules)}/{len(DSAR_EXPECTED_MODULES)}. "
                    f"Missing: {missing_modules or 'none'}"
                ),
                request=request,
            )

            # ── Build response in requested format ───────────────────────
            if export_format == "csv":
                sections = build_dsar_sections(data)
                csv_content = _rows_to_csv(sections)
                response = HttpResponse(csv_content, content_type="text/csv")
                response["Content-Disposition"] = f'attachment; filename="{base}.csv"'
                file_size = len(csv_content.encode("utf-8"))
            elif export_format == "xlsx":
                sections = build_dsar_sections(data)
                xlsx_bytes = _rows_to_xlsx_bytes(sections)
                response = HttpResponse(
                    xlsx_bytes,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                response["Content-Disposition"] = f'attachment; filename="{base}.xlsx"'
                file_size = len(xlsx_bytes)
            else:
                json_str = json.dumps(data, indent=2, default=str)
                response = HttpResponse(json_str, content_type="application/json")
                response["Content-Disposition"] = f'attachment; filename="{base}.json"'
                file_size = len(json_str.encode("utf-8"))

            # ── NFR-PDPA-003: Complete DSAR request tracking ────────────
            completed_at = _tz.now()
            duration = (completed_at - dsar.started_at).total_seconds()
            within_sla = duration <= 1800  # 30 minutes

            dsar.status = DSARRequestStatus.COMPLETED
            dsar.completed_at = completed_at
            dsar.duration_seconds = duration
            dsar.within_sla = within_sla
            dsar.file_size_bytes = file_size
            dsar.save(update_fields=[
                "target_label", "status", "completed_at", "duration_seconds",
                "within_sla", "file_size_bytes", "modules_included",
            ])

            if not within_sla:
                logger.warning(
                    "DSAR export SLA breach: %s#%s took %.1f seconds (limit: 1800s)",
                    target_type, target_id, duration,
                )

            return response

        except Exception as e:
            # Mark DSAR request as failed
            dsar.status = DSARRequestStatus.FAILED
            dsar.completed_at = _tz.now()
            dsar.duration_seconds = (_tz.now() - dsar.started_at).total_seconds()
            dsar.error_message = str(e)
            dsar.save(update_fields=[
                "status", "completed_at", "duration_seconds", "error_message",
            ])
            logger.exception("DSAR export failed")
            return JsonResponse({"error": str(e)}, status=500)
