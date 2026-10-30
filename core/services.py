"""All writes are transactional. Serialize company writes in PostgreSQL, then recheck state."""
import hashlib
import json
from datetime import date, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .access import (require_member, require_write, require_project, require_finance_write,
                     can_manage, WRITE_FINANCE)
from .models import (Organization, Project, ProjectMember, Task, Milestone, CashPlan, Timesheet,
                     Document, LedgerEntry, AuditEvent, CostRate, Risk, Employee, DomainEvent)
from .task_policy import validate_key_task

MAX_MONEY = 10**12  # CNY 10 billion; exact within 64-bit and JSON safe integer ranges.

def cents(value):
    try:
        amount = Decimal(str(value).replace(",", "").strip())
        if not amount.is_finite() or amount < 0 or amount > Decimal(MAX_MONEY) / 100:
            raise ValueError()
        if amount.as_tuple().exponent < -2:
            raise ValueError()
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("金额须为非负数字，最多两位小数，且不超过100亿元")

def time_cost(rate_cents, minutes):
    return int((Decimal(rate_cents) * Decimal(minutes) / 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def lock_company(member):
    require_member(member)
    return Organization.objects.select_for_update().get(pk=member.organization_id)

def check_period(org, occurred_on):
    if org.locked_through and occurred_on <= org.locked_through:
        raise ValidationError(f"{org.locked_through}及之前已锁账，请在开放期间冲销或补记")
    if occurred_on > timezone.localdate():
        raise ValidationError("实际业务日期不能在未来；未来收付款请建立现金计划")

def check_open(project):
    if project.stage in {Project.Stage.CLOSED, Project.Stage.CANCELLED, Project.Stage.ON_HOLD}:
        raise ValidationError("项目已结束或暂停；新增交付工作前请由负责人恢复项目")

def audit_digest(org_id, actor_id, action, entity, entity_id, payload, previous_hash, created_at):
    content = {"organization": org_id, "actor": actor_id, "action": action, "entity": entity,
               "entity_id": str(entity_id), "payload": payload, "previous": previous_hash,
               "created_at": created_at.astimezone(dt_timezone.utc).isoformat()}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()

@transaction.atomic
def audit(member, action, obj, payload=None):
    lock_company(member)
    previous = AuditEvent.objects.filter(organization=member.organization).order_by("-id").first()
    payload = json.loads(json.dumps(payload or {}, default=str))
    created = timezone.now()
    prev_hash = previous.event_hash if previous else ""
    name = obj.__class__.__name__
    event_hash = audit_digest(member.organization_id, member.user_id, action, name, obj.pk, payload, prev_hash, created)
    event = AuditEvent.objects.create(organization=member.organization, actor=member.user, action=action,
        entity=name, entity_id=str(obj.pk), payload=payload, previous_hash=prev_hash, event_hash=event_hash, created_at=created)
    if not action.startswith("plugin."):
        DomainEvent.objects.create(organization=member.organization, source=event, topic=action + ".v1",
                                   data={"entity": name, "entity_id": str(obj.pk), "action": action})
    return event

def verify_audit(org):
    previous = ""
    for event in AuditEvent.objects.filter(organization=org).order_by("id").iterator():
        expected = audit_digest(org.pk, event.actor_id, event.action, event.entity, event.entity_id,
                                event.payload, previous, event.created_at)
        if expected != event.event_hash or event.previous_hash != previous:
            return False
        previous = event.event_hash
    return True

@transaction.atomic
def create_project(member, data):
    lock_company(member)
    require_write(member)
    if member.role not in {"owner", "pm"}:
        raise PermissionDenied("只有公司负责人和项目经理可以立项")
    manager = data["manager"]
    if manager.organization_id != member.organization_id or not manager.active or manager.role not in {"owner", "pm"}:
        raise ValidationError("项目负责人必须是本公司的有效负责人或项目经理")
    if member.role == "pm" and manager != member:
        raise PermissionDenied("项目经理只能为自己负责的项目立项")
    project = Project(organization=member.organization, **data)
    project.full_clean()
    project.save()
    ProjectMember.objects.create(project=project, member=manager)
    audit(member, "project.created", project, {"code": project.code, "template": project.template})
    return project

@transaction.atomic
def submit_time(member, data, token):
    org = lock_company(member)
    if set(data) - {"project", "non_project_destination", "employee", "task", "work_date", "minutes", "note"}:
        raise ValidationError("工时包含非白名单字段")
    # Existing browser, CSV and personal API all enter here.
    from .labor_cutover import monthly_mode
    from .labor_accounting import fresh_labor_member
    from .models import Membership, NonProjectDestination
    member = Membership.objects.select_related("organization", "user").get(pk=member.pk)
    require_write(member)
    work_date = data["work_date"]
    monthly = monthly_mode(org, work_date)
    if monthly:
        member = fresh_labor_member(member)
    project = data.get("project")
    destination = data.get("non_project_destination")
    if bool(project) == bool(destination) or (destination and not monthly):
        raise ValidationError("请选择一个项目或已启用月度口径的非项目去向")
    if project:
        project = Project.objects.select_for_update().get(pk=project.pk)
        require_project(member, project)
        check_open(project)
    if destination:
        destination = NonProjectDestination.objects.get(pk=destination.pk)
        if destination.organization_id != org.pk or not destination.active or destination.code != "ADMIN":
            raise ValidationError("非项目投入只能选择本公司有效的普通非项目去向")
    existing = Timesheet.objects.filter(token=token).first()
    if existing:
        if existing.project_id != (project.pk if project else None) or existing.non_project_destination_id != (destination.pk if destination else None) or existing.submitted_by_id != member.user_id:
            raise ValidationError("提交标识已被使用")
        for field in ["employee", "task", "work_date", "minutes", "note"]:
            if getattr(existing, field) != data.get(field):
                raise ValidationError("同一来源编号的工时内容已改变，请使用更正流程或新的来源编号")
        return existing
    employee = Employee.objects.select_related("member").get(pk=data["employee"].pk)
    if employee.organization_id != org.id or not employee.active:
        raise ValidationError("员工不属于当前公司或已停用")
    if (member.role == "member" or (destination and member.role not in {"owner", "finance"})) and (not employee.member_id or employee.member.user_id != member.user_id):
        raise PermissionDenied("成员只能登记自己的投入；非项目代录由负责人或财务办理")
    if data.get("task") and (not project or data["task"].project_id != project.id):
        raise ValidationError("任务不属于所选项目")
    check_period(org, work_date)
    minutes = data["minutes"]
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValidationError("单次工时须为1至1440整数分钟")
    spent = Timesheet.objects.filter(employee=employee, work_date=work_date,
        status__in=["submitted", "approved"]).aggregate(n=Sum("minutes"))["n"] or 0
    if spent + minutes > 1440:
        raise ValidationError("同一员工跨项目与非项目的当日总工时不能超过24小时")
    values = {**data, "employee": employee, "project": project}
    if monthly:
        values.update(organization=org, non_project_destination=destination, accounting_mode="monthly_pool",
                      cost_state="unpriced", hourly_rate_cents=None, cost_cents=None, rate_basis="月度池待分配")
        row = Timesheet.objects.create(**values, submitted_by=member.user, token=token)
        audit(member, "plugin.costing.time_submitted", row, {"minutes": minutes, "mode": "monthly_pool"})
    else:
        rate = CostRate.objects.filter(employee=employee, effective_from__lte=work_date).first()
        if not rate:
            raise ValidationError("该员工在工时日期没有有效成本费率，请先配置费率")
        row = Timesheet.objects.create(**values, submitted_by=member.user, token=token,
            hourly_rate_cents=rate.hourly_cents, cost_cents=time_cost(rate.hourly_cents, minutes), rate_basis=rate.basis)
        audit(member, "time.submitted", row, {"minutes": minutes, "rate_id": rate.id})
    return row

@transaction.atomic
def submit_document(member, data, token):
    org = lock_company(member)
    require_write(member)
    project = Project.objects.select_for_update().get(pk=data["project"].pk)
    require_project(member, project)
    if data["kind"] not in {"receipt", "payment"}:
        check_open(project)
    elif project.stage == "closed":
        raise ValidationError("归档项目需先恢复后再补记实际收付")
    if member.role == "member" and data["kind"] != "expense":
        raise PermissionDenied("成员可以提交自己的项目费用；其他财务事项由负责人处理")
    if member.role != "member":
        require_finance_write(member, project)
    existing = Document.objects.filter(token=token).first()
    if existing:
        if existing.project_id != project.id or existing.submitted_by_id != member.user_id:
            raise ValidationError("提交标识已被使用")
        for field in ["kind", "title", "amount_cents", "tax_cents", "recoverable_tax", "occurred_on", "counterparty", "reference", "note", "category", "plan"]:
            default = {"tax_cents": 0, "recoverable_tax": False, "counterparty": "", "note": "", "category": "other", "plan": None}.get(field)
            if getattr(existing, field) != data.get(field, default):
                raise ValidationError("同一来源编号的单据内容已改变，请使用更正流程或新的来源编号")
        return existing
    check_period(org, data["occurred_on"])
    kind = data["kind"]
    if kind not in Document.Kind.values:
        raise ValidationError("不支持的单据类型")
    amount = data["amount_cents"]
    if kind != "acceptance" and not 0 < amount <= MAX_MONEY:
        raise ValidationError("金额须大于0且不超过100亿元")
    if not 0 <= data.get("tax_cents", 0) <= amount:
        raise ValidationError("税额不能超过含税金额")
    plan = data.get("plan")
    if plan:
        plan = CashPlan.objects.select_for_update().get(pk=plan.pk)
        data = {**data, "plan": plan, "project": project}
    if plan and (plan.project_id != project.pk or not plan.approved or not plan.active):
        raise ValidationError("只能关联本项目已审批的现金计划")
    expected = {"receipt": "in", "payment": "out"}
    if plan and (kind not in expected or plan.direction != expected[kind]):
        raise ValidationError("计划方向须与收付款方向相符；费用不会核销现金计划")
    if Document.objects.filter(project=project, kind=kind, reference=data["reference"],
                                status__in=["submitted", "approved"]).exists():
        raise ValidationError("本项目已存在同类型、同来源编号的单据；分摊明细请使用不同子编号")
    if kind == "baseline" and project.budget_approved:
        raise ValidationError("预算基线已审批，后续调整请用预算变更")
    row = Document(**data, submitted_by=member.user, token=token)
    row.full_clean()
    row.save()
    audit(member, "document.submitted", row, {"kind": kind, "amount_cents": amount})
    return row


@transaction.atomic
def decide_document(member, *, source_ref, expected_version, decision, note="", request_token,
                    dry_run=True):
    """Named core.document.decide service; the bank_charge handler is the only one wired in PH-250."""
    if not isinstance(source_ref, dict):
        raise ValidationError("审批来源无效")
    if source_ref.get("namespace") != "costing" or source_ref.get("kind") != "bank_charge":
        raise ValidationError("本片仅实现 bank_charge 审批通道；其它单据类型尚未接入 core.document.decide")
    if decision not in {"submit", "approve", "reject", "return"}:
        raise ValidationError("审批结论无效")
    from pmo_plugins.costing.bank_charge_services import decide_bank_charge, submit_bank_charge
    charge_id = source_ref.get("id")
    if decision == "submit":
        return submit_bank_charge(member, charge_id, request_token=request_token,
                                  expected_version=expected_version, dry_run=dry_run)
    return decide_bank_charge(member, charge_id, decision=decision, note=note,
                              request_token=request_token, expected_version=expected_version,
                              dry_run=dry_run)


def plan_remaining(plan):
    expected_book = "cash_in" if plan.direction == "in" else "cash_out"
    settled = LedgerEntry.objects.filter(document__plan=plan, book=expected_book).aggregate(n=Sum("amount_cents"))["n"] or 0
    return plan.amount_cents - settled

@transaction.atomic
def review(member, kind, pk, decision, note=""):
    org = lock_company(member)
    require_write(member)
    model = {"time": Timesheet, "document": Document, "plan": CashPlan}.get(kind)
    if not model or decision not in {"approve", "reject"}:
        raise ValidationError("审批请求无效")
    row = model.objects.select_for_update(of=("self",)).select_related("project").get(pk=pk)
    if kind == "time" and row.accounting_mode == "monthly_pool":
        from .labor_projection import review_monthly_time
        return review_monthly_time(member, row, decision, note)
    require_project(member, row.project)
    if kind == "time":
        if not can_manage(member, row.project) and member.role not in WRITE_FINANCE:
            raise PermissionDenied("没有此项目的工时审批权限")
        employee_user = row.employee.member.user_id if row.employee.member_id else None
        self_approve = row.submitted_by_id == member.user_id or employee_user == member.user_id
    else:
        if member.role not in WRITE_FINANCE:
            raise PermissionDenied("现金计划和财务单据需要项目财务或公司负责人审批")
        self_approve = (row.created_by_id if kind == "plan" else row.submitted_by_id) == member.user_id
    if self_approve:
        raise ValidationError("提交人和业务本人不能审批自己的记录，请由另一位负责人处理")
    if kind == "plan":
        if row.approved:
            return row
        if row.rejected or not row.active:
            raise ValidationError("计划已处理，请刷新查看")
        if decision == "reject" and not note.strip():
            raise ValidationError("退回时必须填写原因")
        row.approved = decision == "approve"
        row.rejected = decision == "reject"
        row.review_note = note[:300]
        row.save(update_fields=["approved", "rejected", "review_note"])
        audit(member, "plan.approved" if row.approved else "plan.rejected", row, {"amount_cents": row.amount_cents, "note": note})
        return row
    if row.status != "submitted":
        if row.status == "approved" and decision == "approve":
            return row
        raise ValidationError("记录已处理，请刷新后查看")
    if kind == "time" or row.kind not in {"receipt", "payment"}:
        check_open(row.project)
    elif row.project.stage == "closed":
        raise ValidationError("归档项目需先恢复后再审批收付")
    check_period(org, row.work_date if kind == "time" else row.occurred_on)
    if decision == "reject" and not note.strip():
        raise ValidationError("退回时必须填写原因")
    row.status = "approved" if decision == "approve" else "rejected"
    row.reviewed_by = member.user
    row.reviewed_at = timezone.now()
    row.review_note = note[:300]
    if decision == "approve":
        if kind == "time":
            LedgerEntry.objects.create(project=row.project, book="cost", amount_cents=row.cost_cents,
                category="labor", occurred_on=row.work_date, timesheet=row, posting_key=f"time:{row.pk}")
        elif row.kind in {"expense", "receipt", "payment"}:
            if row.plan_id and (not row.plan.active or not row.plan.approved or row.amount_cents > plan_remaining(row.plan)):
                raise ValidationError("本次核销超过计划未结金额；请先核对计划或拆分未匹配部分")
            amount = row.amount_cents - row.tax_cents if row.kind == "expense" and row.recoverable_tax else row.amount_cents
            LedgerEntry.objects.create(project=row.project, book={"expense": "cost", "receipt": "cash_in", "payment": "cash_out"}[row.kind],
                amount_cents=amount, category=row.category, occurred_on=row.occurred_on, document=row, posting_key=f"doc:{row.pk}")
        elif row.kind in {"baseline", "change"}:
            project = Project.objects.select_for_update().get(pk=row.project_id)
            if row.kind == "baseline" and project.budget_approved:
                raise ValidationError("预算基线已存在，请通过变更调整")
            if row.kind == "change" and not project.budget_approved:
                raise ValidationError("须先审批预算基线再做变更")
            # Both baseline and change specify the NEW total, never a floating delta.
            project.budget_cents = row.amount_cents
            project.budget_approved = True
            project.version += 1
            project.save(update_fields=["budget_cents", "budget_approved", "version"])
        elif row.kind == "acceptance":
            if not row.project.acceptance_criteria or not row.reference:
                raise ValidationError("请补齐项目验收标准和验收证据")
    row.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    audit(member, f"{kind}.{row.status}", row, {"note": note, "project_id": row.project_id})
    return row

@transaction.atomic
def reverse(member, kind, pk, note):
    org = lock_company(member)
    require_write(member)
    if member.role not in WRITE_FINANCE:
        raise PermissionDenied("冲销需要公司负责人或项目财务")
    if len(note.strip()) < 4:
        raise ValidationError("请填写至少4字的冲销原因")
    model = {"time": Timesheet, "document": Document}.get(kind)
    if not model:
        raise ValidationError("无法冲销此类记录")
    row = model.objects.select_for_update().get(pk=pk)
    if kind == "time" and row.accounting_mode == "monthly_pool":
        from .labor_projection import reverse_monthly_time
        return reverse_monthly_time(member, row, note)
    require_project(member, row.project)
    today = timezone.localdate()
    check_period(org, today)
    if row.status == "reversed":
        return row
    if row.status != "approved":
        raise ValidationError("只能冲销已入账记录")
    entries = row.project.entries.filter(**{f"{'timesheet' if kind == 'time' else 'document'}_id": row.pk}, reverses__isnull=True)
    if not entries.exists():
        raise ValidationError("该记录不生成账目，预算请走新变更、验收请走恢复流程")
    for original in entries:
        LedgerEntry.objects.create(project=row.project, book=original.book, amount_cents=-original.amount_cents,
            category=original.category, occurred_on=today, document=original.document, timesheet=original.timesheet,
            reverses=original, posting_key=f"reverse:{original.pk}")
    row.status = "reversed"
    row.save(update_fields=["status"])
    audit(member, f"{kind}.reversed", row, {"reason": note, "reversal_date": today})
    return row

@transaction.atomic
def create_task(member, data):
    lock_company(member)
    project = Project.objects.select_for_update().get(pk=data["project"].pk)
    data = {**data, "project": project}
    require_write(member)
    require_project(member, project, manage=True)
    check_open(project)
    milestone, cash_plan = validate_key_task(project, data)
    data = {**data, "milestone": milestone, "cash_plan": cash_plan}
    if data["owner"].organization_id != project.organization_id or not data["owner"].active:
        raise ValidationError("任务负责人须为本公司有效成员")
    if not (project.manager_id == data["owner"].id or ProjectMember.objects.filter(project=project, member=data["owner"]).exists()):
        raise ValidationError("请先将任务负责人加入项目团队")
    if data.get("dependency") and data["dependency"].project_id != project.id:
        raise ValidationError("前置任务须属于同一项目")
    task = Task(**data)
    task.full_clean()
    task.save()
    audit(member, "task.created", task, {"project_id": project.id})
    return task

@transaction.atomic
def update_task(member, pk, status, version, completion_note=""):
    lock_company(member)
    # Company writes are already serialized above. Avoid locking the nullable
    # dependency join, which PostgreSQL rejects, while retaining the task lock.
    task = Task.objects.select_for_update(of=("self",)).select_related("project", "dependency").get(pk=pk)
    require_write(member)
    require_project(member, task.project)
    check_open(task.project)
    if not can_manage(member, task.project) and task.owner_id != member.pk:
        raise PermissionDenied("只能更新自己负责的任务")
    if status not in Task.Status.values:
        raise ValidationError("任务状态无效")
    if task.version != version:
        raise ValidationError("任务已被其他人更新，请刷新后重试")
    if status in {"doing", "done"} and task.dependency and task.dependency.status != "done":
        raise ValidationError("前置任务尚未完成")
    if status == "done" and not completion_note.strip():
        raise ValidationError("完成任务时请填写交付说明或证据编号")
    if task.status == "done" and status != "done" and Task.objects.filter(dependency=task, status__in=["doing", "done"]).exists():
        raise ValidationError("后续任务已经开始，请先调整后续任务再恢复前置任务")
    before = task.status
    task.status, task.completion_note = status, completion_note[:2000]
    task.version += 1
    task.save(update_fields=["status", "completion_note", "version"])
    audit(member, "task.status_changed", task, {"from": before, "to": status, "evidence": completion_note})
    return task

TRANSITIONS = {
    "initiation": {"planning", "cancelled", "on_hold"},
    "planning": {"execution", "cancelled", "on_hold"},
    "execution": {"acceptance", "cancelled", "on_hold"},
    "acceptance": {"execution", "warranty", "on_hold"},
    "warranty": {"closed", "execution", "on_hold"},
    "closed": {"warranty"}, "cancelled": {"initiation"}, "on_hold": set(),
}

@transaction.atomic
def transition(member, pk, target, version, reason):
    lock_company(member)
    project = Project.objects.select_for_update().get(pk=pk)
    require_write(member)
    require_project(member, project, manage=True)
    if project.version != version:
        raise ValidationError("项目已发生变化，请刷新后重试")
    allowed = TRANSITIONS[project.stage] if project.stage != "on_hold" else {project.previous_stage}
    if target not in allowed:
        raise ValidationError("该阶段不能直接跳转到目标阶段")
    if len(reason.strip()) < 4:
        raise ValidationError("请写明至少4字的阶段变更依据")
    if target == "cancelled" and member.role != "owner":
        raise PermissionDenied("取消项目须由公司负责人确认")
    if project.stage in {"closed", "cancelled"} and member.role != "owner":
        raise PermissionDenied("恢复已结束项目需要公司负责人")
    if target == "planning" and (not project.scope or not project.acceptance_criteria):
        raise ValidationError("进入规划前须填写范围、交付物和验收标准")
    if target == "execution":
        if not project.budget_approved or not project.contract_reference:
            raise ValidationError("启动执行前须审批预算基线并填写合同或立项依据")
        if not project.tasks.exists() or not project.milestones.exists():
            raise ValidationError("启动执行前须至少配置一项任务和一个里程碑")
    if target == "acceptance" and project.tasks.exclude(status="done").exists():
        raise ValidationError("还有未完成任务，不能进入验收阶段")
    if target == "warranty" and not project.documents.filter(kind="acceptance", status="approved").exists():
        raise ValidationError("进入维保前需要独立审批通过的项目验收记录")
    if target == "closed":
        if project.etc_cents:
            raise ValidationError("还有预计剩余成本，请先核对并将结项剩余成本更新为0")
        if project.cash_plans.filter(approved=False, rejected=False, active=True).exists():
            raise ValidationError("还有待确认的现金计划，不能归档")
        if project.milestones.filter(accepted_at__isnull=True).exists():
            raise ValidationError("还有未验收里程碑，不能归档")
        if project.risks.filter(resolved=False, severity="high").exists():
            raise ValidationError("还有未关闭的高风险，不能归档")
        if project.timesheets.filter(status="submitted").exists() or project.documents.filter(status="submitted").exists():
            raise ValidationError("还有待审批记录，不能归档")
        if any(plan_remaining(p) > 0 for p in project.cash_plans.filter(approved=True, active=True)):
            raise ValidationError("现金计划尚未结清，不能归档；请先核对回款和应付事项")
    before = project.stage
    if target == "on_hold": project.previous_stage = before
    project.stage = target
    project.version += 1
    project.save(update_fields=["stage", "version", "previous_stage"])
    audit(member, "project.stage_changed", project, {"from": before, "to": target, "reason": reason})
    return project

@transaction.atomic
def accept_milestone(member, pk, evidence):
    lock_company(member)
    milestone = Milestone.objects.select_for_update().get(pk=pk)
    require_write(member)
    require_project(member, milestone.project, manage=True)
    check_open(milestone.project)
    if len(evidence.strip()) < 4:
        raise ValidationError("请填写实际验收证据编号或文件位置")
    if milestone.accepted_at:
        return milestone
    milestone.evidence = evidence[:240]
    milestone.accepted_at, milestone.accepted_by = timezone.now(), member.user
    milestone.save(update_fields=["evidence", "accepted_at", "accepted_by"])
    audit(member, "milestone.accepted", milestone, {"evidence": evidence})
    return milestone

@transaction.atomic
def edit_task(member, pk, data, version, reason):
    lock_company(member)
    task = Task.objects.select_for_update().select_related("project").get(pk=pk)
    require_write(member)
    require_project(member, task.project, manage=True)
    check_open(task.project)
    if task.version != version: raise ValidationError("任务已经被更新，请刷新后重试")
    if data["project"].pk != task.project_id: raise ValidationError("已建任务不能移到其他项目")
    # Omitted link keys keep the current links; an explicit None still clears a
    # link, and the resulting plan must retain at least one link.
    data = {**data}
    data.setdefault("milestone", task.milestone)
    data.setdefault("cash_plan", task.cash_plan)
    milestone, cash_plan = validate_key_task(task.project, data)
    data.update(milestone=milestone, cash_plan=cash_plan)
    if data["owner"].organization_id != member.organization_id or not data["owner"].active: raise ValidationError("任务负责人无效")
    if data["owner"].pk != task.project.manager_id and not ProjectMember.objects.filter(project=task.project, member=data["owner"]).exists():
        raise ValidationError("请先将任务负责人加入项目团队")
    dependency = data.get("dependency")
    seen = {task.pk}
    while dependency:
        if dependency.pk in seen or dependency.project_id != task.project_id:
            raise ValidationError("任务依赖不能跨项目或形成循环")
        seen.add(dependency.pk)
        dependency = dependency.dependency
    if data.get("dependency") and task.status in {"doing", "done"} and data["dependency"].status != "done":
        raise ValidationError("正在执行或已完成的任务不能依赖未完成事项")
    before = {key: str(getattr(task, key)) for key in data}
    for key, value in data.items(): setattr(task, key, value)
    task.version += 1
    task.full_clean(); task.save()
    audit(member, "task.plan_changed", task, {"before": before, "reason": reason})
    return task
