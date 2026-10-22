import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from core.agent_auth import issue_token, revoke_token, token_digest
from core.models import (Organization, Membership, Employee, CostRate, Project, ProjectMember,
                         Task, Timesheet, Document, LedgerEntry, AuditEvent, AgentToken, AgentOperation)

class PersonalAgentApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        day = timezone.localdate()
        cls.day = day
        cls.org = Organization.objects.create(name="API公司")
        cls.members = {}
        for name, role in [("api-owner", "owner"), ("api-pm", "pm"), ("api-member", "member"), ("api-other", "member")]:
            user = get_user_model().objects.create_user(name, password="Api-Testing-Only-9182")
            member = Membership.objects.create(organization=cls.org, user=user, role=role)
            cls.members[role if role != "member" else name] = member
            employee = Employee.objects.create(organization=cls.org, name=name, member=member)
            CostRate.objects.create(employee=employee, effective_from=day - timedelta(days=30), hourly_cents=12345, basis="PRIVATE_PERSONAL_COST_BASIS")
        cls.member = cls.members["api-member"]
        cls.pm = cls.members["pm"]
        cls.owner = cls.members["owner"]
        cls.other = cls.members["api-other"]
        cls.project = Project.objects.create(organization=cls.org, code="API-001", name="授权项目", client="客户",
            manager=cls.pm, stage="execution", start_date=day - timedelta(days=30), end_date=day + timedelta(days=30),
            budget_approved=True, budget_cents=1000000)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        ProjectMember.objects.create(project=cls.project, member=cls.other)
        cls.task = Task.objects.create(project=cls.project, name="本人的任务", owner=cls.member,
            start_date=day - timedelta(days=1), due_date=day + timedelta(days=1))
        cls.other_task = Task.objects.create(project=cls.project, name="他人的任务", owner=cls.other,
            start_date=day - timedelta(days=1), due_date=day + timedelta(days=1))
        cls.other_org = Organization.objects.create(name="另一家公司")
        other_user = get_user_model().objects.create_user("foreign-owner", password="Other-Test-9182")
        other_member = Membership.objects.create(organization=cls.other_org, user=other_user, role="owner")
        cls.foreign_project = Project.objects.create(organization=cls.other_org, code="API-001", name="保密项目", client="秘密客户",
            manager=other_member, start_date=day, end_date=day + timedelta(days=1))

    def setUp(self):
        self.token, self.raw = issue_token(self.member, "办公助手", ["projects:read", "tasks:read", "time:write_self", "expense:write_self", "tasks:write_self"], [self.project.pk])
        self.client = Client(HTTP_AUTHORIZATION="Bearer " + self.raw)
        self.key = str(uuid.uuid4())

    def post(self, url, data, key=None, client=None):
        return (client or self.client).post(url, data=data, content_type="application/json", HTTP_IDEMPOTENCY_KEY=key or self.key)

    def time_payload(self, **changes):
        data = {"project_id": self.project.pk, "work_date": str(self.day), "minutes": 120, "note": "真实联调记录", "dry_run": False}
        data.update(changes)
        return data

    def test_session_alone_and_query_token_do_not_authenticate_api(self):
        browser = Client()
        browser.force_login(self.member.user)
        self.assertEqual(browser.get("/api/v1/me").status_code, 401)
        self.assertEqual(Client().get("/api/v1/me", {"token": self.raw}).status_code, 401)

    def test_token_is_stored_hashed_and_not_recorded_in_audit(self):
        self.assertEqual(self.token.token_hash, token_digest(self.raw))
        self.assertNotIn(self.raw, repr(list(AuditEvent.objects.values("payload"))))
        self.assertNotIn(self.raw, repr(AgentToken.objects.values().get(pk=self.token.pk)))

    def test_project_scope_and_tenant_are_enforced(self):
        listed = self.client.get("/api/v1/projects").json()["projects"]
        self.assertEqual([x["id"] for x in listed], [self.project.pk])
        self.assertEqual(self.client.get(f"/api/v1/projects/{self.foreign_project.pk}").status_code, 404)
        self.assertEqual(self.post("/api/v1/timesheets", self.time_payload(project_id=self.foreign_project.pk)).status_code, 404)
        self.assertFalse(Timesheet.objects.exists())

    def test_scope_cannot_be_issued_for_foreign_project_or_privileged_action(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError): issue_token(self.member, "bad", ["projects:read"], [self.foreign_project.pk])
        with self.assertRaises(ValidationError): issue_token(self.owner, "bad", ["payments:approve"], [self.project.pk])

    def test_read_only_token_cannot_submit(self):
        token, raw = issue_token(self.member, "只读", ["projects:read"], [self.project.pk])
        client = Client(HTTP_AUTHORIZATION="Bearer " + raw)
        self.assertEqual(self.post("/api/v1/timesheets", self.time_payload(), client=client).status_code, 403)

    def test_dry_run_is_default_and_rolls_back_business_and_outbox(self):
        from core.models import DomainEvent
        audits, events = AuditEvent.objects.count(), DomainEvent.objects.count()
        data = self.time_payload(); data.pop("dry_run")
        response = self.post("/api/v1/timesheets", data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["dry_run"])
        self.assertFalse(Timesheet.objects.exists())
        self.assertFalse(AgentOperation.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), audits)
        self.assertEqual(DomainEvent.objects.count(), events)

    def test_timesheet_submission_is_personal_pending_and_idempotent(self):
        first = self.post("/api/v1/timesheets", self.time_payload())
        second = self.post("/api/v1/timesheets", self.time_payload())
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Timesheet.objects.count(), 1)
        row = Timesheet.objects.get()
        self.assertEqual(row.employee.member_id, self.member.id)
        self.assertEqual(row.status, "submitted")
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertNotIn("cost_cents", first.json())

    def test_same_idempotency_key_changed_payload_is_conflict(self):
        self.post("/api/v1/timesheets", self.time_payload())
        response = self.post("/api/v1/timesheets", self.time_payload(minutes=180))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Timesheet.objects.get().minutes, 120)

    def test_dry_run_after_submission_reports_existing_without_second_write(self):
        self.post("/api/v1/timesheets", self.time_payload())
        response = self.post("/api/v1/timesheets", self.time_payload(dry_run=True))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dry_run"])
        self.assertTrue(response.json()["already_submitted"])
        self.assertEqual(Timesheet.objects.count(), 1)

    def test_spoofed_employee_approval_and_unknown_fields_are_rejected(self):
        for field, value in [("employee_id", self.other.employee.id), ("submitted_by", self.owner.user_id), ("status", "approved"), ("organization_id", self.other_org.pk)]:
            response = self.post("/api/v1/timesheets", self.time_payload(**{field: value}))
            self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Timesheet.objects.exists())

    def test_strict_units_booleans_and_idempotency_header(self):
        for bad in [True, "120", 120.5]:
            self.assertEqual(self.post("/api/v1/timesheets", self.time_payload(minutes=bad)).status_code, 400)
        self.assertEqual(self.post("/api/v1/timesheets", self.time_payload(dry_run="false")).status_code, 400)
        no_key = self.client.post("/api/v1/timesheets", data=self.time_payload(), content_type="application/json")
        self.assertEqual(no_key.status_code, 400)

    def test_expense_api_cannot_create_payment_or_approve_cost(self):
        data = {"project_id": self.project.pk, "title": "差旅垫付", "amount_cents": 1050,
                "occurred_on": str(self.day), "counterparty": "出租车", "reference": "API-FY-001", "dry_run": False}
        response = self.post("/api/v1/expenses", data)
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(Document.objects.get().kind, "expense")
        self.assertEqual(Document.objects.get().status, "submitted")
        self.assertFalse(LedgerEntry.objects.exists())
        response = self.post("/api/v1/expenses", {**data, "kind": "payment"}, key=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 400)

    def test_task_update_only_own_and_repeat_does_not_increment_version(self):
        data = {"status": "doing", "expected_version": 1, "dry_run": False}
        url = f"/api/v1/tasks/{self.task.pk}/status"
        response = self.post(url, data)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.post(url, data).json(), response.json())
        self.task.refresh_from_db(); self.assertEqual(self.task.version, 2)
        self.assertEqual(self.post(f"/api/v1/tasks/{self.other_task.pk}/status", data).status_code, 404)

    def test_personal_and_read_scopes_do_not_disclose_finance(self):
        response = self.client.get(f"/api/v1/projects/{self.project.pk}")
        self.assertNotIn("finance", response.json()["project"])
        self.assertNotIn("PRIVATE_PERSONAL_COST_BASIS", response.content.decode())
        token, raw = issue_token(self.pm, "项目汇总", ["projects:read", "finance:read_summary"], [self.project.pk])
        response = Client(HTTP_AUTHORIZATION="Bearer " + raw).get(f"/api/v1/projects/{self.project.pk}")
        self.assertIn("cost_cents", response.json()["project"]["finance"])
        self.assertNotIn("hourly", response.content.decode())

    def test_revoke_expire_deactivate_password_change_are_effective(self):
        revoke_token(self.member, self.token.pk)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        for mode in ["expire", "deactivate", "password"]:
            token, raw = issue_token(self.member, mode, ["projects:read"], [self.project.pk])
            if mode == "expire":
                token.expires_at = timezone.now() - timedelta(seconds=1); token.save(update_fields=["expires_at"])
            elif mode == "deactivate":
                Membership.objects.filter(pk=self.member.pk).update(active=False)
            else:
                self.member.user.set_password("Changed-Testing-Password-9182"); self.member.user.save()
            self.assertEqual(Client(HTTP_AUTHORIZATION="Bearer " + raw).get("/api/v1/me").status_code, 401)
            if mode == "deactivate": Membership.objects.filter(pk=self.member.pk).update(active=True)

    def test_role_downgrade_removes_write_and_rate_limit_is_enforced(self):
        Membership.objects.filter(pk=self.member.pk).update(role="auditor")
        self.assertEqual(self.post("/api/v1/timesheets", self.time_payload()).status_code, 403)
        self.token.window_count = 60; self.token.save(update_fields=["window_count"])
        self.assertEqual(self.client.get("/api/v1/me").status_code, 429)

    def test_agent_api_has_no_approval_payment_or_lock_route(self):
        for url in ["/api/v1/approve", "/api/v1/payments", "/api/v1/lock", "/api/v1/users"]:
            self.assertEqual(self.post(url, {}).status_code, 404)
