from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.utils import timezone
from django.http import JsonResponse
from django.contrib.auth.mixins import LoginRequiredMixin
import json
from core.permissions import RoleRequiredMixin
from users.models import UserRole
from .models import CalendarEvent, EventAcknowledgement

class EventListView(RoleRequiredMixin, ListView):
    model = CalendarEvent
    template_name = "events/calendar_visible.html"
    context_object_name = "events"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.HEAD_OF_SCHOOL, UserRole.ADMIN_OFFICER, UserRole.TEACHER, UserRole.PARENT, UserRole.FINANCE_OFFICER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD, UserRole.LOWER_SECONDARY_HOD]
    required_permission = "events.view_calendarevent"
    
    def get_queryset(self):
        qs = super().get_queryset()
        # FR-CAL-004: Only show published, non-archived events
        qs = qs.filter(is_published=True, is_archived=False)
        # FR-CAL-004: Scope by target audience
        role = self.request.user.role
        if role == UserRole.PARENT:
            qs = qs.filter(notify_parents=True)
        else:
            qs = qs.filter(notify_staff=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        # Get current month from query params or use current date
        month_param = self.request.GET.get('month')
        if month_param:
            try:
                year, month = map(int, month_param.split('-'))
                current_month = timezone.datetime(year, month, 1).date()
            except:
                current_month = timezone.now().date()
        else:
            current_month = timezone.now().date()
        
        ctx['current_month'] = current_month
        
        # Pre-fetch acknowledgements for the current user if they are a parent
        user_acknowledged_ids = set()
        if self.request.user.is_authenticated and self.request.user.role == UserRole.PARENT:
            user_acknowledged_ids = set(
                EventAcknowledgement.objects.filter(parent=self.request.user)
                .values_list("event_id", flat=True)
            )
        ctx["user_acknowledged_ids_json"] = json.dumps(list(user_acknowledged_ids))
        
        # Convert events to JSON for JavaScript
        events_data = []
        for event in ctx['events']:
            ack_count = event.acknowledgements.count() if hasattr(event, 'acknowledgements') else 0
            events_data.append({
                'id': event.id,
                'title': event.title,
                'category': event.category,
                'category_display': event.get_category_display(),
                'description': event.description,
                'start_date': event.start_date.strftime('%Y-%m-%d'),
                'end_date': event.end_date.strftime('%Y-%m-%d') if event.end_date else None,
                'is_public_holiday': event.is_public_holiday,
                'notify_parents': event.notify_parents,
                'notify_staff': event.notify_staff,
                'parent_acknowledgement_required': event.parent_acknowledgement_required,
                'acknowledgement_count': ack_count,
            })
        
        # FR-ACAD-015: Term View Support - Robust Detection
        from academics.models import AcademicYear, Term
        today = timezone.now().date()
        
        # 1. Try to find the term currently in progress (any year)
        current_term_obj = Term.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
            is_locked=False
        ).first()
        
        # 2. If not in a term, find the next upcoming unlocked term
        if not current_term_obj:
            current_term_obj = Term.objects.filter(
                start_date__gte=today,
                is_locked=False
            ).order_by('start_date').first()
            
        # 3. If no upcoming, get the most recent unlocked term
        if not current_term_obj:
            current_term_obj = Term.objects.filter(
                is_locked=False
            ).order_by('-start_date').first()

        # 4. Final fallback: Any term at all
        if not current_term_obj:
            current_term_obj = Term.objects.order_by('-start_date').first()

        ctx['current_term_data'] = None
        if current_term_obj and current_term_obj.start_date and current_term_obj.end_date:
            ctx['current_term_data'] = {
                'name': current_term_obj.name,
                'year_name': current_term_obj.academic_year.name,
                'start_date': current_term_obj.start_date.isoformat(),
                'end_date': current_term_obj.end_date.isoformat(),
            }
        
        # Retrieve all terms for navigation
        all_terms = Term.objects.all().order_by('start_date')
        ctx['all_terms_data'] = [
            {
                'id': t.id,
                'name': t.name,
                'year_name': t.academic_year.name,
                'start_date': t.start_date.isoformat() if t.start_date else None,
                'end_date': t.end_date.isoformat() if t.end_date else None,
            }
            for t in all_terms if t.start_date and t.end_date
        ]
        
        ctx['events_json'] = json.dumps(events_data)
        ctx['term_json'] = json.dumps(ctx['current_term_data'])
        ctx['all_terms_json'] = json.dumps(ctx['all_terms_data'])
        return ctx

