from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from jobs.avatar_utils import avatar_public_url
from jobs.models import UserBlock

from support.models import (
    InAppNotification,
    OrganizationMembership,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
)
from support.permission_codes import CHAT_MANAGE
from support.permissions import (
    active_membership_for,
    has_permission,
    require_worker_connection_access,
    worker_connection_queryset_for,
)

from .audit import record_audit_event
from .entitlements import support_access_snapshot_for
from .notifications import enqueue_support_notification


def _contact_identity(user):
    profile = getattr(user, "profile", None)
    return {
        "first_name": (user.first_name or "").strip(),
        "last_name": (user.last_name or "").strip(),
        "display_name": (
            user.get_full_name().strip() or user.username or user.email
        ),
        "avatar_url": avatar_public_url(getattr(profile, "avatar_key", "")),
    }


def find_private_manager_conversation(*, organization, worker, manager):
    """Return only the private conversation belonging to this exact pair."""

    return (
        SupportConversation.objects.filter(
            organization=organization,
            kind=SupportConversation.KIND_MANAGER,
            state=SupportConversation.STATE_ACTIVE,
        )
        .filter(
            Q(private_worker=worker, private_manager=manager)
            | Q(
                private_worker__isnull=True,
                private_manager__isnull=True,
                connection__candidate=worker,
            )
        )
        .filter(
            members__user=manager,
            members__role=SupportConversationMember.ROLE_STAFF,
            members__left_at__isnull=True,
        )
        .filter(
            members__user=worker,
            members__role=SupportConversationMember.ROLE_WORKER,
            members__left_at__isnull=True,
        )
        .distinct()
        .order_by("-updated_at", "-id")
        .first()
    )


def _restore_private_conversation_member(
    *, conversation, user, role, organization_membership=None
):
    member, created = SupportConversationMember.objects.get_or_create(
        conversation=conversation,
        user=user,
        defaults={
            "organization_membership": organization_membership,
            "role": role,
        },
    )
    if created:
        return
    updates = []
    if member.role != role:
        member.role = role
        updates.append("role")
    expected_membership_id = (
        organization_membership.id if organization_membership is not None else None
    )
    if member.organization_membership_id != expected_membership_id:
        member.organization_membership = organization_membership
        updates.append("organization_membership")
    if member.left_at is not None:
        member.left_at = None
        updates.append("left_at")
    if updates:
        member.save(update_fields=updates)


