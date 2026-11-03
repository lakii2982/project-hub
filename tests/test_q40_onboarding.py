"""Q-40 first-run wizard: empty DB -> company -> customer -> project -> contract -> time."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import onboarding
from core.contract_changes import contract_amount_cents
from core.models import (AuditEvent, CostRate, Document, Employee, LedgerEntry, Membership,
                         NonProjectDestination, Organization, Project, Timesheet)
from pmo_plugins.billing.models import Account, Invoice

PASSWORD = "FirstRun-2026!ok"
TODAY = timezone.localdate()
STRONG = {"company_name": "星野咨询", "username": "firstowner",
          "password": PASSWORD, "password_confirm": PASSWORD}


class OnboardingFlowTests(TestCase):
    def start_company(self, client=None, **overrides):
        payload = dict(STRONG)
        payload.update(overrides)
        return (client or self.client).post(reverse("setup"), payload)

    def complete_company(self, client=None):
        response = self.start_company(client)
        self.assertEqual(response.status_code, 302, response.content[:800])
        return Membership.objects.get(role="owner")

    def complete_customer(self, client=None):
        return (client or self.client).post(reverse("setup-customer"),
                                            {"customer_name": "星河零售"})

    def complete_project(self, client=None, code="P-FIRST"):
        return (client or self.client).post(reverse("setup-project"), {
            "name": "首个数据平台项目", "code": code, "start_date": TODAY,
            "end_date": TODAY + timedelta(days=30)})

    def complete_contract(self, client=None, amount="1234.56"):
        return (client or self.client).post(reverse("setup-contract"), {"amount": amount})

    def complete_time(self, client=None, rate="300.00"):
        return (client or self.client).post(reverse("setup-time"), {
            "work_date": TODAY, "minutes": 60, "note": "首次需求梳理与启动会",
            "hourly_rate": rate})

    def test_wizard_completes_from_empty_db_and_result_is_usable(self):
        self.assertFalse(Organization.objects.exists())
        self.assertContains(self.client.get(reverse("setup")), "建立你的公司工作区")
        self.assertContains(self.client.get(reverse("login")), "从零开始启用")
        self.assertEqual(self.complete_company().user.username, "firstowner")

        organization = Organization.objects.get()
        self.assertEqual(organization.currency, "CNY")
        self.assertFalse(organization.is_demo)
        self.assertEqual(organization.name, "星野咨询")
        self.assertEqual(Membership.objects.count(), 1)
        self.assertEqual(Employee.objects.get().member_id, Membership.objects.get().pk)
        self.assertEqual(NonProjectDestination.objects.filter(organization=organization).count(), 2)
        started = onboarding.accounting_started_on(organization)
        self.assertEqual(started, TODAY)
        event = AuditEvent.objects.get(action="organization.initialized")
        self.assertEqual(event.payload["accounting_started_on"], TODAY.isoformat())
        self.assertEqual(event.payload["currency"], "CNY")
        self.assertEqual(event.payload["timezone"], "Asia/Shanghai")

        response = self.client.get(reverse("setup"))
        self.assertRedirects(response, reverse("setup-customer"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("setup-customer")), "首个客户")
        self.assertEqual(self.complete_customer().status_code, 302)
        self.assertContains(self.client.get(reverse("setup-project")), "星河零售")
        self.assertEqual(self.complete_project().status_code, 302)

        project = Project.objects.get()
        self.assertEqual(project.client, "星河零售")
        self.assertEqual(project.manager.user.username, "firstowner")
        self.assertEqual(project.contract_cents, 0)
        self.assertEqual(Document.objects.count(), 0)  # no baseline / fake document
        self.assertEqual(LedgerEntry.objects.count(), 0)

        self.assertContains(self.client.get(reverse("setup-contract")), "合同额")
        self.assertEqual(self.complete_contract().status_code, 302)
        self.assertEqual(Project.objects.get().contract_cents, 0)
        self.assertEqual(contract_amount_cents(Project.objects.get()), 123_456)
        self.assertEqual(Project.objects.get().contract_changes.get().amount_cents, 123_456)

        self.assertContains(self.client.get(reverse("setup-time")), "首个工时")
        self.assertEqual(self.complete_time().status_code, 302)
        project.refresh_from_db()
        entry = Timesheet.objects.get()
        self.assertEqual(entry.minutes, 60)
        self.assertEqual(entry.cost_cents, 30_000)  # ¥300/h × 1h
        self.assertEqual(entry.cost_state, "priced")
        self.assertEqual(CostRate.objects.get().hourly_cents, 30_000)
        self.assertEqual(LedgerEntry.objects.count(), 0)  # simplified time never posts a ledger row
        self.assertEqual(Document.objects.count(), 0)

        page = self.client.get(reverse("setup"))
        self.assertContains(page, "基础启用完成")
        self.assertContains(page, "星野咨询")
        self.assertContains(page, "星河零售")
        self.assertContains(page, "1,234.56")
        self.assertContains(page, TODAY.strftime("%Y-%m-%d"))  # started-on date rendered
        self.assertContains(page, "尚未开票")
        self.assertContains(page, "还没有资金账户")
        state = onboarding.onboarding_state(Membership.objects.get())
        self.assertTrue(state["complete"])
        self.assertEqual(state["organization"].pk, organization.pk)

    def test_steps_redirect_to_company_when_workspace_is_empty(self):
        for name in ("setup-customer", "setup-project", "setup-contract", "setup-time"):
            self.assertRedirects(self.client.get(reverse(name)), reverse("setup"),
                                 fetch_redirect_response=False)

    @override_settings(PMO_SCOPE="micro")
    def test_dashboard_shows_resume_banner_until_complete(self):
        self.complete_company()
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "首次启用还未完成")
        self.assertContains(page, "继续启用")
        self.complete_customer()
        self.complete_project()
        self.complete_contract()
        self.complete_time()
        page = self.client.get(reverse("dashboard"))
        self.assertNotContains(page, "首次启用还未完成")

    def test_readiness_lists_missing_items_and_resume_path(self):
        self.complete_company()
        page = self.client.get(reverse("setup") + "?summary=1")
        self.assertContains(page, "就绪检查")
        self.assertContains(page, "还没有项目与客户")
        self.assertContains(page, "还没有第一个工时")
        self.assertContains(page, "查看项目")
        # The summary is readable before the wizard is complete and points back to the next step.
        self.assertContains(page, "刷新进度")
        self.assertContains(page, "去设置")
        self.complete_customer()
        self.complete_project()
        page = self.client.get(reverse("setup") + "?summary=1")
        self.assertContains(page, "项目尚未录入合同额")
        self.assertContains(page, "尚未设置负责人内部成本费率")
        self.assertContains(page, "还没有资金账户")

    def test_company_page_loads_submit_once_script(self):
        # Q-42 finding: the unauthenticated company form also needs app.js for the
        # data-submit-once busy/disabled guard (auth pages previously loaded no JS).
        response = self.client.get(reverse("setup"))
        self.assertContains(response, "/static/app.js")
        self.assertContains(response, "data-submit-once")

    def test_company_creation_is_rejected_once_workspace_exists(self):
        self.complete_company()
        count = (Organization.objects.count(), get_user_model().objects.count())
        self.client.logout()
        response = self.client.post(reverse("setup"), {
            "company_name": "重复公司", "username": "someone",
            "password": PASSWORD, "password_confirm": PASSWORD})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((Organization.objects.count(), get_user_model().objects.count()), count)
        self.assertRedirects(self.client.get(reverse("setup")), reverse("login"),
                             fetch_redirect_response=False)
        with self.assertRaises(onboarding.SetupClosed):
            onboarding.start_company(name="重复公司", username="someone",
                                     password=PASSWORD, password_confirm=PASSWORD)
        self.assertEqual(Organization.objects.count(), 1)

    def test_resume_after_interruption_and_no_duplicate_facts(self):
        member = self.complete_company()
        # Login state survives a new client; setup resumes at the customer step.
        self.client.logout()
        self.client.force_login(member.user)
        self.assertRedirects(self.client.get(reverse("setup")), reverse("setup-customer"),
                             fetch_redirect_response=False)
        self.complete_customer()
        # Re-posting customer before a project does not duplicate anything.
        self.complete_customer()
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(self.complete_project().status_code, 302)
        # Duplicate submit after the project exists redirects to the next step and never duplicates.
        self.assertRedirects(self.complete_project(), reverse("setup"),
                             fetch_redirect_response=False)
        self.assertEqual(Project.objects.count(), 1)
        self.complete_contract()
        self.complete_time()
        # Re-posting a completed step is a redirect, never a duplicate row.
        self.assertRedirects(self.client.post(reverse("setup-time"), {
            "work_date": TODAY, "minutes": 60, "note": "重复提交", "hourly_rate": ""}),
            reverse("setup"), fetch_redirect_response=False)
        self.assertEqual(Timesheet.objects.count(), 1)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(Project.objects.get().contract_changes.count(), 1)

    def test_owner_work_is_scoped_to_first_workspace_and_other_users_are_denied(self):
        member = self.complete_company()
        # A second user without membership cannot enter the wizard.
        outsider = get_user_model().objects.create_user(username="outsider", password=PASSWORD)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("setup")).status_code, 403)
        # A second organization is rejected by the single-workspace guard.
        other_org = Organization.objects.create(name="第二公司")
        other_user = get_user_model().objects.create_user(username="secondowner", password=PASSWORD)
        Membership.objects.create(organization=other_org, user=other_user, role="owner")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse("setup")).status_code, 403)
        # The first owner is still the only one able to see readiness.
        self.client.force_login(member.user)
        self.assertEqual(self.client.get(reverse("setup")).status_code, 302)

    def test_non_owner_member_of_first_workspace_is_denied(self):
        member = self.complete_company()
        user = get_user_model().objects.create_user(username="pmfirst", password=PASSWORD)
        Membership.objects.create(organization=member.organization, user=user, role="pm")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("setup")).status_code, 403)

    def test_invalid_company_form_writes_nothing_and_keeps_error_state(self):
        response = self.client.post(reverse("setup"), {
            "company_name": "", "username": "x", "password": "short", "password_confirm": "other"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "必填", status_code=400)
        self.assertFalse(Organization.objects.exists())
        self.assertFalse(get_user_model().objects.exists())

    def test_empty_workspace_starts_and_company_defaults_are_not_silently_changed(self):
        self.complete_company()
        organization = Organization.objects.get()
        started = onboarding.accounting_started_on(organization)
        settings_page = self.client.get(reverse("settings"))
        self.assertContains(settings_page, "公司名称")
        # The settings form exposes only name and lock date; the start date has no input.
        self.assertNotContains(settings_page, 'name="accounting_started_on"')
        self.client.post(reverse("settings"), {"name": "改名后的公司", "locked_through": ""})
        organization.refresh_from_db()
        self.assertEqual(organization.name, "改名后的公司")
        self.assertEqual(onboarding.accounting_started_on(organization), started)
        self.assertEqual(organization.currency, "CNY")

    def test_first_run_does_not_create_demo_or_financial_facts(self):
        self.complete_company()
        self.complete_customer()
        self.complete_project()
        self.complete_contract()
        self.complete_time()
        self.assertFalse(Organization.objects.filter(is_demo=True).exists())
        self.assertFalse(Project.objects.exclude(code="P-FIRST").exists())
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Account.objects.count(), 0)
        self.assertEqual(Invoice.objects.count(), 0)
        # No demo seed command was ever invoked; audit only has first-run facts.
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        self.assertNotIn("organization.demo_seeded", actions)
        self.assertNotIn("demo.seeded", actions)
        self.assertIn("organization.initialized", actions)
        self.assertIn("project.created", actions)

    def test_uninvoiced_time_and_contract_are_integer_cents(self):
        self.complete_company()
        self.complete_customer()
        self.complete_project()
        self.client.post(reverse("setup-contract"), {"amount": "0.01"})
        self.assertEqual(Project.objects.get().contract_changes.get().amount_cents, 1)
        self.client.post(reverse("setup-contract"), {"amount": "9999999999.99"})
        self.assertEqual(Project.objects.get().contract_changes.count(), 1)  # completed step redirects


    def test_backdated_first_time_with_supplied_rate_is_priced(self):
        # Q-40 follow-up: project starts yesterday, first time is yesterday, rate ¥300/h.
        yesterday = TODAY - timedelta(days=1)
        self.complete_company()
        self.complete_customer()
        self.assertEqual(self.client.post(reverse("setup-project"), {
            "name": "回填首个项目", "code": "P-BACKDATED", "start_date": yesterday,
            "end_date": TODAY + timedelta(days=30)}).status_code, 302)
        self.assertEqual(self.complete_contract().status_code, 302)
        response = self.client.post(reverse("setup-time"), {
            "work_date": yesterday, "minutes": 60, "note": "回填昨日启动会",
            "hourly_rate": "300.00"})
        self.assertEqual(response.status_code, 302, response.content[:800])
        row = Timesheet.objects.get()
        self.assertEqual(row.work_date, yesterday)
        self.assertEqual(row.hourly_rate_cents, 30_000)
        self.assertEqual(row.cost_cents, 30_000)  # ¥300/h × 1h
        self.assertEqual(row.cost_state, "priced")
        project = Project.objects.get()
        from core.weekly_time import simplified_labor_cost
        self.assertEqual(simplified_labor_cost(project), 30_000)


class PostgresWorkspaceCreationConcurrencyTests(TransactionTestCase):
    """Real independent connections: first-run creation must never create two companies."""

    reset_sequences = True

    def test_two_connections_create_exactly_one_workspace(self):
        from threading import Barrier, Thread
        from django.db import connection, connections, close_old_connections

        if connection.vendor != "postgresql":
            self.skipTest("Requires real PostgreSQL independent connections")
        barrier = Barrier(2)
        results, errors = [], []

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                onboarding.start_company(name="并发启用公司", username="raceowner",
                                         password=PASSWORD, password_confirm=PASSWORD)
                results.append("created")
            except onboarding.SetupClosed:
                results.append("closed")
            except Exception as error:  # pragma: no cover - diagnostic only
                errors.append(repr(error))
            finally:
                connections.close_all()

        threads = [Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(errors, errors)
        self.assertEqual(sorted(results), ["closed", "created"])
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(get_user_model().objects.filter(username="raceowner").count(), 1)
