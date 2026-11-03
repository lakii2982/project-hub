"""Q-37 极简开票/收款/资金账户业务服务。

不变式：金额整数分、Decimal + ROUND_HALF_UP、只追加（作废/退款追加负值并保留
原始来源）、同一收款只消费一次、超额核销与重复提交被拒绝、资金账户余额派生。
简化流程不写 core ``LedgerEntry``；付款事实由 Q-36 接入，当前显式标记为缺口。
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import require_member, require_write
from core.models import Document, Project
from core.services import MAX_MONEY, audit, check_period, lock_company

from .models import Account, Invoice, Receipt

BILLING_ROLES = frozenset({"owner", "pm"})
PAYMENT_SOURCE = "expense_payment"  # Q-36 交付前付款来源缺口；不显示假净额


def yuan_to_cents(value, *, allow_negative=False):
    """Decimal/str 元 -> 整数分；只接受最多两位小数，ROUND_HALF_UP。"""
    try:
        amount = Decimal(str(value))
    except Exception:
        raise ValidationError("金额须为数字")
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise ValidationError("金额最多两位小数")
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not allow_negative and cents < 0:
        raise ValidationError("金额不能为负")
    if not -MAX_MONEY <= cents <= MAX_MONEY:
        raise ValidationError("金额不能超过100亿元")
    return cents


def billing_member(member):
    require_member(member)
    if member.role not in BILLING_ROLES:
        raise PermissionDenied("开票与收款仅向公司负责人和项目经理开放")
    return member


def billing_projects(member):
    """当前角色可读写的开票项目：老板全部，项目经理仅本人负责的项目。"""
    billing_member(member)
    rows = Project.objects.filter(organization=member.organization)
    if member.role != "owner":
        rows = rows.filter(manager=member)
    return rows


def _require_project_write(member, project):
    if project.organization_id != member.organization_id:
        raise PermissionDenied("不能为其他公司的项目开票或收款")
    if member.role == "owner":
        return
    if member.role == "pm" and project.manager_id == member.id:
        return
    raise PermissionDenied("项目经理只能为本人负责的项目开票或收款")


def _sum(rows, field="amount_cents"):
    return int(rows.aggregate(total=Sum(field))["total"] or 0)


def tax_from_gross(amount_cents, tax_rate_bp):
    """税率可选：按价税合计反算税额；未填税率时不猜测税额（返回 0 并保留未填事实）。"""
    if tax_rate_bp is None:
        return 0
    if type(tax_rate_bp) is not int or not 0 <= tax_rate_bp <= 10000:
        raise ValidationError("税率须为 0% 至 100%，最多两位小数")
    return int((Decimal(amount_cents) * tax_rate_bp / (10000 + tax_rate_bp)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def _check_amount(cents, label):
    if type(cents) is not int or not 0 < cents <= MAX_MONEY:
        raise ValidationError(f"{label}须大于0且不超过100亿元")


# ---------- 资金账户 ----------

def account_balance_cents(account):
    """余额 = 期初余额 + 已收款 − 已付款；Q-37 只有收款（退款为负值），付款待 Q-36。"""
    return account.opening_balance_cents + _sum(account.receipts.all())


@transaction.atomic
def register_account(member, *, name, kind, opening_balance_cents, note, token=None):
    org = lock_company(member)
    billing_member(member)
    require_write(member)
    if member.role != "owner":
        raise PermissionDenied("资金账户由公司负责人维护")
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise ValidationError("请填写1至80字的账户名称")
    if kind not in Account.Kind.values:
        raise ValidationError("请选择有效的账户类型")
    if type(opening_balance_cents) is not int or not -MAX_MONEY <= opening_balance_cents <= MAX_MONEY:
        raise ValidationError("期初余额不能超过100亿元")
    if Account.objects.filter(organization=org, name=name).exists():
        raise ValidationError("同名资金账户已存在，请直接选择或换一个名称")
    row = Account.objects.create(organization=org, name=name, kind=kind,
                                 opening_balance_cents=opening_balance_cents,
                                 note=(note or "").strip()[:240], created_by=member.user)
    audit(member, "billing.account.created", row, {"name": row.name, "kind": row.kind,
                                                   "opening_balance_cents": row.opening_balance_cents})
    return row


# ---------- 发票 ----------

def invoice_received_cents(invoice):
    """一张发票已消费的收款（含退款负值）。"""
    return _sum(Receipt.objects.filter(invoice=invoice))


def invoice_remaining_cents(invoice):
    if invoice.reverses_id or Invoice.objects.filter(reverses=invoice).exists():
        return 0
    return invoice.amount_cents - invoice_received_cents(invoice)


@transaction.atomic
def register_invoice(member, *, project, number, amount_cents, tax_rate_bp, issued_on,
                     contract_reference="", note="", token):
    org = lock_company(member)
    billing_member(member)
    require_write(member)
    project = Project.objects.select_for_update().get(pk=project.pk)
    _require_project_write(member, project)
    existing = Invoice.objects.filter(organization=org, token=token).first()
    if existing:
        if existing.created_by_id != member.user_id or existing.project_id != project.pk:
            raise ValidationError("提交标识已被使用，请刷新后重试")
        return existing
    number = (number or "").strip()
    if not 1 <= len(number) <= 60:
        raise ValidationError("请填写1至60字的发票号码")
    if Invoice.objects.filter(organization=org, number=number, reverses__isnull=True).exists():
        raise ValidationError("发票号码已存在；更正请作废原票并重新开票，不重复开票")
    _check_amount(amount_cents, "发票金额")
    tax_cents = tax_from_gross(amount_cents, tax_rate_bp)
    check_period(org, issued_on)
    row = Invoice.objects.create(
        organization=org, project=project, number=number, amount_cents=amount_cents,
        tax_rate_bp=tax_rate_bp, tax_cents=tax_cents, issued_on=issued_on,
        contract_reference=(contract_reference or "").strip()[:120] or project.contract_reference,
        note=(note or "").strip()[:240], token=token, created_by=member.user)
    audit(member, "billing.invoice.issued", row, {
        "number": row.number, "amount_cents": row.amount_cents, "tax_cents": row.tax_cents,
        "tax_rate_bp": row.tax_rate_bp, "project_id": project.pk})
    return row


@transaction.atomic
def void_invoice(member, invoice_id, reason, token):
    org = lock_company(member)
    billing_member(member)
    require_write(member)
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    if invoice.organization_id != org.pk:
        raise PermissionDenied("发票不属于当前公司")
    _require_project_write(member, invoice.project)
    duplicate = Invoice.objects.filter(organization=org, token=token).first()
    if duplicate:
        return duplicate
    if invoice.reverses_id:
        raise ValidationError("作废冲销记录不能再次作废")
    if Invoice.objects.filter(reverses=invoice).exists():
        raise ValidationError("发票已作废，不能重复作废")
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise ValidationError("请填写至少4字的作废原因")
    today = timezone.localdate()
    check_period(org, today)
    if today < invoice.issued_on:
        raise ValidationError("作废日期不能早于开票日期")
    row = Invoice.objects.create(
        organization=org, project=invoice.project, number=invoice.number,
        amount_cents=-invoice.amount_cents, tax_rate_bp=invoice.tax_rate_bp,
        tax_cents=-invoice.tax_cents, issued_on=today,
        contract_reference=invoice.contract_reference, note=(reason or "")[:240],
        reverses=invoice, reason=reason, token=token, created_by=member.user)
    audit(member, "billing.invoice.voided", invoice, {
        "reversal_id": row.pk, "reason": reason, "amount_cents": invoice.amount_cents})
    return row


# ---------- 收款 ----------

@transaction.atomic
def register_receipt(member, *, project, non_project, account, invoice, amount_cents,
                     received_on, counterparty="", reference="", note="", token):
    org = lock_company(member)
    billing_member(member)
    require_write(member)
    existing = Receipt.objects.filter(organization=org, token=token).first()
    if existing:
        if existing.created_by_id != member.user_id:
            raise ValidationError("提交标识已被使用，请刷新后重试")
        return existing
    if non_project:
        if member.role != "owner":
            raise PermissionDenied("非项目收款须由公司负责人登记")
        if project is not None:
            raise ValidationError("非项目收款不能再选择项目")
        project = None
    else:
        if project is None:
            raise ValidationError("请选择项目，或显式标记为非项目收款")
        project = Project.objects.select_for_update().get(pk=project.pk)
        _require_project_write(member, project)
    account = Account.objects.select_for_update().get(pk=account.pk)
    if account.organization_id != org.pk:
        raise PermissionDenied("资金账户不属于当前公司")
    if not account.active:
        raise ValidationError("资金账户已停用，请选择其他启用账户")
    _check_amount(amount_cents, "收款金额")
    check_period(org, received_on)
    reference = (reference or "").strip()
    if not 1 <= len(reference) <= 120:
        raise ValidationError("请填写1至120字的银行流水号或收款来源编号")
    if Receipt.objects.filter(organization=org, reference=reference,
                              reverses__isnull=True).exists():
        raise ValidationError("相同来源编号的收款已登记；重复提交已被拒绝")
    if invoice is not None:
        if project is None:
            raise ValidationError("非项目收款不能关联发票")
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.organization_id != org.pk or invoice.project_id != project.pk:
            raise ValidationError("所选发票不属于该项目")
        if invoice.reverses_id:
            raise ValidationError("作废冲销记录不能用于核销")
        if Invoice.objects.filter(reverses=invoice).exists():
            raise ValidationError("发票已作废，不能登记收款")
        remaining = invoice.amount_cents - invoice_received_cents(invoice)
        if amount_cents > remaining:
            raise ValidationError(
                f"本次收款超过发票未收金额，最多可收 ¥{remaining / 100:,.2f}")
    row = Receipt.objects.create(
        organization=org, account=account, project=project, non_project=bool(non_project),
        invoice=invoice, amount_cents=amount_cents, received_on=received_on,
        counterparty=(counterparty or "").strip()[:120] or (project.client if project else ""),
        reference=reference, note=(note or "").strip()[:240], token=token,
        created_by=member.user)
    audit(member, "billing.receipt.recorded", row, {
        "amount_cents": amount_cents, "account_id": account.pk,
        "project_id": project.pk if project else None,
        "invoice_id": invoice.pk if invoice else None, "reference": reference})
    return row


@transaction.atomic
def refund_receipt(member, receipt_id, amount_cents, reason, token):
    org = lock_company(member)
    billing_member(member)
    require_write(member)
    receipt = Receipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.organization_id != org.pk:
        raise PermissionDenied("收款不属于当前公司")
    if receipt.project_id:
        _require_project_write(member, receipt.project)
    elif member.role != "owner":
        raise PermissionDenied("非项目收款退款须由公司负责人办理")
    duplicate = Receipt.objects.filter(organization=org, token=token).first()
    if duplicate:
        return duplicate
    if receipt.reverses_id:
        raise ValidationError("退款记录不能再次退款")
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise ValidationError("请填写至少4字的退款原因")
    _check_amount(amount_cents, "退款金额")
    refunded = _sum(Receipt.objects.filter(reverses=receipt))
    refundable = receipt.amount_cents + refunded
    if amount_cents > refundable:
        raise ValidationError(f"退款金额超过可退金额，最多可退 ¥{refundable / 100:,.2f}")
    today = timezone.localdate()
    check_period(org, today)
    if today < receipt.received_on:
        raise ValidationError("退款日期不能早于收款日期")
    row = Receipt.objects.create(
        organization=org, account=receipt.account, project=receipt.project,
        non_project=receipt.non_project, invoice=receipt.invoice,
        amount_cents=-amount_cents, received_on=today,
        counterparty=receipt.counterparty, reference="", note=reason[:240],
        reverses=receipt, reason=reason, token=token, created_by=member.user)
    audit(member, "billing.receipt.refunded", receipt, {
        "refund_id": row.pk, "amount_cents": -amount_cents, "reason": reason})
    return row


# ---------- 只读聚合 ----------

def project_totals(project):
    invoices = Invoice.objects.filter(project=project)
    receipts = Receipt.objects.filter(project=project)
    invoiced = _sum(invoices)
    tax = _sum(invoices, "tax_cents")
    received = _sum(receipts)
    return {"invoiced_cents": invoiced, "tax_cents": tax, "net_cents": invoiced - tax,
            "received_cents": received, "outstanding_cents": invoiced - received,
            "payment_gap": True}


def project_billing_summary(member, project):
    """项目页只读块；无权限返回 None（调用方不得用 0 冒充缺口）。"""
    if member is None or member.role not in BILLING_ROLES:
        return None
    if project.organization_id != member.organization_id:
        return None
    if member.role == "pm" and project.manager_id != member.id:
        return None
    return project_totals(project)


def account_rows(organization):
    sums = {row["account_id"]: int(row["total"] or 0) for row in
            Receipt.objects.filter(organization=organization).values("account_id")
            .annotate(total=Sum("amount_cents"))}
    rows = []
    for account in Account.objects.filter(organization=organization):
        account.received_cents = sums.get(account.pk, 0)
        account.balance_cents = account.opening_balance_cents + account.received_cents
        rows.append(account)
    return rows


def invoice_rows(member, limit=60):
    rows = list(Invoice.objects.filter(project__in=billing_projects(member))
                .select_related("project", "created_by").order_by("-created_at")[:limit])
    ids = [row.pk for row in rows]
    received = {row["invoice_id"]: int(row["total"] or 0) for row in
                Receipt.objects.filter(invoice_id__in=ids).values("invoice_id")
                .annotate(total=Sum("amount_cents"))}
    voided = set(Invoice.objects.filter(reverses_id__in=ids).values_list("reverses_id", flat=True))
    for row in rows:
        row.received_cents = received.get(row.pk, 0)
        row.is_voided = row.reverses_id is not None or row.pk in voided
        row.outstanding_cents = (0 if row.is_voided else row.amount_cents - row.received_cents)
    return rows


def receipt_rows(member, limit=60):
    visible = Receipt.objects.filter(organization=member.organization)
    if member.role != "owner":
        visible = visible.filter(project__in=billing_projects(member))
    rows = list(visible.select_related("project", "account", "invoice", "reverses")
                .select_related("reverses__created_by").order_by("-created_at")[:limit])
    refunded = {row["reverses_id"]: int(row["total"] or 0) for row in
                Receipt.objects.filter(reverses_id__in=[r.pk for r in rows])
                .values("reverses_id").annotate(total=Sum("amount_cents"))}
    for row in rows:
        row.refunded_cents = -refunded.get(row.pk, 0)
        row.refundable_cents = row.amount_cents - row.refunded_cents
    return rows


def billing_snapshot(member):
    org = member.organization
    projects = list(billing_projects(member).order_by("-created_at"))
    project_ids = [project.pk for project in projects]
    invoice_sums = {row["project_id"]: row for row in
                    Invoice.objects.filter(organization=org, project_id__in=project_ids)
                    .values("project_id").annotate(amount=Sum("amount_cents"), tax=Sum("tax_cents"))}
    receipt_sums = {row["project_id"]: int(row["amount"] or 0) for row in
                    Receipt.objects.filter(organization=org, project_id__in=project_ids)
                    .values("project_id").annotate(amount=Sum("amount_cents"))}
    project_rows = []
    for project in projects:
        invoice_row = invoice_sums.get(project.pk) or {}
        invoiced = int(invoice_row.get("amount") or 0)
        tax = int(invoice_row.get("tax") or 0)
        received = receipt_sums.get(project.pk, 0)
        if not (invoiced or received):
            continue
        project_rows.append({"project": project, "invoiced_cents": invoiced,
                             "tax_cents": tax, "net_cents": invoiced - tax,
                             "received_cents": received,
                             "outstanding_cents": invoiced - received})
    accounts = account_rows(org)
    # 项目经理只聚合本人负责的项目；老板看全公司（含显式非项目收款）。
    invoices = Invoice.objects.filter(organization=org)
    receipts = Receipt.objects.filter(organization=org)
    if member.role != "owner":
        invoices = invoices.filter(project_id__in=project_ids)
        receipts = receipts.filter(project_id__in=project_ids)
    non_project_received = _sum(receipts.filter(project__isnull=True)) \
        if member.role == "owner" else 0
    non_project_count = receipts.filter(project__isnull=True).count() \
        if member.role == "owner" else 0
    legacy = Document.objects.filter(project_id__in=project_ids, kind="receipt",
                                     status__in=["submitted", "approved"]).count()
    tax_unspecified = invoices.filter(tax_rate_bp__isnull=True, reverses__isnull=True,
                                      reversal__isnull=True).count()
    return {
        "projects": projects,
        "project_rows": project_rows,
        "accounts": accounts,
        "account_total_cents": sum(account.balance_cents for account in accounts),
        "invoiced_cents": _sum(invoices),
        "tax_cents": _sum(invoices, "tax_cents"),
        "net_cents": _sum(invoices) - _sum(invoices, "tax_cents"),
        "received_cents": _sum(receipts.filter(project__isnull=False)),
        "outstanding_cents": _sum(invoices) - _sum(receipts.filter(project__isnull=False)),
        "non_project_received_cents": non_project_received,
        "invoice_rows": invoice_rows(member),
        "receipt_rows": receipt_rows(member),
        "gaps": {"legacy_receipts": legacy, "payments_connected": False,
                 "tax_unspecified": tax_unspecified,
                 "non_project_receipts": non_project_count},
    }