def _open_private_manager_conversation(*, connection, manager_membership, created_by):
    """Restore one private organization/worker/manager conversation."""

    conversation = (
        SupportConversation.objects.select_for_update()
        .filter(
            organization=connection.organization,
            kind=SupportConversation.KIND_MANAGER,
            private_worker=connection.candidate,
            private_manager=manager_membership.user,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if conversation is None:
        conversation = (
            SupportConversation.objects.select_for_update()
            .filter(
                organization=connection.organization,
                kind=SupportConversation.KIND_MANAGER,
                connection__candidate=connection.candidate,
                private_worker__isnull=True,
                private_manager__isnull=True,
                members__user=manager_membership.user,
                members__role=SupportConversationMember.ROLE_STAFF,
            )
            .order_by("-updated_at", "-id")
            .first()
        )
    created = conversation is None
    if conversation is None:
        conversation = SupportConversation.objects.create(
            organization=connection.organization,
            connection=connection,
            kind=SupportConversation.KIND_MANAGER,
            private_worker=connection.candidate,
            private_manager=manager_membership.user,
            created_by=created_by,
        )
    else:
        updates = []
        if conversation.private_worker_id != connection.candidate_id:
            conversation.private_worker = connection.candidate
            updates.append("private_worker")
        if conversation.private_manager_id != manager_membership.user_id:
            conversation.private_manager = manager_membership.user
            updates.append("private_manager")
        if conversation.state != SupportConversation.STATE_ACTIVE:
            conversation.state = SupportConversation.STATE_ACTIVE
            updates.append("state")
        if conversation.archived_at is not None:
            conversation.archived_at = None
            updates.append("archived_at")
        if updates:
            conversation.save(update_fields=[*updates, "updated_at"])

    _restore_private_conversation_member(
        conversation=conversation,
        user=connection.candidate,
        role=SupportConversationMember.ROLE_WORKER,
    )
    _restore_private_conversation_member(
        conversation=conversation,
        user=manager_membership.user,
        role=SupportConversationMember.ROLE_STAFF,
        organization_membership=manager_membership,
    )
    SupportConversationMember.objects.filter(
        conversation=conversation,
        left_at__isnull=True,
    ).exclude(
        user_id__in=(connection.candidate_id, manager_membership.user_id)
    ).update(left_at=timezone.now())
    return conversation, created


def open_manager_conversation(*, candidate, connection):
    """Candidate action that opens the approved manager chat.

    Approval alone never starts a conversation.  A candidate must have an
    effective Support entitlement and choose to open it themselves.
    """

    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .get(pk=connection.pk)
        )
        if connection.candidate_id != candidate.id or connection.is_archived:
            raise PermissionDenied("support_connection_not_available")
        if support_access_snapshot_for(candidate)["state"] != "active":
            raise PermissionDenied("support_access_required")
        if connection.stage == SupportConnection.STAGE_CLOSED:
            raise ValidationError({"connection": "manager_chat_not_available_at_current_stage"})
        manager_membership = connection.assigned_manager
        if manager_membership is None or not manager_membership.is_active:
            raise ValidationError({"connection": "assigned_manager_not_available"})
        if not has_permission(
            user=manager_membership.user,
            organization=connection.organization,
            permission_code=CHAT_MANAGE,
        ):
            raise ValidationError({"connection": "assigned_manager_chat_not_available"})

        conversation, created = _open_private_manager_conversation(
            connection=connection,
            manager_membership=manager_membership,
            created_by=candidate,
        )
        if connection.stage == SupportConnection.STAGE_AWAITING_SUPPORT:
            previous_stage = connection.stage
            connection.stage = SupportConnection.STAGE_MANAGER
            connection.save(update_fields=["stage", "updated_at"])
            from support.models import ConnectionStageEvent

            ConnectionStageEvent.objects.create(
                connection=connection,
                previous_stage=previous_stage,
                next_stage=SupportConnection.STAGE_MANAGER,
                reason="candidate_opened_manager_chat",
                actor=candidate,
            )
        record_audit_event(
            organization=connection.organization,
            actor=candidate,
            action="conversation.manager_opened",
            target=conversation,
            details={"created": created},
        )
    return conversation, created


def open_manager_conversation_for_staff(*, actor, connection):
    """Create or restore the actor's own private worker conversation."""

    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .get(pk=connection.pk)
        )
        if connection.is_archived or connection.stage == SupportConnection.STAGE_CLOSED:
            raise ValidationError({"connection": "manager_chat_not_available_at_current_stage"})
        actor_membership = active_membership_for(
            user=actor,
            organization=connection.organization,
        )
        if actor_membership is None or not has_permission(
            user=actor,
            organization=connection.organization,
            permission_code=CHAT_MANAGE,
        ):
            raise PermissionDenied("support_permission_denied")
        conversation, created = _open_private_manager_conversation(
            connection=connection,
            manager_membership=actor_membership,
            created_by=actor,
        )
        record_audit_event(
            organization=connection.organization,
            actor=actor,
            action="conversation.manager_opened_by_staff",
            target=conversation,
            details={"created": created},
        )
    return conversation, created


