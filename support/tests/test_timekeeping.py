from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    CalendarMarkBatch,
    CalendarMarkTemplate,
    NotificationOutbox,
    OrganizationMembership,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    ScheduledWorkShift,
    ScheduledShiftBatch,
    ShiftTemplate,
    WorkTimeEntry,
    WorkTimeEntryRevision,
    WorkerRequest,
)
from support.permission_codes import (
    SCHEDULE_MANAGE,
    TIME_EDIT,
    TIME_EXPORT,
    TIME_REVIEW,
    TIME_VIEW,
    WORKER_VIEW,
)
from support.services.organizations import (
    activate_organization,
    create_organization,
    grant_permission,
    grant_worker_access_scope,
)
from support.services.timekeeping import (
    create_scheduled_shift,
    publish_scheduled_shift,
    replace_scheduled_shift,
)


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class TimekeepingTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="time-operator",
            email="time-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="time-owner",
            email="time-owner@example.com",
            password="password",
        )
        self.accountant = User.objects.create_user(
            username="time-accountant",
            email="time-accountant@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="time-worker",
            first_name="Ihor",
            last_name="Hours",
            email="time-worker@example.com",
            password="password",
        )
        self.other_worker = User.objects.create_user(
            username="time-other-worker",
            first_name="Olena",
            last_name="Outside",
            email="time-other-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Time Agency sp. z o.o.",
            display_name="Time Agency",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        self.accountant_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.accountant,
            display_role="Accountant",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        for permission_code in (
            SCHEDULE_MANAGE,
            TIME_VIEW,
            TIME_REVIEW,
            TIME_EDIT,
            WORKER_VIEW,
        ):
            grant_permission(
                actor=self.owner,
                organization=self.organization,
                membership=self.accountant_membership,
                permission_code=permission_code,
            )
        self.connection = self._connection_for(self.worker, "main")
        self.other_connection = self._connection_for(self.other_worker, "other")
        self.worker_grant = SupportAccessGrant.objects.create(
            user=self.worker,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=7),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        SupportAccessGrant.objects.create(
            user=self.other_worker,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=7),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        grant_worker_access_scope(
            actor=self.owner,
            organization=self.organization,
            membership=self.accountant_membership,
            connection=self.connection,
        )
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker)
        self.other_worker_client = APIClient()
        self.other_worker_client.force_authenticate(self.other_worker)
        self.accountant_client = APIClient()
        self.accountant_client.force_authenticate(self.accountant)

    def _connection_for(self, worker, suffix):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Time vacancy {suffix}",
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

    @staticmethod
    def _iso(value):
        return value.isoformat()

    def test_replacing_published_shift_preserves_history_and_publishes_update(self):
        now = timezone.now().replace(second=0, microsecond=0)
        starts_at = now + timedelta(days=1, hours=1)
        ends_at = starts_at + timedelta(hours=8)
        work_date = timezone.localtime(starts_at).date()
        original = create_scheduled_shift(
            actor=self.accountant,
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=30,
            worker_label="Morning shift",
        )
        publish_scheduled_shift(actor=self.accountant, shift=original)

        replacement = replace_scheduled_shift(
            actor=self.accountant,
            shift=original,
            work_date=work_date,
            starts_at=starts_at + timedelta(hours=1),
            ends_at=ends_at + timedelta(hours=1),
            break_minutes=45,
            worker_label="Late shift",
        )

        original.refresh_from_db()
        self.assertEqual(original.state, ScheduledWorkShift.STATE_CANCELLED)
        self.assertEqual(replacement.state, ScheduledWorkShift.STATE_PUBLISHED)
        self.assertEqual(replacement.work_date, work_date)
        self.assertEqual(replacement.worker_label, "Late shift")
        self.assertEqual(replacement.break_minutes, 45)
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                connection=self.connection,
                work_date=work_date,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            1,
        )
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_code="schedule.shift_published",
                recipient=self.worker,
            ).count(),
            2,
        )

    def test_worker_staff_timekeeping_flow_keeps_minutes_revisions_and_scope(self):
        now = timezone.now().replace(second=0, microsecond=0)
        first_start = now - timedelta(hours=3)
        first_end = now - timedelta(hours=1)
        work_date = timezone.localtime(first_start).date()
        organization_url = f"/api/v2/support/organizations/{self.organization.public_id}"

        draft_shift = self.accountant_client.post(
            f"{organization_url}/scheduled-shifts/",
            {
                "connection_id": str(self.connection.public_id),
                "work_date": work_date.isoformat(),
                "starts_at": self._iso(first_start),
                "ends_at": self._iso(first_end),
                "break_minutes": 15,
                "worker_label": "Morning shift",
            },
            format="json",
        )
        self.assertEqual(draft_shift.status_code, 201, draft_shift.data)
        shift_id = draft_shift.data["scheduled_shift"]["id"]
        worker_summary = self.accountant_client.get(
            f"{organization_url}/connections/{self.connection.public_id}/summary/"
        )
        self.assertEqual(worker_summary.status_code, 200, worker_summary.data)
        self.assertTrue(worker_summary.data["available_actions"]["create_shift"])
        self.assertEqual(worker_summary.data["scheduled_shifts"][0]["state"], "draft")
        published_shift = self.accountant_client.post(
            f"/api/v2/support/scheduled-shifts/{shift_id}/publish/",
            {},
            format="json",
        )
        self.assertEqual(published_shift.status_code, 200, published_shift.data)
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_code="schedule.shift_published",
                recipient=self.worker,
            ).exists()
        )

        day_url = (
            f"/api/v2/support/connections/{self.connection.public_id}/time-entries/mine/"
            f"?work_date={work_date.isoformat()}"
        )
        day = self.worker_client.get(day_url)
        self.assertEqual(day.status_code, 200, day.data)
        self.assertEqual(day.data["scheduled_shift"]["id"], shift_id)
        self.assertIsNone(day.data["time_entry"])

        submit_url = (
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "time-entries/mine/submit/"
        )
        submitted = self.worker_client.post(
            submit_url,
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(first_start),
                "ended_at": self._iso(first_end),
                "break_minutes": 15,
            },
            format="json",
        )
        self.assertEqual(submitted.status_code, 201, submitted.data)
        entry_id = submitted.data["time_entry"]["id"]
        self.assertEqual(submitted.data["time_entry"]["worked_minutes"], 105)
        self.assertEqual(submitted.data["time_entry"]["worked_duration"], "1:45")
        self.assertEqual(submitted.data["time_entry"]["decimal_hours"], "1.75")

        workspace_summary = self.accountant_client.get(
            f"{organization_url}/workspace-summary/"
        )
        self.assertEqual(workspace_summary.status_code, 200, workspace_summary.data)
        self.assertTrue(workspace_summary.data["permissions"]["time_view"])
        self.assertTrue(workspace_summary.data["permissions"]["time_review"])
        self.assertTrue(workspace_summary.data["permissions"]["time_edit"])
        self.assertEqual(workspace_summary.data["counts"]["time_entries_to_review"], 1)

        correction = self.accountant_client.post(
            f"/api/v2/support/time-entries/{entry_id}/request-correction/",
            {"reason": "Please check the end time."},
            format="json",
        )
        self.assertEqual(correction.status_code, 200, correction.data)
        self.assertEqual(correction.data["time_entry"]["status"], "correction_requested")

        corrected_end = now - timedelta(minutes=30)
        resubmitted = self.worker_client.post(
            submit_url,
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(first_start),
                "ended_at": self._iso(corrected_end),
                "break_minutes": 15,
            },
            format="json",
        )
        self.assertEqual(resubmitted.status_code, 201, resubmitted.data)
        self.assertEqual(resubmitted.data["time_entry"]["revision"], 2)
        self.assertEqual(resubmitted.data["time_entry"]["worked_minutes"], 135)

        confirmed = self.accountant_client.post(
            f"/api/v2/support/time-entries/{entry_id}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data["time_entry"]["status"], "confirmed")
        blocked_worker_change = self.worker_client.post(
            submit_url,
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(first_start),
                "ended_at": self._iso(corrected_end),
                "break_minutes": 15,
            },
            format="json",
        )
        self.assertEqual(blocked_worker_change.status_code, 400)

        adjusted_start = now - timedelta(hours=4)
        adjusted_end = now - timedelta(hours=1)
        staff_edit = self.accountant_client.post(
            f"/api/v2/support/time-entries/{entry_id}/edit/",
            {
                "started_at": self._iso(adjusted_start),
                "ended_at": self._iso(adjusted_end),
                "break_minutes": 10,
                "reason": "Corrected after the official worksite record.",
            },
            format="json",
        )
        self.assertEqual(staff_edit.status_code, 200, staff_edit.data)
        self.assertEqual(staff_edit.data["time_entry"]["status"], "manager_adjusted")
        self.assertEqual(staff_edit.data["time_entry"]["worked_minutes"], 170)
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_code="time.entry_changed",
                recipient=self.worker,
            ).exists()
        )

        acknowledged = self.worker_client.post(
            f"/api/v2/support/time-entries/{entry_id}/acknowledge-manager-adjustment/",
            {},
            format="json",
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.data)
        self.assertEqual(acknowledged.data["time_entry"]["status"], "confirmed")

        entry = WorkTimeEntry.objects.get(public_id=entry_id)
        self.assertEqual(entry.revision, 3)
        self.assertEqual(entry.worked_minutes, 170)
        self.assertEqual(entry.decimal_hours, Decimal("2.83"))
        self.assertEqual(WorkTimeEntryRevision.objects.filter(entry=entry).count(), 6)

        staff_list = self.accountant_client.get(
            f"{organization_url}/time-entries/?date_from={work_date.isoformat()}"
            f"&date_to={work_date.isoformat()}"
        )
        self.assertEqual(staff_list.status_code, 200, staff_list.data)
        self.assertEqual(staff_list.data["totals"]["worked_minutes"], 170)
        self.assertEqual(staff_list.data["totals"]["worked_duration"], "2:50")
        self.assertEqual(staff_list.data["totals"]["decimal_hours"], "2.83")
        self.assertEqual(len(staff_list.data["results"]), 1)
        self.assertEqual(staff_list.data["results"][0]["worker"]["display_name"], "Ihor Hours")

    def test_shift_template_batch_is_scoped_drafted_and_published_atomically(self):
        organization_url = f"/api/v2/support/organizations/{self.organization.public_id}"
        starts_on = timezone.localdate() + timedelta(days=7)
        ends_on = starts_on + timedelta(days=1)
        template = self.accountant_client.post(
            f"{organization_url}/shift-templates/",
            {
                "name": "Warehouse morning",
                "starts_at_time": "07:00:00",
                "ends_at_time": "15:30:00",
                "break_minutes": 30,
                "worker_label": "Warehouse",
            },
            format="json",
        )
        self.assertEqual(template.status_code, 201, template.data)
        template_id = template.data["shift_template"]["id"]

        blocked = self.accountant_client.post(
            f"{organization_url}/scheduled-shift-batches/",
            {
                "template_id": template_id,
                "connection_ids": [str(self.other_connection.public_id)],
                "starts_on": starts_on.isoformat(),
                "ends_on": starts_on.isoformat(),
                "weekdays": [starts_on.weekday()],
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(ScheduledShiftBatch.objects.count(), 0)

        grant_worker_access_scope(
            actor=self.owner,
            organization=self.organization,
            membership=self.accountant_membership,
            connection=self.other_connection,
        )
        draft = self.accountant_client.post(
            f"{organization_url}/scheduled-shift-batches/",
            {
                "template_id": template_id,
                "connection_ids": [
                    str(self.connection.public_id),
                    str(self.other_connection.public_id),
                ],
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
                "weekdays": [starts_on.weekday(), ends_on.weekday()],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        batch_id = draft.data["scheduled_shift_batch"]["id"]
        self.assertEqual(draft.data["scheduled_shift_batch"]["state"], "draft")
        self.assertEqual(draft.data["scheduled_shift_batch"]["shift_count"], 4)
        self.assertEqual(NotificationOutbox.objects.count(), 0)
        self.assertEqual(
            ScheduledWorkShift.objects.filter(batch__public_id=batch_id, state="draft").count(),
            4,
        )

        workspace = self.accountant_client.get(f"{organization_url}/schedule-workspace/")
        self.assertEqual(workspace.status_code, 200, workspace.data)
        self.assertEqual(workspace.data["templates"][0]["id"], template_id)
        self.assertEqual(workspace.data["batches"][0]["id"], batch_id)
        self.assertEqual(len(workspace.data["batches"][0]["workers"]), 2)

        published = self.accountant_client.post(
            f"/api/v2/support/scheduled-shift-batches/{batch_id}/publish/",
            {},
            format="json",
        )
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["scheduled_shift_batch"]["state"], "published")
        self.assertEqual(
            ScheduledWorkShift.objects.filter(batch__public_id=batch_id, state="published").count(),
            4,
        )
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_code="schedule.shift_published"
            ).count(),
            4,
        )

        conflicting = self.accountant_client.post(
            f"{organization_url}/scheduled-shift-batches/",
            {
                "template_id": template_id,
                "connection_ids": [str(self.connection.public_id)],
                "starts_on": starts_on.isoformat(),
                "ends_on": starts_on.isoformat(),
                "weekdays": [starts_on.weekday()],
            },
            format="json",
        )
        self.assertEqual(conflicting.status_code, 400)
        self.assertEqual(ScheduledShiftBatch.objects.count(), 1)
        self.assertEqual(ShiftTemplate.objects.count(), 1)

    def test_staff_scope_blocks_other_workers_and_worker_cannot_submit_future_day(self):
        now = timezone.now().replace(second=0, microsecond=0)
        work_date = timezone.localtime(now - timedelta(hours=2)).date()
        other_submit_url = (
            f"/api/v2/support/connections/{self.other_connection.public_id}/"
            "time-entries/mine/submit/"
        )
        other_created = self.other_worker_client.post(
            other_submit_url,
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(now - timedelta(hours=2)),
                "ended_at": self._iso(now - timedelta(minutes=1)),
                "break_minutes": 0,
            },
            format="json",
        )
        self.assertEqual(other_created.status_code, 201, other_created.data)
        other_entry_id = other_created.data["time_entry"]["id"]

        blocked_review = self.accountant_client.post(
            f"/api/v2/support/time-entries/{other_entry_id}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(blocked_review.status_code, 403)
        staff_list = self.accountant_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/time-entries/"
        )
        self.assertEqual(staff_list.status_code, 200, staff_list.data)
        self.assertEqual(staff_list.data["results"], [])

        future_day = timezone.localdate() + timedelta(days=1)
        blocked_future = self.worker_client.post(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "time-entries/mine/submit/",
            {
                "work_date": future_day.isoformat(),
                "started_at": self._iso(now - timedelta(hours=2)),
                "ended_at": self._iso(now - timedelta(minutes=1)),
                "break_minutes": 0,
            },
            format="json",
        )
        self.assertEqual(blocked_future.status_code, 400)

    def test_calendar_mark_template_batches_only_approved_scoped_requests(self):
        work_date = timezone.localdate() + timedelta(days=7)
        approved_request = WorkerRequest.objects.create(
            organization=self.organization,
            connection=self.connection,
            request_type=WorkerRequest.TYPE_VACATION,
            status=WorkerRequest.STATUS_APPROVED,
            starts_on=work_date,
            ends_on=work_date + timedelta(days=2),
            reviewed_by=self.owner,
            reviewed_at=timezone.now(),
            last_changed_by=self.owner,
        )
        outside_request = WorkerRequest.objects.create(
            organization=self.organization,
            connection=self.other_connection,
            request_type=WorkerRequest.TYPE_VACATION,
            status=WorkerRequest.STATUS_APPROVED,
            starts_on=work_date,
            ends_on=work_date,
            reviewed_by=self.owner,
            reviewed_at=timezone.now(),
            last_changed_by=self.owner,
        )
        organization_url = f"/api/v2/support/organizations/{self.organization.public_id}"
        created_template = self.accountant_client.post(
            f"{organization_url}/calendar-mark-templates/",
            {"name": "Summer leave", "request_type": "vacation"},
            format="json",
        )
        self.assertEqual(created_template.status_code, 201, created_template.data)
        template_id = created_template.data["calendar_mark_template"]["id"]
        self.assertEqual(CalendarMarkTemplate.objects.count(), 1)

        blocked = self.accountant_client.post(
            f"{organization_url}/calendar-mark-batches/",
            {
                "template_id": template_id,
                "worker_request_ids": [str(outside_request.public_id)],
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 404, blocked.data)
        self.assertFalse(CalendarMarkBatch.objects.exists())

        drafted = self.accountant_client.post(
            f"{organization_url}/calendar-mark-batches/",
            {
                "template_id": template_id,
                "worker_request_ids": [str(approved_request.public_id)],
            },
            format="json",
        )
        self.assertEqual(drafted.status_code, 201, drafted.data)
        batch_id = drafted.data["calendar_mark_batch"]["id"]
        self.assertEqual(drafted.data["calendar_mark_batch"]["state"], "draft")
        self.assertFalse(NotificationOutbox.objects.exists())

        workspace = self.accountant_client.get(f"{organization_url}/schedule-workspace/")
        self.assertEqual(workspace.status_code, 200, workspace.data)
        self.assertEqual(len(workspace.data["calendar_templates"]), 1)
        self.assertEqual(len(workspace.data["calendar_mark_batches"]), 1)
        self.assertEqual(
            workspace.data["calendar_mark_batches"][0]["requests"][0]["request_id"],
            str(approved_request.public_id),
        )

        published = self.accountant_client.post(
            f"/api/v2/support/calendar-mark-batches/{batch_id}/publish/",
            {},
            format="json",
        )
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["calendar_mark_batch"]["state"], "published")
        self.assertFalse(NotificationOutbox.objects.exists())

        cancelled = self.accountant_client.post(
            f"/api/v2/support/calendar-mark-batches/{batch_id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        approved_request.refresh_from_db()
        self.assertEqual(approved_request.status, WorkerRequest.STATUS_APPROVED)

    def test_worker_time_api_requires_active_support_access(self):
        self.worker_grant.status = SupportAccessGrant.STATUS_REVOKED
        self.worker_grant.revoked_at = timezone.now()
        self.worker_grant.save(update_fields=["status", "revoked_at", "updated_at"])

        response = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/time-entries/mine/"
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_time_ledger_web_screen_uses_the_same_scope_and_services(self):
        now = timezone.now().replace(second=0, microsecond=0)
        start = now - timedelta(hours=3)
        end = now - timedelta(hours=1)
        work_date = timezone.localtime(start).date()
        screen_url = (
            f"/employer/support/time/?organization={self.organization.public_id}"
            f"&date_from={work_date.isoformat()}&date_to={work_date.isoformat()}"
        )
        self.client.force_login(self.accountant)
        opened = self.client.get(screen_url)
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "Employee timesheet")
        self.assertContains(opened, "Ihor Hours")
        self.assertNotContains(opened, 'value="scheduled_shift_draft"')

        shift = create_scheduled_shift(
            actor=self.accountant,
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=start,
            ends_at=end,
            break_minutes=15,
            worker_label="Morning shift",
        )
        self.assertEqual(shift.state, ScheduledWorkShift.STATE_DRAFT)
        publish_scheduled_shift(actor=self.accountant, shift=shift)
        shift.refresh_from_db()
        self.assertEqual(shift.state, ScheduledWorkShift.STATE_PUBLISHED)

        submitted = self.worker_client.post(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "time-entries/mine/submit/",
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(start),
                "ended_at": self._iso(end),
                "break_minutes": 15,
            },
            format="json",
        )
        self.assertEqual(submitted.status_code, 201, submitted.data)
        entry_id = submitted.data["time_entry"]["id"]

        ledger = self.client.get(screen_url)
        self.assertEqual(ledger.status_code, 200)
        self.assertContains(ledger, "1:45")
        self.assertContains(ledger, "Waiting for review")

        confirmed = self.client.post(
            screen_url,
            {
                "action": "time_entry_confirm",
                "date_from": work_date.isoformat(),
                "date_to": work_date.isoformat(),
                "entry_id": entry_id,
            },
        )
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(
            WorkTimeEntry.objects.get(public_id=entry_id).status,
            WorkTimeEntry.STATUS_CONFIRMED,
        )

    def test_weekly_timesheet_bulk_confirm_and_csv_export(self):
        grant_permission(
            actor=self.owner,
            organization=self.organization,
            membership=self.accountant_membership,
            permission_code=TIME_EXPORT,
        )
        starts_at = (timezone.now() - timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        ends_at = starts_at + timedelta(hours=8)
        work_date = timezone.localtime(starts_at).date()
        shift = create_scheduled_shift(
            actor=self.accountant,
            organization=self.organization,
            connection=self.connection,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=30,
            worker_label="Export shift",
        )
        publish_scheduled_shift(actor=self.accountant, shift=shift)
        submitted = self.worker_client.post(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "time-entries/mine/submit/",
            {
                "work_date": work_date.isoformat(),
                "started_at": self._iso(starts_at),
                "ended_at": self._iso(ends_at),
                "break_minutes": 30,
            },
            format="json",
        )
        self.assertEqual(submitted.status_code, 201, submitted.data)
        entry_id = submitted.data["time_entry"]["id"]
        screen_url = (
            f"/employer/support/time/?organization={self.organization.public_id}"
            f"&date_from={work_date.isoformat()}&date_to={work_date.isoformat()}"
        )
        self.client.force_login(self.accountant)
        bulk = self.client.post(
            screen_url,
            {
                "action": "time_entries_confirm_bulk",
                "date_from": work_date.isoformat(),
                "date_to": work_date.isoformat(),
                "entry_ids": [entry_id],
            },
        )
        self.assertEqual(bulk.status_code, 302)
        self.assertEqual(
            WorkTimeEntry.objects.get(public_id=entry_id).status,
            WorkTimeEntry.STATUS_CONFIRMED,
        )
        exported = self.client.get(f"{screen_url}&export=csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/csv", exported["Content-Type"])
        self.assertIn("Ihor Hours", exported.content.decode("utf-8-sig"))
