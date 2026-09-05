"""
Communications views — FRD Section 11.
"""
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import DetailView, ListView, RedirectView, TemplateView, UpdateView, View

from core.permissions import RoleRequiredMixin
from core.teacher_context import is_ecd_teacher
from users.models import User, UserRole

from .models import Broadcast, BroadcastAudience, BroadcastStatus, Notification, NotificationCategory, WeeklyFocus, WeeklyFocusStatus


class NotificationListView(RoleRequiredMixin, ListView):
    """Inbox: current user's unread + recent notifications.
    Permission-driven: any role holding at least one communications view
    permission can read their own inbox; queries stay owner-scoped.
    """
    template_name = "communications/inbox.html"
    context_object_name = "notifications"
    paginate_by = 30
    required_permissions_any = [
        "communications.view_notification",
        "communications.view_broadcast",
        "communications.view_weeklyfocus",
    ]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unread_count"] = Notification.objects.filter(recipient=self.request.user, is_read=False).count()
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        return ctx


class MarkNotificationReadView(RoleRequiredMixin, View):
    required_permissions_any = [
        "communications.view_notification",
        "communications.view_broadcast",
        "communications.view_weeklyfocus",
    ]

    def post(self, request, pk):
        n = get_object_or_404(Notification, pk=pk, recipient=request.user)
        n.is_read = True
        n.read_at = timezone.now()
        n.save(update_fields=["is_read", "read_at"])
        
        # Check if it's an AJAX request
        if request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            return JsonResponse({"ok": True, "unread_count": Notification.objects.filter(recipient=request.user, is_read=False).count()})
        return redirect(n.link or "communications:inbox")


class MarkAllReadView(RoleRequiredMixin, View):
    required_permissions_any = [
        "communications.view_notification",
        "communications.view_broadcast",
        "communications.view_weeklyfocus",
    ]

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        
        # Check if it's an AJAX request
        if request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            return JsonResponse({"ok": True})
        
        messages.success(request, "All notifications marked as read.")
        return redirect("communications:inbox")


class BroadcastListView(RoleRequiredMixin, ListView):
    """Broadcast inbox — all roles see relevant broadcasts scoped to their audience."""
    template_name = "communications/broadcast_list.html"
    context_object_name = "broadcasts"
    paginate_by = 20
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN,
                     UserRole.PARENT, UserRole.TEACHER, UserRole.PRIMARY_HOD, UserRole.ECD_HOD,
                     UserRole.FINANCE_OFFICER]
    required_permission = "communications.view_broadcast"

    def get_queryset(self):
        user = self.request.user
        qs = Broadcast.objects.select_related("created_by").order_by("-created_at")
        if user.role == UserRole.PARENT:
            qs = qs.filter(
                Q(audience=BroadcastAudience.ALL_PARENTS)
                | Q(audience=BroadcastAudience.GRADE, target_grade__in=self._parent_grade_levels())
                | Q(audience=BroadcastAudience.INDIVIDUAL, target_user=user)
            )
        elif user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL):
            qs = qs.filter(audience=BroadcastAudience.STAFF)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # FR-COM-005: Sent log (full broadcast list with admin metadata) is AO/HOS/SA only
        ctx["is_sent_log"] = self.request.user.role in {
            UserRole.SUPER_ADMIN, UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL,
        }
        return ctx

    def _parent_grade_levels(self):
        from students.models import StudentGuardian
        guardian_ids = StudentGuardian.objects.filter(
            guardian__user=self.request.user
        ).values_list("student__class_name", flat=True)
        return list(guardian_ids)


