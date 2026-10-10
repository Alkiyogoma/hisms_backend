"""
Lesson plan viewer: a faithful preview of the attached plan beside a
comment thread between the teacher and reviewers.

* ``thread``  — JSON for the viewer: the plan's files (with how each is
  previewed) and its comments.
* ``preview`` — an attachment as PDF (office files converted on the server).
* ``comment`` — add a comment. Comments never change the plan's status.
"""
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View

from academics import lesson_plan_files
from academics.models import GradeClass, LessonPlan, LessonPlanAttachment, LessonPlanComment, LessonPlanStatus
from core.permissions import RoleRequiredMixin

COMMENT_MAX_LENGTH = 4000


def can_view_plan(user, plan) -> bool:
    """The teacher, and anyone who sees the plan in their lesson plan list:
    reviewers and section heads see every plan once it has been submitted."""
    from academics.views.lesson_plans import _is_hod_like

    if plan.teacher_id == user.id or user.is_school_wide:
        return True
    if user.has_perm("academics.view_all_lessonplans"):
        return True
    reviewer = _is_hod_like(user) or user.has_perm("academics.can_review_lessonplan")
    return reviewer and plan.status != LessonPlanStatus.DRAFT


def can_review_plan(user, plan) -> bool:
    """Same department rule as the Approve / Reject action."""
    from academics.views.lesson_plans import _hod_departments

    if not user.has_perm("academics.can_review_lessonplan"):
        return False
    depts = _hod_departments(user)
    if depts is None:
        return True
    return GradeClass.objects.filter(department__in=depts, name=plan.class_name).exists()


def can_comment(user, plan) -> bool:
    if plan.teacher_id == user.id:
        return True
    return plan.status != LessonPlanStatus.DRAFT and can_review_plan(user, plan)


def _comment_json(c, user):
    author = c.author
    return {
        "id": c.pk,
        "author": author.get_full_name() or author.username,
        "initials": "".join(p[0] for p in (author.get_full_name() or author.username).split()[:2]).upper(),
        "role": "Teacher" if c.author_id == c.lesson_plan.teacher_id else "Reviewer",
        "mine": c.author_id == user.id,
        "body": c.body,
        "created_at": timezone.localtime(c.created_at).isoformat(),
        "created_label": timezone.localtime(c.created_at).strftime("%d %b %Y, %H:%M"),
    }


def _plan_for(request, pk):
    plan = get_object_or_404(LessonPlan.objects.select_related("teacher", "term"), pk=pk)
    if not can_view_plan(request.user, plan):
        raise PermissionDenied()
    return plan


class LessonPlanThreadView(RoleRequiredMixin, View):
    """Files and comments of one plan, for the viewer."""
    required_permission = "academics.view_lessonplan"

    def get(self, request, pk):
        plan = _plan_for(request, pk)
        files = [{
            "id": a.pk,
            "name": a.filename,
            "kind": lesson_plan_files.preview_kind(a.filename),
            "preview_url": reverse("academics:lesson_plan_attachment_preview", args=[a.pk]),
            "download_url": a.file.url,
        } for a in plan.attachments.order_by("created_at", "id")]
        comments = plan.comments.select_related("author", "lesson_plan")
        return JsonResponse({
            "plan": {
                "id": plan.pk,
                "label": f"{plan.lesson_title or plan.subject_name} — {plan.class_name}",
                "teacher": plan.teacher.get_full_name() or plan.teacher.username,
                "status": plan.get_status_display(),
                "feedback": plan.reviewer_feedback,
            },
            "files": files,
            "comments": [_comment_json(c, request.user) for c in comments],
            "can_comment": can_comment(request.user, plan),
            "comment_url": reverse("academics:lesson_plan_comment", args=[plan.pk]),
        })


class LessonPlanAttachmentPreviewView(RoleRequiredMixin, View):
    """An attachment for the viewer: PDFs as they are, office files converted
    to PDF, images and text as they are. ``?download=1`` is not needed: the
    original stays available at its media URL."""
    required_permission = "academics.view_lessonplan"

    def get(self, request, pk):
        att = get_object_or_404(LessonPlanAttachment.objects.select_related("lesson_plan"), pk=pk)
        if not can_view_plan(request.user, att.lesson_plan):
            raise PermissionDenied()
        kind = lesson_plan_files.preview_kind(att.filename)
        if kind == "pdf":
            try:
                path = lesson_plan_files.pdf_for(att)
            except lesson_plan_files.PreviewUnavailable as exc:
                return HttpResponse(str(exc), status=503, content_type="text/plain")
            response = FileResponse(open(path, "rb"), content_type="application/pdf")
        elif kind in ("image", "text"):
            response = FileResponse(att.file.open("rb"))
            if kind == "text":
                response["Content-Type"] = "text/plain; charset=utf-8"
        else:
            return HttpResponse("This file type has no preview.", status=415, content_type="text/plain")
        response["Content-Disposition"] = "inline"
        response["X-Content-Type-Options"] = "nosniff"
        # Uploaded files are untrusted: opened directly (an SVG, say) they
        # must not run script on the app's origin.
        response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        response["Cache-Control"] = "private, max-age=300"
        return response


class LessonPlanCommentView(RoleRequiredMixin, View):
    """Add a comment to a plan's thread; notifies the other side."""
    required_permission = "academics.view_lessonplan"

    def post(self, request, pk):
        plan = _plan_for(request, pk)
        if not can_comment(request.user, plan):
            raise PermissionDenied()
        body = (request.POST.get("body") or "").strip()
        if not body:
            return JsonResponse({"error": "Write a comment first."}, status=400)
        if len(body) > COMMENT_MAX_LENGTH:
            return JsonResponse({"error": f"Keep comments under {COMMENT_MAX_LENGTH} characters."}, status=400)
        comment = LessonPlanComment.objects.create(lesson_plan=plan, author=request.user, body=body)

        from audit.models import log_event
        log_event(
            actor=request.user, action_type="LESSON_PLAN_COMMENT", model_name="LessonPlan", object_id=plan.pk,
            description=f"Comment on lesson plan {plan.class_name} — {plan.subject_name}", request=request,
        )
        _notify_comment(plan, comment)
        return JsonResponse({"comment": _comment_json(comment, request.user)}, status=201)


def _notify_comment(plan, comment):
    """A reviewer's comment goes to the teacher; the teacher's reply goes to
    the reviewers already in the thread (or who reviewed the plan), else to
    the section heads who would review it."""
    from communications.email_service import dispatch_notification
    from users.models import User

    actor = comment.author
    name = actor.get_full_name() or actor.username
    what = f"{plan.class_name} — {plan.subject_name} ({plan.term.name})"
    excerpt = comment.body if len(comment.body) <= 300 else comment.body[:297] + "…"
    link = f"{reverse('academics:lesson_plans')}?plan={plan.pk}"
    if actor.pk != plan.teacher_id:
        recipients = [plan.teacher]
        title = "Comment on your lesson plan"
    else:
        ids = set(plan.comments.exclude(author_id=plan.teacher_id).values_list("author_id", flat=True))
        if plan.reviewed_by_id:
            ids.add(plan.reviewed_by_id)
        if not ids:
            from academics.views.lesson_plans import _notify_hod_lesson_plan
            _notify_hod_lesson_plan(plan, actor, event="comment", detail=excerpt)
            return
        recipients = User.objects.filter(pk__in=ids, is_active=True)
        title = "Teacher replied on a lesson plan"
    for user in recipients:
        dispatch_notification(user=user, title=title, message=f"{name} on {what}:\n\n{excerpt}", link=link, actor=actor)