def open_staff_conversation(*, actor, target_membership):
    """Open a private company chat between two active staff accounts."""

    organization = target_membership.organization
    actor_membership = active_membership_for(user=actor, organization=organization)
    if (
        actor_membership is None
        or not target_membership.is_active
        or target_membership.user_id == actor.id
    ):
        raise PermissionDenied("support_staff_conversation_not_available")

    with transaction.atomic():
        candidates = (
            SupportConversation.objects.select_for_update()
            .filter(
                organization=organization,
                connection__isnull=True,
                kind=SupportConversation.KIND_JOBHUB,
                members__user=actor,
            )
            .prefetch_related("members__user")
            .distinct()
        )
        conversation = next(
            (
                item
                for item in candidates
                if {
                    member.user_id
                    for member in item.members.all()
                    if member.left_at is None
                }
                == {actor.id, target_membership.user_id}
                and all(
                    member.role == SupportConversationMember.ROLE_STAFF
                    for member in item.members.all()
                    if member.left_at is None
                )
            ),
            None,
        )
        created = conversation is None
        if conversation is None:
            conversation = SupportConversation.objects.create(
                organization=organization,
                kind=SupportConversation.KIND_JOBHUB,
                created_by=actor,
            )
            SupportConversationMember.objects.bulk_create(
                [
                    SupportConversationMember(
                        conversation=conversation,
                        user=actor,
                        organization_membership=actor_membership,
                        role=SupportConversationMember.ROLE_STAFF,
                    ),
                    SupportConversationMember(
                        conversation=conversation,
                        user=target_membership.user,
                        organization_membership=target_membership,
                        role=SupportConversationMember.ROLE_STAFF,
                    ),
                ]
            )
        else:
            updates = []
            if conversation.state != SupportConversation.STATE_ACTIVE:
                conversation.state = SupportConversation.STATE_ACTIVE
                updates.append("state")
            if conversation.archived_at is not None:
                conversation.archived_at = None
                updates.append("archived_at")
            if updates:
                conversation.save(update_fields=[*updates, "updated_at"])
            for membership in (actor_membership, target_membership):
                member = conversation.members.get(user=membership.user)
                member_updates = []
                if member.left_at is not None:
                    member.left_at = None
                    member_updates.append("left_at")
                if member.organization_membership_id != membership.id:
                    member.organization_membership = membership
                    member_updates.append("organization_membership")
                if member_updates:
                    member.save(update_fields=member_updates)

        record_audit_event(
            organization=organization,
            actor=actor,
            action="conversation.staff_opened",
            target=conversation,
            details={
                "created": created,
                "target_membership_id": str(target_membership.public_id),
            },
        )
    return conversation, created


def _active_member_or_denied(*, user, conversation):
    if (
        conversation.kind == SupportConversation.KIND_MANAGER
        and conversation.private_worker_id is not None
        and conversation.private_manager_id is not None
        and user.id not in {
            conversation.private_worker_id,
            conversation.private_manager_id,
        }
    ):
        raise PermissionDenied("support_conversation_not_available")
    member = SupportConversationMember.objects.filter(
        conversation=conversation,
        user=user,
        left_at__isnull=True,
    ).select_related("organization_membership__organization").first()
    if member is None:
        raise PermissionDenied("support_conversation_not_available")
    return member


def _can_use_conversation(*, user, conversation, member):
    if member.role == SupportConversationMember.ROLE_STAFF:
        active_membership = active_membership_for(user=user, organization=conversation.organization)
        if active_membership is None:
            raise PermissionDenied("support_conversation_not_available")
        if conversation.kind == SupportConversation.KIND_JOBHUB and conversation.connection_id is None:
            return
        if not has_permission(
            user=user,
            organization=conversation.organization,
            permission_code=CHAT_MANAGE,
        ):
            raise PermissionDenied("support_permission_denied")
        return
    if support_access_snapshot_for(user)["state"] != "active":
        raise PermissionDenied("support_access_required")


def require_conversation_access(*, user, conversation):
    """Return the active member after applying worker/staff access rules."""

    member = _active_member_or_denied(user=user, conversation=conversation)
    _can_use_conversation(user=user, conversation=conversation, member=member)
    return member


