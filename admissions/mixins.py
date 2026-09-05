from django.db.models import Count, Q

from admissions.models import Applicant, ApplicantStatus


class PermissionCacheMixin:
    """Precompute user permissions once per request and cache on request._user_perms.

    The {% has_perm %} template tag reads from this cache for O(1) lookups
    instead of hitting the DB up to 5 times per call.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            from users.templatetags.role_tags import _build_user_perms
            _build_user_perms(request.user)
        return super().dispatch(request, *args, **kwargs)


class AdmissionsCountsMixin:
    """Mixin that provides standard admissions counts to template context.

    Injects ``assessment_count``, ``waitlist_count``, and
    ``critical_action_count`` used by the top navigation tabs across all
    admissions views.
    """

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["assessment_count"] = (
            Applicant.objects.filter(assessment__isnull=False)
            .exclude(
                status__in=[
                    ApplicantStatus.ASSESSMENT_COMPLETED,
                    ApplicantStatus.HOS_REVIEW,
                    ApplicantStatus.HOS_DECISION,
                    ApplicantStatus.ADMITTED,
                    ApplicantStatus.CONDITIONAL,
                    ApplicantStatus.ENROLLED,
                    ApplicantStatus.WITHDRAWN,
                    ApplicantStatus.DENIED,
                    ApplicantStatus.MEETING_COMPLETED,
                    ApplicantStatus.DECLINED_AT_MEETING,
                    ApplicantStatus.REPORT_PENDING,
                    ApplicantStatus.ASSESSMENT_FAILED,
                    ApplicantStatus.FORM_SUBMITTED,
                    ApplicantStatus.INVOICE_GENERATED,
                    ApplicantStatus.INVOICE_PAID,
                    ApplicantStatus.FLAGGED_FOR_REVIEW,
                ]
            )
            .count()
        )
        ctx["waitlist_count"] = Applicant.objects.filter(
            status=ApplicantStatus.WAITLISTED
        ).count()
        from admissions.services import get_critical_actions
        ctx["critical_action_count"] = len(get_critical_actions())
        return ctx