class BroadcastComposeView(RoleRequiredMixin, TemplateView):
    template_name = "communications/broadcast_compose.html"
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.add_broadcast"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["audience_choices"] = BroadcastAudience.choices
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        # Available grades for targeted broadcast
        from students.models import Student
        from communications.templates_data import MESSAGE_TEMPLATES
        ctx["message_templates"] = MESSAGE_TEMPLATES
        ctx["grades"] = list(
            Student.objects.values_list("class_name", flat=True).distinct().order_by("class_name")
        )
        return ctx

    def post(self, request):
        audience = request.POST.get("audience")
        target_grade = request.POST.get("target_grade", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        if not subject or not body:
            messages.error(request, "Subject and body are required.")
            return redirect("communications:broadcast_compose")
        broadcast = Broadcast.objects.create(
            created_by=request.user,
            audience=audience,
            target_grade=target_grade if audience == BroadcastAudience.GRADE else "",
            subject=subject,
            body=body,
            status=BroadcastStatus.DRAFT,
        )
        messages.success(request, f"Broadcast draft saved. Preview and send when ready.")
        return redirect("communications:broadcast_detail", pk=broadcast.pk)


class BroadcastDetailView(RoleRequiredMixin, DetailView):
    template_name = "communications/broadcast_detail.html"
    model = Broadcast
    context_object_name = "broadcast"
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN, UserRole.PARENT]
    required_permission = "communications.view_broadcast"

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # FR-CAL-001/FR-COM-001: mark preview as viewed for server-side enforcement
        obj = self.get_object()
        if obj.status == BroadcastStatus.DRAFT and not obj.preview_viewed:
            obj.preview_viewed = True
            obj.save(update_fields=["preview_viewed"])
        return response


class BroadcastUpdateView(RoleRequiredMixin, TemplateView):
    """Edit a draft broadcast — FR-COM-001."""
    template_name = "communications/broadcast_compose.html"
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.change_broadcast"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        broadcast = get_object_or_404(Broadcast, pk=self.kwargs["pk"])
        ctx["broadcast"] = broadcast
        ctx["is_edit"] = True
        ctx["audience_choices"] = BroadcastAudience.choices
        ctx["today_date"] = date.today().strftime("%d %B %Y")
        from students.models import Student
        from communications.templates_data import MESSAGE_TEMPLATES
        ctx["message_templates"] = MESSAGE_TEMPLATES
        ctx["grades"] = list(
            Student.objects.values_list("class_name", flat=True).distinct().order_by("class_name")
        )
        return ctx

    def post(self, request, pk):
        broadcast = get_object_or_404(Broadcast, pk=pk)
        if broadcast.status == BroadcastStatus.SENT:
            messages.error(request, "Cannot edit a sent broadcast.")
            return redirect("communications:broadcast_detail", pk=pk)

        broadcast.audience = request.POST.get("audience", broadcast.audience)
        broadcast.target_grade = request.POST.get("target_grade", "").strip()
        broadcast.subject = request.POST.get("subject", "").strip()
        broadcast.body = request.POST.get("body", "").strip()
        broadcast.preview_viewed = False  # must re-preview after edits
        if not broadcast.subject or not broadcast.body:
            messages.error(request, "Subject and body are required.")
            return redirect("communications:broadcast_edit", pk=pk)
        broadcast.save(update_fields=["audience", "target_grade", "subject", "body", "preview_viewed", "updated_at"])
        messages.success(request, "Broadcast draft updated.")
        return redirect("communications:broadcast_detail", pk=pk)


class BroadcastDeleteView(RoleRequiredMixin, View):
    """Delete a draft broadcast."""
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.delete_broadcast"

    def post(self, request, pk):
        broadcast = get_object_or_404(Broadcast, pk=pk)
        if broadcast.status == BroadcastStatus.SENT:
            messages.error(request, "Cannot delete a sent broadcast.")
            return redirect("communications:broadcast_detail", pk=pk)
        broadcast.delete()
        messages.success(request, "Broadcast draft deleted.")
        return redirect("communications:broadcast_list")