def send_text_message(
    *,
    sender,
    conversation,
    body,
    original_language,
    client_message_id,
    reply_to=None,
    forwarded_from=None,
    kind=SupportMessage.KIND_TEXT,
    shared_contact_user=None,
    shared_contact_connection=None,
    shared_contact_membership=None,
):
    with transaction.atomic():
        conversation = SupportConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.state != SupportConversation.STATE_ACTIVE:
            raise ValidationError({"conversation": "conversation_archived"})
        member = require_conversation_access(user=sender, conversation=conversation)
        if not member.can_send:
            raise PermissionDenied("support_message_sending_not_available")
        other_user_ids = SupportConversationMember.objects.filter(
            conversation=conversation,
            left_at__isnull=True,
        ).exclude(user=sender).values_list("user_id", flat=True)
        if UserBlock.objects.filter(
            Q(blocker=sender, blocked_user_id__in=other_user_ids)
            | Q(blocked_user=sender, blocker_id__in=other_user_ids)
        ).exists():
            raise PermissionDenied("support_conversation_blocked")
        if reply_to is not None and reply_to.conversation_id != conversation.id:
            raise ValidationError({"reply_to": "support_reply_message_not_available"})
        message, created = SupportMessage.objects.get_or_create(
            conversation=conversation,
            client_message_id=client_message_id,
            defaults={
                "sender": sender,
                "body": body.strip(),
                "original_language": original_language,
                "reply_to": reply_to,
                "forwarded_from": forwarded_from,
                "kind": kind,
                "shared_contact_user": shared_contact_user,
                "shared_contact_connection": shared_contact_connection,
                "shared_contact_membership": shared_contact_membership,
            },
        )
        if not created and message.sender_id != sender.id:
            raise PermissionDenied("support_message_id_not_available")
        if created:
            conversation.updated_at = timezone.now()
            conversation.save(update_fields=["updated_at"])
            recipients = list(
                SupportConversationMember.objects.filter(
                    conversation=conversation,
                    left_at__isnull=True,
                ).exclude(user=sender)
            )
            for recipient_member in recipients:
                if (
                    recipient_member.role == SupportConversationMember.ROLE_WORKER
                    and support_access_snapshot_for(recipient_member.user)["state"] != "active"
                ):
                    continue
                enqueue_support_notification(
                    organization=conversation.organization,
                    recipient=recipient_member.user,
                    notification_code="conversation.message",
                    target_kind="conversation",
                    target_public_id=conversation.public_id,
                    target_key=f"support:conversation:{conversation.public_id}",
                    collapse_key=f"support:conversation:{conversation.public_id}",
                    dedupe_key=(
                        f"conversation.message:{conversation.public_id}:{message.public_id}:"
                        f"{recipient_member.user_id}"
                    ),
                    push_requested=(
                        conversation.kind != SupportConversation.KIND_GROUP
                        or recipient_member.group_push_enabled
                    ),
                )
    return message, created


def contact_share_options(*, sender, conversation):
    """Return tenant-scoped people a staff sender may share as a contact."""

    member = require_conversation_access(user=sender, conversation=conversation)
    organization = conversation.organization
    if (
        member.role != SupportConversationMember.ROLE_STAFF
        or not has_permission(
            user=sender,
            organization=organization,
            permission_code=CHAT_MANAGE,
        )
    ):
        raise PermissionDenied("support_contact_sharing_not_available")

    options = []
    workers = worker_connection_queryset_for(
        user=sender,
        organization=organization,
        queryset=SupportConnection.objects.filter(is_archived=False).exclude(
            stage=SupportConnection.STAGE_CLOSED
        ),
    ).select_related("candidate", "candidate__profile", "vacancy")
    seen_users = set()
    for connection in workers.order_by("candidate__first_name", "candidate__last_name", "id"):
        if connection.candidate_id == sender.id or connection.candidate_id in seen_users:
            continue
        seen_users.add(connection.candidate_id)
        options.append(
            {
                "target_type": "worker",
                "target_id": str(connection.public_id),
                **_contact_identity(connection.candidate),
                "subtitle": connection.vacancy.internal_title,
            }
        )
    memberships = OrganizationMembership.objects.filter(
        organization=organization,
        state=OrganizationMembership.STATE_ACTIVE,
    ).select_related("user", "user__profile")
    for membership in memberships.order_by("user__first_name", "user__last_name", "id"):
        if membership.user_id == sender.id:
            continue
        options.append(
            {
                "target_type": "staff",
                "target_id": str(membership.public_id),
                **_contact_identity(membership.user),
                "subtitle": membership.display_role,
            }
        )
    return options


