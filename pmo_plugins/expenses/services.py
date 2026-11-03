"""Q-36 simplified expense registration, confirmation, correction and payment records.

Business rules (docs/product-scope.md §4.2/§4.4/§8.2):
* one expense record (core ``Document(kind=expense)``) is the single cost fact;
  confirming it posts exactly one cost ledger entry;
* a payment is a child record under that same expense: it posts cash only and can
  never add a second cost; split payments and an unpaid remainder are normal;
* the unpaid remainder of a confirmed expense enters the read-only 8-week cash plan
  keyed ``expense:<document id>`` (deduplicated by source), overdue items are listed
  separately and are never moved into the current week;
* every write is tenant/project/role/period checked, transactional, audited and
  token idempotent; corrections append a reversal instead of editing ledger history;
* the simplified 1.0 flow is owner/PM only (review D-11 micro roles): finance/auditor
  roles stay on their legacy read records and receive the same 403 as members here.
"""
import hashlib
import uuid
from datetime import date, timedelta
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import (can_read_finance, require_finance_write, require_member,
                         visible_projects)
from core.forms import CATEGORIES
from core.models import Document, Evidence, LedgerEntry, Membership
from core.services import MAX_MONEY, audit, check_period, lock_company

from .models import ExpenseDetail, ExpensePayment

CATEGORY_LABELS = dict(CATEGORIES)
CATEGORY_VALUES = frozenset(CATEGORY_LABELS)
NOTE_MAX = 8000
# 1.0 expense role policy (docs/product-scope.md §5/§8.6): the simplified expense flow is
# owned by the boss and the responsible project manager. Finance/auditor roles keep their
# legacy professional read records but are not part of the 1.0 expense UI, so they get the
# same 403 as members instead of a half-visible page or a dashboard gap they cannot close.
WRITER_ROLES = frozenset({"owner", "pm"})
READER_ROLES = frozenset({"owner", "pm"})
ATTACHMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".csv", ".txt", ".xlsx", ".docx"}
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
EIGHT_WEEKS = 8


def to_cents(value):
    """Parse yuan to integer cents with Decimal/ROUND_HALF_UP; floats are rejected."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError("金额须用整数分或两位小数文本填写，不接受浮点数")
    if isinstance(value, int):
        amount = value
    else:
        try:
            parsed = Decimal(str(value).replace(",", "").strip())
            if not parsed.is_finite() or parsed < 0:
                raise ValidationError("金额须为非负数字")
            amount = int((parsed * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (DecimalException, ValueError, TypeError):
            raise ValidationError("金额格式无效，请填写数字")
    if not 0 < amount <= MAX_MONEY:
        raise ValidationError("金额须大于0且不超过100亿元")
    return amount


def _integer_cents(value):
    if type(value) is not int or not 0 < value <= MAX_MONEY:
        raise ValidationError("金额须为大于0且不超过100亿元的整数分")
    return value


def _date(value, label):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{label}无效，请重新选择")


def _text(value, label, maximum, required=False):
    text = (value or "").strip() if isinstance(value, str) else ""
    if required and not text:
        raise ValidationError(f"请填写{label}")
    if len(text) > maximum:
        raise ValidationError(f"{label}不能超过{maximum}字")
    return text


def _token(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("提交编号无效，请刷新后重试")


def _fresh(member):
    try:
        return Membership.objects.select_related("organization", "user").get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied("公司成员上下文无效")


def _reader(member):
    member = _fresh(member)
    require_member(member)
    if member.role not in READER_ROLES:
        raise PermissionDenied("费用与外包仅向老板和项目负责人开放")
    return member


def _writer(member):
    member = _fresh(member)
    require_member(member)
    if member.role not in WRITER_ROLES:
        raise PermissionDenied("登记或确认费用需要老板或项目经理权限")
    return member


def readable_projects(member):
    member = _reader(member)
    projects = visible_projects(member)
    if member.role == "owner":
        return projects
    return projects.filter(manager=member)


def writable_projects(member, *, open_only=True):
    member = _writer(member)
    projects = visible_projects(member)
    if member.role == "pm":
        projects = projects.filter(manager=member)
    if open_only:
        projects = projects.exclude(stage__in=["closed", "cancelled"])
    return projects


def _locked_project(member, project):
    project_id = getattr(project, "pk", project)
    locked = visible_projects(member).filter(pk=project_id).first()
    if locked is None:
        raise ValidationError("项目不存在或无权访问")
    require_finance_write(member, locked)
    return locked


def payment_accounts(member):
    """Active funding accounts of the member's company; owner/PM may choose any."""
    member = _writer(member)
    from pmo_plugins.billing.models import Account
    return Account.objects.filter(organization=member.organization, active=True)


