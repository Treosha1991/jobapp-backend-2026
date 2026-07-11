from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .avatar_utils import avatar_public_url
from .chat_notifications import notify_user_about_chat_message
from .models import ChatConversation, ChatMessage, ChatReport, UserBlock, UserProfile, Vacancy
from .serializers import (
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatMessageUpdateSerializer,
    ChatReportCreateSerializer,
    chat_message_has_external_links,
)
from .service_sources import is_service_board_user


CHAT_MESSAGE_RATE_LIMIT = 20
CHAT_MESSAGE_RATE_WINDOW = timedelta(minutes=1)
CHAT_PAGE_SIZE = 50


def _user_display_name(user):
    profile = _user_profile(user)
    nickname = (getattr(profile, "nickname", "") or "").strip()
    # Email is an account credential, not a public chat identity.
    return nickname or UserProfile.generated_nickname(user.id)


def _user_avatar_url(user):
    profile = _user_profile(user)
    return avatar_public_url(getattr(profile, "avatar_key", ""))


def _user_profile(user):
    if not user:
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def _chat_users_are_blocked(first_user, second_user):
    return UserBlock.objects.filter(
        Q(blocker=first_user, blocked_user=second_user)
        | Q(blocker=second_user, blocked_user=first_user)
    ).exists()


def _viewer_hides_conversation(viewer, conversation):
    other_user = conversation.other_user_for(viewer)
    return bool(
        other_user
        and UserBlock.objects.filter(blocker=viewer, blocked_user=other_user).exists()
    )


def _conversation_for_user(user, conversation_id):
    return (
        ChatConversation.objects.filter(
            Q(candidate=user) | Q(employer=user),
            id=conversation_id,
        )
        .select_related(
            "candidate",
            "candidate__profile",
            "employer",
            "employer__profile",
            "initial_vacancy",
        )
        .first()
    )


def _vacancy_can_start_chat(vacancy):
    current_time = timezone.now()
    return bool(
        vacancy
        and vacancy.is_approved
        and not vacancy.is_paused_by_owner
        and not vacancy.is_deleted_by_moderator
        and vacancy.expires_at > current_time
    )


def _user_has_active_vacancies(user):
    return Vacancy.objects.filter(
        created_by=user,
        is_approved=True,
        is_paused_by_owner=False,
        is_deleted_by_moderator=False,
        expires_at__gt=timezone.now(),
    ).exists()


def _block_state(viewer, other_user):
    if not other_user:
        return False, False
    blocked_by_me = UserBlock.objects.filter(
        blocker=viewer,
        blocked_user=other_user,
    ).exists()
    blocked_by_other = UserBlock.objects.filter(
        blocker=other_user,
        blocked_user=viewer,
    ).exists()
    return blocked_by_me, blocked_by_other


def _unread_counts(conversations, viewer):
    if not conversations:
        return {}

    read_at_by_conversation = {}
    for conversation in conversations:
        read_at_by_conversation[conversation.id] = (
            conversation.candidate_last_read_at
            if conversation.candidate_id == viewer.id
            else conversation.employer_last_read_at
        )

    counts = {conversation.id: 0 for conversation in conversations}
    messages = (
        ChatMessage.objects.filter(conversation_id__in=counts)
        .exclude(sender=viewer)
        .values("conversation_id", "created_at")
    )
    for message in messages:
        last_read_at = read_at_by_conversation.get(message["conversation_id"])
        if last_read_at is None or message["created_at"] > last_read_at:
            counts[message["conversation_id"]] += 1
    return counts