def send_contact_message(
    *,
    sender,
    conversation,
    target_type,
    target_id,
    original_language,
    client_message_id,
):
    """Send an internal profile card without exposing phone or e-mail data."""

    options = contact_share_options(sender=sender, conversation=conversation)
    selected = next(
        (
            option
            for option in options
            if option["target_type"] == target_type
            and option["target_id"] == str(target_id)
        ),
        None,
    )
    if selected is None:
        raise PermissionDenied("support_contact_share_target_not_available")
    if target_type == "worker":
        connection = SupportConnection.objects.select_related(
            "candidate", "candidate__profile"
        ).get(
            organization=conversation.organization,
            public_id=target_id,
            is_archived=False,
        )
        membership = None
        shared_user = connection.candidate
    else:
        membership = OrganizationMembership.objects.select_related(
            "user", "user__profile"
        ).get(
            organization=conversation.organization,
            public_id=target_id,
            state=OrganizationMembership.STATE_ACTIVE,
        )
        connection = None
        shared_user = membership.user
    message, created = send_text_message(
        sender=sender,
        conversation=conversation,
        body="",
        original_language=original_language,
        client_message_id=client_message_id,
        kind=SupportMessage.KIND_CONTACT,
        shared_contact_user=shared_user,
        shared_contact_connection=connection,
        shared_contact_membership=membership,
    )
    if created:
        record_audit_event(
            organization=conversation.organization,
            actor=sender,
            action="conversation.contact_shared",
            target=message,
            details={
                "target_type": target_type,
                "target_id": selected["target_id"],
            },
        )
    return message, created


def _open_worker_peer_conversation(
    *,
    actor,
    target_connection,
    actor_connection=None,
    unavailable_code="support_shared_contact_not_available",
):
    organization = target_connection.organization
    organization.__class__.objects.select_for_update().get(pk=organization.pk)
    if actor_connection is None:
        actor_connection = (
            SupportConnection.objects.filter(
                organization=organization,
                candidate=actor,
                is_archived=False,
            )
            .exclude(stage=SupportConnection.STAGE_CLOSED)
            .order_by("-updated_at", "-id")
            .first()
        )
    if (
        actor_connection is None
        or actor_connection.organization_id != organization.id
        or actor_connection.candidate_id != actor.id
        or actor_connection.is_archived
        or actor_connection.stage == SupportConnection.STAGE_CLOSED
        or target_connection.is_archived
        or target_connection.stage == SupportConnection.STAGE_CLOSED
        or actor.id == target_connection.candidate_id
        or support_access_snapshot_for(actor)["state"] != "active"
        or support_access_snapshot_for(target_connection.candidate)["state"] != "active"
    ):
        raise PermissionDenied(unavailable_code)
    candidates = list(
        SupportConversation.objects.select_for_update()
        .filter(
            organization=organization,
            connection__isnull=True,
            kind=SupportConversation.KIND_DRIVER,
            members__user=actor,
        )
        .prefetch_related("members")
        .distinct()
    )
    conversation = next(
        (
            item
            for item in candidates
            if {
                member.user_id for member in item.members.all() if member.left_at is None
            }
            == {actor.id, target_connection.candidate_id}
        ),
        None,
    )
    created = conversation is None
    if conversation is None:
        conversation = SupportConversation.objects.create(
            organization=organization,
            kind=SupportConversation.KIND_DRIVER,
            created_by=actor,
        )
        SupportConversationMember.objects.bulk_create(
            [
                SupportConversationMember(
                    conversation=conversation,
                    user=actor,
                    role=SupportConversationMember.ROLE_WORKER,
                ),
                SupportConversationMember(
                    conversation=conversation,
                    user=target_connection.candidate,
                    role=SupportConversationMember.ROLE_WORKER,
                ),
            ]
        )
    else:
        updates = []
        if conversation.state != SupportConversation.STATE_ACTIVE:
            conversation.state = SupportConversation.STATE_ACTIVE
            updates.append("state")
        if conversation.archived_at is not None:
            conversation.archived_at = None
            updates.append("archived_at")
        if updates:
            conversation.save(update_fields=[*updates, "updated_at"])
        for user in (actor, target_connection.candidate):
            member = conversation.members.get(user=user)
            if member.left_at is not None:
                member.left_at = None
                member.save(update_fields=("left_at",))
    return conversation, created


