"""Q-40 first-run onboarding: from an empty database to a usable 1.0 workspace.

The wizard is deliberately **fact-driven and stateless**: every completed step is read
back from real business rows (Organization/Membership/Project/contract change/Timesheet),
so an interrupted setup resumes at the first missing fact instead of relying on a separate
progress table.  It never seeds demo data, never posts ledger entries and never rewrites
history; the company/owner creation is the only write that can happen before login.

The accounting start date is the date the company was confirmed; it is recorded in the
``organization.initialized`` audit payload and is never editable afterwards (the settings
form does not expose it).  Currency (CNY) and timezone (Asia/Shanghai) are the 1.0 fixed
defaults.
"""
import re
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .access import require_member, require_write
from .contract_changes import contract_amount_cents
from .masterdata import create_rate, ensure_labor_destinations
from .models import AuditEvent, CostRate, Employee, Membership, Organization, Project, Timesheet
from .services import audit
from .weekly_time import save_week, week_start_for

CURRENCY = "CNY"
TIMEZONE = "Asia/Shanghai"
PASSWORD_MIN = 12
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
COMPANY_MAX = 100
CUSTOMER_MAX = 120
PROJECT_CODE_MAX = 32
PROJECT_NAME_MAX = 120
TIME_NOTE_MAX = 300


class SetupClosed(ValidationError):
    """The workspace already exists; first-run creation is rejected, never duplicated."""


def setup_required():
    return not Organization.objects.exists()


def setup_organization():
    """The 1.0 single workspace is the first organization created by the wizard."""
    return Organization.objects.order_by("created_at", "id").first()


