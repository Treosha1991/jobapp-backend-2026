from calendar import monthrange
from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.models import UserProfile

from support.models import (
    Announcement,
    AnnouncementAcknowledgement,
    DocumentRequestPackage,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    ProjectCrew,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportVacancy,
    SupportWorkerDocumentReference,
    TaskAssignment,
    Vehicle,
    WorkerRequest,
    WorkerScheduleDayOff,
    WorkerTask,
    WorkProject,
    WorkTimeEntry,
    Worksite,
)
from support.services.organizations import create_organization


@override_settings(
    SUPPORT_FEATURE_ENABLED=True,
    SUPPORT_PROJECT_FIRST_ENABLED=True,
    AVATAR_PUBLIC_BASE_URL="https://avatars.example.test",
)
class WorkerWorkspaceAPITests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="worker-workspace-operator",
            email="worker-workspace-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="worker-workspace-owner",
            email="worker-workspace-owner@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="worker-workspace-worker",
            email="worker-workspace-worker@example.com",
            password="password",
            first_name="Viktoriia",
            last_name="Tkachenko",
        )
        self.other_worker = User.objects.create_user(
            username="worker-workspace-other",
            email="worker-workspace-other@example.com",
            password="password",
            first_name="Oleh",
            last_name="Savchuk",
        )
        self.organization, self.owner_membership = create_organization(
            jobhub_operator=self.operator,
            legal_name="Worker Workspace Agency sp. z o.o.",
            display_name="Worker Workspace Agency",
            owner_email=self.owner.email,
        )
        self.connection = self._connection(
            self.worker,
            "worker",
            assigned_manager=self.owner_membership,
            has_driving_license=True,
        )
        self.driver_connection = self._connection(
            self.other_worker,
            "driver",
            has_driving_license=True,
        )
        self.future_driver_user = User.objects.create_user(
            username="worker-workspace-future-driver",
            email="worker-workspace-future-driver@example.com",
            password="password",
            first_name="Denys",
            last_name="Shevchenko",
        )
        self.future_driver_connection = self._connection(
            self.future_driver_user,
            "future-driver",
            has_driving_license=True,
        )
        UserProfile.objects.create(
            user=self.other_worker,
            avatar_key="users/driver/avatar.jpg",
        )
        self.grant = SupportAccessGrant.objects.create(
            user=self.worker,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=30),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        self.driver_grant = SupportAccessGrant.objects.create(
            user=self.other_worker,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=30),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        SupportAccessGrant.objects.create(
            user=self.future_driver_user,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=30),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.worker)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_worker)

        self.today = timezone.localdate()
        self.month_start = self.today.replace(day=1)
        self.month_end = self.month_start.replace(
            day=monthrange(self.today.year, self.today.month)[1]
        )
        self.month = self.month_start.strftime("%Y-%m")
        self.url = (
            f"/api/v2/support/connections/{self.connection.public_id}/"
            f"workspace/mine/?month={self.month}"
        )
        self.day_off_date = self.month_start
        self.absence_date = self.month_start + timedelta(days=1)
        if self.day_off_date == self.today:
            self.day_off_date = self.month_start + timedelta(days=2)
        if self.absence_date in {self.today, self.day_off_date}:
            self.absence_date = self.month_start + timedelta(days=3)

        self.worksite = self._worksite("Current plant", "Currentstraat", "10")
        self.current_project = self._project("Current project", self.worksite)
        self.current_vehicle = self._vehicle("Vivaro", "CUR-01")
        self.current_crew = self._crew("Crew Current", self.current_project)
        ProjectCrewResourceAssignment.objects.create(
            crew=self.current_crew,
            driver_connection=self.driver_connection,
            vehicle=self.current_vehicle,
            starts_on=self.month_start,
            created_by=self.owner,
        )
        ProjectCrewPassenger.objects.create(
            crew=self.current_crew,
            connection=self.connection,
            starts_on=self.month_start,
            created_by=self.owner,
        )
        self.today_shift = self._shift(
            crew=self.current_crew,
            work_date=self.today,
            driver=self.driver_connection,
            passenger=self.connection,
            vehicle=self.current_vehicle,
        )
        self.absence_shift = self._shift(
            crew=self.current_crew,
            work_date=self.absence_date,
            driver=self.driver_connection,
            vehicle=self.current_vehicle,
        )
        ProjectCrewMemberAbsence.objects.create(
            organization=self.organization,
            crew=self.current_crew,
            connection=self.connection,
            work_date=self.absence_date,
            created_by=self.owner,
        )
        WorkerScheduleDayOff.objects.create(
            organization=self.organization,
            connection=self.connection,
            work_date=self.day_off_date,
            created_by=self.owner,
        )

        self.future_worksite = self._worksite("Future plant", "Futurestraat", "20")
        self.future_project = self._project("Future project", self.future_worksite)
        self.future_vehicle = self._vehicle("Panda", "FUT-02")
        self.future_crew = self._crew("Crew Future", self.future_project)
        ProjectCrewResourceAssignment.objects.create(
            crew=self.future_crew,
            driver_connection=self.future_driver_connection,
            vehicle=self.future_vehicle,
            starts_on=self.month_start,
            created_by=self.owner,
        )
        self.next_shift = self._shift(
            crew=self.future_crew,
            work_date=self.today + timedelta(days=1),
            driver=self.future_driver_connection,
            passenger=self.connection,
            vehicle=self.future_vehicle,
        )

        self.time_entry = WorkTimeEntry.objects.create(
            organization=self.organization,
            connection=self.connection,
            work_date=self.today,
            started_at=self._aware(self.today, time(6, 0)),
            ended_at=self._aware(self.today, time(14, 0)),
            break_minutes=30,
            worked_minutes=450,
            status=WorkTimeEntry.STATUS_MANAGER_ADJUSTED,
            submitted_at=timezone.now(),
            last_changed_by=self.owner,
        )
        self._housing()
        self._attention_items()

    def _connection(
        self,
        user,
        suffix,
        *,
        assigned_manager=None,
        has_driving_license=False,
    ):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Vacancy {suffix}",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=user,
            revision=1,
            preferred_language="ru",
            citizenship_country_code="BY",
            current_country_code="PL",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        return SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=application,
            candidate=user,
            assigned_manager=assigned_manager,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
            has_driving_license=has_driving_license,
        )

    def _worksite(self, name, street, building):
        return Worksite.objects.create(
            organization=self.organization,
            internal_name=name,
            country_code="NL",
            city="Lelystad",
            postal_code="8223AA",
            street=street,
            building=building,
            instructions=f"Instructions for {name}",
            created_by=self.owner,
        )

    def _project(self, name, worksite):
        return WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name=name,
            worker_visible_name=name,
            instructions=f"Worker instructions for {name}",
            worker_capacity=9,
            starts_on=self.month_start,
            created_by=self.owner,
        )

    def _vehicle(self, name, registration):
        return Vehicle.objects.create(
            organization=self.organization,
            internal_name=name,
            registration_identifier=registration,
            seat_capacity=9,
            created_by=self.owner,
        )

    def _crew(self, name, project):
        return ProjectCrew.objects.create(
            organization=self.organization,
            project=project,
            internal_name=name,
            created_by=self.owner,
        )

    @staticmethod
    def _aware(work_date, value):
        return timezone.make_aware(datetime.combine(work_date, value))

    def _shift(self, *, crew, work_date, driver, vehicle, passenger=None):
        starts_at = self._aware(work_date, time(6, 0))
        shift = ProjectCrewShift.objects.create(
            crew=crew,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=8),
            break_minutes=30,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=vehicle,
            created_by=self.owner,
        )
        if passenger is not None:
            ProjectCrewShiftMember.objects.create(
                shift=shift,
                connection=passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
                created_by=self.owner,
            )
        return shift

    def _housing(self):
        site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Worker house",
            country_code="NL",
            city="Lelystad",
            postal_code="8223XP",
            street="Zandbang",
            building="22",
            rules_text="Keep shared rooms tidy.",
            contact_name="House manager",
            contact_phone="+31123456789",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=site, label="1A", capacity=2)
        current_place = HousingPlace.objects.create(room=room, label="1")
        upcoming_place = HousingPlace.objects.create(room=room, label="2")
        now = timezone.now()
        self.current_housing = HousingAssignment.objects.create(
            organization=self.organization,
            connection=self.connection,
            place=current_place,
            check_in_at=now - timedelta(days=5),
            check_out_at=now + timedelta(days=1),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=now - timedelta(days=5),
        )
        self.upcoming_housing = HousingAssignment.objects.create(
            organization=self.organization,
            connection=self.connection,
            place=upcoming_place,
            check_in_at=now + timedelta(days=2),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=now,
        )

    def _attention_items(self):
        reference = SupportWorkerDocumentReference.objects.create(
            user=self.worker,
            reference_code="JH-WORKSPACE-01",
        )
        DocumentRequestPackage.objects.create(
            organization=self.organization,
            connection=self.connection,
            recipient_email="documents@example.com",
            account_reference=reference,
            requested_items=[{"type": "passport"}],
            status=DocumentRequestPackage.STATUS_REQUESTED,
            created_by=self.owner,
        )
        WorkerRequest.objects.create(
            organization=self.organization,
            connection=self.connection,
            request_type=WorkerRequest.TYPE_DAY_OFF,
            status=WorkerRequest.STATUS_NEEDS_CLARIFICATION,
            starts_on=self.today,
            ends_on=self.today,
            submitted_at=timezone.now(),
            last_changed_by=self.owner,
        )
        task = WorkerTask.objects.create(
            organization=self.organization,
            title="Contact the coordinator",
            instructions="Confirm the meeting time.",
            state=WorkerTask.STATE_PUBLISHED,
            published_by=self.owner,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        TaskAssignment.objects.create(
            task=task,
            connection=self.connection,
            status=TaskAssignment.STATUS_RETURNED,
            last_changed_by=self.owner,
        )
        announcement = Announcement.objects.create(
            organization=self.organization,
            title="Schedule update",
            body="Read the updated arrival information.",
            requires_acknowledgement=True,
            state=Announcement.STATE_PUBLISHED,
            published_by=self.owner,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        AnnouncementAcknowledgement.objects.create(
            announcement=announcement,
            connection=self.connection,
        )
        self.conversation = SupportConversation.objects.create(
            organization=self.organization,
            connection=self.connection,
            private_worker=self.worker,
            private_manager=self.owner,
            kind=SupportConversation.KIND_MANAGER,
            created_by=self.owner,
        )
        SupportConversationMember.objects.create(
            conversation=self.conversation,
            user=self.worker,
            role=SupportConversationMember.ROLE_WORKER,
        )
        SupportConversationMember.objects.create(
            conversation=self.conversation,
            user=self.owner,
            organization_membership=self.owner_membership,
            role=SupportConversationMember.ROLE_STAFF,
        )
        SupportMessage.objects.create(
            conversation=self.conversation,
            sender=self.owner,
            body="Please check today's information.",
            original_language=SupportMessage.LANGUAGE_EN,
        )

    def test_snapshot_uses_project_first_current_and_next_shift(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["connection"]["id"], str(self.connection.public_id))
        self.assertEqual(response.data["worker"]["display_name"], "Viktoriia Tkachenko")
        self.assertTrue(response.data["worker"]["has_driving_license"])
        self.assertEqual(response.data["current_assignment"]["basis"], "today_shift")
        self.assertEqual(
            response.data["current_assignment"]["project"]["name"],
            "Current project",
        )
        self.assertEqual(response.data["today_shift"]["project"]["name"], "Current project")
        self.assertEqual(response.data["next_shift"]["project"]["name"], "Future project")
        self.assertEqual(
            response.data["today_shift"]["effective_vehicle"]["registration_identifier"],
            "CUR-01",
        )
        effective_driver = response.data["today_shift"]["effective_driver"]
        self.assertEqual(effective_driver["display_name"], "Oleh Savchuk")
        self.assertEqual(effective_driver["first_name"], "Oleh")
        self.assertEqual(
            effective_driver["avatar_url"],
            "https://avatars.example.test/users/driver/avatar.jpg",
        )
        self.assertEqual(
            response.data["current_assignment"]["driver"],
            effective_driver,
        )
        self.assertEqual(
            set(response.data["today_shift"]["crew_members"][0]),
            {"first_name", "last_name", "display_name", "avatar_url", "role"},
        )
        self.assertEqual(
            response.data["manager_conversation_id"],
            str(self.conversation.public_id),
        )
        self.assertTrue(response.data["can_open_manager_chat"])

    def test_active_worker_manager_chat_reuses_the_exact_assigned_pair(self):
        response = self.client.post(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "open-manager-chat/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["created"])
        self.assertEqual(
            response.data["conversation"]["id"],
            str(self.conversation.public_id),
        )
        self.assertEqual(
            set(
                self.conversation.members.filter(
                    left_at__isnull=True,
                ).values_list("user_id", flat=True)
            ),
            {self.worker.id, self.owner.id},
        )

    def test_active_worker_can_create_missing_assigned_manager_chat(self):
        self.conversation.delete()

        response = self.client.post(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "open-manager-chat/"
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["created"])
        conversation = SupportConversation.objects.get(
            public_id=response.data["conversation"]["id"]
        )
        self.assertEqual(conversation.private_worker, self.worker)
        self.assertEqual(conversation.private_manager, self.owner)
        self.assertEqual(
            set(
                conversation.members.filter(left_at__isnull=True).values_list(
                    "user_id",
                    flat=True,
                )
            ),
            {self.worker.id, self.owner.id},
        )

    def test_week_snapshot_has_exactly_seven_days_and_day_specific_members(self):
        response = self.client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "workspace/mine/week/",
            {"selected_date": self.today.isoformat()},
        )

        self.assertEqual(response.status_code, 200, response.data)
        week_start = self.today - timedelta(days=self.today.weekday())
        self.assertEqual(response.data["selected_date"], self.today.isoformat())
        self.assertEqual(response.data["week"]["starts_on"], week_start.isoformat())
        self.assertEqual(
            response.data["week"]["ends_on"],
            (week_start + timedelta(days=6)).isoformat(),
        )
        self.assertEqual(response.data["week"]["number"], self.today.isocalendar().week)
        self.assertEqual(len(response.data["days"]), 7)
        self.assertEqual(
            [item["date"] for item in response.data["days"]],
            [
                (week_start + timedelta(days=offset)).isoformat()
                for offset in range(7)
            ],
        )
        self.assertEqual(
            sum(item["is_selected"] for item in response.data["days"]),
            1,
        )
        self.assertEqual(
            sum(item["is_today"] for item in response.data["days"]),
            1,
        )
        by_date = {item["date"]: item for item in response.data["days"]}
        today = by_date[self.today.isoformat()]
        self.assertEqual(today["shift"]["id"], str(self.today_shift.public_id))
        self.assertEqual(today["shifts"][0]["id"], str(self.today_shift.public_id))
        members = today["shift"]["crew_members"]
        self.assertEqual(
            {item["connection_id"] for item in members},
            {
                str(self.connection.public_id),
                str(self.driver_connection.public_id),
            },
        )
        driver = next(item for item in members if item["role"] == "driver")
        self.assertEqual(driver["first_name"], "Oleh")
        self.assertEqual(driver["display_name"], "Oleh Savchuk")
        self.assertEqual(
            driver["avatar_url"],
            "https://avatars.example.test/users/driver/avatar.jpg",
        )
        self.assertFalse(driver["is_self"])
        self.assertTrue(driver["can_open_chat"])
        self_member = next(item for item in members if item["is_self"])
        self.assertFalse(self_member["can_open_chat"])
        self.assertEqual(response.data["worker"]["first_name"], "Viktoriia")
        self.assertEqual(response.data["worker"]["last_name"], "Tkachenko")
        self.assertIn("avatar_url", response.data["worker"])

        if self.next_shift.work_date <= week_start + timedelta(days=6):
            next_members = by_date[self.next_shift.work_date.isoformat()]["shift"][
                "crew_members"
            ]
            self.assertEqual(
                {item["connection_id"] for item in next_members},
                {
                    str(self.connection.public_id),
                    str(self.future_driver_connection.public_id),
                },
            )
            self.assertNotIn(
                str(self.driver_connection.public_id),
                {item["connection_id"] for item in next_members},
            )

    def test_week_time_entry_access_waits_for_latest_shift_end_on_multi_shift_day(self):
        self.time_entry.delete()
        self.today_shift.delete()
        now = timezone.now().replace(second=0, microsecond=0)
        early = ProjectCrewShift.objects.create(
            crew=self.current_crew,
            work_date=self.today,
            starts_at=now - timedelta(hours=4),
            ends_at=now - timedelta(hours=2),
            break_minutes=0,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=early,
            connection=self.driver_connection,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.current_vehicle,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=early,
            connection=self.connection,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        late = ProjectCrewShift.objects.create(
            crew=self.future_crew,
            work_date=self.today,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=2),
            break_minutes=0,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=late,
            connection=self.future_driver_connection,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.future_vehicle,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=late,
            connection=self.connection,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )

        response = self.client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "workspace/mine/week/",
            {"selected_date": self.today.isoformat()},
        )

        self.assertEqual(response.status_code, 200, response.data)
        today = next(item for item in response.data["days"] if item["is_today"])
        self.assertEqual(len(today["shifts"]), 2)
        self.assertEqual(today["shift"]["id"], str(early.public_id))
        self.assertEqual(today["time_entry_access"]["code"], "shift_not_finished")
        self.assertEqual(today["time_entry_access"]["available_at"], late.ends_at)

    def test_shift_peer_chat_is_idempotent_and_rejects_non_members(self):
        url = (
            f"/api/v2/support/connections/{self.connection.public_id}/"
            f"project-first/shifts/{self.today_shift.public_id}/open-worker-chat/"
        )
        payload = {"target_connection_id": str(self.driver_connection.public_id)}

        first = self.client.post(url, payload, format="json")
        second = self.client.post(url, payload, format="json")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            first.data["conversation"]["id"],
            second.data["conversation"]["id"],
        )
        conversation = SupportConversation.objects.get(
            public_id=first.data["conversation"]["id"]
        )
        self.assertEqual(conversation.kind, SupportConversation.KIND_DRIVER)
        self.assertEqual(
            set(
                conversation.members.filter(left_at__isnull=True).values_list(
                    "user_id",
                    flat=True,
                )
            ),
            {self.worker.id, self.other_worker.id},
        )

        not_on_this_day = self.client.post(
            url,
            {
                "target_connection_id": str(
                    self.future_driver_connection.public_id
                )
            },
            format="json",
        )
        self_target = self.client.post(
            url,
            {"target_connection_id": str(self.connection.public_id)},
            format="json",
        )
        wrong_owner = self.other_client.post(url, payload, format="json")
        self.assertEqual(not_on_this_day.status_code, 403, not_on_this_day.data)
        self.assertEqual(self_target.status_code, 403, self_target.data)
        self.assertEqual(wrong_owner.status_code, 404, wrong_owner.data)

        self.driver_grant.status = SupportAccessGrant.STATUS_REVOKED
        self.driver_grant.revoked_at = timezone.now()
        self.driver_grant.save(
            update_fields=("status", "revoked_at", "updated_at")
        )
        inactive_target = self.client.post(url, payload, format="json")
        self.assertEqual(inactive_target.status_code, 403, inactive_target.data)

    def test_worker_can_scope_own_conversations_to_workspace_organization(self):
        response = self.client.get(
            "/api/v2/support/conversations/mine/",
            {"organization": str(self.organization.public_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [item["id"] for item in response.data["results"]],
            [str(self.conversation.public_id)],
        )

    def test_snapshot_returns_complete_month_exceptions_and_server_time_totals(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["calendar_days"]), self.month_end.day)
        by_date = {item["date"]: item for item in response.data["calendar_days"]}
        today = by_date[self.today.isoformat()]
        self.assertEqual(today["shift"]["id"], str(self.today_shift.public_id))
        self.assertEqual(today["time_entry"]["status"], WorkTimeEntry.STATUS_MANAGER_ADJUSTED)
        self.assertEqual(today["time_entry"]["worked_minutes"], 450)
        self.assertEqual(today["time_entry"]["worked_duration"], "7:30")
        self.assertEqual(today["time_entry"]["decimal_hours"], "7.50")
        self.assertFalse(today["time_entry_access"]["can_edit"])
        self.assertEqual(today["time_entry_access"]["code"], "manager_adjusted")
        self.assertTrue(by_date[self.day_off_date.isoformat()]["day_off"])
        absence = by_date[self.absence_date.isoformat()]
        self.assertTrue(absence["absence"])
        self.assertEqual(absence["shift"]["own_role"], ProjectCrewShiftMember.ROLE_PASSENGER)
        self.assertEqual(response.data["time_summary"]["month"]["worked_minutes"], 450)
        self.assertEqual(response.data["time_summary"]["month"]["worked_duration"], "7:30")
        self.assertEqual(response.data["time_summary"]["month"]["decimal_hours"], "7.50")
        expected_month_planned = 450 + (
            450 if self.next_shift.work_date <= self.month_end else 0
        )
        self.assertEqual(
            response.data["time_summary"]["month"]["planned_minutes"],
            expected_month_planned,
        )
        self.assertEqual(
            response.data["time_summary"]["month"]["planned_duration"],
            f"{expected_month_planned // 60}:{expected_month_planned % 60:02d}",
        )
        self.assertEqual(response.data["time_summary"]["current_week"]["worked_minutes"], 450)

    def test_snapshot_returns_only_own_housing_and_action_counts(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        housing = response.data["housing"]
        self.assertEqual(housing["current"]["id"], str(self.current_housing.public_id))
        self.assertEqual(housing["current"]["site_name"], "Worker house")
        self.assertEqual(housing["current"]["room_label"], "1A")
        self.assertEqual(housing["current"]["place_label"], "1")
        self.assertEqual(housing["current"]["contact"]["name"], "House manager")
        self.assertEqual(
            [item["id"] for item in housing["upcoming"]],
            [str(self.upcoming_housing.public_id)],
        )
        self.assertEqual(
            response.data["attention"],
            {
                "document_requests": 1,
                "worker_requests": 1,
                "tasks": 1,
                "announcements": 1,
                "time_entries": 1,
                "unread_conversations": 1,
                "unread_messages": 1,
                "total": 6,
            },
        )

    def test_snapshot_requires_active_access_and_owned_non_archived_connection(self):
        other_response = self.other_client.get(self.url)
        self.assertEqual(other_response.status_code, 404, other_response.data)
        other_week_response = self.other_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "workspace/mine/week/",
            {"selected_date": self.today.isoformat()},
        )
        self.assertEqual(
            other_week_response.status_code,
            404,
            other_week_response.data,
        )

        self.grant.status = SupportAccessGrant.STATUS_REVOKED
        self.grant.revoked_at = timezone.now()
        self.grant.save(update_fields=["status", "revoked_at", "updated_at"])
        no_access_response = self.client.get(self.url)
        self.assertEqual(no_access_response.status_code, 403, no_access_response.data)

        self.grant.status = SupportAccessGrant.STATUS_ACTIVE
        self.grant.revoked_at = None
        self.grant.save(update_fields=["status", "revoked_at", "updated_at"])
        self.connection.is_archived = True
        self.connection.archived_at = timezone.now()
        self.connection.save(update_fields=["is_archived", "archived_at", "updated_at"])
        archived_response = self.client.get(self.url)
        self.assertEqual(archived_response.status_code, 404, archived_response.data)

    def test_snapshot_rejects_invalid_month_and_disabled_project_first_workspace(self):
        invalid = self.client.get(
            self.url.replace(f"month={self.month}", "month=2026-99")
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(invalid.data["month"], "invalid_month")

        invalid_week = self.client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "workspace/mine/week/",
            {"selected_date": "2026-99-99"},
        )
        self.assertEqual(invalid_week.status_code, 400, invalid_week.data)
        self.assertEqual(invalid_week.data["selected_date"], "invalid_date")
        overflow_week = self.client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "workspace/mine/week/",
            {"selected_date": "9999-12-31"},
        )
        self.assertEqual(overflow_week.status_code, 400, overflow_week.data)
        self.assertEqual(overflow_week.data["selected_date"], "invalid_date")

        with override_settings(SUPPORT_PROJECT_FIRST_ENABLED=False):
            disabled = self.client.get(self.url)
        self.assertEqual(disabled.status_code, 404, disabled.data)
