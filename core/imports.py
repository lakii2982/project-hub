"""Bounded UTF-8 CSV parsing, preview then all-or-nothing submission, stable source IDs."""
import csv
import io
import json
import uuid
from collections import OrderedDict
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
    "invoicing": ["source_id", "counterparty_name", "invoice_number", "invoice_date",
                  "gross_cents", "net_cents", "tax_cents", "evidence_json", "line_no",
                  "line_gross_cents", "line_net_cents", "line_tax_cents",
                  "allocation_namespace", "allocation_kind", "allocation_id",
                  "allocation_version", "allocation_gross_cents",
                  "allocation_net_cents", "allocation_tax_cents", "note"],
}

INVOICING_DIRECTION = "sales"
INVOICING_ALLOCATION_KINDS = {
    ("contracts", "contract_line"),
    ("settlement", "open_item"),
    ("settlement", "settlement"),
}
MAX_CENTS = 10 ** 12

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


def invoicing_invoice_token(organization_id, direction, invoice_number):
    """Frozen owner token for one invoice (contract §4)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"project-hub:{organization_id}:invoicing:invoice:{direction}:{invoice_number}")


def invoicing_allocation_token(organization_id, direction, invoice_number, version):
    """Frozen owner token for one invoice's line/allocation write (contract §4)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"project-hub:{organization_id}:invoicing:allocate:{direction}:{invoice_number}:{version}")


def owner_issue_code(message, *, default="owner_validation_failed"):
    """Map owner ValidationError text to the frozen import issue registry."""
    text = str(message or "")
    if any(token in text for token in ("额度", "可分配", "可消费", "覆盖不足")):
        return "source_quota_exceeded"
    if ("往来方" in text and "不一致" in text) or any(
            token in text for token in ("未批准", "未生效", "只有已生效",
                                        "批准依据", "批准明细", "批准版本")):
        return "row_source_unapproved"
    if "发票号码已存在" in text or ("发票号" in text and "存在" in text):
        return "invoice_number_duplicate"
    if "分配金额合计" in text:
        return "line_total_mismatch"
    if "发票行合计" in text:
        return "invoice_total_mismatch"
    if "request_token" in text or "输入已改变" in text:
        return "owner_idempotency_conflict"
    return default


def _invoicing_counterparty(organization, name):
    from django.db.models import Q
    from pmo_plugins.contracts.models import Counterparty, normalized_key
    text = str(name or "").strip()
    if not text:
        raise ValidationError("往来方名称必填")
    candidates = Counterparty.objects.filter(
        organization=organization, status="active").filter(
        Q(display_name=text) | Q(normalized_name=normalized_key(text)))
    matches = [party for party in candidates if "customer" in (party.roles or [])]
    if len(matches) != 1:
        raise ValidationError("往来方不存在、已停用、非客户或存在歧义")
    return matches[0]


def _invoicing_int(value, label, *, positive=False):
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValidationError(f"{label}须为整数分")
    amount = int(text)
    if positive and amount <= 0:
        raise ValidationError(f"{label}须大于0")
    if not 0 <= amount <= MAX_CENTS:
        raise ValidationError(f"{label}超过100亿元上限")
    return amount


def _invoicing_evidence(value):
    text = str(value or "").strip()
    if not text:
        raise ValidationError("证据JSON必填")
    if len(text) > 2000:
        raise ValidationError("证据JSON不能超过2000字符")
    try:
        return json.loads(text)
    except ValueError:
        raise ValidationError("证据须为有效JSON")


def _invoicing_ref(row):
    namespace = str(row.get("allocation_namespace") or "").strip()
    kind = str(row.get("allocation_kind") or "").strip()
    if (namespace, kind) not in INVOICING_ALLOCATION_KINDS:
        raise ValidationError("业务来源仅支持合同收费明细/结算未清项/结算来源")
    try:
        identifier = str(uuid.UUID(str(row.get("allocation_id") or "").strip()))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("业务来源标识须为有效UUID")
    try:
        version = int(str(row.get("allocation_version") or "").strip())
    except (TypeError, ValueError):
        raise ValidationError("业务来源版本须为整数")
    if version < 1:
        raise ValidationError("业务来源版本须大于0")
    return {"namespace": namespace, "kind": kind, "id": identifier,
            "version": version}


