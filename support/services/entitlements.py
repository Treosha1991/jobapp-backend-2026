from django.db import transaction
from django.utils import timezone

from support.models import SupportAccessGrant

from .notifications import enqueue_support_notification


def active_temporary_grant_for(user, *, at_time=None):
    current_time = at_time or timezone.now()
    return (
        SupportAccessGrant.objects.filter(
            user=user,
            status=SupportAccessGrant.STATUS_ACTIVE,
            starts_at__lte=current_time,
            ends_at__gt=current_time,
        )
        .order_by("-ends_at", "-id")
        .first()
    )


def support_access_snapshot_for(user, *, at_time=None):
    """Return the Package-1 portion of a future effective Support entitlement.

    Store subscriptions are deliberately not consulted yet.  A later package
    adds verified Apple/Google subscription records to this one service rather
    than making screens inspect payment models on their own.
    """

    grant = active_temporary_grant_for(user, at_time=at_time)
    if grant is None:
        return {"state": "not_configured", "source": "none", "ends_at": None}
    return {
        "state": "active",
        "source": "temporary_grant",
        "ends_at": grant.ends_at,
    }


def expire_elapsed_temporary_access_grants(*, limit=100, at_time=None):
    """Mark elapsed temporary grants expired and notify the affected user.

    Access evaluation already checks ``ends_at``.  This job adds the durable
    audit-friendly state transition and the neutral notification-center event;
    it never prolongs or recreates access.
    """

    current_time = at_time or timezone.now()
    candidate_ids = list(
        SupportAccessGrant.objects.filter(
            status=SupportAccessGrant.STATUS_ACTIVE,
            ends_at__lte=current_time,
        )
        .order_by("ends_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    expired = 0
    for grant_id in candidate_ids:
        with transaction.atomic():
            grant = (
                SupportAccessGrant.objects.select_for_update()
                .select_related("user", "organization")
                .filter(
                    pk=grant_id,
                    status=SupportAccessGrant.STATUS_ACTIVE,
                    ends_at__lte=current_time,
                )
                .first()
            )
            if grant is None:
                continue
            grant.status = SupportAccessGrant.STATUS_EXPIRED
            grant.save(update_fields=["status", "updated_at"])
            enqueue_support_notification(
                organization=grant.organization,
                recipient=grant.user,
                notification_code="support.access_changed",
                target_kind="support_access",
                target_public_id=grant.public_id,
                target_key=f"support:access:{grant.public_id}",
                collapse_key=f"support:access:{grant.user_id}",
                dedupe_key=f"support.access.expired:{grant.public_id}",
            )
            expired += 1
    return expired