class BroadcastSendView(RoleRequiredMixin, View):
    """Deliver broadcast as in-app notifications + optional email to target users."""
    allowed_roles = [UserRole.ADMIN_OFFICER, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.change_broadcast"

    def post(self, request, pk):
        broadcast = get_object_or_404(Broadcast, pk=pk)
        send_email = request.POST.get("send_email") == "on"

        if broadcast.status == BroadcastStatus.SENT:
            messages.warning(request, "This broadcast has already been sent.")
            return redirect("communications:broadcast_detail", pk=pk)

        if broadcast.status != BroadcastStatus.DRAFT:
            messages.error(request, "Only draft broadcasts can be sent.")
            return redirect("communications:broadcast_detail", pk=pk)

        # FR-COM-001: Must preview before sending
        if not broadcast.preview_viewed:
            messages.error(request, "Please review the broadcast preview before sending.")
            return redirect("communications:broadcast_detail", pk=pk)

        # Determine recipient users
        parent_qs = User.objects.filter(role=UserRole.PARENT, is_active=True)
        if broadcast.audience == BroadcastAudience.GRADE and broadcast.target_grade:
            from students.models import StudentGuardian
            guardian_ids = StudentGuardian.objects.filter(
                student__class_name=broadcast.target_grade
            ).values_list("guardian__user_id", flat=True)
            recipients = User.objects.filter(pk__in=guardian_ids, is_active=True)
        elif broadcast.audience == BroadcastAudience.INDIVIDUAL and broadcast.target_user_id:
            recipients = User.objects.filter(pk=broadcast.target_user_id)
        elif broadcast.audience == BroadcastAudience.STAFF:
            recipients = User.objects.exclude(role=UserRole.PARENT).filter(is_active=True)
        else:
            recipients = parent_qs

        count = recipients.count()
        if count == 0:
            messages.error(request, "Cannot send broadcast: the selected audience has no recipients.")
            return redirect("communications:broadcast_detail", pk=pk)

        # Create in-app notifications
        notifications = [
            Notification(
                recipient=user,
                category=NotificationCategory.BROADCAST,
                title=broadcast.subject,
                body=broadcast.body,
                link=f"/communications/broadcasts/{pk}/",
            )
            for user in recipients
        ]
        Notification.objects.bulk_create(notifications)

        # Optionally send email via dispatch_notification
        if send_email:
            from communications.email_service import dispatch_notification
            email_count = 0
            for user in recipients:
                if user.email:
                    dispatch_notification(
                        user=user,
                        title=broadcast.subject,
                        message=broadcast.body,
                        link=f"/communications/broadcasts/{pk}/",
                        actor=request.user,
                    )
                    email_count += 1

        broadcast.status = BroadcastStatus.SENT
        broadcast.sent_at = timezone.now()
        broadcast.recipient_count = count
        broadcast.save(update_fields=["status", "sent_at", "recipient_count"])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="CREATE",
            model_name="Broadcast",
            object_id=broadcast.pk,
            description=f"Broadcast '{broadcast.subject}' sent to {count} recipients" + (f" ({email_count} emailed)" if send_email else ""),
            request=request,
        )

        msg = f"Broadcast sent to {count} recipients."
        if send_email:
            msg += f" Email sent to {email_count} recipients."
        messages.success(request, msg)
        return redirect("communications:broadcast_detail", pk=pk)


# ---------------------------------------------------------------------------
# ECD Weekly Focus — FRD FR-COM-007 … FR-COM-010
# ---------------------------------------------------------------------------

class WeeklyFocusListView(RoleRequiredMixin, TemplateView):
    """List/review page for Weekly Focus entries. Teachers see own; HODs see review queue."""
    template_name = "communications/weekly_focus_list.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.view_weeklyfocus"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.TEACHER and not is_ecd_teacher(request.user):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date as dt
        today = dt.today()
        current_week = today.isocalendar()[1]

        ctx["current_week"] = current_week
        ctx["today_date"] = today.strftime("%d %B %Y")
        ctx["is_hod"] = self.request.user.has_perm("communications.change_weeklyfocus")

        # All entries for this user (or all for HOD)
        qs = WeeklyFocus.objects.select_related("teacher").order_by("-created_at")
        if self.request.user.role == UserRole.TEACHER:
            qs = qs.filter(teacher=self.request.user)
        ctx["entries"] = qs

        # HOD: pending review count
        if ctx["is_hod"]:
            ctx["pending_count"] = WeeklyFocus.objects.filter(status=WeeklyFocusStatus.SUBMITTED).count()
        else:
            ctx["pending_count"] = 0
        return ctx