def _locked_payment_account(member, account):
    """Validate the funding account inside the same lock; never trust a posted id."""
    from pmo_plugins.billing.models import Account
    account_id = getattr(account, "pk", account)
    row = None
    if account_id is not None:
        row = Account.objects.select_for_update().filter(pk=account_id).first()
    if row is None or row.organization_id != member.organization_id:
        raise ValidationError("请选择当前公司的启用资金账户")
    if not row.active:
        raise ValidationError("资金账户已停用，请选择其他启用账户")
    return row


def _locked_expense(member, pk):
    member = _writer(member)
    # PostgreSQL rejects FOR UPDATE on the nullable side of an outer join, so lock only
    # the document row here and load the (nullable) detail with a separate query.
    doc = (Document.objects.select_for_update(of=("self",))
           .select_related("project")
           .filter(pk=pk, kind=Document.Kind.EXPENSE,
                   project__organization=member.organization).first())
    if doc is None:
        raise Document.DoesNotExist
    require_finance_write(member, doc.project)
    return doc


def _require_detail(doc):
    try:
        return doc.expense_detail
    except ExpenseDetail.DoesNotExist:
        raise ValidationError("该费用由历史专业流程登记，只读保留；请在语义/项目财务中查看，"
                              "如需处理请走新的简明费用登记")


def _check_project_open(project):
    if project.stage in {"closed", "cancelled", "on_hold"}:
        raise ValidationError("项目已结束或暂停；登记费用前请由负责人恢复项目")


def _derived_title(category, note):
    label = CATEGORY_LABELS.get(category, "费用")
    first_line = (note or "").strip().splitlines()[0][:120] if (note or "").strip() else ""
    return (f"{label}：{first_line}" if first_line else label)[:160]


def _attachment_digest(uploaded):
    if not uploaded:
        return None
    suffix = Path(getattr(uploaded, "name", "") or "").suffix.lower()
    if uploaded.size > ATTACHMENT_MAX_BYTES or suffix not in ATTACHMENT_SUFFIXES:
        raise ValidationError("附件须为10MB以内的PDF、图片、文本或Office凭证")
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


def _save_attachment(member, document, uploaded, digest=None):
    if not uploaded:
        return None
    digest = digest or _attachment_digest(uploaded)
    evidence = Evidence(document=document, original_name=Path(uploaded.name).name[:180],
                        sha256=digest, uploaded_by=member.user)
    evidence.file.save(uploaded.name, uploaded, save=False)
    try:
        evidence.save()
    except Exception:
        evidence.file.delete(save=False)
        raise
    audit(member, "evidence.added", evidence,
          {"document_id": document.pk, "sha256": evidence.sha256})
    return evidence


def _expense_snapshot(doc):
    detail = doc.expense_detail
    payments = list(doc.expense_payments.all())
    paid = sum(int(payment.amount_cents) for payment in payments)
    unpaid = int(doc.amount_cents) - paid
    due_on = (detail.expected_pay_on or doc.occurred_on) if detail else doc.occurred_on
    return {
        "document": doc, "detail": detail, "payments": payments,
        "amount_cents": int(doc.amount_cents), "paid_cents": paid, "unpaid_cents": unpaid,
        "confirmed": doc.status == Document.Status.APPROVED,
        "pending": doc.status == Document.Status.SUBMITTED,
        "due_on": due_on,
        "category_label": CATEGORY_LABELS.get(doc.category, doc.category),
        "overdue": doc.status == Document.Status.APPROVED and unpaid > 0
                   and due_on < timezone.localdate(),
        "version": detail.version if detail else None,
        "expected_pay_on": detail.expected_pay_on if detail else None,
    }


