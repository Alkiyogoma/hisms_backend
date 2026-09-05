"""
ECD Break Time Configuration — FR-TT-007.
Model-backed CRUD views for managing ECD class break/nap schedules.
"""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView

from core.permissions import RoleRequiredMixin
from timetable.models import EcdBreakConfig


BREAK_CONFIG_PERMS = {
    "view": "timetable.view_ecdbreakconfig",
    "change": "timetable.change_ecdbreakconfig",
}


class EcdBreakConfigListView(RoleRequiredMixin, TemplateView):
    """List all ECD break configurations."""
    template_name = "timetable/break_config_list.html"
    login_url = "/accounts/login/"
    required_permission = BREAK_CONFIG_PERMS["view"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["break_configs"] = EcdBreakConfig.objects.all().order_by("class_name")
        return ctx


class EcdBreakConfigCreateView(RoleRequiredMixin, TemplateView):
    """Create a new ECD break configuration."""
    template_name = "timetable/break_config_form.html"
    login_url = "/accounts/login/"
    required_permission = BREAK_CONFIG_PERMS["change"]

    def post(self, request):
        class_name = request.POST.get("class_name", "").strip()
        snack_start = request.POST.get("snack_break_start", "").strip()
        snack_end = request.POST.get("snack_break_end", "").strip()
        lunch_start = request.POST.get("lunch_break_start", "").strip()
        lunch_end = request.POST.get("lunch_break_end", "").strip()
        nap_start = request.POST.get("nap_time_start", "").strip() or None
        nap_end = request.POST.get("nap_time_end", "").strip() or None

        if not class_name or not snack_start or not snack_end or not lunch_start or not lunch_end:
            messages.error(request, "Class name, snack break, and lunch break times are required.")
            return redirect("timetable:break_config_create")

        from datetime import time as time_type

        try:
            cfg = EcdBreakConfig.objects.create(
                class_name=class_name,
                snack_break_start=time_type.fromisoformat(snack_start),
                snack_break_end=time_type.fromisoformat(snack_end),
                lunch_break_start=time_type.fromisoformat(lunch_start),
                lunch_break_end=time_type.fromisoformat(lunch_end),
                nap_time_start=time_type.fromisoformat(nap_start) if nap_start else None,
                nap_time_end=time_type.fromisoformat(nap_end) if nap_end else None,
            )
            messages.success(request, f"Break configuration created for {cfg.class_name}.")
        except Exception as e:
            messages.error(request, f"Error creating break configuration: {e}")
        return redirect("timetable:break_config_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_edit"] = False
        return ctx


class EcdBreakConfigUpdateView(RoleRequiredMixin, TemplateView):
    """Edit an existing ECD break configuration."""
    template_name = "timetable/break_config_form.html"
    login_url = "/accounts/login/"
    required_permission = BREAK_CONFIG_PERMS["change"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = get_object_or_404(EcdBreakConfig, pk=self.kwargs["pk"])
        ctx["break_config"] = cfg
        ctx["is_edit"] = True
        return ctx

    def post(self, request, pk):
        cfg = get_object_or_404(EcdBreakConfig, pk=pk)
        from datetime import time as time_type

        try:
            cfg.class_name = request.POST.get("class_name", cfg.class_name).strip()
            cfg.snack_break_start = time_type.fromisoformat(request.POST.get("snack_break_start", "").strip())
            cfg.snack_break_end = time_type.fromisoformat(request.POST.get("snack_break_end", "").strip())
            cfg.lunch_break_start = time_type.fromisoformat(request.POST.get("lunch_break_start", "").strip())
            cfg.lunch_break_end = time_type.fromisoformat(request.POST.get("lunch_break_end", "").strip())
            nap_start = request.POST.get("nap_time_start", "").strip()
            nap_end = request.POST.get("nap_time_end", "").strip()
            cfg.nap_time_start = time_type.fromisoformat(nap_start) if nap_start else None
            cfg.nap_time_end = time_type.fromisoformat(nap_end) if nap_end else None
            cfg.save()
            messages.success(request, f"Break configuration updated for {cfg.class_name}.")
        except Exception as e:
            messages.error(request, f"Error updating break configuration: {e}")
        return redirect("timetable:break_config_list")