def _advisory_lock():
    """Serialize first-run creation on PostgreSQL; SQLite already serializes writers."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('pmo:first-run-onboarding'))")


def accounting_started_on(organization):
    event = (AuditEvent.objects.filter(organization=organization, action="organization.initialized")
             .order_by("-created_at", "-id").first())
    payload = (event.payload or {}) if event is not None else {}
    value = payload.get("accounting_started_on")
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return timezone.localtime(organization.created_at).date()


def _validate_company_input(name, username, password, password_confirm):
    name = (name or "").strip()
    username = (username or "").strip()
    if not 1 <= len(name) <= COMPANY_MAX:
        raise ValidationError(f"公司名称须为1至{COMPANY_MAX}字")
    if not USERNAME_RE.fullmatch(username):
        raise ValidationError("登录名须为3至32位字母、数字、下划线、点或短横线")
    if Organization.objects.exists():
        raise SetupClosed("工作区已经完成首次启用，请直接登录；不能重复创建公司")
    User = get_user_model()
    if User.objects.filter(username=username).exists():
        raise ValidationError("此登录名已被使用，请换一个")
    if password != password_confirm:
        raise ValidationError("两次输入的密码不一致")
    user = User(username=username)
    try:
        validate_password(password, user)
    except ValidationError as error:
        raise ValidationError("；".join(error.messages))


@transaction.atomic
def start_company(*, name, username, password, password_confirm):
    """Create the company + its first owner account exactly once."""
    _advisory_lock()
    _validate_company_input(name, username, password, password_confirm)
    today = timezone.localdate()
    User = get_user_model()
    user = User.objects.create_user(username=username.strip(), password=password,
                                    first_name="公司负责人")
    organization = Organization.objects.create(name=name.strip(), currency=CURRENCY)
    member = Membership.objects.create(organization=organization, user=user, role="owner")
    employee = Employee.objects.create(organization=organization, name="公司负责人", member=member)
    ensure_labor_destinations(member)
    audit(member, "organization.initialized", organization, {
        "mode": "real", "source": "first_run_wizard", "currency": CURRENCY,
        "timezone": TIMEZONE, "accounting_started_on": today.isoformat(),
        "owner": user.username,
    })
    return member, employee


def generated_project_code(organization):
    year = timezone.localdate().year
    for index in range(1, 1000):
        code = f"P-{year}-{index:03d}"
        if not Project.objects.filter(organization=organization, code=code).exists():
            return code
    raise ValidationError("项目编号已用尽，请手工填写项目编号")


def owner_employee(member, *, create=False):
    employee = Employee.objects.filter(organization=member.organization, member=member, active=True).first()
    if employee is None and create:
        employee = Employee.objects.create(organization=member.organization, name="公司负责人", member=member)
        audit(member, "employee.created", employee, {"name": employee.name, "source": "first_run_wizard"})
    return employee


def first_project(member):
    return (Project.objects.filter(organization=member.organization)
            .select_related("manager__user", "manager").order_by("created_at", "id").first())


def first_time_entry(member, project):
    if project is None:
        return None
    return (Timesheet.objects.filter(organization=member.organization, project=project,
                                     accounting_mode="simplified", status="approved")
            .order_by("work_date", "id").first())


def owner_rate(member, employee, today=None):
    if employee is None:
        return None
    day = today or timezone.localdate()
    return (CostRate.objects.filter(employee=employee, effective_from__lte=day)
            .order_by("-effective_from", "-id").first())


def billing_counts(organization):
    from pmo_plugins.billing.models import Account, Invoice
    return {"accounts": Account.objects.filter(organization=organization).count(),
            "invoices": Invoice.objects.filter(organization=organization).count()}


def onboarding_state(member, *, session_customer=None):
    """Read-only wizard/readiness state derived from persisted facts."""
    require_member(member)
    organization = member.organization
    project = first_project(member)
    contract_cents = contract_amount_cents(project) if project is not None else 0
    time_entry = first_time_entry(member, project)
    employee = owner_employee(member)
    rate = owner_rate(member, employee)
    people = Membership.objects.filter(organization=organization, active=True).count()
    bills = billing_counts(organization)
    steps = [
        {"key": "company", "label": "公司", "done": True},
        {"key": "customer", "label": "首个客户", "done": bool(project or session_customer)},
        {"key": "project", "label": "首个项目", "done": project is not None},
        {"key": "contract", "label": "合同额", "done": contract_cents > 0},
        {"key": "time", "label": "首个工时", "done": time_entry is not None},
    ]
    gaps = []
    if project is None:
        gaps.append({"key": "project", "text": "还没有项目与客户"})
    if project is not None and contract_cents == 0:
        gaps.append({"key": "contract", "text": "项目尚未录入合同额"})
    if time_entry is None:
        gaps.append({"key": "time", "text": "还没有第一个工时"})
    if rate is None:
        gaps.append({"key": "rate", "text": "尚未设置负责人内部成本费率，工时不计价（显示缺口而非 0 成本）"})
    if bills["accounts"] == 0:
        gaps.append({"key": "account", "text": "还没有资金账户，登记收款前需先建立账户"})
    if bills["invoices"] == 0:
        gaps.append({"key": "invoice", "text": "尚未开票，项目收入为 0（显示缺口而不是假零）"})
    if people <= 1:
        gaps.append({"key": "member", "text": "目前只有负责人一个账号，可稍后开通项目经理/成员"})
    complete = (project is not None and contract_cents > 0 and time_entry is not None)
    return {
        "organization": organization, "member": member, "steps": steps,
        "project": project, "contract_cents": contract_cents,
        "time_entry": time_entry, "employee": employee, "rate": rate,
        "started_on": accounting_started_on(organization), "currency": CURRENCY,
        "timezone": TIMEZONE, "people": people, "billing": bills,
        "gaps": gaps, "complete": complete,
        "customer": project.client if project is not None else (session_customer or ""),
    }


def next_step(state):
    for step in state["steps"]:
        if not step["done"]:
            return step["key"]
    return "ready"


def create_first_customer(member, name):
    """Customer is the first project's client in the 1.0 simplified model (no separate master row)."""
    require_write(member)
    name = (name or "").strip()
    if not 1 <= len(name) <= CUSTOMER_MAX:
        raise ValidationError(f"客户名称须为1至{CUSTOMER_MAX}字")
    return name