def legacy_expense_count(member, project=None):
    """Historical professional-cycle expenses, kept read-only and never restated/planned."""
    projects = readable_projects(member)
    docs = Document.objects.filter(kind=Document.Kind.EXPENSE, expense_detail__isnull=True,
                                   project__in=projects,
                                   status__in=[Document.Status.SUBMITTED, Document.Status.APPROVED])
    if project is not None:
        projects = projects.filter(pk=getattr(project, "pk", project))
        docs = docs.filter(project__in=projects)
    return docs.count()


def project_scope_rows(member, project=None):
    projects = readable_projects(member)
    if project is not None:
        project_id = getattr(project, "pk", project)
        if not projects.filter(pk=project_id).exists():
            raise PermissionDenied("无权访问此项目费用")
    docs = (Document.objects.filter(kind=Document.Kind.EXPENSE, expense_detail__isnull=False,
                                    project__in=projects)
            .select_related("project", "submitted_by", "expense_detail")
            .prefetch_related("evidence_files", "expense_payments")
            .order_by("-occurred_on", "-id"))
    if project is not None:
        docs = docs.filter(project_id=getattr(project, "pk", project))
    return list(docs)


def expense_snapshot(member, project=None, limit=100):
    member = _reader(member)
    rows = [_expense_snapshot(doc) for doc in project_scope_rows(member, project)]
    pending = [row for row in rows if row["pending"]]
    confirmed = [row for row in rows if row["confirmed"]]
    totals = {
        "registered_count": len(rows),
        "pending_count": len(pending),
        "pending_amount_cents": sum(row["amount_cents"] for row in pending),
        "confirmed_count": len(confirmed),
        "confirmed_cost_cents": sum(row["amount_cents"] for row in confirmed),
        "paid_cents": sum(row["paid_cents"] for row in rows),
        "unpaid_cents": sum(row["unpaid_cents"] for row in confirmed),
    }
    return {"rows": rows[:limit], "pending_rows": pending[:limit],
            "confirmed_rows": confirmed[:limit], "totals": totals,
            "legacy_count": legacy_expense_count(member, project),
            "plan": cash_plan(member, project=project)}


def cash_plan(member, project=None, weeks=EIGHT_WEEKS):
    """Read-only 8-week plan for unpaid confirmed expenses (one row per source)."""
    member = _reader(member)
    today = timezone.localdate()
    rows = [row for row in (_expense_snapshot(doc) for doc in project_scope_rows(member, project))
            if row["confirmed"] and row["unpaid_cents"] > 0]
    items, seen = [], set()
    for row in rows:
        source_id = f"expense:{row['document'].pk}"
        if source_id in seen:
            continue
        seen.add(source_id)
        days = (row["due_on"] - today).days
        week_index = days // 7 if days >= 0 else None
        items.append({
            "source_kind": "expense", "source_id": source_id,
            "expense_id": row["document"].pk, "project": row["document"].project,
            "due_on": row["due_on"], "amount_cents": row["unpaid_cents"],
            "settled_cents": row["paid_cents"], "days": days,
            "overdue": days < 0, "week_index": week_index,
            "title": row["document"].title, "category_label": row["category_label"],
        })
    items.sort(key=lambda item: (item["overdue"] is False, item["due_on"], item["expense_id"]))
    overdue = [item for item in items if item["overdue"]]
    within = [item for item in items if item["week_index"] is not None
              and item["week_index"] < weeks]
    beyond = [item for item in items if item["week_index"] is not None
              and item["week_index"] >= weeks]
    buckets = []
    for index in range(weeks):
        start = today + timedelta(days=7 * index)
        end = start + timedelta(days=6)
        bucket_items = [item for item in within if item["week_index"] == index]
        buckets.append({
            "index": index, "items": bucket_items,
            "amount_cents": sum(item["amount_cents"] for item in bucket_items),
            "label": f"{start:%m.%d}–{end:%m.%d}",
        })
    return {"items": items[:200], "overdue": overdue[:100], "weeks": buckets,
            "within_amount_cents": sum(item["amount_cents"] for item in within),
            "overdue_amount_cents": sum(item["amount_cents"] for item in overdue),
            "beyond_amount_cents": sum(item["amount_cents"] for item in beyond),
            "unpaid_total_cents": sum(item["amount_cents"] for item in items),
            "overdue_count": len(overdue), "within_count": len(within)}


