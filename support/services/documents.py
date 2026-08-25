"""Document request cards for the employer's external e-mail channel."""

import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    DocumentRequestPackage,
    DocumentRequestPackageEvent,
    SupportConnection,
    SupportWorkerDocumentReference,
)
from support.permission_codes import DOCUMENT_REQUEST
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event
from .notifications import enqueue_support_notification


DOCUMENT_TYPE_KEYS = frozenset(
    {
        "passport",
        "visa",
        "residence_permit",
        "pesel",
        "bsn",
        "bank_account",
        "driving_license",
        "custom",
    }
)


def _new_reference_code():
    return f"JH-{secrets.token_hex(3).upper()}-{secrets.token_hex(2).upper()}"


def document_reference_for(*, user):
    """Return a stable account-only reference, with a collision-safe retry."""

    existing = SupportWorkerDocumentReference.objects.filter(user=user).first()
    if existing is not None:
        return existing
    for _attempt in range(8):
        try:
            # A nested transaction keeps the caller's transaction usable if a
            # very unlikely unique-code collision happens.
            with transaction.atomic():
                return SupportWorkerDocumentReference.objects.create(
                    user=user,
                    reference_code=_new_reference_code(),
                )
        except IntegrityError:
            existing = SupportWorkerDocumentReference.objects.filter(user=user).first()
            if existing is not None:
                return existing
    raise ValidationError({"reference": "document_reference_could_not_be_created"})


def _record_event(*, package, action, actor):
    return DocumentRequestPackageEvent.objects.create(
        package=package,
        action=action,
        status_after=package.status,
        actor=actor,
    )


def create_document_request_package(
    *,
    actor,
    organization,
    connection,
    requested_items,
    additional_instructions="",
):
    """Create an e-mail hand-off card; it never accepts a document or file."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=DOCUMENT_REQUEST,
    )
    require_worker_connection_access(
        user=actor,
        organization=organization,
        connection=connection,
    )
    if connection.is_archived:
        raise ValidationError({"connection": "connection_is_archived"})
    recipient_email = organization.verified_document_email.strip().lower()
    if not recipient_email:
        raise ValidationError({"organization": "verified_document_email_required"})
    with transaction.atomic():
        reference = document_reference_for(user=connection.candidate)
        package = DocumentRequestPackage.objects.create(
            organization=organization,
            connection=connection,
            recipient_email=recipient_email,
            account_reference=reference,
            requested_items=requested_items,
            additional_instructions=(additional_instructions or "").strip(),
            created_by=actor,
        )
        event = _record_event(
            package=package,
            action=DocumentRequestPackageEvent.ACTION_CREATED,
            actor=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="document_package.created",
            target=package,
            details={
                "connection": str(connection.public_id),
                "item_types": [item["type"] for item in requested_items],
            },
        )
        target_key = f"support:connection:{connection.public_id}:documents"
        enqueue_support_notification(
            organization=organization,
            recipient=connection.candidate,
            notification_code="documents.requested",
            target_kind="connection",
            target_public_id=connection.public_id,
            target_key=target_key,
            collapse_key=target_key,
            dedupe_key=f"document-package:{package.public_id}:{event.public_id}",
        )
    return package


def mark_document_request_package_sent(*, worker, package):
    """Worker confirms the e-mail was sent; JobHub does not inspect e-mail."""

    with transaction.atomic():
        item = (
            DocumentRequestPackage.objects.select_for_update()
            .select_related("organization", "connection")
            .get(pk=package.pk)
        )
        if item.connection.candidate_id != worker.id:
            raise PermissionDenied("support_document_package_not_owned")
        if item.connection.is_archived:
            raise ValidationError({"connection": "connection_is_archived"})
        if item.status not in {
            DocumentRequestPackage.STATUS_REQUESTED,
            DocumentRequestPackage.STATUS_NEEDS_CORRECTION,
        }:
            raise ValidationError({"package": "document_package_not_open_for_worker_confirmation"})
        item.status = DocumentRequestPackage.STATUS_SENT_TO_EMPLOYER
        item.sent_marked_at = timezone.now()
        item.save(update_fields=["status", "sent_marked_at", "updated_at"])
        _record_event(
            package=item,
            action=DocumentRequestPackageEvent.ACTION_WORKER_MARKED_SENT,
            actor=worker,
        )
        record_audit_event(
            organization=item.organization,
            actor=worker,
            action="document_package.worker_marked_sent",
            target=item,
            details={"connection": str(item.connection.public_id)},
        )
    return item


def review_document_request_package(*, actor, package, action, manager_note=""):
    """Employer owns the result of the external e-mail review."""

    organization = package.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=DOCUMENT_REQUEST,
    )
    with transaction.atomic():
        item = (
            DocumentRequestPackage.objects.select_for_update()
            .select_related("organization", "connection")
            .get(pk=package.pk)
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=item.connection,
        )
        transitions = {
            "needs_correction": (
                DocumentRequestPackage.STATUS_NEEDS_CORRECTION,
                DocumentRequestPackageEvent.ACTION_NEEDS_CORRECTION,
            ),
            "complete": (
                DocumentRequestPackage.STATUS_COMPLETED,
                DocumentRequestPackageEvent.ACTION_COMPLETED,
            ),
            "not_required": (
                DocumentRequestPackage.STATUS_NOT_REQUIRED,
                DocumentRequestPackageEvent.ACTION_NOT_REQUIRED,
            ),
            "cancel": (
                DocumentRequestPackage.STATUS_CANCELLED,
                DocumentRequestPackageEvent.ACTION_CANCELLED,
            ),
        }
        try:
            next_status, event_action = transitions[action]
        except KeyError as error:
            raise ValidationError({"action": "unsupported_document_package_action"}) from error
        if action == "needs_correction" and not (manager_note or "").strip():
            raise ValidationError({"manager_note": "document_package_correction_note_required"})
        item.status = next_status
        item.manager_note = (manager_note or "").strip()
        item.reviewed_at = timezone.now()
        item.reviewed_by = actor
        item.save(
            update_fields=[
                "status",
                "manager_note",
                "reviewed_at",
                "reviewed_by",
                "updated_at",
            ]
        )
        event = _record_event(package=item, action=event_action, actor=actor)
        record_audit_event(
            organization=organization,
            actor=actor,
            action=f"document_package.{action}",
            target=item,
            details={"connection": str(item.connection.public_id)},
        )
        if action == "needs_correction":
            target_key = f"support:connection:{item.connection.public_id}:documents"
            enqueue_support_notification(
                organization=organization,
                recipient=item.connection.candidate,
                notification_code="documents.needs_correction",
                target_kind="connection",
                target_public_id=item.connection.public_id,
                target_key=target_key,
                collapse_key=target_key,
                dedupe_key=f"document-package:{item.public_id}:{event.public_id}",
            )
    return item