@transaction.atomic
def open_project_shift_peer_conversation(
    *,
    actor,
    actor_connection,
    target_connection,
    shift,
):
    """Open an exact worker pair only when both belong to this published day."""

    if (
        actor_connection.candidate_id != actor.id
        or actor_connection.organization_id != shift.crew.organization_id
        or target_connection.organization_id != shift.crew.organization_id
        or shift.state != ProjectCrewShift.STATE_PUBLISHED
        or target_connection.id == actor_connection.id
        or not ProjectCrewShiftMember.objects.filter(
            shift=shift,
            connection=actor_connection,
        ).exists()
        or not ProjectCrewShiftMember.objects.filter(
            shift=shift,
            connection=target_connection,
        ).exists()
    ):
        raise PermissionDenied("support_shift_peer_chat_not_available")

    conversation, created = _open_worker_peer_conversation(
        actor=actor,
        actor_connection=actor_connection,
        target_connection=target_connection,
        unavailable_code="support_shift_peer_chat_not_available",
    )
    record_audit_event(
        organization=shift.crew.organization,
        actor=actor,
        action="conversation.shift_peer_opened",
        target=conversation,
        details={
            "shift_id": str(shift.public_id),
            "target_connection_id": str(target_connection.public_id),
            "created": created,
        },
    )
    return conversation, created


@transaction.atomic
def open_shared_contact_conversation(*, actor, message):
    source_conversation = message.conversation
    require_conversation_access(user=actor, conversation=source_conversation)
    if (
        message.kind != SupportMessage.KIND_CONTACT
        or message.shared_contact_user_id is None
        or message.shared_contact_user_id == actor.id
    ):
        raise ValidationError({"contact": "support_shared_contact_not_available"})
    organization = source_conversation.organization
    actor_membership = active_membership_for(user=actor, organization=organization)
    if actor_membership is not None:
        if message.shared_contact_connection_id is not None:
            require_worker_connection_access(
                user=actor,
                organization=organization,
                connection=message.shared_contact_connection,
            )
            conversation, created = open_manager_conversation_for_staff(
                actor=actor,
                connection=message.shared_contact_connection,
            )
        elif message.shared_contact_membership_id is not None:
            conversation, created = open_staff_conversation(
                actor=actor,
                target_membership=message.shared_contact_membership,
            )
        else:
            raise PermissionDenied("support_shared_contact_not_available")
    else:
        actor_connection = (
            SupportConnection.objects.filter(
                organization=organization,
                candidate=actor,
                is_archived=False,
            )
            .exclude(stage=SupportConnection.STAGE_CLOSED)
            .first()
        )
        if actor_connection is None:
            raise PermissionDenied("support_shared_contact_not_available")
        if message.shared_contact_connection_id is not None:
            conversation, created = _open_worker_peer_conversation(
                actor=actor,
                target_connection=message.shared_contact_connection,
            )
        elif message.shared_contact_membership_id is not None:
            target_membership = message.shared_contact_membership
            if not target_membership.is_active or not has_permission(
                user=target_membership.user,
                organization=organization,
                permission_code=CHAT_MANAGE,
            ):
                raise PermissionDenied("support_shared_contact_not_available")
            conversation, created = _open_private_manager_conversation(
                connection=actor_connection,
                manager_membership=target_membership,
                created_by=actor,
            )
        else:
            raise PermissionDenied("support_shared_contact_not_available")
    record_audit_event(
        organization=organization,
        actor=actor,
        action="conversation.shared_contact_opened",
        target=message,
        details={"conversation_id": str(conversation.public_id), "created": created},
    )
    return conversation, created