def dashboard_expense_facts(member):
    """Dashboard gap + 8-week unpaid summary; ``None`` when the role may not read it."""
    if getattr(member, "role", None) not in READER_ROLES:
        return None
    try:
        member = _reader(member)
    except PermissionDenied:
        return None
    plan = cash_plan(member)
    pending = [row for row in (_expense_snapshot(doc)
                               for doc in project_scope_rows(member)) if row["pending"]]
    return {"pending_count": len(pending),
            "pending_amount_cents": sum(row["amount_cents"] for row in pending),
            "pending_projects": len({row["document"].project_id for row in pending}),
            "plan": plan}


def project_expense_summary(member, project):
    if getattr(member, "role", None) not in READER_ROLES:
        return None
    if not can_read_finance(member, project):
        return None
    rows = [_expense_snapshot(doc) for doc in project_scope_rows(member, project)]
    confirmed_ids = [row["document"].pk for row in rows if row["confirmed"]]
    cost_cents = (LedgerEntry.objects.filter(document_id__in=confirmed_ids,
                                             book=LedgerEntry.Book.COST)
                  .aggregate(total=Sum("amount_cents"))["total"] or 0)
    return {
        "confirmed_count": len(confirmed_ids),
        "cost_cents": int(cost_cents),
        "pending_count": sum(1 for row in rows if row["pending"]),
        "pending_amount_cents": sum(row["amount_cents"] for row in rows if row["pending"]),
        "paid_cents": sum(row["paid_cents"] for row in rows),
        "unpaid_cents": sum(row["unpaid_cents"] for row in rows if row["confirmed"]),
        "overdue_count": sum(1 for row in rows if row["overdue"]),
        "rows": rows[:20],
    }


@transaction.atomic
def register_expense(member, *, project, category, amount_cents, occurred_on,
                     expected_pay_on=None, note="", counterparty="", token,
                     attachment=None):
    org = lock_company(member)
    member = _writer(member)
    project = _locked_project(member, project)
    _check_project_open(project)
    if category not in CATEGORY_VALUES:
        raise ValidationError("费用类别无效，请从列表中选择")
    amount = _integer_cents(amount_cents)
    occurred_on = _date(occurred_on, "发生日期")
    expected_pay_on = _date(expected_pay_on, "预计付款日") if expected_pay_on else None
    note = _text(note, "费用说明", NOTE_MAX, required=True)
    counterparty = _text(counterparty, "供应商或往来方", 120)
    check_period(org, occurred_on)
    token = _token(token)
    existing = Document.objects.filter(token=token).first()
    if existing is not None:
        detail = getattr(existing, "expense_detail", None)
        if (existing.kind != Document.Kind.EXPENSE or existing.project_id != project.pk
                or existing.submitted_by_id != member.user_id or detail is None):
            raise ValidationError("提交编号已被使用，请刷新后核对")
        same = (existing.category == category and existing.amount_cents == amount
                and existing.occurred_on == occurred_on
                and detail.expected_pay_on == expected_pay_on
                and existing.note == note and existing.counterparty == counterparty)
        if not same:
            raise ValidationError("同一提交编号的费用内容已改变，请刷新后重新登记")
        if attachment is not None:
            digest = _attachment_digest(attachment)
            if not existing.evidence_files.filter(sha256=digest).exists():
                raise ValidationError("同一提交编号已登记过不同附件；请刷新或使用新的提交编号")
        return existing
    document = Document(
        project=project, kind=Document.Kind.EXPENSE, title=_derived_title(category, note),
        category=category, amount_cents=amount, tax_cents=0, occurred_on=occurred_on,
        counterparty=counterparty, reference=f"EXP-{str(token)[:12]}", note=note,
        status=Document.Status.SUBMITTED, submitted_by=member.user, token=token)
    document.full_clean()
    document.save()
    detail = ExpenseDetail.objects.create(document=document, organization=org,
                                          expected_pay_on=expected_pay_on)
    _save_attachment(member, document, attachment)
    audit(member, "expense.registered", document, {
        "project_id": project.pk, "category": category, "amount_cents": amount,
        "occurred_on": occurred_on.isoformat(),
        "expected_pay_on": expected_pay_on.isoformat() if expected_pay_on else None,
        "attachment": bool(attachment), "version": detail.version,
    })
    return document


