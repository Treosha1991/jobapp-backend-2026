import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def normalize_private_worker_conversations(apps, schema_editor):
    Conversation = apps.get_model("support", "SupportConversation")
    Member = apps.get_model("support", "SupportConversationMember")
    Message = apps.get_model("support", "SupportMessage")
    Report = apps.get_model("support", "SupportConversationReport")
    database = schema_editor.connection.alias
    now = timezone.now()
    canonical_by_pair = {}
    conversations = (
        Conversation.objects.using(database)
        .exclude(connection_id=None)
        .exclude(kind="group")
        .select_related("connection__candidate", "connection__assigned_manager")
        .prefetch_related("members")
        .order_by("-updated_at", "-id")
    )

    for conversation in conversations.iterator(chunk_size=200):
        worker_id = conversation.connection.candidate_id
        active_members = [
            member
            for member in conversation.members.all()
            if member.left_at is None
        ]
        active_staff = [
            member for member in active_members if member.role == "staff"
        ]
        if not active_staff or not any(
            member.user_id == worker_id for member in active_members
        ):
            continue

        active_staff_by_id = {member.user_id: member for member in active_staff}
        assigned_manager = conversation.connection.assigned_manager
        if conversation.created_by_id in active_staff_by_id:
            manager_member = active_staff_by_id[conversation.created_by_id]
        elif (
            assigned_manager is not None
            and assigned_manager.user_id in active_staff_by_id
        ):
            manager_member = active_staff_by_id[assigned_manager.user_id]
        else:
            manager_member = min(active_staff, key=lambda member: member.id)

        pair = (conversation.organization_id, worker_id, manager_member.user_id)
        canonical = canonical_by_pair.get(pair)
        if canonical is None:
            conversation.kind = "manager"
            conversation.private_worker_id = worker_id
            conversation.private_manager_id = manager_member.user_id
            conversation.state = "active"
            conversation.archived_at = None
            conversation.save(
                using=database,
                update_fields=(
                    "kind",
                    "private_worker",
                    "private_manager",
                    "state",
                    "archived_at",
                    "updated_at",
                ),
            )
            Member.objects.using(database).filter(
                conversation_id=conversation.id,
                left_at__isnull=True,
            ).exclude(user_id__in=(worker_id, manager_member.user_id)).update(
                left_at=now
            )
            canonical_by_pair[pair] = conversation
            continue

        for message in (
            Message.objects.using(database)
            .filter(conversation_id=conversation.id)
            .order_by("created_at", "id")
        ):
            if Message.objects.using(database).filter(
                conversation_id=canonical.id,
                client_message_id=message.client_message_id,
            ).exists():
                message.client_message_id = uuid.uuid4()
                update_fields = ("conversation", "client_message_id")
            else:
                update_fields = ("conversation",)
            message.conversation_id = canonical.id
            message.save(using=database, update_fields=update_fields)

        Report.objects.using(database).filter(
            conversation_id=conversation.id
        ).update(conversation_id=canonical.id)
        Member.objects.using(database).filter(
            conversation_id=conversation.id,
            left_at__isnull=True,
        ).update(left_at=now)
        conversation.state = "archived"
        conversation.archived_at = now
        conversation.private_worker_id = None
        conversation.private_manager_id = None
        conversation.save(
            using=database,
            update_fields=(
                "state",
                "archived_at",
                "private_worker",
                "private_manager",
                "updated_at",
            ),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0035_backfill_worker_driving_licences"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="supportconversation",
            name="support_one_manager_conversation_per_connection",
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="private_worker",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="private_worker_support_conversations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="supportconversation",
            name="private_manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="private_manager_support_conversations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            normalize_private_worker_conversations,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="supportconversation",
            constraint=models.UniqueConstraint(
                condition=Q(kind="manager"),
                fields=("organization", "private_worker", "private_manager"),
                name="support_one_private_manager_worker_conversation",
            ),
        ),
        migrations.AddIndex(
            model_name="supportconversation",
            index=models.Index(
                fields=("organization", "private_worker", "private_manager"),
                name="support_conv_private_pair",
            ),
        ),
    ]
