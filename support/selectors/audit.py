"""Safe, human-readable audit history for the employer workspace."""

from django.http import Http404

from support.models import (
    Announcement,
    AuditEvent,
    DocumentRequestPackage,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    OrganizationMembership,
    PermissionGrant,
    ProjectCrew,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportApplication,
    SupportAccessExtensionRequest,
    SupportAccessGrant,
    SupportConnection,
    SupportVacancy,
    TaskAssignment,
    Vehicle,
    WorkProject,
    WorkerAccessScope,
    WorkerRequest,
    WorkerTask,
    WorkTimeEntry,
)
from support.permission_codes import AUDIT_VIEW, ORGANIZATION_MANAGE
from support.permissions import has_permission

from .workspace import _display_name, _select_membership


AUDIT_CATEGORIES = (
    "all",
    "projects",
    "crews",
    "schedule",
    "housing",
    "fleet",
    "team",
    "announcements",
    "requests",
    "documents",
    "other",
)


def _category_for_action(action):
    if action.startswith(("work.project", "work.worksite", "vacancy.")):
        return "projects"
    if action.startswith("project_crew."):
        return "crews"
    if action.startswith(("schedule.", "time.", "calendar_mark.", "worker.schedule")):
        return "schedule"
    if action.startswith("housing."):
        return "housing"
    if action.startswith("transport."):
        return "fleet"
    if action.startswith(("membership.", "permission.", "worker_scope.", "organization.")):
        return "team"
    if action.startswith("announcement."):
        return "announcements"
    if action.startswith(("worker_request.", "support_extension.", "support_access.")):
        return "requests"
    if action.startswith("document_package."):
        return "documents"
    return "other"


def _verb_for_action(action):
    suffix = action.rsplit(".", 1)[-1]
    if suffix in {
        "created",
        "drafted",
        "submitted",
        "requested",
        "invited",
        "granted",
        "assigned",
    }:
        return "created"
    if suffix in {"published", "confirmed", "activated", "accepted"}:
        return "published"
    if suffix in {
        "updated",
        "edited",
        "replaced",
        "rescheduled",
        "swapped",
        "moved",
        "changed",
        "resolved",
    }:
        return "changed"
    if suffix in {
        "cancelled",
        "archived",
        "revoked",
        "removed",
        "released",
        "deleted",
        "declined",
    }:
        return "removed"
    if suffix in {"acknowledged", "marked_sent", "completed"}:
        return "confirmed"
    return "changed"


