from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    OrganizationMembership,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
)
from support.permission_codes import CHAT_MANAGE
from support.permissions import active_membership_for, has_permission

from .audit import record_audit_event
from .entitlements import support_access_snapshot_for
from .notifications import enqueue_support_notification


def open_manager_conversation(*, candidate, connection):
    """Candidate action that opens the approved manager chat.

    Approval alone never starts a conversation.  A candidate must have an
    effective Support entitlement and choose to open it themselves.
    """

    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .select_related("organization", "assigned_manager__user")
            .get(pk=connection.pk)
        )
        if connection.candidate_id != candidate.id or connection.is_archived:
            raise PermissionDenied("support_connection_not_available")
        if support_access_snapshot_for(candidate)["state"] != "active":
            raise PermissionDenied("support_access_required")
        if connection.stage not in {
            SupportConnection.STAGE_AWAITING_SUPPORT,
            SupportConnection.STAGE_MANAGER,
        }:
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

        conversation, created = SupportConversation.objects.get_or_create(
            connection=connection,
            kind=SupportConversation.KIND_MANAGER,
            defaults={
                "organization": connection.organization,
                "created_by": candidate,
            },
        )
        if created:
            SupportConversationMember.objects.bulk_create(
                [
                    SupportConversationMember(
                        conversation=conversation,
                        user=candidate,
                        role=SupportConversationMember.ROLE_WORKER,
                    ),
                    SupportConversationMember(
                        conversation=conversation,
                        user=manager_membership.user,
                        organization_membership=manager_membership,
                        role=SupportConversationMember.ROLE_STAFF,
                    ),
                ]
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


def _active_member_or_denied(*, user, conversation):
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


def send_text_message(*, sender, conversation, body, original_language, client_message_id):
    with transaction.atomic():
        conversation = SupportConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.state != SupportConversation.STATE_ACTIVE:
            raise ValidationError({"conversation": "conversation_archived"})
        member = require_conversation_access(user=sender, conversation=conversation)
        if not member.can_send:
            raise PermissionDenied("support_message_sending_not_available")
        message, created = SupportMessage.objects.get_or_create(
            conversation=conversation,
            client_message_id=client_message_id,
            defaults={
                "sender": sender,
                "body": body.strip(),
                "original_language": original_language,
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


def mark_conversation_read(*, user, conversation):
    member = require_conversation_access(user=user, conversation=conversation)
    member.last_read_at = timezone.now()
    member.save(update_fields=["last_read_at"])
    return member