def forward_text_message(*, sender, source_message, recipient, client_message_id):
    """Copy a Support message into a private in-organization conversation.

    The recipient is resolved server-side from the current organization.  A
    posted user id can therefore never be used to forward content outside the
    employer's active staff and worker directory.
    """

    source_conversation = source_message.conversation
    organization = source_conversation.organization
    require_conversation_access(user=sender, conversation=source_conversation)
    if (
        recipient.id == sender.id
        or source_message.deleted_at is not None
        or source_message.conversation.organization_id != organization.id
    ):
        raise ValidationError({"recipient": "support_forward_recipient_not_available"})

    recipient_membership = active_membership_for(
        user=recipient,
        organization=organization,
    )
    recipient_connection = None
    if recipient_membership is not None and has_permission(
        user=recipient,
        organization=organization,
        permission_code=CHAT_MANAGE,
    ):
        recipient_role = SupportConversationMember.ROLE_STAFF
    else:
        recipient_connection = (
            SupportConnection.objects.filter(
                organization=organization,
                candidate=recipient,
                is_archived=False,
            )
            .exclude(stage=SupportConnection.STAGE_CLOSED)
            .order_by("-updated_at", "-id")
            .first()
        )
        if (
            recipient_connection is None
            or support_access_snapshot_for(recipient)["state"] != "active"
        ):
            raise PermissionDenied("support_forward_recipient_not_available")
        recipient_role = SupportConversationMember.ROLE_WORKER

    sender_membership = active_membership_for(
        user=sender,
        organization=organization,
    )
    if sender_membership is None or not has_permission(
        user=sender,
        organization=organization,
        permission_code=CHAT_MANAGE,
    ):
        raise PermissionDenied("support_permission_denied")

    with transaction.atomic():
        organization.__class__.objects.select_for_update().get(pk=organization.pk)
        if recipient_role == SupportConversationMember.ROLE_WORKER:
            target_conversation, _ = open_manager_conversation_for_staff(
                actor=sender,
                connection=recipient_connection,
            )
        else:
            possible_conversations = list(
                SupportConversation.objects.filter(
                    organization=organization,
                    state=SupportConversation.STATE_ACTIVE,
                    members__user=sender,
                    members__left_at__isnull=True,
                )
                .filter(
                    members__user=recipient,
                    members__left_at__isnull=True,
                )
                .exclude(kind=SupportConversation.KIND_GROUP)
                .prefetch_related("members")
                .distinct()
                .order_by("-updated_at", "-id")
            )
            target_conversation = next(
                (
                    item
                    for item in possible_conversations
                    if {
                        member.user_id
                        for member in item.members.all()
                        if member.left_at is None
                    }
                    == {sender.id, recipient.id}
                ),
                None,
            )
        if target_conversation is None:
            target_conversation = SupportConversation.objects.create(
                organization=organization,
                connection=(
                    recipient_connection
                    if recipient_role == SupportConversationMember.ROLE_WORKER
                    else None
                ),
                kind=SupportConversation.KIND_JOBHUB,
                title="",
                created_by=sender,
            )
            SupportConversationMember.objects.bulk_create(
                [
                    SupportConversationMember(
                        conversation=target_conversation,
                        user=sender,
                        organization_membership=sender_membership,
                        role=SupportConversationMember.ROLE_STAFF,
                    ),
                    SupportConversationMember(
                        conversation=target_conversation,
                        user=recipient,
                        organization_membership=(
                            recipient_membership
                            if recipient_role == SupportConversationMember.ROLE_STAFF
                            else None
                        ),
                        role=recipient_role,
                    ),
                ]
            )
        message, created = send_text_message(
            sender=sender,
            conversation=target_conversation,
            body=source_message.body,
            original_language=source_message.original_language,
            client_message_id=client_message_id,
            forwarded_from=source_message,
        )
    return target_conversation, message, created


def mark_conversation_read(*, user, conversation):
    """Mark the conversation and its notification-center entries as read.

    The native phone notification uses the same stable target key.  Mobile
    clients clear that target from the system tray when this endpoint is
    called/opened, while this update removes the matching unread items from
    JobHub's in-app bell.
    """

    member = require_conversation_access(user=user, conversation=conversation)
    read_at = timezone.now()
    with transaction.atomic():
        member.last_read_at = read_at
        member.save(update_fields=["last_read_at"])
        InAppNotification.objects.filter(
            recipient=user,
            read_at__isnull=True,
            outbox__target_kind="conversation",
            outbox__target_public_id=conversation.public_id,
        ).update(read_at=read_at)
    return member
