from support.models import AuditEvent


def record_audit_event(
    *, organization, actor, action, target=None, details=None, request_id=None
):
    """Write an append-only Support audit event with safe metadata only.

    Callers must pass a deliberately small allow-listed ``details`` mapping.
    No message body, document value, bank data, e-mail content, or push body is
    valid audit metadata.
    """

    target_public_id = getattr(target, "public_id", None) if target else None
    target_type = target.__class__.__name__ if target else ""
    return AuditEvent.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        target_type=target_type,
        target_public_id=target_public_id,
        details=details or {},
        request_id=request_id,
    )