@transaction.atomic
def confirm_expense(member, expense_id):
    org = lock_company(member)
    member = _writer(member)
    document = _locked_expense(member, expense_id)
    detail = _require_detail(document)
    if document.status == Document.Status.APPROVED:
        return document
    if document.status != Document.Status.SUBMITTED:
        raise ValidationError("该费用已处理或已冲销，请刷新后核对")
    _check_project_open(document.project)
    check_period(org, document.occurred_on)
    document.status = Document.Status.APPROVED
    document.reviewed_by = member.user
    document.reviewed_at = timezone.now()
    document.review_note = "简易费用确认（1.0 免审批，录入即生效，审计留痕）"
    document.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    entry = LedgerEntry.objects.create(
        project=document.project, book=LedgerEntry.Book.COST,
        amount_cents=document.amount_cents, category=document.category,
        occurred_on=document.occurred_on, document=document,
        posting_key=f"expense:{document.pk}")
    audit(member, "expense.confirmed", document, {
        "amount_cents": int(document.amount_cents), "occurred_on": document.occurred_on.isoformat(),
        "category": document.category, "ledger_entry_id": entry.pk, "version": detail.version,
    })
    return document


@transaction.atomic
def correct_expense(member, expense_id, *, category, amount_cents, occurred_on,
                    expected_pay_on=None, note="", counterparty="", version,
                    attachment=None):
    org = lock_company(member)
    member = _writer(member)
    document = _locked_expense(member, expense_id)
    _require_detail(document)
    detail = ExpenseDetail.objects.select_for_update().filter(document=document).first()
    if detail is None:
        raise ValidationError("该费用由历史专业流程登记，只读保留，不能在简明费用页更正")
    if type(version) is not int or version != detail.version:
        raise ValidationError("记录已被其他人更新，请刷新后重试")
    if document.status not in {Document.Status.SUBMITTED, Document.Status.APPROVED}:
        raise ValidationError("已冲销或退回的费用不能更正")
    if category not in CATEGORY_VALUES:
        raise ValidationError("费用类别无效，请从列表中选择")
    amount = _integer_cents(amount_cents)
    occurred_on = _date(occurred_on, "发生日期")
    expected_pay_on = _date(expected_pay_on, "预计付款日") if expected_pay_on else None
    note = _text(note, "费用说明", NOTE_MAX, required=True)
    counterparty = _text(counterparty, "供应商或往来方", 120)
    paid = (ExpensePayment.objects.filter(expense=document)
            .aggregate(total=Sum("amount_cents"))["total"] or 0)
    if amount < paid:
        raise ValidationError("更正后的支出金额不能低于已付款合计；请先核对付款记录")
    _check_project_open(document.project)
    check_period(org, occurred_on)
    before = {"category": document.category, "amount_cents": int(document.amount_cents),
              "occurred_on": document.occurred_on.isoformat(),
              "expected_pay_on": detail.expected_pay_on.isoformat() if detail.expected_pay_on else None,
              "note": document.note, "counterparty": document.counterparty}
    after = {"category": category, "amount_cents": amount,
             "occurred_on": occurred_on.isoformat(),
             "expected_pay_on": expected_pay_on.isoformat() if expected_pay_on else None,
             "note": note, "counterparty": counterparty}
    attachment_digest = _attachment_digest(attachment) if attachment is not None else None
    if (before == after and (attachment_digest is None
            or document.evidence_files.filter(sha256=attachment_digest).exists())):
        return {"document": document, "changed": False, "detail": detail}
    cost_changed = (before["amount_cents"] != amount or before["category"] != category
                    or before["occurred_on"] != occurred_on.isoformat())
    if document.status == Document.Status.APPROVED and cost_changed:
        check_period(org, timezone.localdate())  # the reversal is appended today
        current = (LedgerEntry.objects.filter(document=document, book=LedgerEntry.Book.COST,
                                             reversal__isnull=True)
                   .order_by("-id").first())
        if current is not None:
            LedgerEntry.objects.create(
                project=document.project, book=LedgerEntry.Book.COST,
                amount_cents=-current.amount_cents, category=current.category,
                occurred_on=timezone.localdate(), document=document, reverses=current,
                posting_key=f"expense:{document.pk}:rev:{detail.version}")
    document.category = category
    document.amount_cents = amount
    document.occurred_on = occurred_on
    document.note = note
    document.counterparty = counterparty
    document.title = _derived_title(category, note)
    document.full_clean()
    document.save(update_fields=["category", "amount_cents", "occurred_on", "note",
                                 "counterparty", "title"])
    detail.expected_pay_on = expected_pay_on
    detail.version += 1
    detail.save(update_fields=["expected_pay_on", "version", "updated_at"])
    if document.status == Document.Status.APPROVED and cost_changed:
        LedgerEntry.objects.create(
            project=document.project, book=LedgerEntry.Book.COST,
            amount_cents=amount, category=category, occurred_on=occurred_on,
            document=document, posting_key=f"expense:{document.pk}:v{detail.version}")
    _save_attachment(member, document, attachment, digest=attachment_digest)
    audit(member, "expense.corrected", document, {
        "before": before, "after": after, "version": detail.version,
        "cost_restated": document.status == Document.Status.APPROVED and cost_changed,
        "attachment": bool(attachment),
    })
    return {"document": document, "changed": True, "detail": detail}


