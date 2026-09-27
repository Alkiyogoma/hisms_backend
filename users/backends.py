"""
Staff sign in with either their username (e.g. ``ebenezer.robert``) or their
school email address; the same password works for both.
"""
from django.contrib.auth.backends import ModelBackend

from users.models import User


def find_user_by_login(identifier):
    """Resolve a login identifier to a user: exact username first, then email
    (case-insensitive). Returns None when nothing — or more than one account —
    matches, so an ambiguous email can never sign someone into the wrong account."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = User.objects.filter(username=identifier).first()
    if user is not None:
        return user
    if "@" in identifier:
        matches = list(User.objects.filter(email__iexact=identifier)[:2])
        if len(matches) == 1:
            return matches[0]
    return None


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        user = find_user_by_login(username)
        if user is None:
            # Run the hasher anyway so response time doesn't reveal whether
            # the account exists (same mitigation as ModelBackend).
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