class WeeklyFocusSubmitView(RoleRequiredMixin, TemplateView):
    """ECD teacher submits a Weekly Focus for the current week — FR-COM-007/008."""
    template_name = "communications/weekly_focus_submit.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.SUPER_ADMIN]
    required_permission = "communications.add_weeklyfocus"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.TEACHER and not is_ecd_teacher(request.user):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date as dt
        today = dt.today()
        ctx["current_week"] = today.isocalendar()[1]
        ctx["current_year"] = today.year
        ctx["today_date"] = today.strftime("%d %B %Y")
        from core.teacher_context import get_teacher_assigned_classes
        my_classes = sorted(get_teacher_assigned_classes(self.request.user))
        # FR-COM-007: teacher dropdown shows only their assigned classes
        ctx["assigned_classes"] = my_classes
        try:
            from academics.utils import get_current_term
            from hr.models import TeacherClassAssignment
            term = get_current_term()
            assignment = TeacherClassAssignment.objects.filter(
                teacher__user=self.request.user, term=term
            ).first()
            ctx["default_class"] = assignment.grade_class.name if assignment else (my_classes[0] if my_classes else "")
        except Exception:
            ctx["default_class"] = my_classes[0] if my_classes else ""

        qs = WeeklyFocus.objects.select_related("teacher").order_by("-created_at")
        if self.request.user.role == UserRole.TEACHER:
            qs = qs.filter(teacher=self.request.user)
        ctx["recent_entries"] = qs[:5]
        ctx["is_hod"] = self.request.user.has_perm("communications.change_weeklyfocus")
        return ctx

    def post(self, request):
        class_name = request.POST.get("class_name", "").strip()
        week_number = request.POST.get("week_number", "").strip()
        theme = request.POST.get("theme", "").strip()
        planned_activities = request.POST.get("planned_activities", "").strip()
        items_to_bring = request.POST.get("items_to_bring", "").strip()
        academic_year = request.POST.get("academic_year", "").strip()

        if not all([class_name, week_number, theme, planned_activities]):
            messages.error(request, "Class, week number, theme and planned activities are all required.")
            return redirect("communications:weekly_focus_submit")

        if request.user.role == UserRole.TEACHER:
            from core.teacher_context import get_teacher_assigned_classes
            my_classes = set(get_teacher_assigned_classes(request.user))
            # FR-COM-007: teachers can only submit for their own assigned classes
            allowed = my_classes
            if class_name not in allowed:
                messages.error(request, "You can only submit weekly focus for your assigned classes.")
                return redirect("communications:weekly_focus_submit")

        try:
            week_number = int(week_number)
        except ValueError:
            messages.error(request, "Week number must be a number.")
            return redirect("communications:weekly_focus_submit")

        # Validate week matches current week
        from datetime import date as dt
        current_week = dt.today().isocalendar()[1]
        if week_number != current_week:
            messages.error(request, f"You can only submit for the current week (Week {current_week}).")
            return redirect("communications:weekly_focus_submit")

        # Permission-driven: holders of change_weeklyfocus publish directly; others submit for review
        can_publish_directly = request.user.has_perm("communications.change_weeklyfocus")
        now = timezone.now()

        if can_publish_directly:
            entry, created = WeeklyFocus.objects.update_or_create(
                teacher=request.user,
                class_name=class_name,
                week_number=week_number,
                academic_year=academic_year,
                defaults={
                    "theme": theme,
                    "planned_activities": planned_activities,
                    "items_to_bring": items_to_bring,
                    "status": WeeklyFocusStatus.APPROVED,
                    "is_published": True,
                    "published_at": now,
                    "submitted_at": now,
                },
            )
        else:
            entry, created = WeeklyFocus.objects.update_or_create(
                teacher=request.user,
                class_name=class_name,
                week_number=week_number,
                academic_year=academic_year,
                defaults={
                    "theme": theme,
                    "planned_activities": planned_activities,
                    "items_to_bring": items_to_bring,
                    "status": WeeklyFocusStatus.SUBMITTED,
                    "submitted_at": now,
                    "reviewer_feedback": "",
                },
            )

        # Only send notifications when published (approved)
        if entry.is_published:
            from students.models import Student, StudentGuardian
            students = Student.objects.filter(class_name=class_name, is_archived=False)
            parent_user_ids = StudentGuardian.objects.filter(
                student__in=students, is_primary=True, guardian__user__isnull=False
            ).values_list("guardian__user_id", flat=True)
            parent_users = User.objects.filter(pk__in=parent_user_ids)

            notifications = [
                Notification(
                    recipient=u,
                    category=NotificationCategory.BROADCAST,
                    title=f"Week {week_number} Focus — {class_name}: {theme}",
                    body=f"Planned activities:\n{planned_activities}" + (
                        f"\n\nItems to bring:\n{items_to_bring}" if items_to_bring else ""
                    ),
                    link=f"/communications/weekly-focus/{entry.pk}/",  # noqa: redirects to list
                )
                for u in parent_users
            ]
            Notification.objects.bulk_create(notifications)

        from audit.models import log_event
        action_type = "CREATE" if created else "UPDATE"
        status_label = entry.get_status_display()
        log_event(
            actor=request.user,
            action_type=action_type,
            model_name="WeeklyFocus",
            object_id=entry.pk,
            description=f"Weekly Focus Week {week_number} {status_label} for {class_name}",
            request=request,
        )

        if can_publish_directly:
            messages.success(request, "Weekly Focus published and sent to parents.")
        else:
            messages.success(request, "Weekly Focus submitted for review. Parents will be notified once approved.")
        return redirect("communications:weekly_focus_submit")


