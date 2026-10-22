"""Bounded UTF-8 CSV parsing, preview then all-or-nothing submission, stable source IDs."""
import csv
import io
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from .access import visible_projects, require_finance_write
from .models import Employee, CashPlan
from .services import cents, submit_time, submit_document, audit, lock_company

HEADERS = {
    "time": ["source_id", "project_code", "employee_name", "work_date", "hours", "note"],
    "expense": ["source_id", "project_code", "title", "amount", "tax", "recoverable_tax", "occurred_on", "counterparty", "reference", "note"],
    "plan": ["source_id", "project_code", "direction", "title", "amount", "due_date", "counterparty", "reference"],
}

def parse_csv(kind, uploaded):
    if kind not in HEADERS: raise ValidationError("不支持的导入类型")
    if uploaded.size > 2 * 1024 * 1024: raise ValidationError("CSV文件不能超过2MB")
    try: text = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError: raise ValidationError("请保存为UTF-8编码的CSV")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or len(reader.fieldnames) != len(HEADERS[kind]) or set(reader.fieldnames) != set(HEADERS[kind]):
        raise ValidationError("CSV列必须为：" + ",".join(HEADERS[kind]))
    rows = []
    seen = set()
    try:
        for index, row in enumerate(reader, 2):
            if len(rows) >= 500: raise ValidationError("每批最多500行，请拆分文件")
            if None in row or any(v is None or len(v) > 8000 for v in row.values()): raise ValidationError(f"第{index}行列数不符或字段过长")
            row = {k: v.strip() for k, v in row.items()}
            if not row["source_id"] or len(row["source_id"]) > 100 or row["source_id"] in seen:
                raise ValidationError(f"第{index}行来源编号为空、过长或重复")
            seen.add(row["source_id"])
            rows.append(row)
    except csv.Error: raise ValidationError("CSV格式无效")
    if not rows: raise ValidationError("CSV没有数据行")
    return rows


def import_token(member, kind, source_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"project-hub:{member.organization_id}:{kind}:{source_id}")

def apply_rows(member, kind, rows):
    projects = {p.code: p for p in visible_projects(member)}
    objects = []
    for index, row in enumerate(rows, 2):
        try:
            project = projects.get(row["project_code"])
            if not project: raise ValidationError("项目编号不存在或没有权限")
            token = import_token(member, kind, row["source_id"])
            if kind == "time":
                employees = Employee.objects.filter(organization=member.organization, name=row["employee_name"], active=True)
                if employees.count() != 1: raise ValidationError("员工姓名不存在或重名，请先核对员工资料")
                hours = Decimal(row["hours"])
                if not hours.is_finite() or hours * 60 != int(hours * 60): raise ValidationError("工时须为整数分钟")
                obj = submit_time(member, {"project": project, "employee": employees.get(), "work_date": date.fromisoformat(row["work_date"]),
                    "minutes": int(hours * 60), "note": row["note"], "task": None}, token)
            elif kind == "expense":
                if row["recoverable_tax"] not in {"0", "1"}: raise ValidationError("抵扣标记仅可填0或1")
                obj = submit_document(member, {"project": project, "kind": "expense", "title": row["title"],
                    "amount_cents": cents(row["amount"]), "tax_cents": cents(row["tax"]), "recoverable_tax": row["recoverable_tax"] == "1",
                    "occurred_on": date.fromisoformat(row["occurred_on"]), "counterparty": row["counterparty"],
                    "reference": row["reference"], "note": row["note"], "category": "other", "plan": None}, token)
            elif kind == "plan":
                require_finance_write(member, project)
                existing = CashPlan.objects.filter(token=token).first()
                if existing:
                    if existing.created_by_id != member.user_id or existing.project_id != project.pk: raise ValidationError("来源编号已使用")
                    expected = {"title": row["title"], "direction": row["direction"], "counterparty": row["counterparty"], "amount_cents": cents(row["amount"]), "due_date": date.fromisoformat(row["due_date"]), "reference": row["reference"]}
                    if any(getattr(existing, key) != value for key, value in expected.items()): raise ValidationError("同一来源编号的计划内容已改变，请使用更正流程或新的来源编号")
                    obj = existing
                else:
                    if row["direction"] not in {"in", "out"}: raise ValidationError("收付款方向只能为in或out")
                    obj = CashPlan(project=project, title=row["title"], direction=row["direction"], counterparty=row["counterparty"],
                        amount_cents=cents(row["amount"]), due_date=date.fromisoformat(row["due_date"]), reference=row["reference"], token=token, created_by=member.user)
                    obj.full_clean(); obj.save()
                    audit(member, "plan.imported", obj, {"source_id": row["source_id"]})
            else: raise ValidationError("导入类型无效")
            objects.append(obj)
        except (ValidationError, ValueError, InvalidOperation, OverflowError) as error:
            detail = "；".join(error.messages) if isinstance(error, ValidationError) else "日期或数字格式无效"
            raise ValidationError(f"第{index}行：{detail}")
    return objects

@transaction.atomic
def preview_rows(member, kind, rows):
    lock_company(member)
    objects = apply_rows(member, kind, rows)
    count = len(objects)
    transaction.set_rollback(True)
    return count

@transaction.atomic
def commit_rows(member, kind, rows):
    lock_company(member)
    return apply_rows(member, kind, rows)
