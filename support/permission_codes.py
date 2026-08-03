"""Stable permission identifiers for the JobHub Support domain.

Display names may change per employer.  These identifiers never do, so a
renamed role cannot accidentally gain server-side authority.
"""

ORGANIZATION_MANAGE = "organization.manage"
MEMBER_INVITE = "member.invite"
MEMBER_DELEGATE_PERMISSIONS = "member.delegate_permissions"
PIPELINE_REVIEW = "pipeline.review"
CONNECTION_TRANSITION = "connection.transition"
WORKER_VIEW = "worker.view"
CHAT_MANAGE = "chat.manage"
DOCUMENT_REQUEST = "document.request"
HOUSING_MANAGE = "housing.manage"
TRANSPORT_MANAGE = "transport.manage"
SCHEDULE_MANAGE = "schedule.manage"
TIME_VIEW = "time.view"
TIME_REVIEW = "time.review"
TIME_EDIT = "time.edit"
TIME_EXPORT = "time.export"
REQUEST_DECIDE = "request.decide"
TASK_MANAGE = "task.manage"
ANNOUNCEMENT_MANAGE = "announcement.manage"
FINANCE_VIEW = "finance.view"
FINANCE_MANAGE = "finance.manage"
AUDIT_VIEW = "audit.view"
WORKER_EXPORT_BASIC = "worker.export_basic"
WORKER_EXPORT_DETAIL = "worker.export_detail"
SUPPORT_EXTENSION_REQUEST = "support_extension.request"

ALL_PERMISSION_CODES = frozenset(
    {
        ORGANIZATION_MANAGE,
        MEMBER_INVITE,
        MEMBER_DELEGATE_PERMISSIONS,
        PIPELINE_REVIEW,
        CONNECTION_TRANSITION,
        WORKER_VIEW,
        CHAT_MANAGE,
        DOCUMENT_REQUEST,
        HOUSING_MANAGE,
        TRANSPORT_MANAGE,
        SCHEDULE_MANAGE,
        TIME_VIEW,
        TIME_REVIEW,
        TIME_EDIT,
        TIME_EXPORT,
        REQUEST_DECIDE,
        TASK_MANAGE,
        ANNOUNCEMENT_MANAGE,
        FINANCE_VIEW,
        FINANCE_MANAGE,
        AUDIT_VIEW,
        WORKER_EXPORT_BASIC,
        WORKER_EXPORT_DETAIL,
        SUPPORT_EXTENSION_REQUEST,
    }
)
