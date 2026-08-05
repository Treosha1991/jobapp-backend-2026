"""Support data models are introduced package by package.

Keeping them in a separate package prevents the public JobHub ``jobs.models``
module from becoming the source of truth for Support operations.
"""

from .audit import AuditEvent
from .entitlement import SupportAccessExtensionRequest, SupportAccessGrant
from .organization import (
    DelegablePermissionGrant,
    InvitationPermissionGrant,
    MembershipInvitation,
    OrganizationMembership,
    PermissionGrant,
    SupportOrganization,
)
from .messaging import (
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportMessageTranslation,
)
from .notifications import InAppNotification, NotificationOutbox, PushDelivery
from .operations import (
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    RouteStop,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkerProjectScheduleTemplateSelection,
    ProjectScheduleTemplate,
    WorkerAccessScope,
    WorkProject,
    Worksite,
)
from .pipeline import (
    ApplicationDecisionEvent,
    BotContentRevision,
    ConnectionStageEvent,
    EmploymentExclusivityLock,
    PartnerPairRequest,
    SupportApplicantReference,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
)
from .timekeeping import (
    CalendarMarkBatch,
    CalendarMarkBatchItem,
    CalendarMarkTemplate,
    ScheduledShiftBatch,
    ScheduledWorkShift,
    ShiftTemplate,
    WorkTimeEntry,
    WorkTimeEntryRevision,
)
from .worker_requests import WorkerRequest, WorkerRequestEvent
from .tasks import (
    Announcement,
    AnnouncementAcknowledgement,
    ContentTemplate,
    TaskAssignment,
    WorkerTask,
)
from .documents import (
    DocumentRequestPackage,
    DocumentRequestPackageEvent,
    SupportWorkerDocumentReference,
)

__all__ = [
    "AuditEvent",
    "CalendarMarkBatch",
    "CalendarMarkBatchItem",
    "CalendarMarkTemplate",
    "ContentTemplate",
    "Announcement",
    "AnnouncementAcknowledgement",
    "ApplicationDecisionEvent",
    "BotContentRevision",
    "ConnectionStageEvent",
    "DelegablePermissionGrant",
    "DocumentRequestPackage",
    "DocumentRequestPackageEvent",
    "DriverVehicleAssignment",
    "EmploymentExclusivityLock",
    "HousingAssignment",
    "HousingPlace",
    "HousingRoom",
    "HousingSite",
    "InvitationPermissionGrant",
    "InAppNotification",
    "MembershipInvitation",
    "OrganizationMembership",
    "NotificationOutbox",
    "PermissionGrant",
    "PartnerPairRequest",
    "PushDelivery",
    "RouteStop",
    "SupportAccessExtensionRequest",
    "SupportAccessGrant",
    "SupportApplicantReference",
    "SupportApplication",
    "SupportConnection",
    "SupportConversation",
    "SupportConversationMember",
    "SupportMessage",
    "SupportMessageTranslation",
    "SupportOrganization",
    "SupportVacancy",
    "SupportWorkerDocumentReference",
    "TaskAssignment",
    "ScheduledWorkShift",
    "ScheduledShiftBatch",
    "ShiftTemplate",
    "TransportPassengerAssignment",
    "TransportRoute",
    "Vehicle",
    "WorkerProjectAssignment",
    "WorkerProjectScheduleTemplateSelection",
    "ProjectScheduleTemplate",
    "WorkerAccessScope",
    "WorkProject",
    "Worksite",
    "WorkTimeEntry",
    "WorkTimeEntryRevision",
    "WorkerRequest",
    "WorkerRequestEvent",
    "WorkerTask",
]
