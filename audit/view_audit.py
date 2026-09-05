"""
NFR-PDPA-006: View-level audit logging for sensitive personal data.

Every time a user accesses medical information, welfare records, or
financial records, an audit entry is created capturing the user,
the record accessed, and the timestamp.

Usage in views:
    from audit.view_audit import log_sensitive_access
    log_sensitive_access(request, "Student", student.pk, "medical")
"""

import logging
from core.utils import get_client_ip
from .models import AuditLog

logger = logging.getLogger(__name__)

SENSITIVE_CATEGORIES = {
    "medical": "Sensitive medical data accessed",
    "welfare": "Welfare record accessed",
    "finance": "Financial record accessed",
}


def log_sensitive_access(request, model_name, object_id, category, description=None):
    """
    Log access to sensitive personal data per NFR-PDPA-006.

    Args:
        request: The HTTP request (used to extract user and IP).
        model_name: The model being accessed (e.g. "Student", "WelfareObservation").
        object_id: The primary key of the record accessed.
        category: One of "medical", "welfare", "finance".
        description: Optional human-readable description override.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return

    ip = get_client_ip(request)

    action_desc = description or SENSITIVE_CATEGORIES.get(category, f"Sensitive data accessed ({category})")

    try:
        AuditLog.objects.create(
            actor=user,
            action_type="SENSITIVE_DATA_VIEW",
            model_name=model_name,
            object_id=str(object_id),
            description=action_desc,
            ip_address=ip,
        )
    except Exception as exc:
        logger.warning("Failed to log sensitive data access: %s", exc)
