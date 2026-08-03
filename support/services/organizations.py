from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    DelegablePermissionGrant,
    InvitationPermissionGrant,
    MembershipInvitation,
    OrganizationMembership,
    PermissionGrant,
    SupportOrganization,
    WorkerAccessScope,
)
from support.permission_codes import (
    MEMBER_DELEGATE_PERMISSIONS,
    MEMBER_INVITE,
    ORGANIZATION_MANAGE,
)
from support.permissions import (
    active_membership_for,
    may_delegate_permission,
    require_permission,
    validate_permission_code,
)

from .audit import record_audit_event


DEFAULT_INVITATION_DAYS = 7


def _normalized_email(email):
    return (email or "").strip().lower()


def create_organization(*, jobhub_operator, legal_name, display_name, owner_email):
    if not jobhub_operator or not jobhub_operator.is_staff:
        raise PermissionDenied("support_operator_required")

    normalized_email = _normalized_email(owner_email)
    user_model = get_user_model()
    owner = user_model.objects.filter(email__iexact=normalized_email, is_active=True).first()
    if owner is None:
        raise ValidationError({"owner_email": "registered_jobhub_account_required"})

    with transaction.atomic():
        organization = SupportOrganization.objects.create(
            legal_name=(legal_name or "").strip(),
            display_name=(display_name or "").strip(),
            created_by=jobhub_operator,
        )
        membership = OrganizationMembership.objects.create(
            organization=organization,
            user=owner,
            display_role="Owner",
            is_owner=True,
            created_by=jobhub_operator,
            accepted_at=timezone.now(),
        )
        record_audit_event(
            organization=organization,
            actor=jobhub_operator,
            action="organization.created",
            target=organization,
            details={"owner_membership": str(membership.public_id)},
        )
    return organization, membership


def activate_organization(*, jobhub_operator, organization):
    """Complete JobHub's manual verification step for an employer firm."""

    if not jobhub_operator or not jobhub_operator.is_staff:
        raise PermissionDenied("support_operator_required")
    with transaction.atomic():
        organization = SupportOrganization.objects.select_for_update().get(pk=organization.pk)
        if organization.status == SupportOrganization.STATUS_ARCHIVED:
            raise ValidationError({"organization": "archived_organization_cannot_be_activated"})
        if organization.status != SupportOrganization.STATUS_ACTIVE:
            organization.status = SupportOrganization.STATUS_ACTIVE
            organization.activated_at = timezone.now()
            organization.suspended_at = None
            organization.save(
                update_fields=["status", "activated_at", "suspended_at", "updated_at"]
            )
            record_audit_event(
                organization=organization,
                actor=jobhub_operator,
                action="organization.activated",
                target=organization,
                details={},
            )
    return organization


def create_membership_invitation(
    *,
    actor,
    organization,
    invited_email,
    display_role,
    permission_codes,
    expires_at=None,
):
    actor_membership = require_permission(
        user=actor,
        organization=organization,
        permission_code=MEMBER_INVITE,
    )
    normalized_codes = sorted({validate_permission_code(code) for code in permission_codes})
    if not actor_membership.is_owner:
        require_permission(
            user=actor,
            organization=organization,
            permission_code=MEMBER_DELEGATE_PERMISSIONS,
        )
        denied_codes = [
            code
            for code in normalized_codes
            if not may_delegate_permission(
                user=actor,
                organization=organization,
                permission_code=code,
            )
        ]
        if denied_codes:
            raise PermissionDenied("support_permission_delegation_denied")

    normalized_email = _normalized_email(invited_email)
    user_model = get_user_model()
    invited_user = user_model.objects.filter(
        email__iexact=normalized_email,
        is_active=True,
    ).first()
    if invited_user is None:
        raise ValidationError({"email": "registered_jobhub_account_required"})
    if OrganizationMembership.objects.filter(
        organization=organization,
        user=invited_user,
    ).exists():
        raise ValidationError({"email": "organization_membership_already_exists"})

    expiry = expires_at or (timezone.now() + timedelta(days=DEFAULT_INVITATION_DAYS))
    if expiry <= timezone.now():
        raise ValidationError({"expires_at": "invitation_expiry_must_be_future"})

    try:
        with transaction.atomic():
            invitation = MembershipInvitation.objects.create(
                organization=organization,
                invited_email=normalized_email,
                invited_user=invited_user,
                display_role=(display_role or "").strip(),
                created_by=actor,
                expires_at=expiry,
            )
            InvitationPermissionGrant.objects.bulk_create(
                [
                    InvitationPermissionGrant(
                        invitation=invitation,
                        permission_code=permission_code,
                    )
                    for permission_code in normalized_codes
                ]
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="membership.invited",
                target=invitation,
                details={"permission_codes": normalized_codes},
            )
    except IntegrityError as exc:
        raise ValidationError({"email": "pending_invitation_already_exists"}) from exc
    return invitation