class WeeklyFocusEditView(RoleRequiredMixin, TemplateView):
    """ECD teacher edits an existing Weekly Focus entry — FR-COM-007/008."""
    template_name = "communications/weekly_focus_submit.html"
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.SUPER_ADMIN]
    required_permission = "communications.add_weeklyfocus"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.TEACHER and not is_ecd_teacher(request.user):
            raise PermissionDenied()
        entry = get_object_or_404(WeeklyFocus, pk=kwargs.get("pk", 0))
        if request.user.role == UserRole.TEACHER:
            if entry.teacher != request.user:
                raise PermissionDenied("You can only edit your own submissions.")
            # FR-COM-007: teachers can edit DRAFT, REJECTED, or SUBMITTED entries
            # (SUBMITTED allowed so they can correct errors before HOD review)
            if entry.status not in (WeeklyFocusStatus.DRAFT, WeeklyFocusStatus.REJECTED, WeeklyFocusStatus.SUBMITTED):
                messages.error(request, "You can only edit entries that are in Draft, Rejected, or Submitted status.")
                return redirect("communications:weekly_focus_submit")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        entry = get_object_or_404(WeeklyFocus, pk=self.kwargs["pk"])
        ctx["entry"] = entry
        ctx["is_edit"] = True
        ctx["current_week"] = entry.week_number
        from datetime import date as dt
        ctx["current_year"] = dt.today().year
        ctx["today_date"] = dt.today().strftime("%d %B %Y")
        from core.teacher_context import get_teacher_assigned_classes
        my_classes = sorted(get_teacher_assigned_classes(self.request.user))
        # FR-COM-007: edit dropdown shows only teacher's assigned classes
        ctx["assigned_classes"] = my_classes
        ctx["default_class"] = entry.class_name
        ctx["is_hod"] = self.request.user.has_perm("communications.change_weeklyfocus")

        qs = WeeklyFocus.objects.select_related("teacher").order_by("-created_at")
        if self.request.user.role == UserRole.TEACHER:
            qs = qs.filter(teacher=self.request.user)
        ctx["recent_entries"] = qs[:5]
        return ctx

    def post(self, request, pk):
        entry = get_object_or_404(WeeklyFocus, pk=pk)
        if request.user.role == UserRole.TEACHER and entry.teacher != request.user:
            raise PermissionDenied("You can only edit your own submissions.")

        # Teachers can only edit entries from the current week
        from datetime import date as dt
        current_week = dt.today().isocalendar()[1]
        if request.user.role == UserRole.TEACHER and entry.week_number != current_week:
            messages.error(request, f"You can only edit entries from the current week (Week {current_week}).")
            return redirect("communications:weekly_focus_submit")

        entry.class_name = request.POST.get("class_name", entry.class_name).strip()
        entry.theme = request.POST.get("theme", "").strip()
        entry.planned_activities = request.POST.get("planned_activities", "").strip()
        entry.items_to_bring = request.POST.get("items_to_bring", "").strip()

        if not all([entry.class_name, entry.theme, entry.planned_activities]):
            messages.error(request, "Class, theme and planned activities are required.")
            return redirect("communications:weekly_focus_edit", pk=pk)

        # Re-submit after edit (reset status for teacher, direct publish for HOD)
        if request.user.role in (UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN):
            entry.status = WeeklyFocusStatus.APPROVED
            entry.is_published = True
            entry.published_at = timezone.now()
        else:
            entry.status = WeeklyFocusStatus.SUBMITTED
            entry.submitted_at = timezone.now()
            entry.reviewer_feedback = ""

        entry.save(update_fields=[
            "class_name", "theme", "planned_activities", "items_to_bring",
            "status", "submitted_at", "is_published", "published_at", "updated_at"
        ])

        from audit.models import log_event
        log_event(
            actor=request.user,
            action_type="UPDATE",
            model_name="WeeklyFocus",
            object_id=entry.pk,
            description=f"Weekly Focus Week {entry.week_number} updated and re-submitted for {entry.class_name}",
            request=request,
        )
        messages.success(request, "Weekly Focus updated and re-submitted.")
        return redirect("communications:weekly_focus_submit")