def _apply_invoicing_rows(member, rows):
    """Group invoice rows by invoice number and line number; write only via owner services."""
    from pmo_plugins.invoicing import services as invoicing_services

    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValidationError("导入批次没有数据行")
    groups = OrderedDict()
    for index, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            raise ValidationError(f"第{index}行：导入行格式无效")
        number = str(row.get("invoice_number") or "").strip()
        if not number:
            raise ValidationError(f"第{index}行：发票号码必填")
        groups.setdefault(number, []).append((index, row))

    refs = []
    for number, entries in groups.items():
        first_index, header = entries[0]
        try:
            party = _invoicing_counterparty(
                member.organization, header.get("counterparty_name"))
            try:
                invoice_date = date.fromisoformat(
                    str(header.get("invoice_date") or "").strip())
            except ValueError:
                raise ValidationError("发票日期须为YYYY-MM-DD")
            gross = _invoicing_int(header.get("gross_cents"), "发票含税金额",
                                   positive=True)
            net = _invoicing_int(header.get("net_cents"), "发票不含税金额")
            tax = _invoicing_int(header.get("tax_cents"), "发票税额")
            if gross != net + tax:
                raise ValidationError("发票含税金额必须等于不含税金额加税额")
            evidence = _invoicing_evidence(header.get("evidence_json"))
            header_signature = (
                " ".join(str(header.get(key) or "").strip() for key in
                         ("counterparty_name", "invoice_date", "gross_cents",
                          "net_cents", "tax_cents", "evidence_json")))
            for index, row in entries[1:]:
                signature = (
                    " ".join(str(row.get(key) or "").strip() for key in
                             ("counterparty_name", "invoice_date", "gross_cents",
                              "net_cents", "tax_cents", "evidence_json")))
                if signature != header_signature:
                    raise ValidationError(f"第{index}行：同票多行的票头信息不一致")

            lines = OrderedDict()
            for index, row in entries:
                line_no = _invoicing_int(row.get("line_no"), "发票行号",
                                         positive=True)
                line_gross = _invoicing_int(row.get("line_gross_cents"), "发票行含税金额",
                                            positive=True)
                line_net = _invoicing_int(row.get("line_net_cents"), "发票行不含税金额")
                line_tax = _invoicing_int(row.get("line_tax_cents"), "发票行税额")
                if line_gross != line_net + line_tax:
                    raise ValidationError(f"第{index}行：发票行含税金额必须等于不含税金额加税额")
                allocation_gross = _invoicing_int(
                    row.get("allocation_gross_cents"), "分配含税金额", positive=True)
                allocation_net = _invoicing_int(
                    row.get("allocation_net_cents"), "分配不含税金额")
                allocation_tax = _invoicing_int(
                    row.get("allocation_tax_cents"), "分配税额")
                if allocation_gross != allocation_net + allocation_tax:
                    raise ValidationError(f"第{index}行：分配含税金额必须等于不含税金额加税额")
                bucket = lines.setdefault(line_no, {
                    "index": index, "gross": line_gross, "net": line_net,
                    "tax": line_tax, "allocations": []})
                if (bucket["gross"], bucket["net"], bucket["tax"]) != (
                        line_gross, line_net, line_tax):
                    raise ValidationError(f"第{index}行：同一发票行号的行金额不一致")
                bucket["allocations"].append({
                    "business_source_ref": _invoicing_ref(row),
                    "gross_cents": allocation_gross,
                    "net_cents": allocation_net,
                    "tax_cents": allocation_tax,
                })

            line_rows, total_gross, total_net, total_tax = [], 0, 0, 0
            for line_no, bucket in sorted(lines.items()):
                sum_gross = sum(item["gross_cents"] for item in bucket["allocations"])
                sum_net = sum(item["net_cents"] for item in bucket["allocations"])
                sum_tax = sum(item["tax_cents"] for item in bucket["allocations"])
                if (sum_gross, sum_net, sum_tax) != (
                        bucket["gross"], bucket["net"], bucket["tax"]):
                    raise ValidationError(
                        f"第{bucket['index']}行：分配金额合计必须等于发票行金额")
                total_gross += bucket["gross"]
                total_net += bucket["net"]
                total_tax += bucket["tax"]
                line_rows.append({
                    "line_no": line_no, "gross_cents": bucket["gross"],
                    "net_cents": bucket["net"], "tax_cents": bucket["tax"],
                    "allocations": bucket["allocations"]})
            if (total_gross, total_net, total_tax) != (gross, net, tax):
                raise ValidationError(f"第{first_index}行：发票行合计必须等于发票头金额")

            invoice_token = invoicing_invoice_token(
                member.organization_id, INVOICING_DIRECTION, number)
            recorded = invoicing_services.record_invoice(
                member, direction=INVOICING_DIRECTION, counterparty_id=party.pk,
                invoice_number=number, invoice_date=invoice_date,
                gross_cents=gross, net_cents=net, tax_cents=tax,
                evidence=evidence, request_token=invoice_token, dry_run=False)
            invoice_ref = recorded["invoice_ref"]
            allocation_token = invoicing_allocation_token(
                member.organization_id, INVOICING_DIRECTION, number,
                invoice_ref["version"])
            allocated = invoicing_services.allocate_invoice_lines(
                member, invoice_id=invoice_ref["id"], lines=line_rows,
                expected_version=invoice_ref["version"],
                request_token=allocation_token, dry_run=False)
            refs.append(invoice_ref)
            refs.extend(allocated.get("line_refs") or [])
            refs.extend(allocated.get("allocation_refs") or [])
        except (ValidationError, ValueError, InvalidOperation, OverflowError) as error:
            detail = ("；".join(error.messages) if isinstance(error, ValidationError)
                      else "日期或数字格式无效")
            if not detail.startswith("第"):
                detail = f"第{first_index}行：{detail}"
            raise ValidationError(detail)
    return refs


def apply_rows(member, kind, rows):
    if kind == "invoicing":
        return _apply_invoicing_rows(member, rows)
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
    if kind == "invoicing":
        raise ValidationError("发票批量导入必须经持久预览提交，不能使用通用导入提交")
    lock_company(member)
    return apply_rows(member, kind, rows)