def accept_membership_invitation(*, user, invitation):
    with transaction.atomic():
        invitation = (
            MembershipInvitation.objects.select_for_update()
            .select_related("organization", "invited_user")
            .prefetch_related("permission_grants")
            .filter(pk=invitation.pk)
            .first()
        )
        if invitation is None or invitation.invited_user_id != user.id:
            raise PermissionDenied("support_invitation_not_available")
        if invitation.state != MembershipInvitation.STATUS_PENDING:
            raise ValidationError({"invitation": "invitation_not_pending"})
        if invitation.expires_at <= timezone.now():
            invitation.state = MembershipInvitation.STATUS_EXPIRED
            invitation.save(update_fields=["state", "updated_at"])
            raise ValidationError({"invitation": "invitation_expired"})
        if OrganizationMembership.objects.filter(
            organization=invitation.organization,
            user=user,
        ).exists():
            raise ValidationError({"invitation": "organization_membership_already_exists"})

        membership = OrganizationMembership.objects.create(
            organization=invitation.organization,
            user=user,
            display_role=invitation.display_role,
            created_by=invitation.created_by,
            accepted_at=timezone.now(),
        )
        PermissionGrant.objects.bulk_create(
            [
                PermissionGrant(
                    membership=membership,
                    permission_code=permission_grant.permission_code,
                    granted_by=invitation.created_by,
                )
                for permission_grant in invitation.permission_grants.all()
            ]
        )
        invitation.state = MembershipInvitation.STATUS_ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["state", "accepted_at", "updated_at"])
        record_audit_event(
            organization=invitation.organization,
            actor=user,
            action="membership.invitation_accepted",
            target=membership,
            details={"invitation": str(invitation.public_id)},
        )
    return membership


def grant_permission(*, actor, organization, membership, permission_code):
    normalized_code = validate_permission_code(permission_code)
    if membership.organization_id != organization.id or not membership.is_active:
        raise ValidationError({"membership": "membership_not_active_in_organization"})
    actor_membership = require_permission(
        user=actor,
        organization=organization,
        permission_code=MEMBER_DELEGATE_PERMISSIONS,
    )
    if not actor_membership.is_owner and not may_delegate_permission(
        user=actor,
        organization=organization,
        permission_code=normalized_code,
    ):
        raise PermissionDenied("support_permission_delegation_denied")

    grant, created = PermissionGrant.objects.get_or_create(
        membership=membership,
        permission_code=normalized_code,
        scope_kind=PermissionGrant.SCOPE_ORGANIZATION,
        is_active=True,
        defaults={"granted_by": actor},
    )
    if created:
        record_audit_event(
            organization=organization,
            actor=actor,
            action="permission.granted",
            target=grant,
            details={"permission_code": normalized_code},
        )
    return grant, created


def grant_delegable_permission(*, actor, organization, membership, permission_code):
    normalized_code = validate_permission_code(permission_code)
    actor_membership = active_membership_for(user=actor, organization=organization)
    if actor_membership is None or not actor_membership.is_owner:
        raise PermissionDenied("support_owner_required")
    if membership.organization_id != organization.id or not membership.is_active:
        raise ValidationError({"membership": "membership_not_active_in_organization"})

    grant, created = DelegablePermissionGrant.objects.get_or_create(
        membership=membership,
        permission_code=normalized_code,
        scope_kind=DelegablePermissionGrant.SCOPE_ORGANIZATION,
        is_active=True,
        defaults={"granted_by": actor},
    )
    if created:
        record_audit_event(
            organization=organization,
            actor=actor,
            action="permission.delegable_granted",
            target=grant,
            details={"permission_code": normalized_code},
        )
    return grant, created


def grant_worker_access_scope(*, actor, organization, membership, connection):
    """Give one staff membership access to one worker without widening rights."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    if membership.organization_id != organization.id or not membership.is_active:
        raise ValidationError({"membership": "membership_not_active_in_organization"})
    if connection.organization_id != organization.id or connection.is_archived:
        raise ValidationError({"connection": "connection_not_in_organization"})
    scope, created = WorkerAccessScope.objects.get_or_create(
        membership=membership,
        connection=connection,
        is_active=True,
        defaults={"granted_by": actor},
    )
    if created:
        record_audit_event(
            organization=organization,
            actor=actor,
            action="worker_scope.granted",
            target=scope,
            details={
                "membership": str(membership.public_id),
                "connection": str(connection.public_id),
            },
        )
    return scope, created


def revoke_worker_access_scope(*, actor, scope):
    """Revoke one worker scope while retaining the access history."""

    organization = scope.membership.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    with transaction.atomic():
        scope = (
            WorkerAccessScope.objects.select_for_update()
            .select_related("membership__organization", "connection")
            .get(pk=scope.pk)
        )
        if not scope.is_active:
            raise ValidationError({"scope": "worker_scope_already_revoked"})
        scope.is_active = False
        scope.revoked_by = actor
        scope.revoked_at = timezone.now()
        scope.save(update_fields=["is_active", "revoked_by", "revoked_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="worker_scope.revoked",
            target=scope,
            details={
                "membership": str(scope.membership.public_id),
                "connection": str(scope.connection.public_id),
            },
        )
    return scope
