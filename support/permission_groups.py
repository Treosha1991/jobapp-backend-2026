"""Human-sized permission groups for the Support team workspace.

The database and services keep using stable, fine-grained permission codes.
This module only groups those codes for a clear first invitation screen, so an
employer never has to make a security decision based on a raw technical code.
"""

from .permission_codes import (
    ANNOUNCEMENT_MANAGE,
    AUDIT_VIEW,
    CHAT_MANAGE,
    CONNECTION_TRANSITION,
    DOCUMENT_REQUEST,
    FINANCE_MANAGE,
    FINANCE_VIEW,
    HOUSING_MANAGE,
    MEMBER_DELEGATE_PERMISSIONS,
    MEMBER_INVITE,
    ORGANIZATION_MANAGE,
    PIPELINE_REVIEW,
    REQUEST_DECIDE,
    SCHEDULE_MANAGE,
    SUPPORT_EXTENSION_REQUEST,
    TASK_MANAGE,
    TIME_EDIT,
    TIME_EXPORT,
    TIME_REVIEW,
    TIME_VIEW,
    TRANSPORT_MANAGE,
    WORKER_EXPORT_BASIC,
    WORKER_VIEW,
)


# ``id`` values are intentionally separate from permission codes. They are
# presentation choices, while every authorization decision stays server-side
# and is made with the permission codes in ``codes``.
TEAM_PERMISSION_GROUPS = (
    ("pipeline", "support_team_permission_pipeline", (PIPELINE_REVIEW, CONNECTION_TRANSITION)),
    ("workers", "support_team_permission_workers", (WORKER_VIEW,)),
    ("chats", "support_team_permission_chats", (CHAT_MANAGE,)),
    ("documents", "support_team_permission_documents", (DOCUMENT_REQUEST,)),
    ("housing", "support_team_permission_housing", (HOUSING_MANAGE,)),
    ("transport", "support_team_permission_transport", (TRANSPORT_MANAGE,)),
    ("schedule", "support_team_permission_schedule", (SCHEDULE_MANAGE,)),
    ("time_review", "support_team_permission_time_review", (TIME_VIEW, TIME_REVIEW, TIME_EDIT)),
    ("time_export", "support_team_permission_time_export", (TIME_VIEW, TIME_EXPORT)),
    ("worker_requests", "support_team_permission_worker_requests", (REQUEST_DECIDE,)),
    ("content", "support_team_permission_content", (TASK_MANAGE, ANNOUNCEMENT_MANAGE)),
    ("finance", "support_team_permission_finance", (FINANCE_VIEW, FINANCE_MANAGE)),
    ("basic_export", "support_team_permission_basic_export", (WORKER_EXPORT_BASIC,)),
    ("audit", "support_team_permission_audit", (AUDIT_VIEW,)),
    ("support_extension", "support_team_permission_support_extension", (SUPPORT_EXTENSION_REQUEST,)),
    (
        "team_management",
        "support_team_permission_team_management",
        (ORGANIZATION_MANAGE, MEMBER_INVITE, MEMBER_DELEGATE_PERMISSIONS),
    ),
)


def permission_codes_for_group_ids(group_ids):
    """Expand known presentation choices into a safe, unique code list."""

    selected = {str(group_id).strip() for group_id in group_ids if str(group_id).strip()}
    codes = []
    for group_id, _label_key, group_codes in TEAM_PERMISSION_GROUPS:
        if group_id in selected:
            codes.extend(group_codes)
    return sorted(set(codes))

