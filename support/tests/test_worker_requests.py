from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection as database_connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    NotificationOutbox,
    OrganizationMembership,
    ProjectCrew,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    ScheduledWorkShift,
    WorkProject,
    Worksite,
    WorkerRequest,
    WorkerRequestDate,
    WorkerRequestEvent,
)
from support.permission_codes import CHAT_MANAGE, REQUEST_DECIDE, SCHEDULE_MANAGE
from support.services.organizations import (
    activate_organization,
    create_organization,
    grant_permission,
    grant_worker_access_scope,
)
from support.selectors.workspace import worker_requests_snapshot


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class WorkerRequestTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="request-operator",
            email="request-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="request-owner",
            email="request-owner@example.com",
            password="password",
        )
        self.manager = User.objects.create_user(
            username="request-manager",
            email="request-manager@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="request-worker",
            first_name="Ihor",
            last_name="Requests",
            email="request-worker@example.com",
            password="password",
        )
        self.other_worker = User.objects.create_user(
            username="request-other-worker",
            first_name="Olena",
            last_name="Outside",
            email="request-other-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Request Agency sp. z o.o.",
            display_name="Request Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=self.organization)
        self.manager_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.manager,
            display_role="Manager",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        grant_permission(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            permission_code=REQUEST_DECIDE,
        )
        grant_permission(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            permission_code=CHAT_MANAGE,
        )
        self.connection = self._connection_for(self.worker, "main")
        self.other_connection = self._connection_for(self.other_worker, "other")
        self.connection.assigned_manager = self.manager_membership
        self.connection.save(update_fields=["assigned_manager", "updated_at"])
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Request test worksite",
            country_code="PL",
            city="Warsaw",
            street="Testowa",
            building="1",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Request test project",
            worker_visible_name="Request test project",
            worker_capacity=10,
            starts_on=timezone.localdate(),
            created_by=self.owner,
        )
        self.project_crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Request test crew",
            created_by=self.owner,
        )
        for worker in (self.worker, self.other_worker):
            SupportAccessGrant.objects.create(
                user=worker,
                organization=self.organization,
                granted_by=self.operator,
                ends_at=timezone.now() + timedelta(days=7),
                reason=SupportAccessGrant.REASON_TECHNICAL,
            )
        grant_worker_access_scope(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            connection=self.connection,
        )
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker)
        self.other_worker_client = APIClient()
        self.other_worker_client.force_authenticate(self.other_worker)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(self.manager)

    def _connection_for(self, worker, suffix):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Request vacancy {suffix}",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=worker,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        return SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=application,
            candidate=worker,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
        )

    def _worker_request_url(self, connection=None):
        connection = connection or self.connection
        return f"/api/v2/support/connections/{connection.public_id}/worker-requests/mine/"

    def _publish_project_first_shift(self, *, connection, work_date):
        starts_at = timezone.make_aware(datetime.combine(work_date, time(8, 0)))
        ends_at = timezone.make_aware(datetime.combine(work_date, time(16, 0)))
        shift = ProjectCrewShift.objects.create(
            crew=self.project_crew,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=30,
            state=ProjectCrewShift.STATE_PUBLISHED,
            created_by=self.owner,
            updated_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=connection,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        return shift

    def test_worker_can_submit_list_and_cancel_an_unresolved_vacation_request(self):
        starts_on = timezone.localdate() + timedelta(days=4)
        ends_on = starts_on + timedelta(days=4)
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "vacation",
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
                "worker_note": "Please consider these dates.",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        item_id = created.data["worker_request"]["id"]
        self.assertEqual(created.data["worker_request"]["status"], "submitted")
        self.assertFalse(created.data["worker_request"]["is_urgent"])
        self.assertFalse(NotificationOutbox.objects.exists())

        listed = self.worker_client.get(self._worker_request_url())
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data["results"]), 1)
        self.assertEqual(listed.data["results"][0]["id"], item_id)

        cancelled = self.worker_client.post(
            f"/api/v2/support/worker-requests/{item_id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data["worker_request"]["status"], "cancelled")
        item = WorkerRequest.objects.get(public_id=item_id)
        self.assertEqual(WorkerRequestEvent.objects.filter(request=item).count(), 2)

    def test_urgent_unable_today_notifies_only_assigned_manager_with_neutral_code(self):
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "unable_today",
                "starts_on": (timezone.localdate() + timedelta(days=7)).isoformat(),
                "ends_on": (timezone.localdate() + timedelta(days=9)).isoformat(),
                "worker_note": "I cannot attend today.",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data["worker_request"]["is_urgent"])
        self.assertEqual(created.data["worker_request"]["starts_on"], timezone.localdate())
        self.assertEqual(created.data["worker_request"]["ends_on"], timezone.localdate())
        notice = NotificationOutbox.objects.get(notification_code="worker_request.urgent_submitted")
        self.assertEqual(notice.recipient, self.manager)
        self.assertEqual(notice.target_kind, "worker_request")
        self.assertEqual(notice.safe_context, {})
        self.assertFalse(NotificationOutbox.objects.filter(recipient=self.worker).exists())

    def test_submission_lock_query_does_not_join_nullable_assigned_manager(self):
        starts_on = timezone.localdate() + timedelta(days=1)

        with CaptureQueriesContext(database_connection) as captured:
            created = self.worker_client.post(
                self._worker_request_url(),
                {
                    "request_type": "day_off",
                    "starts_on": starts_on.isoformat(),
                    "ends_on": starts_on.isoformat(),
                },
                format="json",
            )

        self.assertEqual(created.status_code, 201, created.data)
        connection_reads = [
            query["sql"]
            for query in captured.captured_queries
            if 'FROM "support_supportconnection"' in query["sql"]
        ]
        self.assertTrue(connection_reads)
        self.assertNotIn(
            'JOIN "support_organizationmembership"',
            connection_reads[-1],
        )

    def test_worker_can_offer_non_contiguous_extra_shift_dates(self):
        dates = [
            timezone.localdate() + timedelta(days=2),
            timezone.localdate() + timedelta(days=9),
            timezone.localdate() + timedelta(days=40),
        ]

        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [item.isoformat() for item in dates],
                "worker_note": "I can work on these Saturdays.",
            },
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.data)
        payload = created.data["worker_request"]
        self.assertTrue(payload["is_extra_shift"])
        self.assertEqual(payload["starts_on"], dates[0])
        self.assertEqual(payload["ends_on"], dates[-1])
        self.assertEqual(
            [item["date"] for item in payload["requested_dates"]],
            dates,
        )
        self.assertTrue(
            all(item["status"] == "requested" for item in payload["requested_dates"])
        )
        notice = NotificationOutbox.objects.get(
            notification_code="worker_request.extra_shift_submitted"
        )
        self.assertEqual(notice.recipient, self.manager)

    def test_extra_shift_rejects_scheduled_and_duplicate_open_dates(self):
        work_date = timezone.localdate() + timedelta(days=3)
        self._publish_project_first_shift(
            connection=self.connection,
            work_date=work_date,
        )

        scheduled = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [work_date.isoformat()],
            },
            format="json",
        )
        self.assertEqual(scheduled.status_code, 400, scheduled.data)

        available_date = work_date + timedelta(days=1)
        first = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [available_date.isoformat()],
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        duplicate = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [available_date.isoformat()],
            },
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400, duplicate.data)

    def test_manager_declines_one_extra_shift_date_without_closing_others(self):
        dates = [
            timezone.localdate() + timedelta(days=5),
            timezone.localdate() + timedelta(days=12),
        ]
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [item.isoformat() for item in dates],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["worker_request"]["id"]
        date_id = created.data["worker_request"]["requested_dates"][0]["id"]

        declined = self.manager_client.post(
            f"/api/v2/support/worker-requests/{request_id}/dates/{date_id}/decline/",
            {"manager_note": "No work is planned on this date."},
            format="json",
        )

        self.assertEqual(declined.status_code, 200, declined.data)
        payload = declined.data["worker_request"]
        self.assertEqual(payload["status"], WorkerRequest.STATUS_SUBMITTED)
        self.assertEqual(
            [item["status"] for item in payload["requested_dates"]],
            [WorkerRequestDate.STATUS_DECLINED, WorkerRequestDate.STATUS_REQUESTED],
        )
        self.assertTrue(
            NotificationOutbox.objects.filter(
                recipient=self.worker,
                notification_code="worker_request.extra_shift_changed",
            ).exists()
        )

    def test_published_shift_completes_matching_extra_shift_date(self):
        work_date = timezone.localdate() + timedelta(days=6)
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "extra_shift",
                "requested_dates": [work_date.isoformat()],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self._publish_project_first_shift(
            connection=self.connection,
            work_date=work_date,
        )

        listed = self.worker_client.get(self._worker_request_url())

        self.assertEqual(listed.status_code, 200, listed.data)
        payload = listed.data["results"][0]
        self.assertEqual(payload["status"], WorkerRequest.STATUS_APPROVED)
        self.assertEqual(
            payload["requested_dates"][0]["status"],
            WorkerRequestDate.STATUS_ASSIGNED,
        )

    def test_unassigned_extra_shift_date_expires_after_it_passes(self):
        work_date = timezone.localdate() - timedelta(days=1)
        item = WorkerRequest.objects.create(
            organization=self.organization,
            connection=self.connection,
            request_type=WorkerRequest.TYPE_EXTRA_SHIFT,
            status=WorkerRequest.STATUS_SUBMITTED,
            starts_on=work_date,
            ends_on=work_date,
            submitted_at=timezone.now() - timedelta(days=2),
            last_changed_by=self.worker,
        )
        WorkerRequestDate.objects.create(request=item, work_date=work_date)

        listed = self.worker_client.get(self._worker_request_url())

        self.assertEqual(listed.status_code, 200, listed.data)
        payload = listed.data["results"][0]
        self.assertEqual(payload["status"], WorkerRequest.STATUS_DECLINED)
        self.assertEqual(
            payload["requested_dates"][0]["status"],
            WorkerRequestDate.STATUS_EXPIRED,
        )

    def test_urgent_request_succeeds_when_push_dispatch_fails_after_commit(self):
        with patch(
            "support.services.notifications.dispatch_outbox_entry",
            side_effect=RuntimeError("push provider unavailable"),
        ), self.captureOnCommitCallbacks(execute=True):
            created = self.worker_client.post(
                self._worker_request_url(),
                {
                    "request_type": "unable_today",
                    "starts_on": timezone.localdate().isoformat(),
                    "ends_on": timezone.localdate().isoformat(),
                    "worker_note": "I cannot attend today.",
                },
                format="json",
            )

        self.assertEqual(created.status_code, 201, created.data)
        item = WorkerRequest.objects.get(
            public_id=created.data["worker_request"]["id"]
        )
        notice = NotificationOutbox.objects.get(
            notification_code="worker_request.urgent_submitted",
            target_public_id=item.public_id,
        )
        self.assertEqual(notice.status, NotificationOutbox.STATUS_PENDING)

    def test_unexpected_submission_error_returns_safe_json_instead_of_html(self):
        with patch(
            "support.api_views.submit_worker_request",
            side_effect=RuntimeError("database internals must stay private"),
        ):
            response = self.worker_client.post(
                self._worker_request_url(),
                {
                    "request_type": "day_off",
                    "starts_on": (timezone.localdate() + timedelta(days=1)).isoformat(),
                    "ends_on": (timezone.localdate() + timedelta(days=1)).isoformat(),
                },
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], "worker_request_submission_failed")
        self.assertNotIn("database internals", response.data["detail"])
        self.assertNotIn("<html", response.data["detail"].lower())

    def test_scoped_manager_can_approve_exit_request_without_changing_worker_stage(self):
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "exit_request",
                "starts_on": (timezone.localdate() + timedelta(days=14)).isoformat(),
                "ends_on": (timezone.localdate() + timedelta(days=14)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["worker_request"]["id"]

        approved = self.manager_client.post(
            f"/api/v2/support/worker-requests/{request_id}/approve/",
            {"manager_note": "We will contact you about the next steps."},
            format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["worker_request"]["status"], "approved")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.stage, SupportConnection.STAGE_ACTIVE_WORKER)

        cancellation = self.worker_client.post(
            f"/api/v2/support/worker-requests/{request_id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(cancellation.status_code, 400)

    def test_worker_request_clarification_endpoint_is_removed(self):
        starts_on = timezone.localdate() + timedelta(days=2)
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "day_off",
                "starts_on": starts_on.isoformat(),
                "ends_on": starts_on.isoformat(),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["worker_request"]["id"]

        removed = self.manager_client.post(
            f"/api/v2/support/worker-requests/{request_id}/clarification/",
            {"manager_note": "Please explain."},
            format="json",
        )

        self.assertEqual(removed.status_code, 404)
        item = WorkerRequest.objects.get(public_id=request_id)
        self.assertEqual(item.status, WorkerRequest.STATUS_SUBMITTED)

    def test_request_queue_and_review_respect_manager_scope(self):
        other_created = self.other_worker_client.post(
            self._worker_request_url(self.other_connection),
            {
                "request_type": "day_off",
                "starts_on": (timezone.localdate() + timedelta(days=2)).isoformat(),
                "ends_on": (timezone.localdate() + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(other_created.status_code, 201, other_created.data)
        other_request_id = other_created.data["worker_request"]["id"]
        queue = self.manager_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/worker-requests/"
        )
        self.assertEqual(queue.status_code, 200, queue.data)
        self.assertEqual(queue.data["results"], [])
        blocked = self.manager_client.post(
            f"/api/v2/support/worker-requests/{other_request_id}/decline/",
            {"manager_note": "No access."},
            format="json",
        )
        self.assertEqual(blocked.status_code, 403)

    def test_staff_web_queue_uses_the_same_scope_and_decision_service(self):
        starts_on = timezone.localdate() + timedelta(days=3)
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "day_off",
                "starts_on": starts_on.isoformat(),
                "ends_on": starts_on.isoformat(),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["worker_request"]["id"]
        screen_url = (
            f"/employer/support/requests/?organization={self.organization.public_id}"
        )
        self.client.force_login(self.manager)
        opened = self.client.get(screen_url)
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "Ihor Requests")
        self.assertContains(opened, "Open chat")
        self.assertNotContains(opened, 'value="request_clarify"')

        opened_chat = self.client.post(
            screen_url,
            {
                "action": "request_open_chat",
                "request_id": request_id,
                "filter": "open",
            },
        )
        self.assertEqual(opened_chat.status_code, 302)
        self.assertIn("/employer/support/conversations/", opened_chat.url)

        approved = self.client.post(
            screen_url,
            {
                "action": "request_approve",
                "request_id": request_id,
                "filter": "open",
                "manager_note": "Approved for the selected date.",
            },
        )
        self.assertEqual(approved.status_code, 302)
        item = WorkerRequest.objects.get(public_id=request_id)
        self.assertEqual(item.status, WorkerRequest.STATUS_APPROVED)

    def test_queue_counts_affected_published_shifts_without_revealing_shift_times(self):
        work_date = timezone.localdate() + timedelta(days=5)
        starts_at = timezone.make_aware(datetime.combine(work_date, time(8, 0)))
        ends_at = timezone.make_aware(datetime.combine(work_date, time(16, 0)))
        ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=30,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "vacation",
                "starts_on": work_date.isoformat(),
                "ends_on": work_date.isoformat(),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)

        snapshot = worker_requests_snapshot(
            user=self.manager,
            organization_public_id=self.organization.public_id,
        )
        self.assertEqual(len(snapshot["requests"]), 1)
        item = snapshot["requests"][0]
        self.assertEqual(item.affected_shift_count, 1)
        self.assertEqual(item.affected_shifts, [])

    def test_approved_absence_is_a_calendar_mark_without_changing_shift_or_hours(self):
        """Calendar display is projected from the approved individual request."""

        work_date = timezone.localdate() + timedelta(days=5)
        starts_at = timezone.make_aware(datetime.combine(work_date, time(8, 0)))
        ends_at = timezone.make_aware(datetime.combine(work_date, time(16, 0)))
        shift = ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=30,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        created = self.worker_client.post(
            self._worker_request_url(),
            {
                "request_type": "vacation",
                "starts_on": work_date.isoformat(),
                "ends_on": work_date.isoformat(),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        request_id = created.data["worker_request"]["id"]

        approved = self.manager_client.post(
            f"/api/v2/support/worker-requests/{request_id}/approve/",
            {"manager_note": "Approved."},
            format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.data)

        day = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/time-entries/mine/",
            {"work_date": work_date.isoformat()},
        )
        self.assertEqual(day.status_code, 200, day.data)
        self.assertEqual(day.data["scheduled_shift"]["id"], str(shift.public_id))
        self.assertIsNone(day.data["time_entry"])
        self.assertEqual(day.data["calendar_marks"], [{
            "request_id": request_id,
            "connection_id": str(self.connection.public_id),
            "request_type": "vacation",
            "starts_on": work_date,
            "ends_on": work_date,
        }])
        self.assertFalse(WorkerRequest.objects.get(public_id=request_id).is_urgent)

        calendar = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/scheduled-shifts/mine/",
            {
                "date_from": work_date.isoformat(),
                "date_to": work_date.isoformat(),
            },
        )
        self.assertEqual(calendar.status_code, 200, calendar.data)
        self.assertEqual(len(calendar.data["results"]), 1)
        self.assertEqual(calendar.data["calendar_marks"][0]["request_id"], request_id)

        # The schedule employee gets only a scoped operational projection,
        # never another worker's request or its note.
        other_mark = WorkerRequest.objects.create(
            organization=self.organization,
            connection=self.other_connection,
            request_type=WorkerRequest.TYPE_DAY_OFF,
            status=WorkerRequest.STATUS_APPROVED,
            starts_on=work_date,
            ends_on=work_date,
            reviewed_by=self.owner,
            reviewed_at=timezone.now(),
            last_changed_by=self.owner,
        )
        grant_permission(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            permission_code=SCHEDULE_MANAGE,
        )
        workspace = self.manager_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/schedule-workspace/"
        )
        self.assertEqual(workspace.status_code, 200, workspace.data)
        self.assertEqual(len(workspace.data["calendar_marks"]), 1)
        staff_mark = workspace.data["calendar_marks"][0]
        self.assertEqual(staff_mark["request_id"], request_id)
        self.assertIn("avatar_url", staff_mark["worker"])
        self.assertNotEqual(staff_mark["request_id"], str(other_mark.public_id))
        self.assertNotIn("manager_note", staff_mark)
        self.assertNotIn("worker_note", staff_mark)

    def test_worker_can_read_only_see_published_shifts_for_a_request_period(self):
        work_date = timezone.localdate() + timedelta(days=6)
        starts_at = timezone.make_aware(datetime.combine(work_date, time(7, 0)))
        ends_at = timezone.make_aware(datetime.combine(work_date, time(15, 0)))
        shift = ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=20,
            worker_label="Day shift",
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        response = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/scheduled-shifts/mine/"
            f"?date_from={work_date.isoformat()}&date_to={work_date.isoformat()}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], str(shift.public_id))
        self.assertEqual(response.data["results"][0]["worker_label"], "Day shift")
