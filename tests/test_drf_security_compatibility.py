import codecs
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.negotiation import DefaultContentNegotiation, _MediaType
from rest_framework.parsers import JSONParser
from rest_framework.renderers import AdminRenderer, JSONRenderer

from core.agent_auth import issue_token, revoke_token
from core.api import AgentAPIView, TimeInput
from core.models import (
    AgentOperation,
    CostRate,
    Document,
    Employee,
    LedgerEntry,
    Membership,
    Organization,
    Project,
    ProjectMember,
    Timesheet,
)


class DrfSecurityCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.day = timezone.localdate()
        cls.organization = Organization.objects.create(name="DRF兼容公司")
        User = get_user_model()
        pm_user = User.objects.create_user("drf-pm", password="Drf-Testing-Only-9182")
        member_user = User.objects.create_user(
            "drf-member", password="Drf-Testing-Only-9182"
        )
        cls.pm = Membership.objects.create(
            organization=cls.organization, user=pm_user, role="pm"
        )
        cls.member = Membership.objects.create(
            organization=cls.organization, user=member_user, role="member"
        )
        employee = Employee.objects.create(
            organization=cls.organization, name="DRF成员", member=cls.member
        )
        CostRate.objects.create(
            employee=employee,
            effective_from=cls.day - timedelta(days=30),
            hourly_cents=12345,
            basis="PRIVATE_DRF_TEST_BASIS",
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            code="DRF-001",
            name="DRF授权项目",
            client="DRF客户",
            manager=cls.pm,
            stage="execution",
            start_date=cls.day - timedelta(days=30),
            end_date=cls.day + timedelta(days=30),
            budget_approved=True,
            budget_cents=1000000,
        )
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        other_organization = Organization.objects.create(name="DRF保密公司")
        other_user = User.objects.create_user(
            "drf-foreign", password="Drf-Foreign-Testing-9182"
        )
        other_member = Membership.objects.create(
            organization=other_organization, user=other_user, role="owner"
        )
        cls.foreign_project = Project.objects.create(
            organization=other_organization,
            code="DRF-001",
            name="DRF保密项目",
            client="DRF秘密客户",
            manager=other_member,
            start_date=cls.day,
            end_date=cls.day + timedelta(days=1),
        )

    def setUp(self):
        self.token, self.raw = issue_token(
            self.member,
            "DRF安全兼容",
            ["projects:read", "time:write_self"],
            [self.project.pk],
        )
        self.client = Client(HTTP_AUTHORIZATION="Bearer " + self.raw)

    def time_payload(self, **changes):
        data = {
            "project_id": self.project.pk,
            "work_date": str(self.day),
            "minutes": 120,
            "note": "DRF真实请求兼容测试",
        }
        data.update(changes)
        return data

    def post_timesheet(self, data=None, client=None, content_type="application/json"):
        # Send wire bytes directly: Client.post would try encoding the test
        # payload using the hostile charset before it reaches the application.
        return (client or self.client).generic(
            "POST", "/api/v1/timesheets",
            data=json.dumps(data or self.time_payload()).encode("utf-8"),
            content_type=content_type,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    def assertNoBusinessWrites(self):
        for model in (Timesheet, Document, LedgerEntry, AgentOperation):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_agent_api_only_enables_json_and_not_drf_token_authentication(self):
        self.assertEqual(AgentAPIView.parser_classes, [JSONParser])
        self.assertEqual(AgentAPIView.renderer_classes, [JSONRenderer])
        self.assertNotIn(AdminRenderer, AgentAPIView.renderer_classes)
        self.assertNotIn("rest_framework.authtoken", settings.INSTALLED_APPS)
        self.assertFalse(
            any(
                authentication.__module__.startswith("rest_framework.authtoken")
                for authentication in AgentAPIView.authentication_classes
            )
        )

    def test_valid_json_dry_run_preserves_rollback_behavior(self):
        response = self.post_timesheet()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["dry_run"])
        self.assertTrue(response.json()["valid"])
        self.assertNoBusinessWrites()

    def test_valid_json_non_dry_run_preserves_pending_timesheet_behavior(self):
        response = self.post_timesheet(self.time_payload(dry_run=False))
        self.assertEqual(response.status_code, 202, response.content)
        row = Timesheet.objects.get()
        self.assertEqual(row.employee.member, self.member)
        self.assertEqual(row.status, Timesheet.Status.SUBMITTED)
        self.assertEqual(row.minutes, 120)
        self.assertFalse(response.json()["dry_run"])
        self.assertNotIn("cost_cents", response.json())
        self.assertFalse(Document.objects.exists())
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertEqual(AgentOperation.objects.count(), 1)

    def test_anonymous_requests_cannot_read_identity_or_projects(self):
        for url in ("/api/v1/me", "/api/v1/projects"):
            with self.subTest(url=url):
                response = Client().get(url)
                body = response.content.decode()
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(self.organization.name, body)
                self.assertNotIn(self.project.name, body)
                self.assertNotIn(self.foreign_project.client, body)

    def test_cross_company_reads_and_writes_do_not_leak_or_mutate(self):
        response = self.client.get(f"/api/v1/projects/{self.foreign_project.pk}")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.foreign_project.name, response.content.decode())
        self.assertNotIn(self.foreign_project.client, response.content.decode())
        response = self.post_timesheet(
            self.time_payload(project_id=self.foreign_project.pk, dry_run=False)
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.foreign_project.client, response.content.decode())
        self.assertNoBusinessWrites()

    def test_read_only_scope_cannot_submit_timesheet(self):
        token, raw = issue_token(
            self.member, "DRF只读", ["projects:read"], [self.project.pk]
        )
        client = Client(HTTP_AUTHORIZATION="Bearer " + raw)
        response = self.post_timesheet(
            self.time_payload(dry_run=False), client=client
        )
        self.assertEqual(response.status_code, 403)
        self.assertNoBusinessWrites()

    def test_revoked_and_expired_tokens_remain_rejected(self):
        for mode in ("revoked", "expired"):
            with self.subTest(mode=mode):
                token, raw = issue_token(
                    self.member, f"DRF-{mode}", ["projects:read"], [self.project.pk]
                )
                if mode == "revoked":
                    revoke_token(self.member, token.pk)
                else:
                    token.expires_at = timezone.now() - timedelta(seconds=1)
                    token.save(update_fields=["expires_at"])
                response = Client(HTTP_AUTHORIZATION="Bearer " + raw).get("/api/v1/me")
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(self.organization.name, response.content.decode())
                self.assertNotIn(self.member.user.username, response.content.decode())

    def test_non_text_json_codecs_are_rejected_without_side_effects(self):
        for codec in ("base64_codec", "bz2_codec"):
            with self.subTest(codec=codec):
                with mock.patch("rest_framework.parsers.codecs.getreader", wraps=codecs.getreader) as reader:
                    response = self.post_timesheet(
                        self.time_payload(dry_run=False),
                        content_type=f"application/json; charset={codec}",
                    )
                self.assertEqual(response.status_code, 400, response.content)
                reader.assert_not_called()
                self.assertIn("Unsupported charset", response.content.decode())
                self.assertNoBusinessWrites()

    def test_overlong_content_type_is_rejected_without_side_effects(self):
        response = self.post_timesheet(
            self.time_payload(dry_run=False),
            content_type="application/json;" + "a" * 300,
        )
        self.assertEqual(response.status_code, 415, response.content)
        self.assertNoBusinessWrites()

    def test_overlong_accept_header_has_repeatable_bounded_parsing(self):
        header = ",".join(["application/json; q=1"] * 600)
        call_counts = []
        with mock.patch("rest_framework.negotiation._MediaType", wraps=_MediaType) as media_type:
            for _ in range(2):
                response = self.client.get("/api/v1/projects", HTTP_ACCEPT=header)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    [row["id"] for row in response.json()["projects"]],
                    [self.project.pk],
                )
                call_counts.append(media_type.call_count)
                media_type.reset_mock()
        self.assertEqual(call_counts[0], call_counts[1])
        self.assertGreater(call_counts[0], 0)
        self.assertLessEqual(
            call_counts[0],
            DefaultContentNegotiation.max_accept_tokens
            + len(AgentAPIView.renderer_classes),
        )

    def test_unacceptable_accept_header_returns_406_without_project_data(self):
        response = self.client.get(
            "/api/v1/projects", HTTP_ACCEPT="application/xml; unsupported=yes"
        )
        self.assertEqual(response.status_code, 406, response.content)
        self.assertNotIn("projects", response.json())
        self.assertNotIn(self.project.name, response.content.decode())
        self.assertNotIn(self.foreign_project.client, response.content.decode())

    def test_deeply_parameterized_accept_token_is_skipped_before_parsing(self):
        header = 'application/json; quoted="' + (';parameter=value' * 250) + '", application/json'
        with mock.patch("rest_framework.negotiation._MediaType", wraps=_MediaType) as media_type:
            response = self.client.get("/api/v1/projects", HTTP_ACCEPT=header)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["id"] for row in response.json()["projects"]], [self.project.pk])
        self.assertGreater(media_type.call_count, 0)
        self.assertLessEqual(media_type.call_count, 2)
        for call in media_type.call_args_list:
            self.assertLessEqual(len(call.args[0]), DefaultContentNegotiation.max_media_type_length)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1024)
    def test_large_json_is_rejected_before_business_validation_or_writes(self):
        payload = self.time_payload(note="x" * 2048, dry_run=False)
        with mock.patch.object(TimeInput, "is_valid", autospec=True, side_effect=TimeInput.is_valid) as is_valid:
            response = self.post_timesheet(payload)
        self.assertEqual(response.status_code, 400, response.content)
        is_valid.assert_not_called()
        self.assertNoBusinessWrites()
