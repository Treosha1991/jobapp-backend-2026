"""Employer-facing data for owner-controlled Support access extensions."""

from django.http import Http404

from support.models import SupportAccessExtensionRequest, SupportConnection
from support.permission_codes import SUPPORT_EXTENSION_REQUEST
from support.permissions import has_permission, worker_connection_queryset_for

from .workspace import _display_name, _select_membership


def support_extension_workspace_snapshot(*, user, organization_public_id=None):
    """Return only the request actions and records this member may see.

    A manager can request an extension only for workers in their assigned
    worker scope. The owner sees every request in the organization and is the
    only person who can decide it.
    """

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    may_request = (
        not membership.is_owner
        and has_permission(
            user=user,
            organization=organization,
            permission_code=SUPPORT_EXTENSION_REQUEST,
        )
    )
    may_decide = membership.is_owner
    if not may_request and not may_decide:
        raise Http404("support_extension_not_found")

    request_connections = []
    if may_request:
        connections = (
            worker_connection_queryset_for(
                user=user,
                organization=organization,
                queryset=SupportConnection.objects.filter(is_archived=False).exclude(
                    stage=SupportConnection.STAGE_CLOSED
                ),
            )
            .select_related("candidate", "vacancy")
            .order_by(
                "candidate__first_name",
                "candidate__last_name",
                "candidate__username",
                "id",
            )[:250]
        )
        seen_user_ids = set()
        for connection in connections:
            if connection.candidate_id in seen_user_ids:
                continue
            seen_user_ids.add(connection.candidate_id)
            request_connections.append(
                {
                    "id": str(connection.public_id),
                    "label": f"{_display_name(connection.candidate)} · "
                    f"{connection.vacancy.internal_title}",
                }
            )

    request_queryset = SupportAccessExtensionRequest.objects.filter(
        organization=organization
    ).select_related("user", "requested_by__user", "decided_by")
    if not may_decide:
        request_queryset = request_queryset.filter(requested_by=membership)
    extension_requests = list(request_queryset.order_by("-created_at", "-id")[:250])
    for item in extension_requests:
        item.user_label = _display_name(item.user)
        item.requested_by_label = (
            _display_name(item.requested_by.user)
            if item.requested_by is not None
            else "—"
        )
        item.decided_by_label = _display_name(item.decided_by) if item.decided_by else "—"

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "may_request": may_request,
        "may_decide": may_decide,
        "request_connections": request_connections,
        "extension_requests": extension_requests,
        "duration_choices": SupportAccessExtensionRequest.DURATION_CHOICES,
        "reason_choices": SupportAccessExtensionRequest.REASON_CHOICES,
    }
