from copy import deepcopy
from datetime import timedelta
from unittest import mock
import pluggy
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.test import TestCase, Client
from django.utils import timezone
from core.models import Organization, Membership, Project, ProjectMember, PluginState, DomainEvent, EventDelivery
from core.services import audit, verify_audit
from core.agent_auth import issue_token
from core.plugin_runtime import Registry, get_registry, is_enabled, set_enabled, collect_insights, dispatch_pending

hookimpl = pluggy.HookimplMarker("project_hub")

def manifest(name, requires=None, kernel_api=">=1.0,<2"):
    return {"id": name, "name": name, "version": "0.1.0", "kernel_api": kernel_api, "requires": requires or {},
            "default_enabled": True, "capabilities": ["project:read_snapshot", "insights:emit"], "description": "测试插件"}

class PluginRuntimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="插件公司")
        cls.other_org = Organization.objects.create(name="隔离公司")
        cls.owner_user = get_user_model().objects.create_user("plugin-owner", password="Plugin-Test-9182")
        cls.owner = Membership.objects.create(organization=cls.org, user=cls.owner_user, role="owner")
        member_user = get_user_model().objects.create_user("plugin-member", password="Plugin-Test-9182")
        cls.member = Membership.objects.create(organization=cls.org, user=member_user, role="member")
        today = timezone.localdate()
        cls.project = Project.objects.create(organization=cls.org, manager=cls.owner, code="PL-001", name="插件项目", client="客户",
            start_date=today, end_date=today + timedelta(days=30))
        ProjectMember.objects.create(project=cls.project, member=cls.member)

    def snapshot(self):
        return {"id": self.project.id, "name": self.project.name, "url": f"/projects/{self.project.id}/", "stage": "execution",
            "budget_approved": True, "budget_cents": 1000, "cost_cents": 800, "eac_cents": 1200, "etc_cents": 400,
            "has_open_tasks": True, "overdue_tasks": 1, "overdue_milestones": [], "overdue_plans": [],
            "upcoming_milestones": [{"name": "即将交付", "due_date": "2026-10-25", "days_remaining": 3}]}

    def test_defaults_and_company_isolation(self):
        self.assertTrue(is_enabled(self.org, "project_health"))
        self.assertFalse(is_enabled(self.org, "delivery_watch"))
        set_enabled(self.owner, "project_health", False)
        self.assertFalse(is_enabled(self.org, "project_health"))
        self.assertTrue(is_enabled(self.other_org, "project_health"))
        self.assertFalse(is_enabled(self.org, "unknown"))

    def test_only_owner_can_toggle_and_dependencies_are_enforced(self):
        with self.assertRaises(PermissionDenied): set_enabled(self.member, "delivery_watch", True)
        set_enabled(self.owner, "delivery_watch", True)
        with self.assertRaises(ValidationError): set_enabled(self.owner, "project_health", False)
        set_enabled(self.owner, "delivery_watch", False)
        set_enabled(self.owner, "project_health", False)
        with self.assertRaises(ValidationError): set_enabled(self.owner, "delivery_watch", True)
        self.assertTrue(verify_audit(self.org))

    def test_version_mismatch_disables_until_owner_accepts_installed_version(self):
        state = PluginState.objects.create(organization=self.org, plugin_id="project_health", enabled=True, version="0.0.1", changed_by=self.owner_user)
        self.assertFalse(is_enabled(self.org, "project_health"))
        set_enabled(self.owner, "project_health", True)
        self.assertTrue(is_enabled(self.org, "project_health"))

    def test_invalid_kernel_and_dependency_cycle_do_not_break_unrelated_plugins(self):
        class Bad: pass
        class One: pass
        class Two: pass
        class Good: pass
        Bad.manifest = manifest("bad", kernel_api=">=9,<10")
        One.manifest = manifest("one", {"two": ">=0.1"})
        Two.manifest = manifest("two", {"one": ">=0.1"})
        Good.manifest = manifest("good")
        registry = Registry(instances=[Bad(), One(), Two(), Good()])
        self.assertEqual(set(registry.plugins), {"good"})
        self.assertEqual(set(registry.errors), {"bad", "one", "two"})

    def test_base_and_independent_extension_produce_scoped_insights(self):
        before = collect_insights(self.org, [self.snapshot()])
        self.assertTrue(any("预算" in a["title"] for a in before))
        self.assertFalse(any("即将交付" in a["title"] for a in before))
        set_enabled(self.owner, "delivery_watch", True)
        after = collect_insights(self.org, [self.snapshot()])
        self.assertTrue(any("即将交付" in a["title"] for a in after))
        self.assertTrue(all(a["url"].startswith(self.snapshot()["url"]) for a in after))

    def test_non_finance_snapshot_does_not_emit_financial_insights(self):
        snapshot = self.snapshot()
        for field in ["budget_approved", "budget_cents", "cost_cents", "eac_cents", "etc_cents"]: snapshot[field] = None
        alerts = collect_insights(self.org, [snapshot], include_finance=False)
        self.assertTrue(any("任务" in a["title"] for a in alerts))
        self.assertFalse(any("预算" in a["title"] or "成本" in a["title"] for a in alerts))

    def test_failing_or_mutating_plugin_is_isolated_from_other_plugins(self):
        class Bad:
            manifest = manifest("bad")
            @hookimpl
            def project_insights(self, snapshot, include_finance):
                snapshot["name"] = "MUTATED"
                raise RuntimeError("failure")
        class Good:
            manifest = manifest("good")
            @hookimpl
            def project_insights(self, snapshot, include_finance):
                return [{"severity": "info", "title": snapshot["name"], "detail": "检查通过", "url": snapshot["url"]}]
        snapshot = self.snapshot()
        with mock.patch("core.plugin_runtime.get_registry", return_value=Registry(instances=[Bad(), Good()])):
            with self.assertLogs("core.plugin_runtime", level="ERROR"):
                alerts = collect_insights(self.org, [snapshot])
        self.assertEqual(snapshot["name"], self.project.name)
        self.assertTrue(any(a["title"] == self.project.name for a in alerts))
        self.assertTrue(any(a["title"] == "项目检查暂时未完成" for a in alerts))

    def test_untrusted_insight_url_is_rejected(self):
        class BadUrl:
            manifest = manifest("badurl")
            @hookimpl
            def project_insights(self, snapshot, include_finance):
                return [{"severity": "info", "title": "错误链接", "detail": "", "url": "https://other.invalid/"}]
        with mock.patch("core.plugin_runtime.get_registry", return_value=Registry(instances=[BadUrl()])):
            with self.assertLogs("core.plugin_runtime", level="ERROR"):
                alerts = collect_insights(self.org, [self.snapshot()])
        self.assertFalse(any(a["url"].startswith("https:") for a in alerts))

    def test_business_rollback_also_removes_outbox_event(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                audit(self.owner, "test.business_created", self.project, {})
                raise RuntimeError("rollback")
        self.assertFalse(DomainEvent.objects.exists())

    def test_plugin_page_shows_undispatched_backlog_without_other_company_counts(self):
        audit(self.owner, "project.updated", self.project, {})
        other_user = get_user_model().objects.create_user("other-plugin-owner")
        other_owner = Membership.objects.create(organization=self.other_org, user=other_user, role="owner")
        other_source = audit(other_owner, "project.updated", self.other_org, {})
        other_event = DomainEvent.objects.get(source=other_source)
        EventDelivery.objects.create(event=other_event, plugin_id="project_health", status="failed")
        client = Client()
        client.force_login(self.owner_user)
        response = client.get("/plugins/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["unexpanded_count"], 1)
        self.assertEqual(response.context["pending_events"], 0)
        self.assertEqual(response.context["failed_count"], 0)
        self.assertContains(response, "待分发事件")

    def test_delivery_is_idempotent_and_disable_preserves_queued_event(self):
        audit(self.owner, "project.updated", self.project, {})
        set_enabled(self.owner, "project_health", False)
        dispatch_pending()
        row = EventDelivery.objects.get()
        self.assertEqual(row.status, "blocked")
        self.assertEqual(row.attempts, 0)
        set_enabled(self.owner, "project_health", True)
        dispatch_pending()
        row.refresh_from_db(); self.assertEqual(row.status, "done")
        result = deepcopy(row.result)
        dispatch_pending()
        row.refresh_from_db(); self.assertEqual(row.attempts, 1)
        self.assertEqual(row.result, result)
        self.assertEqual(EventDelivery.objects.count(), 1)

    def test_failed_delivery_retries_at_most_three_times(self):
        class Fails:
            manifest = manifest("fails")
            @hookimpl
            def event_received(self, event): raise RuntimeError("failure")
        audit(self.owner, "project.updated", self.project, {})
        with mock.patch("core.plugin_runtime.get_registry", return_value=Registry(instances=[Fails()])):
            for attempt in range(3):
                EventDelivery.objects.update(next_retry_at=timezone.now() - timedelta(seconds=1))
                with self.assertLogs("core.plugin_runtime", level="ERROR"): dispatch_pending()
            row = EventDelivery.objects.get()
            self.assertEqual(row.status, "failed"); self.assertEqual(row.attempts, 3)
            dispatch_pending()
            row.refresh_from_db(); self.assertEqual(row.attempts, 3)

    def test_disabling_agent_plugin_blocks_api_and_new_grants_but_allows_revocation(self):
        token, raw = issue_token(self.member, "办公助手", ["projects:read"], [self.project.pk])
        api_client = Client(HTTP_AUTHORIZATION="Bearer " + raw)
        self.assertEqual(api_client.get("/api/v1/me").status_code, 200)
        set_enabled(self.owner, "agent_gateway", False)
        self.assertEqual(api_client.get("/api/v1/me").status_code, 404)
        with self.assertRaises(ValidationError): issue_token(self.member, "another", ["projects:read"], [self.project.pk])
        from core.agent_auth import revoke_token
        revoke_token(self.member, token.pk)
        token.refresh_from_db(); self.assertIsNotNone(token.revoked_at)
