from typing import Any

from audit.models import AuditLog


def log_event(
    *,
    actor,
    action_type: str,
    model_name: str,
    object_id: str | int = "",
    description: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: dict[str, Any] | None = None,
    ip_address: str | None = None,
    request=None,
) -> AuditLog:
    # Accept both before/after (shorthand) and before_snapshot/after_snapshot
    _before = before or before_snapshot
    _after = after or after_snapshot
    _ip = ip_address
    if _ip is None and request is not None:
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        _ip = x_forwarded.split(",")[0].strip() if x_forwarded else request.META.get("REMOTE_ADDR")
    return AuditLog.objects.create(
        actor=actor,
        action_type=action_type,
        model_name=model_name,
        object_id=str(object_id),
        description=description,
        before_snapshot=_before,
        after_snapshot=_after,
        ip_address=_ip,
    )
