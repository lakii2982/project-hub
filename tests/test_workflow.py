import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from core.models import Organization, Membership, Employee, CostRate, Project, Document, Timesheet, CashPlan, LedgerEntry
from core.services import verify_audit
from core.reporting import cash_forecast, overdue_plan_totals

class BrowserWorkflowTests(TestCase):
    def test_project_from_creation_to_archive_uses_real_forms_and_separate_review(self):
        today = timezone.localdate()
        org = Organization.objects.create(name="流程验收公司")
        people = {}
        for username, role in [("workflow-owner", "owner"), ("workflow-pm", "pm")]:
            user = get_user_model().objects.create_user(username, password="Workflow-Only-9182")
            member = Membership.objects.create(organization=org, user=user, role=role)
            employee = Employee.objects.create(organization=org, member=member, name=username)
            CostRate.objects.create(employee=employee, effective_from=today - timedelta(days=1), hourly_cents=12000, basis="已确认综合成本")
            client = Client(); client.force_login(user)
            people[role] = (member, employee, client)
        pm, employee, browser = people["pm"]
        owner, unused, reviewer = people["owner"]
        def create(kind, values, project=None):
            url = f"/new/{kind}/" + (f"?project={project.pk}" if project else "")
            page = browser.get(url)
            self.assertEqual(page.status_code, 200)
            values = {**values, "submission_token": str(page.context["form"]["submission_token"].value())}
            response = browser.post(url, values)
            self.assertEqual(response.status_code, 302, response.content.decode()[:1000])
        create("project", {"code": "FLOW-001", "name": "从立项到归档", "client": "测试客户", "manager": pm.pk,
            "template": "implementation", "start_date": str(today), "end_date": str(today + timedelta(days=30)),
            "contract_amount": "10000", "initial_budget": "5000", "etc_amount": "4000", "scope": "交付系统配置和培训",
            "acceptance_criteria": "关键流程通过并确认交付", "contract_reference": "HT-FLOW-001"})
        project = Project.objects.get(code="FLOW-001")
        self.assertEqual(project.tasks.count(), 3)
        self.assertFalse(project.tasks.filter(milestone__isnull=True, cash_plan__isnull=True).exists())
        self.assertEqual(project.milestones.count(), 3)
        self.assertFalse(project.budget_approved)
        baseline = project.documents.get(kind="baseline")
        response = browser.post(f"/review/document/{baseline.pk}/", {"decision": "approve"})
        self.assertEqual(response.status_code, 403)  # PM has no financial approval scope.
        baseline.refresh_from_db(); self.assertEqual(baseline.status, "submitted")
        reviewer.post(f"/review/document/{baseline.pk}/", {"decision": "approve", "note": "预算与合同已确认"})
        project.refresh_from_db(); self.assertTrue(project.budget_approved)
        for target in ["planning", "execution"]:
            browser.post(f"/projects/{project.pk}/transition/", {"target": target, "version": project.version, "reason": "阶段条件已核对"})
            project.refresh_from_db(); self.assertEqual(project.stage, target)
        create("time", {"project": project.pk, "employee": employee.pk, "work_date": str(today), "hours": "2", "note": "系统联调与验证"}, project)
        time = project.timesheets.get()
        reviewer.post(f"/review/time/{time.pk}/", {"decision": "approve", "note": "实际工作已确认"})
        self.assertEqual(project.entries.get(timesheet=time).amount_cents, 24000)
        for direction, amount, title in [("in", "10000", "合同回款"), ("out", "1000", "合作方付款")]:
            create("plan", {"project": project.pk, "direction": direction, "title": title, "counterparty": "合同对方",
                "amount": amount, "due_date": str(today), "reference": "PLAN-" + direction}, project)
            plan = project.cash_plans.get(direction=direction)
            reviewer.post(f"/review/plan/{plan.pk}/", {"decision": "approve", "note": "付款条件已确认"})
            create("document", {"project": project.pk, "kind": "receipt" if direction == "in" else "payment", "title": title,
                "category": "other", "amount": amount, "tax": "0", "occurred_on": str(today), "counterparty": "合同对方",
                "reference": "BANK-" + direction, "plan": plan.pk, "note": "按实际银行凭证登记"}, project)
            document = project.documents.get(reference="BANK-" + direction)
            reviewer.post(f"/review/document/{document.pk}/", {"decision": "approve", "note": "流水核对一致"})
        self.assertEqual(project.entries.filter(book="cost").count(), 1)  # Payment did not create a second cost.
        for task in project.tasks.order_by("id"):
            browser.post(f"/tasks/{task.pk}/status/", {"status": "done", "version": task.version, "completion_note": "DEL-" + str(task.pk)})
            task.refresh_from_db(); self.assertEqual(task.status, "done")
        for milestone in project.milestones.all():
            browser.post(f"/milestones/{milestone.pk}/accept/", {"evidence": "客户确认单-" + str(milestone.pk)})
        browser.post(f"/projects/{project.pk}/transition/", {"target": "acceptance", "version": project.version, "reason": "交付任务已全部完成"})
        project.refresh_from_db(); self.assertEqual(project.stage, "acceptance")
        create("document", {"project": project.pk, "kind": "acceptance", "title": "最终验收", "category": "other", "amount": "0",
            "tax": "0", "occurred_on": str(today), "reference": "FINAL-YS-001", "note": "客户验收已完成"}, project)
        acceptance = project.documents.get(kind="acceptance")
        reviewer.post(f"/review/document/{acceptance.pk}/", {"decision": "approve", "note": "核对客户验收记录"})
        browser.post(f"/projects/{project.pk}/transition/", {"target": "warranty", "version": project.version, "reason": "转入交付支持期间"})
        project.refresh_from_db(); self.assertEqual(project.stage, "warranty")
        page = browser.get(f"/projects/{project.pk}/edit/")
        values = {"version": project.version, "scope": project.scope, "acceptance_criteria": project.acceptance_criteria,
                  "end_date": str(project.end_date), "contract_reference": project.contract_reference, "etc_amount": "0", "reason": "全部支持工作已完成"}
        self.assertEqual(browser.post(f"/projects/{project.pk}/edit/", values).status_code, 302)
        project.refresh_from_db()
        browser.post(f"/projects/{project.pk}/transition/", {"target": "closed", "version": project.version, "reason": "成本与收付款已核对结清"})
        project.refresh_from_db(); self.assertEqual(project.stage, "closed")
        self.assertTrue(verify_audit(org))

    def test_overdue_plans_are_separate_from_future_forecast_and_settlements(self):
        today = timezone.localdate()
        user = get_user_model().objects.create_user("forecast-owner")
        org = Organization.objects.create(name="预测公司")
        owner = Membership.objects.create(organization=org, user=user, role="owner")
        project = Project.objects.create(organization=org, manager=owner, code="FC-001", name="预测", client="客户", start_date=today, end_date=today)
        CashPlan.objects.create(project=project, direction="in", title="已逾期", counterparty="客户", amount_cents=10000,
                                due_date=today - timedelta(days=1), reference="old", created_by=user, approved=True)
        CashPlan.objects.create(project=project, direction="in", title="约定未来", counterparty="客户", amount_cents=20000,
                                due_date=today + timedelta(days=8), reference="future", created_by=user, approved=True)
        forecast = cash_forecast([project])
        self.assertEqual(forecast[0]["in_cents"], 0)
        self.assertEqual(forecast[1]["in_cents"], 20000)
        self.assertEqual(overdue_plan_totals([project]), {"count": 1, "in_cents": 10000, "out_cents": 0})