class EventCreateView(RoleRequiredMixin, CreateView):
    model = CalendarEvent
    fields = ["title", "category", "description", "start_date", "end_date", "is_public_holiday", "notify_parents", "notify_staff", "parent_acknowledgement_required"]
    template_name = "events/form.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "events.add_calendarevent"
    success_url = "/events/"

    def form_valid(self, form):
        # FR-CAL-001: Target audience must be selected
        notify_parents = form.cleaned_data.get("notify_parents")
        notify_staff = form.cleaned_data.get("notify_staff")
        if not notify_parents and not notify_staff:
            form.add_error(None, "Please select at least one target audience (Parents and/or Staff).")
            return self.form_invalid(form)

        # FR-CAL-001: Past dates require Super Admin
        from django.utils import timezone
        start_date = form.cleaned_data.get("start_date")
        if start_date and start_date < timezone.now().date():
            if self.request.user.role != UserRole.SUPER_ADMIN:
                form.add_error(None, "Only Super Admin can create events with past dates.")
                return self.form_invalid(form)

        response = super().form_valid(form)
        # Audit log
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="EVENT_CREATED",
            model_name="CalendarEvent",
            object_id=self.object.pk,
            description=f"Event created: {self.object.title} on {self.object.start_date}",
            after={
                "title": self.object.title,
                "category": self.object.category,
                "start_date": str(self.object.start_date),
                "notify_parents": notify_parents,
                "notify_staff": notify_staff,
            },
            request=self.request,
        )

        # Send notifications async via Celery
        from events.tasks import send_event_notifications_task
        send_event_notifications_task.delay(self.object.pk)

        # AJAX mode: return JSON success
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest" or self.request.content_type.startswith("multipart"):
            from django.http import JsonResponse
            return JsonResponse({"success": True, "event_id": self.object.pk, "title": self.object.title})

        return response

    def form_invalid(self, form):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest" or self.request.content_type.startswith("multipart"):
            from django.http import JsonResponse
            errors = []
            for field, field_errors in form.errors.items():
                if field == "__all__":
                    errors.extend(field_errors)
                else:
                    errors.extend(field_errors)
            msg = "; ".join(errors) if errors else "Validation failed. Please check the form fields."
            return JsonResponse({"error": msg}, status=400)
        return super().form_invalid(form)


class EventUpdateView(RoleRequiredMixin, UpdateView):
    """Edit an existing calendar event -- FR-CAL-001."""
    model = CalendarEvent
    fields = ["title", "category", "description", "start_date", "end_date", "is_public_holiday", "notify_parents", "notify_staff", "parent_acknowledgement_required"]
    template_name = "events/form.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "events.change_calendarevent"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.role in [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]:
            return qs
        return qs.filter(auto_generated=False)

    def get_success_url(self):
        return reverse("events:list")

    def form_valid(self, form):
        from django.utils import timezone
        event = form.instance

        # FR-CAL-001: After event date requires Super Admin
        old_start = event.start_date
        old_end = event.end_date
        new_start = form.cleaned_data.get("start_date")
        new_end = form.cleaned_data.get("end_date")
        if old_start and old_start < timezone.now().date():
            if self.request.user.role != UserRole.SUPER_ADMIN:
                messages.error(self.request, "Only Super Admin can edit events that have already occurred.")
                return self.form_invalid(form)

        # FR-CAL-001: Target audience must be selected
        notify_parents = form.cleaned_data.get("notify_parents")
        notify_staff = form.cleaned_data.get("notify_staff")
        if not notify_parents and not notify_staff:
            messages.error(self.request, "Please select at least one target audience (Parents and/or Staff).")
            return self.form_invalid(form)

        response = super().form_valid(form)

        # Audit log
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="EVENT_UPDATED",
            model_name="CalendarEvent",
            object_id=event.pk,
            description=f"Event updated: {event.title}",
            before={"start_date": str(old_start), "end_date": str(old_end) if old_end else None},
            after={"start_date": str(event.start_date), "end_date": str(event.end_date) if event.end_date else None},
            request=self.request,
        )

        # FR-CAL-001: Re-notify if date or end_date changed
        date_changed = (old_start != new_start) or (old_end != new_end)
        if date_changed:
            from events.tasks import send_event_notifications_update_task
            send_event_notifications_update_task.delay(event.pk, date_changed=True)
        return response


