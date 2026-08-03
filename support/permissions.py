from rest_framework.exceptions import PermissionDenied

from .models import (
    DelegablePermissionGrant,
    OrganizationMembership,
    PermissionGrant,
    WorkerAccessScope,
)
from .permission_codes import ALL_PERMISSION_CODES, ORGANIZATION_MANAGE


def validate_permission_code(permission_code):
    normalized = (permission_code or "").strip()
    if normalized not in ALL_PERMISSION_CODES:
        raise ValueError("unsupported_permission_code")
    return normalized


def active_membership_for(*, user, organization):
    return (
        OrganizationMembership.objects.filter(
            organization=organization,
            user=user,
            state=OrganizationMembership.STATE_ACTIVE,
        )
        .select_related("organization", "user")
        .first()
    )


def has_permission(*, user, organization, permission_code):
    membership = active_membership_for(user=user, organization=organization)
    if membership is None:
        return False
    if membership.is_owner:
        return True
    return PermissionGrant.objects.filter(
        membership=membership,
        permission_code=permission_code,
        scope_kind=PermissionGrant.SCOPE_ORGANIZATION,
        is_active=True,
    ).exists()


def require_permission(*, user, organization, permission_code):
    membership = active_membership_for(user=user, organization=organization)
    if membership is None or not has_permission(
        user=user,
        organization=organization,
        permission_code=permission_code,
    ):
        raise PermissionDenied("support_permission_denied")
    return membership


def may_delegate_permission(*, user, organization, permission_code):
    membership = active_membership_for(user=user, organization=organization)
    if membership is None:
        return False
    if membership.is_owner:
        return True
    return DelegablePermissionGrant.objects.filter(
        membership=membership,
        permission_code=permission_code,
        scope_kind=DelegablePermissionGrant.SCOPE_ORGANIZATION,
        is_active=True,
    ).exists()


def has_unrestricted_worker_access(*, user, organization):
    """Whether a staff member can open every worker in their organization.

    The owner always has this access.  A deputy gets it only when the owner
    explicitly grants ``organization.manage``; operational permissions alone
    never silently reveal every worker.
    """

    membership = active_membership_for(user=user, organization=organization)
    if membership is None:
        return False
    if membership.is_owner:
        return True
    return PermissionGrant.objects.filter(
        membership=membership,
        permission_code=ORGANIZATION_MANAGE,
        scope_kind=PermissionGrant.SCOPE_ORGANIZATION,
        is_active=True,
    ).exists()


def worker_connection_queryset_for(*, user, organization, queryset):
    """Restrict a SupportConnection queryset to the staff member's scope."""

    membership = active_membership_for(user=user, organization=organization)
    queryset = queryset.filter(organization=organization)
    if membership is None:
        return queryset.none()
    if has_unrestricted_worker_access(user=user, organization=organization):
        return queryset
    scope_connection_ids = WorkerAccessScope.objects.filter(
        membership=membership,
        is_active=True,
    ).values("connection_id")
    return queryset.filter(id__in=scope_connection_ids)


def has_worker_connection_access(*, user, organization, connection):
    if connection.organization_id != organization.id:
        return False
    if has_unrestricted_worker_access(user=user, organization=organization):
        return True
    membership = active_membership_for(user=user, organization=organization)
    if membership is None:
        return False
    return WorkerAccessScope.objects.filter(
        membership=membership,
        connection=connection,
        is_active=True,
    ).exists()


def require_worker_connection_access(*, user, organization, connection):
    if not has_worker_connection_access(
        user=user,
        organization=organization,
        connection=connection,
    ):
        raise PermissionDenied("support_worker_scope_denied")
    return connection