def create_first_project(member, *, name, code, customer, start_date, end_date):
    """Create the first project only: no budget baseline, no demo ledger, no sample data."""
    from .services import create_project
    require_write(member)
    if member.role != "owner":
        raise PermissionDenied("只有公司负责人可以完成首次启用")
    name = (name or "").strip()
    code = (code or "").strip() or generated_project_code(member.organization)
    if not name:
        raise ValidationError("请填写项目名称")
    if not (1 <= len(name) <= PROJECT_NAME_MAX):
        raise ValidationError(f"项目名称须为1至{PROJECT_NAME_MAX}字")
    if len(code) > PROJECT_CODE_MAX or not re.fullmatch(r"[A-Za-z0-9_-]{2,32}", code):
        raise ValidationError("项目编号须为2至32位字母、数字、短横线或下划线")
    if Project.objects.filter(organization=member.organization, code=code).exists():
        raise ValidationError("此项目编号已经存在，请换一个")
    if start_date is None or end_date is None:
        raise ValidationError("请填写项目开始与计划交付日期")
    if end_date < start_date:
        raise ValidationError("计划交付日期不能早于开始日期")
    if start_date > timezone.localdate():
        raise ValidationError("首次启用的项目开始日期不能晚于今天")
    project = create_project(member, {
        "code": code, "name": name, "client": customer, "manager": member,
        "template": Project.Template.SOFTWARE, "stage": Project.Stage.INITIATION,
        "start_date": start_date, "end_date": end_date, "contract_cents": 0,
        "budget_cents": 0, "budget_approved": False, "etc_cents": 0,
        "scope": "", "acceptance_criteria": "", "contract_reference": "首次启用向导",
    })
    return project


def record_first_contract(member, project, amount_cents, note):
    """Append the first contract amount; the project's initiation value stays historical."""
    from .contract_changes import record_contract_change
    require_write(member)
    if member.role != "owner":
        raise PermissionDenied("只有公司负责人可以完成首次启用")
    if type(amount_cents) is not int or not 0 < amount_cents <= 10 ** 12:
        raise ValidationError("合同额须为大于0且不超过100亿元的整数分")
    note = (note or "首次启用向导录入合同额").strip()
    if len(note) < 4:
        note = "首次启用向导录入合同额"
    return record_contract_change(member, project=project, amount_cents=amount_cents,
                                  note=note[:240], effective_on=timezone.localdate(),
                                  token=__import__("uuid").uuid4())


@transaction.atomic
def record_first_time(member, *, project, work_date, minutes, note, hourly_rate_cents=None):
    """Optional owner rate + first weekly time entry; unpriced rows stay explicit gaps."""
    require_write(member)
    if member.role != "owner":
        raise PermissionDenied("只有公司负责人可以完成首次启用")
    if project is None or project.organization_id != member.organization_id:
        raise PermissionDenied("项目不属于当前公司")
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValidationError("工时须为1至1440的整数分钟")
    note = (note or "").strip()
    if not note:
        raise ValidationError("请填写工作说明")
    if len(note) > TIME_NOTE_MAX:
        raise ValidationError(f"工作说明不能超过{TIME_NOTE_MAX}字")
    if work_date < project.start_date or work_date > timezone.localdate():
        raise ValidationError("工作日期须在项目开始日与今天之间")
    employee = owner_employee(member, create=True)
    if hourly_rate_cents is not None:
        if type(hourly_rate_cents) is not int or hourly_rate_cents < 0:
            raise ValidationError("小时成本须为非负整数分")
        if owner_rate(member, employee) is None:
            # The first week entry may be dated before "today" (the project can start
            # earlier); the supplied rate must cover that business date or the row would
            # stay an unpriced gap despite the owner having entered a rate.
            create_rate(member, {"employee": employee,
                                 "effective_from": min(work_date, timezone.localdate()),
                                 "hourly_amount": Decimal(hourly_rate_cents) / 100,
                                 "basis": "首次启用向导：负责人内部成本费率"})
    row = {"key": "row-0", "id": "", "token": str(__import__("uuid").uuid4()),
           "work_date": work_date.isoformat(), "target": f"project:{project.pk}",
           "minutes": str(minutes), "note": note}
    save_week(member, employee, week_start_for(work_date), [row])
    return Timesheet.objects.filter(organization=member.organization, employee=employee,
                                    accounting_mode="simplified", status="approved",
                                    work_date=work_date).order_by("-id").first()


def resume_hint(member):
    """Dashboard banner for an owner whose first-run setup is not complete."""
    from .scope import is_micro_scope
    if not is_micro_scope() or member.role != "owner":
        return None
    first = setup_organization()
    if first is None or first.pk != member.organization_id:
        return None
    state = onboarding_state(member)
    if state["complete"]:
        return None
    labels = {"customer": "填写首个客户", "project": "建立首个项目",
              "contract": "录入合同额", "time": "登记首个工时"}
    key = next_step(state)
    return {"next_key": key, "next_label": labels.get(key, "继续完成启用"), "state": state}
