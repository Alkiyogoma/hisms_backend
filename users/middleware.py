"""
FR-STAFF-004: Session-cached role middleware.

When a user logs in, their role is stored in the Django session. On every
subsequent request the middleware overwrites user.role on the instance dict
with the session-cached value, so ALL role checks (RoleRequiredMixin,
template tags, direct request.user.role reads) see the login-time role.

Effect: When a Super Admin changes another user's role in the DB, the
affected user's active session continues under the OLD role until they
log out and log back in — matching the BDD requirement exactly.

Django's Field.__get__ reads from instance.__dict__[attname] first, so
writing to __dict__['role'] is sufficient to override the DB value for
the lifetime of the request without touching any other code.
"""

import logging

logger = logging.getLogger(__name__)

# Session key used to store the role at login time
SESSION_ROLE_KEY = "_cached_role"


class SessionRoleMiddleware:
    """
    On every request, if the user is authenticated and a cached role
    exists in the session, inject it into the user instance's __dict__
    so that user.role returns the login-time value.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._apply_session_role(request)
        return self.get_response(request)

    # ------------------------------------------------------------------
    def _apply_session_role(self, request):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return

        session_role = request.session.get(SESSION_ROLE_KEY)
        if not session_role:
            # First request after middleware was added (migration) or
            # session was cleared — seed from the DB so subsequent
            # requests don't break.
            request.session[SESSION_ROLE_KEY] = user.role
            return

        db_role = user.role
        if session_role != db_role:
            # Override the role on the live instance for this request only.
            # Django's CharField.__get__ reads from instance.__dict__
            # first, so this is picked up automatically by:
            #   - RoleRequiredMixin.dispatch()
            #   - Template {% if request.user.role == "..." %} checks
            #   - Direct request.user.role reads in views
            # The DB is NOT touched — the change is request-scoped.
            user.__dict__["role"] = session_role
            logger.debug(
                "Role override applied: user=%s db=%s session=%s",
                user.username, db_role, session_role,
            )