class WeeklyFocusComplianceView(RoleRequiredMixin, TemplateView):
    """ECD HOD compliance view — which teachers have submitted this week — FR-COM-010."""
    template_name = "communications/weekly_focus_compliance.html"
    allowed_roles = [UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.view_weeklyfocus"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from datetime import date as dt
        today = dt.today()
        current_week = today.isocalendar()[1]
        current_year = str(today.year)

        # ECD teachers
        ecd_teachers = User.objects.filter(role=UserRole.TEACHER, is_active=True)
        compliance_rows = []
        for teacher in ecd_teachers:
            entry = WeeklyFocus.objects.filter(
                teacher=teacher, week_number=current_week, academic_year__startswith=current_year
            ).first()
            compliance_rows.append({
                "teacher": teacher,
                "submitted": entry is not None,
                "entry": entry,
            })

        ctx["compliance_rows"] = compliance_rows
        ctx["current_week"] = current_week
        ctx["submitted_count"] = sum(1 for r in compliance_rows if r["submitted"])
        ctx["total_count"] = len(compliance_rows)
        ctx["compliance_rate"] = round(ctx["submitted_count"] / ctx["total_count"] * 100) if ctx["total_count"] else 0
        ctx["today_date"] = today.strftime("%d %B %Y")
        return ctx


class WeeklyFocusReviewView(RoleRequiredMixin, View):
    """ECD HOD/HOS approves or rejects a submitted Weekly Focus — FR-COM-010."""
    allowed_roles = [UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.change_weeklyfocus"

    def post(self, request, pk):
        entry = get_object_or_404(WeeklyFocus, pk=pk)
        if entry.status != WeeklyFocusStatus.SUBMITTED:
            messages.error(request, "Only submitted entries can be reviewed.")
            return redirect("communications:weekly_focus_list")

        decision = request.POST.get("decision", "")
        feedback = request.POST.get("feedback", "").strip()

        if decision == "approve":
            entry.status = WeeklyFocusStatus.APPROVED
            entry.is_published = True
            entry.published_at = timezone.now()
            entry.reviewed_by = request.user
            entry.reviewed_at = timezone.now()
            entry.reviewer_feedback = feedback
            entry.save(update_fields=[
                "status", "is_published", "published_at", "reviewed_by_id",
                "reviewed_at", "reviewer_feedback", "updated_at"
            ])

            # Notify parents
            from students.models import Student, StudentGuardian
            students = Student.objects.filter(class_name=entry.class_name, is_archived=False)
            parent_user_ids = StudentGuardian.objects.filter(
                student__in=students, is_primary=True, guardian__user__isnull=False
            ).values_list("guardian__user_id", flat=True)
            parent_users = User.objects.filter(pk__in=parent_user_ids)

            notifications = [
                Notification(
                    recipient=u,
                    category=NotificationCategory.BROADCAST,
                    title=f"Week {entry.week_number} Focus — {entry.class_name}: {entry.theme}",
                    body=f"Planned activities:\n{entry.planned_activities}" + (
                        f"\n\nItems to bring:\n{entry.items_to_bring}" if entry.items_to_bring else ""
                    ),
                    link=f"/communications/weekly-focus/{entry.pk}/",  # noqa: redirects to list
                )
                for u in parent_users
            ]
            Notification.objects.bulk_create(notifications)

            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="WEEKLY_FOCUS_APPROVED",
                model_name="WeeklyFocus",
                object_id=entry.pk,
                description=f"Weekly Focus Week {entry.week_number} approved for {entry.class_name} by {request.user.get_full_name()}",
                request=request,
            )
            messages.success(request, f"Weekly Focus approved. Parents of {entry.class_name} have been notified.")

        elif decision == "reject":
            if not feedback:
                messages.error(request, "Feedback is required when rejecting a submission.")
                return redirect("communications:weekly_focus_list")
            entry.status = WeeklyFocusStatus.REJECTED
            entry.reviewed_by = request.user
            entry.reviewed_at = timezone.now()
            entry.reviewer_feedback = feedback
            entry.save(update_fields=[
                "status", "reviewed_by_id", "reviewed_at",
                "reviewer_feedback", "updated_at"
            ])

            from audit.models import log_event
            log_event(
                actor=request.user,
                action_type="WEEKLY_FOCUS_REJECTED",
                model_name="WeeklyFocus",
                object_id=entry.pk,
                description=f"Weekly Focus Week {entry.week_number} rejected for {entry.class_name}: {feedback[:100]}",
                request=request,
            )
            messages.success(request, "Weekly Focus rejected. Feedback sent to teacher.")

        else:
            messages.error(request, "Invalid decision. Use 'approve' or 'reject'.")
            return redirect("communications:weekly_focus_list")

        return redirect("communications:weekly_focus_list")


class WeeklyFocusDetailView(RoleRequiredMixin, RedirectView):
    """Redirect to the list (detail is shown inline on the list page)."""
    allowed_roles = [UserRole.TEACHER, UserRole.ECD_HOD, UserRole.HEAD_OF_SCHOOL, UserRole.SUPER_ADMIN]
    required_permission = "communications.view_weeklyfocus"
    pattern_name = "communications:weekly_focus_list"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role == UserRole.TEACHER and not is_ecd_teacher(request.user):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


class WeeklyFocusParentView(RoleRequiredMixin, TemplateView):
    """Parent view: shows published Weekly Focus for their child's ECD class — FR-COM-007."""
    template_name = "communications/weekly_focus_parent.html"
    allowed_roles = [UserRole.PARENT]
    required_permission = "communications.view_weeklyfocus"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from students.models import Student, StudentGuardian
        from academics.models import GradeClass, Department
        from datetime import date as dt

        # Find the parent's ECD children
        parent_user = self.request.user
        guardian_links = StudentGuardian.objects.filter(
            guardian__user=parent_user, is_primary=True
        ).select_related("student")

        ecd_class_names = set(
            GradeClass.objects.filter(department=Department.ECD)
            .values_list("name", flat=True)
        )

        # Collect published weekly focus entries for the parent's ECD children
        entries = []
        seen_classes = set()
        for link in guardian_links:
            cls = link.student.class_name
            if cls in ecd_class_names and cls not in seen_classes:
                seen_classes.add(cls)
                published = WeeklyFocus.objects.filter(
                    class_name=cls, is_published=True
                ).select_related("teacher").order_by("-week_number")
                entries.extend(published)

        ctx["entries"] = entries
        ctx["today_date"] = dt.today().strftime("%d %B %Y")
        return ctx


class RequestOTPView(View):
    """POST {phone} — send OTP for parent verification (USSD/SMS gateway)."""

    def post(self, request):
        import json
        from communications.otp_service import generate_otp
        from core.throttles import rate_limit_or_429

        blocked = rate_limit_or_429(request, "otp_request", limit=5, period=300)
        if blocked:
            return blocked

        try:
            payload = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            payload = request.POST

        phone = (payload.get("phone") or "").strip()
        if len(phone) < 8:
            return JsonResponse({"ok": False, "error": "Valid phone number required."}, status=400)

        debug_code = generate_otp(phone)
        data = {"ok": True, "message": "Verification code sent."}
        if debug_code:
            data["debug_code"] = debug_code
        return JsonResponse(data)


class VerifyOTPView(View):
    """POST {phone, code} — verify OTP."""

    def post(self, request):
        import json
        from communications.otp_service import verify_otp
        from core.throttles import rate_limit_or_429

        blocked = rate_limit_or_429(request, "otp_verify", limit=10, period=300)
        if blocked:
            return blocked

        try:
            payload = json.loads(request.body.decode() or "{}")
        except json.JSONDecodeError:
            payload = request.POST

        phone = (payload.get("phone") or "").strip()
        code = (payload.get("code") or "").strip()
        if not phone or not code:
            return JsonResponse({"ok": False, "error": "Phone and code required."}, status=400)

        if verify_otp(phone, code):
            return JsonResponse({"ok": True, "verified": True})
        return JsonResponse({"ok": False, "verified": False, "error": "Invalid or expired code."}, status=400)
