"""Employer-authorized publishing helpers used by JobHub Board."""

from django.utils import timezone

from .models import EmployerBoardPublishingAuthorization, EmployerBoardPublishingEvent


AUTHORIZATION_VERSION = "2026-07-15"
AUTHORIZATION_TEXT = (
    "I authorize JobHub to prepare and publish job vacancies on behalf of my "
    "employer profile through JobHub Board. I confirm that I have the right to "
    "publish this information. JobHub will use vacancy information that I provide "
    "or approve. I can manage, edit, pause, or remove published vacancies in my "
    "employer portal. I can revoke this permission at any time; revocation blocks "
    "new JobHub Board publications but does not automatically remove vacancies "
    "already published. I will keep the private employer code confidential and "
    "share it only with the JobHub team member preparing a vacancy."
)


def _client_ip(request):
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def request_authorization(employer, *, requested_by=None):
    """Create or renew an employer's pending authorization request."""
    authorization, created = EmployerBoardPublishingAuthorization.objects.get_or_create(
        employer=employer,
        defaults={"requested_by": requested_by, "status": "pending"},
    )
    if not created and authorization.status != "active":
        authorization.status = "pending"
        authorization.requested_by = requested_by
        authorization.accepted_at = None
        authorization.accepted_ip = None
        authorization.accepted_user_agent = ""
        authorization.revoked_at = None
        authorization.revoked_ip = None
        # A renewed request gets a fresh opaque code.
        authorization.board_code = ""
        authorization.save()

    if created or authorization.status == "pending":
        EmployerBoardPublishingEvent.objects.create(
            authorization=authorization,
            action="requested",
            performed_by=requested_by,
            details={"renewed": not created},
        )
    return authorization


def accept_authorization(authorization, *, request):
    if authorization.status != "pending":
        return False
    authorization.status = "active"
    authorization.accepted_at = timezone.now()
    authorization.accepted_ip = _client_ip(request)
    authorization.accepted_user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:500]
    authorization.authorization_version = AUTHORIZATION_VERSION
    authorization.authorization_text = AUTHORIZATION_TEXT
    authorization.save(
        update_fields=[
            "status",
            "accepted_at",
            "accepted_ip",
            "accepted_user_agent",
            "authorization_version",
            "authorization_text",
            "updated_at",
        ]
    )
    EmployerBoardPublishingEvent.objects.create(
        authorization=authorization,
        action="accepted",
        performed_by=authorization.employer,
        details={"version": AUTHORIZATION_VERSION},
    )
    return True


def revoke_authorization(authorization, *, request):
    if authorization.status != "active":
        return False
    authorization.status = "revoked"
    authorization.revoked_at = timezone.now()
    authorization.revoked_ip = _client_ip(request)
    authorization.save(
        update_fields=["status", "revoked_at", "revoked_ip", "updated_at"]
    )
    EmployerBoardPublishingEvent.objects.create(
        authorization=authorization,
        action="revoked",
        performed_by=authorization.employer,
    )
    return True


def active_authorization_for_code(raw_code):
    code = (raw_code or "").strip().upper()
    if not code:
        return None
    return (
        EmployerBoardPublishingAuthorization.objects.select_related("employer")
        .filter(board_code=code, status="active", employer__is_active=True)
        .first()
    )


def authorization_payload_for_employer(employer):
    """Return the safe authorization state shown in the app and web cabinet."""
    authorization = EmployerBoardPublishingAuthorization.objects.filter(
        employer=employer
    ).first()
    if not authorization:
        return {"status": "none", "has_pending_request": False}

    payload = {
        "status": authorization.status,
        "has_pending_request": authorization.status == "pending",
        "requested_at": authorization.requested_at.isoformat()
        if authorization.requested_at
        else None,
        "accepted_at": authorization.accepted_at.isoformat()
        if authorization.accepted_at
        else None,
        "revoked_at": authorization.revoked_at.isoformat()
        if authorization.revoked_at
        else None,
    }
    if authorization.status in {"pending", "active"}:
        payload["authorization_text"] = AUTHORIZATION_TEXT
    if authorization.status == "active":
        payload["board_code"] = authorization.board_code
    return payload


def record_publication(authorization, vacancy):
    return EmployerBoardPublishingEvent.objects.create(
        authorization=authorization,
        action="published",
        vacancy=vacancy,
        details={"vacancy_id": vacancy.id},
    )
