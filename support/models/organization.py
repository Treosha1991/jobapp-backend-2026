import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class SupportOrganization(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    legal_name = models.CharField(max_length=180)
    display_name = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    verified_document_email = models.EmailField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_organizations",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name", "id")
        indexes = [
            models.Index(fields=("status", "updated_at")),
            models.Index(fields=("legal_name",)),
        ]

    def __str__(self):
        return f"SupportOrganization #{self.id} {self.display_name}"


class OrganizationMembership(models.Model):
    STATE_ACTIVE = "active"
    STATE_SUSPENDED = "suspended"
    STATE_REVOKED = "revoked"
    STATE_CHOICES = [
        (STATE_ACTIVE, "Active"),
        (STATE_SUSPENDED, "Suspended"),
        (STATE_REVOKED, "Revoked"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_memberships",
    )
    display_role = models.CharField(max_length=80, blank=True, default="")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    is_owner = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_memberships",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("organization_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "user"),
                name="support_unique_organization_member",
            ),
            models.UniqueConstraint(
                fields=("organization",),
                condition=Q(is_owner=True),
                name="support_unique_organization_owner",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "state")),
            models.Index(fields=("organization", "state")),
        ]

    @property
    def is_active(self):
        return self.state == self.STATE_ACTIVE

    def __str__(self):
        return (
            f"OrganizationMembership organization={self.organization_id} "
            f"user={self.user_id} state={self.state}"
        )


class MembershipInvitation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_EXPIRED = "expired"
    STATUS_REVOKED = "revoked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REVOKED, "Revoked"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="membership_invitations",
    )
    invited_email = models.EmailField(db_index=True)
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_support_invitations",
    )
    display_role = models.CharField(max_length=80, blank=True, default="")
    state = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_support_invitations",
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "invited_email"),
                condition=Q(state="pending"),
                name="support_unique_pending_invitation_email",
            ),
        ]
        indexes = [
            models.Index(fields=("invited_user", "state")),
            models.Index(fields=("organization", "state")),
            models.Index(fields=("state", "expires_at")),
        ]

    def __str__(self):
        return (
            f"MembershipInvitation organization={self.organization_id} "
            f"email={self.invited_email} state={self.state}"
        )


class InvitationPermissionGrant(models.Model):
    invitation = models.ForeignKey(
        MembershipInvitation,
        on_delete=models.CASCADE,
        related_name="permission_grants",
    )
    permission_code = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("invitation", "permission_code"),
                name="support_unique_invitation_permission",
            ),
        ]


class PermissionGrant(models.Model):
    SCOPE_ORGANIZATION = "organization"
    SCOPE_CHOICES = [(SCOPE_ORGANIZATION, "Organization")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.CASCADE,
        related_name="permission_grants",
    )
    permission_code = models.CharField(max_length=64)
    scope_kind = models.CharField(
        max_length=24,
        choices=SCOPE_CHOICES,
        default=SCOPE_ORGANIZATION,
    )
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_support_permissions",
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_support_permissions",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "permission_code", "scope_kind"),
                condition=Q(is_active=True),
                name="support_unique_active_permission_grant",
            ),
        ]
        indexes = [
            models.Index(fields=("membership", "is_active")),
            models.Index(fields=("permission_code", "is_active")),
        ]


class DelegablePermissionGrant(models.Model):
    """The bounded set of rights a deputy may grant to other staff members."""

    SCOPE_ORGANIZATION = PermissionGrant.SCOPE_ORGANIZATION
    SCOPE_CHOICES = PermissionGrant.SCOPE_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.CASCADE,
        related_name="delegable_permission_grants",
    )
    permission_code = models.CharField(max_length=64)
    scope_kind = models.CharField(
        max_length=24,
        choices=SCOPE_CHOICES,
        default=SCOPE_ORGANIZATION,
    )
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delegated_support_permissions",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "permission_code", "scope_kind"),
                condition=Q(is_active=True),
                name="support_unique_active_delegable_permission",
            ),
        ]
        indexes = [
            models.Index(fields=("membership", "is_active")),
        ]
