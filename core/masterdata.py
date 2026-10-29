"""基础资料唯一写入入口：员工档案、成本费率与项目基础资料。

All masterdata edits go through these service functions so company locking, role/tenant
checks, optimistic versioning and audit happen in exactly one place; forms and views stay thin.
Only whitelisted keys are accepted — a stray dict can never touch organization/code/stage/budget.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from .access import require_project, require_write
from .models import CostRate, Employee, Membership, Project
from .services import audit, cents, check_open, lock_company

EMPLOYEE_FIELDS = {"name", "title", "capacity_hours", "active", "member"}
PROJECT_FIELDS = {"name", "client", "manager", "start_date", "end_date",
                  "scope", "acceptance_criteria", "contract_reference", "etc_amount"}
RATE_FIELDS = {"employee", "effective_from", "hourly_amount", "basis"}


def ensure_labor_destinations(member):
    """Called under the cutover transaction; genuine non-project master data."""
    from .models import NonProjectDestination
    result = {}
    for code, name in [('ADMIN', '公司非项目投入'), ('UNALLOCATED', '人工成本待分配')]:
        row, created = NonProjectDestination.objects.get_or_create(
            organization_id=member.organization_id, code=code, defaults={'name': name})
        if created:
            audit(member, 'plugin.costing.destination_created', row, {'code': code})
        result[code] = row
    return result


def _reject_unknown(data, allowed):
    extra = set(data) - allowed
    if extra:
        raise ValidationError("基础资料不允许维护这些字段：" + "、".join(sorted(extra)))


def _amount(value):
    if isinstance(value, (float, bool)):
        raise ValidationError("金额须使用十进制数，不能使用浮点数或布尔值")
    return cents(value)


def _writer(member, what):
    """员工与费率只有公司负责人、财务可以写；项目经理和普通成员一律拒绝。"""
    require_write(member)
    if member.role not in {"owner", "finance"}:
        raise PermissionDenied("只有公司负责人或财务可以维护" + what)


def _minutes(value):
    """capacity_hours 以 Decimal 传入；换算成整数分钟并限制在 1..1440。"""
    try:
        hours = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("容量须为数字（小时）")
    if not hours.is_finite():
        raise ValidationError("容量须为有效数字，不能是无穷或非数")
    minutes = hours * 60
    if minutes != minutes.to_integral_value():
        raise ValidationError("容量按整数分钟维护，请换算成整分钟")
    minutes = int(minutes)
    if not 1 <= minutes <= 1440:
        raise ValidationError("每日容量须在1到1440分钟之间")
    return minutes


def _linkable_account(org, link, exclude=None):
    """关联账户必须同公司、在职且未被其他员工档案占用（重复关联拒绝）。"""
    if not isinstance(link, Membership) or link.organization_id != org.pk:
        raise ValidationError("关联账户必须是本公司成员")
    link = Membership.objects.select_related("user").filter(pk=link.pk).first()
    if link is None:
        raise ValidationError("关联账户必须是本公司成员")
    if link.organization_id != org.pk:
        raise ValidationError("关联账户必须是本公司成员")
    if not link.active or not link.user.is_active:
        raise ValidationError("关联账户已停用，请先恢复账户再关联员工档案")
    taken = Employee.objects.filter(member=link)
    if exclude is not None:
        taken = taken.exclude(pk=exclude)
    if taken.exists():
        raise ValidationError("该账户已关联其他员工档案，不能重复关联")
    return link


def _employee_snapshot(employee):
    return {"name": employee.name, "title": employee.title,
            "capacity_minutes": employee.capacity_minutes, "active": employee.active,
            "member": employee.member_id}


@transaction.atomic
def create_employee(member, data):
    lock_company(member)
    _writer(member, "员工资料")
    _reject_unknown(data, EMPLOYEE_FIELDS)
    fields = {"name": data["name"], "title": data.get("title", ""),
              "capacity_minutes": _minutes(data["capacity_hours"]),
              "active": bool(data.get("active", True))}
    if data.get("member") is not None:
        fields["member"] = _linkable_account(member.organization, data["member"])
    employee = Employee(organization=member.organization, **fields)
    employee.full_clean()
    employee.save()
    audit(member, "employee.created", employee, _employee_snapshot(employee))
    return employee


@transaction.atomic
def update_employee(member, pk, data):
    lock_company(member)
    _writer(member, "员工资料")
    _reject_unknown(data, EMPLOYEE_FIELDS)
    try:
        employee = Employee.objects.select_for_update().get(pk=pk, organization=member.organization)
    except Employee.DoesNotExist:
        raise PermissionDenied("无权访问此员工资料")
    before = _employee_snapshot(employee)
    if "name" in data:
        employee.name = data["name"]
    if "title" in data:
        employee.title = data["title"]
    if "capacity_hours" in data:
        employee.capacity_minutes = _minutes(data["capacity_hours"])
    if "active" in data:
        employee.active = bool(data["active"])
    if "member" in data:
        employee.member = None if data["member"] is None else \
            _linkable_account(member.organization, data["member"], exclude=employee.pk)
    employee.full_clean()
    employee.save()
    audit(member, "employee.updated", employee, {"before": before, "after": _employee_snapshot(employee)})
    return employee


@transaction.atomic
def create_rate(member, data):
    """费率只追加：同人同日拒绝覆盖，历史工时快照不改写。"""
    lock_company(member)
    _writer(member, "成本费率")
    _reject_unknown(data, RATE_FIELDS)
    employee = data["employee"]
    if not isinstance(employee, Employee) or employee.organization_id != member.organization_id:
        raise ValidationError("费率必须是本公司员工档案")
    employee = Employee.objects.select_for_update().filter(pk=employee.pk).first()
    if employee is None:
        raise ValidationError("费率必须是本公司员工档案")
    if employee.organization_id != member.organization_id:
        raise ValidationError("费率必须是本公司员工档案")
    if not employee.active:
        raise ValidationError("员工已停用，不能新增费率；已存在的费率快照保持不变")
    rate = CostRate(employee=employee, effective_from=data["effective_from"],
                    hourly_cents=_amount(data["hourly_amount"]), basis=data["basis"])
    if CostRate.objects.filter(employee=employee, effective_from=rate.effective_from).exists():
        raise ValidationError("该员工在此生效日已有费率；费率只追加不覆盖，请新增更晚生效的一条")
    try:
        rate.full_clean()
        rate.save()
    except IntegrityError:
        raise ValidationError("该员工在此生效日已有费率；费率只追加不覆盖")
    audit(member, "rate.created", rate, {"employee": employee.pk, "employee_name": employee.name,
                                         "effective_from": str(rate.effective_from),
                                         "hourly_cents": rate.hourly_cents, "basis": rate.basis})
    return rate


def _manager_or_denied(member, value):
    if not isinstance(value, Membership) or value.organization_id != member.organization_id or not value.active:
        raise ValidationError("项目负责人必须是本公司的在职成员")
    value = Membership.objects.select_related("user").filter(pk=value.pk).first()
    if value is None:
        raise ValidationError("项目负责人必须是本公司的在职成员")
    if value.organization_id != member.organization_id or not value.active or not value.user.is_active:
        raise ValidationError("项目负责人必须是本公司的在职成员")
    if value.role not in {"owner", "pm"}:
        raise ValidationError("项目负责人必须是本公司负责人或项目经理")
    if member.role == "pm" and value.pk != member.pk:
        raise PermissionDenied("项目经理只能维护自己负责的项目，不能转交给其他人")
    return value


def _project_value(project, key):
    raw = project.manager_id if key == "manager" else project.etc_cents if key == "etc_amount" \
        else getattr(project, key)
    return str(raw)


@transaction.atomic
def update_project(member, pk, data, version, reason):
    """项目基础资料维护：version 乐观锁 + reason 必填，code/预算/阶段不在白名单。"""
    lock_company(member)
    require_write(member)
    _reject_unknown(data, PROJECT_FIELDS)
    if not isinstance(reason, str) or len(reason.strip()) < 4:
        raise ValidationError("请填写本次基础资料变更的原因")
    try:
        if type(version) is not int:
            raise ValueError()
        expected = version
    except (TypeError, ValueError):
        raise ValidationError("版本号无效，请刷新后重试")
    try:
        project = Project.objects.select_for_update().get(pk=pk, organization=member.organization)
    except Project.DoesNotExist:
        raise PermissionDenied("无权访问此项目")
    require_project(member, project, manage=True)
    check_open(project)
    if expected != project.version:
        raise ValidationError("项目基础资料已被他人更新，请刷新后重试")
    before = {key: _project_value(project, key) for key in data}
    for key, value in data.items():
        if key == "etc_amount":
            project.etc_cents = _amount(value)
        elif key == "manager":
            project.manager = _manager_or_denied(member, value)
        else:
            setattr(project, key, value)
    project.version += 1
    project.full_clean()
    project.save()
    after = {key: _project_value(project, key) for key in data}
    audit(member, "project.masterdata_changed", project,
          {"before": before, "after": after, "reason": str(reason).strip()})
    return project