def _target_labels(events):
    """Resolve a small allow-list of target names without exposing details."""

    target_ids_by_type = {}
    for event in events:
        if event.target_type and event.target_public_id:
            target_ids_by_type.setdefault(event.target_type, set()).add(
                event.target_public_id
            )

    querysets = {
        "Announcement": Announcement.objects.only("public_id", "title"),
        "HousingSite": HousingSite.objects.only("public_id", "internal_name"),
        "HousingRoom": HousingRoom.objects.select_related("site").only(
            "public_id", "label", "site_id", "site__internal_name"
        ),
        "HousingPlace": HousingPlace.objects.select_related("room__site").only(
            "public_id", "label", "room_id", "room__label", "room__site_id", "room__site__internal_name"
        ),
        "HousingAssignment": HousingAssignment.objects.select_related(
            "connection__candidate", "place__room__site"
        ).only(
            "public_id",
            "connection_id",
            "place_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
            "place__room_id",
            "place__room__site_id",
            "place__room__site__internal_name",
        ),
        "OrganizationMembership": OrganizationMembership.objects.select_related("user").only(
            "public_id", "user_id", "user__first_name", "user__last_name", "user__username"
        ),
        "PermissionGrant": PermissionGrant.objects.select_related("membership__user").only(
            "public_id",
            "membership_id",
            "membership__user_id",
            "membership__user__first_name",
            "membership__user__last_name",
            "membership__user__username",
        ),
        "ProjectCrew": ProjectCrew.objects.select_related("project").only(
            "public_id", "internal_name", "project_id", "project__internal_name"
        ),
        "ProjectCrewShift": ProjectCrewShift.objects.select_related("crew__project").only(
            "public_id", "work_date", "crew_id", "crew__internal_name", "crew__project_id", "crew__project__internal_name"
        ),
        "ProjectCrewShiftMember": ProjectCrewShiftMember.objects.select_related(
            "connection__candidate", "shift__crew__project"
        ).only(
            "public_id",
            "connection_id",
            "shift_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
            "shift__crew__internal_name",
            "shift__crew_id",
            "shift__crew__project_id",
            "shift__crew__project__internal_name",
        ),
        "SupportApplication": SupportApplication.objects.select_related("candidate").only(
            "public_id", "candidate_id", "candidate__first_name", "candidate__last_name", "candidate__username"
        ),
        "SupportAccessExtensionRequest": SupportAccessExtensionRequest.objects.select_related(
            "user"
        ).only(
            "public_id",
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        ),
        "SupportAccessGrant": SupportAccessGrant.objects.select_related("user").only(
            "public_id",
            "user_id",
            "user__first_name",
            "user__last_name",
            "user__username",
        ),
        "SupportConnection": SupportConnection.objects.select_related("candidate").only(
            "public_id", "candidate_id", "candidate__first_name", "candidate__last_name", "candidate__username"
        ),
        "SupportVacancy": SupportVacancy.objects.only("public_id", "internal_title"),
        "Vehicle": Vehicle.objects.only("public_id", "internal_name"),
        "WorkProject": WorkProject.objects.only("public_id", "internal_name"),
        "WorkerAccessScope": WorkerAccessScope.objects.select_related(
            "connection__candidate"
        ).only(
            "public_id",
            "connection_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
        ),
        "WorkerRequest": WorkerRequest.objects.select_related("connection__candidate").only(
            "public_id",
            "connection_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
        ),
        "WorkerTask": WorkerTask.objects.only("public_id", "title"),
        "TaskAssignment": TaskAssignment.objects.select_related("connection__candidate").only(
            "public_id",
            "connection_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
        ),
        "WorkTimeEntry": WorkTimeEntry.objects.select_related("connection__candidate").only(
            "public_id",
            "connection_id",
            "work_date",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
        ),
        "DocumentRequestPackage": DocumentRequestPackage.objects.select_related(
            "connection__candidate"
        ).only(
            "public_id",
            "connection_id",
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "connection__candidate__username",
        ),
    }
    records = {}
    for target_type, public_ids in target_ids_by_type.items():
        queryset = querysets.get(target_type)
        if queryset is None:
            continue
        records[target_type] = {
            item.public_id: item
            for item in queryset.filter(public_id__in=public_ids)
        }

    labels = {}
    for event in events:
        item = records.get(event.target_type, {}).get(event.target_public_id)
        if item is None:
            continue
        if event.target_type in {"Announcement", "WorkerTask", "SupportVacancy", "WorkProject", "HousingSite", "Vehicle"}:
            label = item.title if hasattr(item, "title") else item.internal_name
        elif event.target_type == "HousingRoom":
            label = f"{item.site.internal_name} · {item.label}"
        elif event.target_type == "HousingPlace":
            label = f"{item.room.site.internal_name} · {item.room.label} · {item.label}"
        elif event.target_type == "HousingAssignment":
            label = f"{_display_name(item.connection.candidate)} · {item.place.room.site.internal_name}"
        elif event.target_type in {"OrganizationMembership", "PermissionGrant"}:
            user = item.user if hasattr(item, "user") else item.membership.user
            label = _display_name(user)
        elif event.target_type == "ProjectCrew":
            label = f"{item.project.internal_name} · {item.internal_name or '#' + str(item.id)}"
        elif event.target_type == "ProjectCrewShift":
            label = f"{item.crew.project.internal_name} · {item.crew.internal_name or '#' + str(item.crew_id)} · {item.work_date:%d.%m.%Y}"
        elif event.target_type == "ProjectCrewShiftMember":
            label = f"{_display_name(item.connection.candidate)} · {item.shift.crew.project.internal_name}"
        elif event.target_type == "SupportAccessExtensionRequest":
            label = _display_name(item.user)
        elif event.target_type == "SupportAccessGrant":
            label = _display_name(item.user)
        elif event.target_type in {"SupportApplication", "SupportConnection", "WorkerAccessScope", "WorkerRequest", "TaskAssignment", "WorkTimeEntry", "DocumentRequestPackage"}:
            label = _display_name(item.connection.candidate) if hasattr(item, "connection") else _display_name(item.candidate)
            if event.target_type == "WorkTimeEntry":
                label = f"{label} · {item.work_date:%d.%m.%Y}"
        else:
            continue
        labels[event.public_id] = label
    return labels


def audit_history_snapshot(*, user, organization_public_id=None, category="all"):
    """Return organization history without broadening a manager's visibility."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    if not has_permission(
        user=user,
        organization=organization,
        permission_code=AUDIT_VIEW,
    ):
        raise Http404("support_audit_not_found")
    normalized_category = (category or "all").strip()
    if normalized_category not in AUDIT_CATEGORIES:
        raise Http404("support_audit_category_not_found")

    can_view_organization_history = membership.is_owner or has_permission(
        user=user,
        organization=organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    queryset = AuditEvent.objects.filter(organization=organization).select_related("actor")
    if not can_view_organization_history:
        queryset = queryset.filter(actor=user)
    events = list(queryset.order_by("-created_at", "-id")[:250])
    for event in events:
        event.category = _category_for_action(event.action)
        event.verb = _verb_for_action(event.action)
        event.actor_label = _display_name(event.actor) if event.actor is not None else "—"
    if normalized_category != "all":
        events = [event for event in events if event.category == normalized_category]
    target_labels = _target_labels(events)
    for event in events:
        event.target_label = target_labels.get(event.public_id, "")

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "events": events,
        "can_view_organization_history": can_view_organization_history,
        "selected_category": normalized_category,
        "categories": AUDIT_CATEGORIES,
    }