@transaction.atomic
def record_payment(member, expense_id, *, account, amount_cents, paid_on, note="", token):
    org = lock_company(member)
    member = _writer(member)
    account = _locked_payment_account(member, account)
    document = _locked_expense(member, expense_id)
    _require_detail(document)
    if document.status not in {Document.Status.SUBMITTED, Document.Status.APPROVED}:
        raise ValidationError("该费用已处理或已冲销，不能登记付款")
    if document.project.stage in {"closed", "cancelled"}:
        raise ValidationError("项目已归档或取消；补记实际付款前请先恢复项目")
    amount = _integer_cents(amount_cents)
    paid_on = _date(paid_on, "付款日期")
    check_period(org, paid_on)
    note = _text(note, "付款说明", 240)
    token = _token(token)
    existing = ExpensePayment.objects.filter(token=token).select_related("expense").first()
    if existing is not None:
        if (existing.expense_id != document.pk or existing.amount_cents != amount
                or existing.paid_on != paid_on or existing.note != note
                or existing.account_id != account.pk):
            raise ValidationError("提交编号已被使用，请刷新后核对")
        return existing
    if Document.objects.filter(token=token).exists():
        raise ValidationError("提交编号已被使用，请刷新后核对")
    paid = (ExpensePayment.objects.filter(expense=document)
            .aggregate(total=Sum("amount_cents"))["total"] or 0)
    if paid + amount > document.amount_cents:
        raise ValidationError("付款累计不能超过支出金额；请核对未付金额后分次登记")
    payment_document = Document(
        project=document.project, kind=Document.Kind.PAYMENT,
        title=f"支出付款 · {document.title}"[:160], category=document.category,
        amount_cents=amount, tax_cents=0, occurred_on=paid_on,
        counterparty=document.counterparty,
        reference=f"EXP-{document.pk}-PAY-{str(token)[:8]}",
        note=f"支出 #{document.pk} 付款：{note}" if note else f"支出 #{document.pk} 付款",
        status=Document.Status.APPROVED, submitted_by=member.user,
        reviewed_by=member.user, reviewed_at=timezone.now(), token=token)
    payment_document.full_clean()
    payment_document.save()
    payment = ExpensePayment.objects.create(
        organization=org, expense=document, payment_document=payment_document,
        amount_cents=amount, paid_on=paid_on, account=account, note=note, token=token,
        created_by=member.user)
    LedgerEntry.objects.create(
        project=document.project, book=LedgerEntry.Book.CASH_OUT, amount_cents=amount,
        category=document.category, occurred_on=paid_on, document=payment_document,
        posting_key=f"expense-payment:{payment_document.pk}")
    unpaid = int(document.amount_cents) - paid - amount
    audit(member, "expense.payment_recorded", payment, {
        "expense_id": document.pk, "payment_document_id": payment_document.pk,
        "amount_cents": amount, "paid_on": paid_on.isoformat(),
        "account_id": account.pk, "paid_total_cents": paid + amount,
        "unpaid_cents": unpaid,
    })
    return payment
