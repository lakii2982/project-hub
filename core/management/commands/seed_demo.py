import os
import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from core.models import Organization, Membership, Employee, CostRate, CashPlan, Milestone, Risk
from core.services import (create_project, create_task, submit_document, submit_time, review,
                           transition, update_task, accept_milestone, audit)

class Command(BaseCommand):
    help = "只在开发环境建立独立演练公司；重复运行保留数据，不重置密码"
    @transaction.atomic
    def handle(self, *args, **options):
        if settings.PRODUCTION: raise CommandError("生产环境禁止演练数据初始化")
        if Organization.objects.filter(is_demo=True).exists():
            self.stdout.write("演练数据已存在，未重置。凭据见 runtime/demo-credentials.txt")
            return
        if Organization.objects.exists(): raise CommandError("当前数据库已有真实公司，禁止混入演练数据")
        today = timezone.localdate()
        org = Organization.objects.create(name="行远技术 · 演练工作区", is_demo=True)
        password = secrets.token_urlsafe(18)
        members = {}
        for username, name, role in [("owner", "陈予安", "owner"), ("finance", "周宁", "finance"),
                                      ("pm", "林知行", "pm"), ("member", "许嘉", "member"), ("auditor", "审阅账户", "auditor")]:
            user = get_user_model().objects.create_user(username=username, first_name=name, password=password)
            member = Membership.objects.create(organization=org, user=user, role=role)
            members[role] = member
            employee = Employee.objects.create(organization=org, name=name, title={"owner": "负责人", "finance": "项目财务", "pm": "交付经理", "member": "软件工程师", "auditor": "审阅"}[role], member=member)
            CostRate.objects.create(employee=employee, effective_from=today - timedelta(days=365),
                                   hourly_cents={"owner": 20000, "finance": 10000, "pm": 16000, "member": 12000, "auditor": 0}[role], basis="演练：包含工资、公司承担福利的标准综合费率")
        owner, pm, finance, member = (members[k] for k in ["owner", "pm", "finance", "member"])
        audit(owner, "organization.initialized", org, {"mode": "demo"})
        specs = [
            ("PH-26001", "云桥 CRM 二期开发", "云桥科技", "software", 36000000, 22000000, 13000000, 3),
            ("PH-26002", "远山制造 ERP 实施", "远山制造", "implementation", 24000000, 15000000, 15800000, 2),
            ("PH-26003", "蓝湾运维年度服务", "蓝湾零售", "service", 18000000, 9000000, 3000000, 5),
            ("PH-26004", "星河数据平台建设", "星河数据", "software", 52000000, 32000000, 30000000, 0),
        ]
        from core.models import ProjectMember
        for index, (code, name, client, template, contract, budget, etc, done_count) in enumerate(specs):
            project = create_project(pm, {"code": code, "name": name, "client": client, "manager": pm, "template": template,
                "start_date": today - timedelta(days=60), "end_date": today + timedelta(days=45 + index * 12),
                "contract_cents": contract, "etc_cents": etc, "scope": "业务梳理、系统交付、用户培训与上线支持；范围以合同附件为准。",
                "acceptance_criteria": "完成约定交付物，关键流程测试通过，取得客户验收确认。", "contract_reference": f"HT-2026-{index+1:03d}"})
            ProjectMember.objects.get_or_create(project=project, member=member)
            baseline = submit_document(pm, {"project": project, "kind": "baseline", "title": "初始预算基线", "amount_cents": budget,
                "occurred_on": today - timedelta(days=29), "reference": f"{code}-B01"}, uuid.uuid4())
            if index < 3: review(owner, "document", baseline.pk, "approve", "确认预算与范围")
            milestones = []
            for pos, title in enumerate(["需求基线确认", "版本交付", "最终验收"]):
                milestone = Milestone.objects.create(project=project, name=title,
                    due_date=today + timedelta(days=(pos - 1) * 21), criteria="交付内容及测试结果经项目负责人确认")
                audit(pm, "milestone.created", milestone, {"project_id": project.pk})
                milestones.append(milestone)
            tasks = []
            for pos, title in enumerate(["范围基线确认", "版本交付验收", "最终验收与交接"]):
                task = create_task(pm, {"project": project, "name": title, "owner": member if pos in {2, 3} else pm,
                    "start_date": today - timedelta(days=60), "due_date": today + timedelta(days=(pos - done_count) * 7 + 4),
                    "description": "记录交付成果并完成阶段确认。", "milestone": milestones[pos]})
                # Dates are clamped because late-start examples still need coherent task intervals.
                tasks.append(task)
                if pos < done_count: update_task(pm, task.pk, "done", task.version, f"{code}-DEL-{pos+1:02d} 客户交付记录")
            for pos, milestone in enumerate(milestones):
                if pos == 0 or index == 2: accept_milestone(pm, milestone.pk, f"{code}-YS-{pos+1:02d} 客户确认单")
            project.refresh_from_db()
            transition(pm, project.pk, "planning", project.version, "立项范围与验收条件已确认")
            project.refresh_from_db()
            if index < 3:
                transition(pm, project.pk, "execution", project.version, "预算合同与交付计划已经准备就绪")
                project.refresh_from_db()
                for d in range(12):
                    row = submit_time(member, {"project": project, "employee": member.employee,
                        "work_date": today - timedelta(days=20 - d), "minutes": 120 + (index % 2) * 30,
                        "note": f"{name}：功能配置、实现与验证", "task": None}, uuid.uuid4())
                    review(pm, "time", row.pk, "approve", "工作内容已确认")
                for pos, amount in enumerate([1260000 + index * 200000, 380000 + index * 100000]):
                    expense = submit_document(pm, {"project": project, "kind": "expense", "title": ["云资源与软件服务", "现场实施差旅"][pos],
                        "amount_cents": amount, "tax_cents": 0, "occurred_on": today - timedelta(days=10 - pos),
                        "reference": f"{code}-FY-{pos}", "category": ["software", "travel"][pos], "counterparty": "演练供应商"}, uuid.uuid4())
                    review(finance, "document", expense.pk, "approve", "费用凭证与归属已核对")
            for direction, amount, days, title in [("in", contract // 3, -12, "首付款"), ("in", contract // 3, 20, "阶段回款"),
                                                  ("in", contract - 2 * (contract // 3), 55, "验收尾款"), ("out", budget // 5, 15, "外包与人工付款")]:
                plan = CashPlan.objects.create(project=project, direction=direction, title=title, counterparty=client if direction == "in" else "项目合作方",
                    amount_cents=amount, due_date=today + timedelta(days=days), reference=f"{code}-{title}", created_by=pm.user)
                audit(pm, "plan.created", plan, {"amount_cents": amount})
                if index < 3:
                    review(finance, "plan", plan.pk, "approve", "合同付款节点已核对")
                    if title == "首付款" and index != 1:
                        received = submit_document(finance, {"project": project, "kind": "receipt", "title": "已收首付款", "amount_cents": amount,
                            "occurred_on": today - timedelta(days=9), "reference": f"{code}-BANK-IN-01", "counterparty": client, "plan": plan, "category": "other"}, uuid.uuid4())
                        review(owner, "document", received.pk, "approve", "银行流水已核对")
                    if direction == "out":
                        paid = submit_document(finance, {"project": project, "kind": "payment", "title": "阶段已付金额", "amount_cents": amount // 2,
                            "occurred_on": today - timedelta(days=3), "reference": f"{code}-BANK-OUT-01", "plan": plan, "category": "vendor", "counterparty": "项目合作方"}, uuid.uuid4())
                        review(owner, "document", paid.pk, "approve", "实际付款流水已核对")
            if index == 1:
                Risk.objects.create(project=project, title="历史数据质量影响上线计划", severity="high", owner=pm,
                    due_date=today + timedelta(days=3), mitigation="与客户确认数据修复清单与责任分工，重新估算剩余投入。")
            if index == 2:
                transition(pm, project.pk, "acceptance", project.version, "全部交付任务已完成，发起项目验收")
                acceptance = submit_document(pm, {"project": project, "kind": "acceptance", "title": "服务交付验收", "amount_cents": 0,
                    "occurred_on": today - timedelta(days=1), "reference": f"{code}-FINAL-YS"}, uuid.uuid4())
                review(owner, "document", acceptance.pk, "approve", "客户确认记录已核对")
                project.refresh_from_db()
                transition(pm, project.pk, "warranty", project.version, "客户验收完成，进入约定支持期")
        pending = submit_time(member, {"project": tasks[0].project, "employee": member.employee,
            "work_date": today, "minutes": 120, "note": "方案准备和技术预研", "task": None}, uuid.uuid4())
        credentials = settings.BASE_DIR / "runtime/demo-credentials.txt"
        credentials.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        credentials.write_text("项目中枢本地演练账户（只用于演练数据库）\n地址：http://127.0.0.1:8765/\n" +
            "\n".join(f"{role}: {m.user.username}" for role, m in members.items()) + f"\n演练密码：{password}\n")
        os.chmod(credentials, 0o600)
        self.stdout.write(self.style.SUCCESS("演练数据已建立。5个角色的本地登录信息保存到 runtime/demo-credentials.txt（仅当前用户可读）。"))