class EventDeleteView(RoleRequiredMixin, DeleteView):
    """Delete an existing calendar event -- FR-CAL-001."""
    model = CalendarEvent
    template_name = "events/confirm_delete.html"
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER]
    required_permission = "events.delete_calendarevent"

    def form_valid(self, form):
        from django.utils import timezone
        event = self.get_object()

        # FR-CAL-001: Past events can only be archived (not deleted) by Admin Officer
        if event.start_date and event.start_date < timezone.now().date():
            if self.request.user.role != UserRole.SUPER_ADMIN:
                # Archive instead of delete
                event.is_archived = True
                event.is_published = False
                event.save(update_fields=["is_archived", "is_published"])
                from audit.models import log_event
                log_event(
                    actor=self.request.user,
                    action_type="EVENT_ARCHIVED",
                    model_name="CalendarEvent",
                    object_id=event.pk,
                    description=f"Event archived (past): {event.title} (was on {event.start_date})",
                    before={"is_published": True, "is_archived": False},
                    after={"is_published": False, "is_archived": True},
                    request=self.request,
                )
                messages.success(self.request, "Past event has been archived.")
                return redirect("events:list")

        # FR-CAL-001: Send cancellation notification async before deleting
        from events.tasks import send_event_cancellation_task
        send_event_cancellation_task.delay(event.title, str(event.start_date), event.category)

        # Audit log
        from audit.models import log_event
        log_event(
            actor=self.request.user,
            action_type="EVENT_DELETED",
            model_name="CalendarEvent",
            object_id=event.pk,
            description=f"Event deleted: {event.title} (was on {event.start_date})",
            before={
                "title": event.title,
                "start_date": str(event.start_date),
                "category": event.category,
            },
            request=self.request,
        )
        return super().form_valid(form)

    def get_success_url(self):
        messages.success(self.request, "Event deleted.")
        return reverse("events:list")


class AcknowledgeEventView(LoginRequiredMixin, View):
    """Record a parent's acknowledgement of an event — FR-CAL-001.
    Permission-driven: requires the parent role AND events.view_calendarevent,
    so stripping that permission in the role-assignment UI revokes acknowledgement.
    """

    def post(self, request, pk):
        event = get_object_or_404(CalendarEvent, pk=pk, is_published=True)
        if request.user.role != UserRole.PARENT:
            return JsonResponse({"error": "Only parents can acknowledge events."}, status=403)
        if not request.user.has_perm("events.view_calendarevent"):
            return JsonResponse({"error": "You do not have permission to acknowledge events."}, status=403)
        obj, created = EventAcknowledgement.objects.get_or_create(
            event=event, parent=request.user
        )
        # NFR-AUDIT-001: Log event acknowledgement for audit trail
        if created:
            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="EVENT_ACKNOWLEDGED",
                model_name="EventAcknowledgement",
                object_id=obj.pk,
                description=f"Parent acknowledged event: {event.title}",
                request=request,
            )
        return JsonResponse({
            "acknowledged": True,
            "acknowledged_at": obj.acknowledged_at.isoformat() if created else obj.acknowledged_at.isoformat(),
            "acknowledgement_count": event.acknowledgements.count(),
        })