def _conversation_payload(conversation, viewer, *, unread_count=0, last_message=None):
    other_user = conversation.other_user_for(viewer)
    if last_message is None:
        last_message = getattr(conversation, "_chat_last_message", None)
    employer = conversation.employer
    blocked_by_me, blocked_by_other = _block_state(viewer, other_user)
    return {
        "id": conversation.id,
        "other_user": {
            "id": other_user.id if other_user else None,
            "nickname": _user_display_name(other_user) if other_user else "",
            "avatar_url": _user_avatar_url(other_user) if other_user else "",
            "has_active_vacancies": bool(other_user and _user_has_active_vacancies(other_user)),
        },
        "initial_vacancy": {
            "id": conversation.initial_vacancy_id,
            "title": conversation.initial_vacancy_title,
        },
        # This context is fixed when the chat is created. It lets both sides
        # keep the employer and the vacancy that started the conversation in view.
        "initial_context": {
            "employer": {
                "id": employer.id,
                "nickname": _user_display_name(employer),
                "avatar_url": _user_avatar_url(employer),
            },
            "vacancy": {
                "id": conversation.initial_vacancy_id,
                "title": conversation.initial_vacancy_title,
            },
        },
        "last_message": (
            {
                "id": last_message.id,
                "body": last_message.body,
                "has_external_links": last_message.has_external_links,
                "created_at": last_message.created_at,
                "sender_id": last_message.sender_id,
                "is_mine": last_message.sender_id == viewer.id,
                "is_deleted": bool(last_message.deleted_at),
            }
            if last_message is not None
            else None
        ),
        "unread_count": unread_count,
        "can_send": not blocked_by_me and not blocked_by_other,
        "blocked_by_me": blocked_by_me,
        "blocked_by_other": blocked_by_other,
        "created_at": conversation.created_at,
        "last_message_at": conversation.last_message_at,
    }


def _latest_messages_by_conversation(conversations):
    conversation_ids = [conversation.id for conversation in conversations]
    if not conversation_ids:
        return {}
    messages = ChatMessage.objects.filter(conversation_id__in=conversation_ids).order_by(
        "conversation_id", "-id"
    )
    latest_by_id = {}
    for message in messages:
        latest_by_id.setdefault(message.conversation_id, message)
    return latest_by_id


class ChatConversationStartAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        vacancy_id = request.data.get("vacancy_id")
        employer_user_id = request.data.get("employer_user_id")
        if bool(vacancy_id) == bool(employer_user_id):
            return Response(
                {"error": "provide_vacancy_id_or_employer_user_id"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vacancy = None
        if vacancy_id:
            vacancy = (
                Vacancy.objects.select_related("created_by", "created_by__profile")
                .filter(id=vacancy_id)
                .first()
            )
            if not _vacancy_can_start_chat(vacancy):
                return Response({"error": "vacancy_chat_unavailable"}, status=status.HTTP_404_NOT_FOUND)
            employer = vacancy.created_by
        else:
            employer = (
                User.objects.select_related("profile")
                .filter(id=employer_user_id, is_active=True)
                .first()
            )
            if not employer:
                return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)
            has_live_vacancies = Vacancy.objects.filter(
                created_by=employer,
                is_approved=True,
                is_paused_by_owner=False,
                is_deleted_by_moderator=False,
                expires_at__gt=timezone.now(),
            ).exists()
            if not has_live_vacancies:
                return Response({"error": "employer_chat_unavailable"}, status=status.HTTP_404_NOT_FOUND)

        if employer.id == request.user.id:
            return Response({"error": "cannot_chat_with_self"}, status=status.HTTP_400_BAD_REQUEST)
        if is_service_board_user(employer):
            return Response({"error": "employer_chat_unavailable"}, status=status.HTTP_404_NOT_FOUND)
        if _chat_users_are_blocked(request.user, employer):
            return Response({"error": "chat_blocked"}, status=status.HTTP_403_FORBIDDEN)

        defaults = {
            "initial_vacancy": vacancy,
            "initial_vacancy_title": (vacancy.title if vacancy else "")[:120],
        }
        try:
            with transaction.atomic():
                conversation, created = ChatConversation.objects.get_or_create(
                    candidate=request.user,
                    employer=employer,
                    defaults=defaults,
                )
        except IntegrityError:
            conversation = ChatConversation.objects.get(
                candidate=request.user,
                employer=employer,
            )
            created = False

        conversation = _conversation_for_user(request.user, conversation.id)
        return Response(
            {
                "conversation": _conversation_payload(conversation, request.user),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ChatConversationListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        conversations = list(
            ChatConversation.objects.filter(
                Q(candidate=request.user) | Q(employer=request.user),
                last_message_at__isnull=False,
            )
            .select_related(
                "candidate",
                "candidate__profile",
                "employer",
                "employer__profile",
                "initial_vacancy",
            )
            .order_by("-last_message_at", "-id")[:CHAT_PAGE_SIZE]
        )
        unread_by_id = _unread_counts(conversations, request.user)
        last_message_by_id = _latest_messages_by_conversation(conversations)
        return Response(
            {
                "count": len(conversations),
                "unread_count": sum(unread_by_id.values()),
                "results": [
                    _conversation_payload(
                        conversation,
                        request.user,
                        unread_count=unread_by_id.get(conversation.id, 0),
                        last_message=last_message_by_id.get(conversation.id),
                    )
                    for conversation in conversations
                ],
            },
            status=status.HTTP_200_OK,
        )


class ChatUnreadCountAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        conversations = list(
            ChatConversation.objects.filter(
                Q(candidate=request.user) | Q(employer=request.user),
                last_message_at__isnull=False,
            ).only(
                "id",
                "candidate_id",
                "employer_id",
                "candidate_last_read_at",
                "employer_last_read_at",
            )
        )
        return Response(
            {"unread_count": sum(_unread_counts(conversations, request.user).values())},
            status=status.HTTP_200_OK,
        )


class ChatConversationDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)

        before_message_id = request.query_params.get("before_message_id")
        messages_query = ChatMessage.objects.filter(conversation=conversation).select_related(
            "sender", "conversation", "reply_to"
        )
        if before_message_id:
            try:
                before_message_id = int(before_message_id)
            except (TypeError, ValueError):
                return Response({"error": "invalid_before_message_id"}, status=status.HTTP_400_BAD_REQUEST)
            messages_query = messages_query.filter(id__lt=before_message_id)

        newest_first = list(messages_query.order_by("-id")[: CHAT_PAGE_SIZE + 1])
        has_more = len(newest_first) > CHAT_PAGE_SIZE
        if has_more:
            newest_first = newest_first[:CHAT_PAGE_SIZE]
        messages = newest_first
        messages.reverse()
        unread_count = _unread_counts([conversation], request.user).get(conversation.id, 0)
        return Response(
            {
                "conversation": _conversation_payload(
                    conversation,
                    request.user,
                    unread_count=unread_count,
                    last_message=messages[-1] if messages else None,
                ),
                "messages": ChatMessageSerializer(messages, many=True, context={"request": request}).data,
                "has_more": has_more,
                "next_before_message_id": messages[0].id if has_more and messages else None,
            },
            status=status.HTTP_200_OK,
        )


class ChatMessageAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)

        recipient = conversation.other_user_for(request.user)
        if _chat_users_are_blocked(request.user, recipient):
            return Response({"error": "chat_blocked"}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        if ChatMessage.objects.filter(
            sender=request.user,
            created_at__gte=now - CHAT_MESSAGE_RATE_WINDOW,
        ).count() >= CHAT_MESSAGE_RATE_LIMIT:
            return Response({"error": "chat_rate_limited"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        serializer = ChatMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data["body"]
        client_message_id = serializer.validated_data.get("client_message_id") or None
        reply_to = None
        reply_to_message_id = serializer.validated_data.get("reply_to_message_id")
        if reply_to_message_id:
            reply_to = ChatMessage.objects.filter(
                id=reply_to_message_id,
                conversation=conversation,
            ).first()
            if not reply_to:
                return Response({"error": "reply_message_not_found"}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            if client_message_id:
                message, created = ChatMessage.objects.get_or_create(
                    conversation=conversation,
                    client_message_id=client_message_id,
                    defaults={
                        "sender": request.user,
                        "body": body,
                        "has_external_links": chat_message_has_external_links(body),
                        "reply_to": reply_to,
                    },
                )
            else:
                message = ChatMessage.objects.create(
                    conversation=conversation,
                    sender=request.user,
                    body=body,
                    has_external_links=chat_message_has_external_links(body),
                    reply_to=reply_to,
                )
                created = True

            if created:
                message_time = message.created_at
                conversation.last_message_at = message_time
                if conversation.candidate_id == request.user.id:
                    conversation.candidate_last_read_at = message_time
                    update_fields = ["last_message_at", "candidate_last_read_at", "updated_at"]
                else:
                    conversation.employer_last_read_at = message_time
                    update_fields = ["last_message_at", "employer_last_read_at", "updated_at"]
                conversation.save(update_fields=update_fields)

                sender_name = _user_display_name(request.user)
                transaction.on_commit(
                    lambda: _send_chat_push_safe(message, recipient, sender_name)
                )

        return Response(
            {
                "message": ChatMessageSerializer(message, context={"request": request}).data,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _send_chat_push_safe(message, recipient, sender_name):
    try:
        summary = notify_user_about_chat_message(
            message,
            recipient=recipient,
            sender_name=sender_name,
        )
        print(f"[CHAT-PUSH] conversation={message.conversation_id} message={message.id} summary={summary}")
    except Exception as exc:
        print(f"[CHAT-PUSH-ERROR] conversation={message.conversation_id} message={message.id}: {exc}")


class ChatConversationReadAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)

        message_id = request.data.get("last_message_id")
        message = None
        if message_id:
            message = ChatMessage.objects.filter(id=message_id, conversation=conversation).first()
            if not message:
                return Response({"error": "message_not_found"}, status=status.HTTP_404_NOT_FOUND)
        else:
            message = ChatMessage.objects.filter(conversation=conversation).order_by("-id").first()

        read_at = message.created_at if message else timezone.now()
        if conversation.candidate_id == request.user.id:
            conversation.candidate_last_read_at = read_at
            conversation.save(update_fields=["candidate_last_read_at", "updated_at"])
        else:
            conversation.employer_last_read_at = read_at
            conversation.save(update_fields=["employer_last_read_at", "updated_at"])
        return Response({"status": "read", "last_read_at": read_at}, status=status.HTTP_200_OK)


class ChatConversationBlockAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)
        other_user = conversation.other_user_for(request.user)
        _, created = UserBlock.objects.get_or_create(
            blocker=request.user,
            blocked_user=other_user,
        )
        return Response({"status": "blocked", "created": created}, status=status.HTTP_200_OK)


class ChatConversationUnblockAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)
        other_user = conversation.other_user_for(request.user)
        deleted, _ = UserBlock.objects.filter(
            blocker=request.user,
            blocked_user=other_user,
        ).delete()
        return Response({"status": "unblocked", "deleted": bool(deleted)}, status=status.HTTP_200_OK)


class ChatMessageMutationAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _message_for_request(self, request, conversation_id, message_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return None, None
        message = ChatMessage.objects.select_related("conversation", "reply_to").filter(
            id=message_id,
            conversation=conversation,
        ).first()
        return conversation, message

    def _can_modify(self, request, message):
        if not message or message.sender_id != request.user.id or message.deleted_at:
            return False
        conversation = message.conversation
        recipient_read_at = (
            conversation.employer_last_read_at
            if message.sender_id == conversation.candidate_id
            else conversation.candidate_last_read_at
        )
        return not recipient_read_at or recipient_read_at < message.created_at

    def patch(self, request, conversation_id, message_id):
        _, message = self._message_for_request(request, conversation_id, message_id)
        if not message:
            return Response({"error": "message_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._can_modify(request, message):
            return Response({"error": "message_already_read"}, status=status.HTTP_409_CONFLICT)
        serializer = ChatMessageUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data["body"]
        message.body = body
        message.has_external_links = chat_message_has_external_links(body)
        message.edited_at = timezone.now()
        message.save(update_fields=["body", "has_external_links", "edited_at"])
        return Response({"message": ChatMessageSerializer(message, context={"request": request}).data})

    def delete(self, request, conversation_id, message_id):
        _, message = self._message_for_request(request, conversation_id, message_id)
        if not message:
            return Response({"error": "message_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._can_modify(request, message):
            return Response({"error": "message_already_read"}, status=status.HTTP_409_CONFLICT)
        message.body = ""
        message.has_external_links = False
        message.deleted_at = timezone.now()
        message.save(update_fields=["body", "has_external_links", "deleted_at"])
        return Response({"status": "deleted", "message_id": message.id})


class ChatReportAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = _conversation_for_user(request.user, conversation_id)
        if not conversation:
            return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ChatReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reported_message = None
        reported_message_id = serializer.validated_data.get("reported_message_id")
        if reported_message_id:
            reported_message = ChatMessage.objects.filter(
                id=reported_message_id,
                conversation=conversation,
            ).first()
            if not reported_message:
                return Response({"error": "message_not_found"}, status=status.HTTP_404_NOT_FOUND)
            if reported_message.sender_id == request.user.id:
                return Response({"error": "cannot_report_own_message"}, status=status.HTTP_400_BAD_REQUEST)

        reported_user = (
            reported_message.sender if reported_message else conversation.other_user_for(request.user)
        )
        report = ChatReport.objects.create(
            conversation=conversation,
            reporter=request.user,
            reported_user=reported_user,
            reported_message=reported_message,
            reason=serializer.validated_data["reason"],
            message=serializer.validated_data.get("message") or "",
        )
        return Response(
            {"status": "reported", "report_id": report.id},
            status=status.HTTP_201_CREATED,
        )
