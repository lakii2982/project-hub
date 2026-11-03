"""Q-37 极简开票、收款与资金账户事实。

只追加的业务事实：发票作废与收款退款都追加负值记录并保留原始来源，
不原地改历史金额。收付款不是 core ``LedgerEntry``（简化流程不写账本），
资金账户余额由收付款派生，不提供直接编辑余额。
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

MAX_MONEY = 10**12  # 单笔上限 100 亿元，与 core 金额边界一致


class Account(models.Model):
    """极简资金账户：名称/类型/期初余额/停用；余额派生，不手工改。"""

    class Kind(models.TextChoices):
        BANK = "bank", "银行账户"
        ALIPAY = "alipay", "支付宝"
        WECHAT = "wechat", "微信"
        CASH = "cash", "现金"

    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='billing_accounts')
    name = models.CharField('账户名称', max_length=80)
    kind = models.CharField('账户类型', max_length=12, choices=Kind.choices, default=Kind.BANK)
    opening_balance_cents = models.BigIntegerField('期初余额（分）', default=0)
    active = models.BooleanField('启用', default=True)
    note = models.CharField('备注', max_length=240, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-active', 'id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'],
                                    name='billing_account_org_name'),
            models.CheckConstraint(
                condition=Q(opening_balance_cents__gte=-MAX_MONEY,
                            opening_balance_cents__lte=MAX_MONEY),
                name='billing_account_opening_bound'),
        ]

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('资金账户只停用不删除，历史收款与余额保持可查')


class Invoice(models.Model):
    """一张发票；价税合计存整数分，税率可选，作废追加负值记录。"""

    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='billing_invoices')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT,
                                related_name='billing_invoices')
    number = models.CharField('发票号码', max_length=60)
    amount_cents = models.BigIntegerField('价税合计（分）')
    tax_rate_bp = models.PositiveIntegerField('税率（基点，可选）', null=True, blank=True)
    tax_cents = models.BigIntegerField('其中税额（分）', default=0)
    issued_on = models.DateField('开票日期')
    contract_reference = models.CharField('合同或立项依据', max_length=120, blank=True)
    note = models.CharField('备注', max_length=240, blank=True)
    reverses = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='reversal')
    reason = models.CharField('作废原因', max_length=240, blank=True)
    token = models.UUIDField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-issued_on', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_cents__gte=-MAX_MONEY, amount_cents__lte=MAX_MONEY)
                & ~Q(amount_cents=0), name='billing_invoice_amount_bound'),
            models.CheckConstraint(
                condition=Q(reverses__isnull=True, amount_cents__gt=0)
                | Q(reverses__isnull=False, amount_cents__lt=0),
                name='billing_invoice_append_sign'),
            models.UniqueConstraint(fields=['organization', 'number'],
                                    condition=Q(reverses__isnull=True),
                                    name='billing_invoice_org_number'),
        ]

    @property
    def is_void(self):
        return self.reverses_id is not None

    @property
    def net_cents(self):
        return self.amount_cents - self.tax_cents

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('发票只追加，不允许删除；作废请追加负值记录')

    @property
    def tax_rate_display(self):
        if self.tax_rate_bp is None:
            return "未填"
        text = format((Decimal(self.tax_rate_bp) / Decimal(100)).normalize(), "f")
        return text + "%"


class Receipt(models.Model):
    """一笔收款或退款：项目或显式非项目；退款追加负值并指向原收款。"""

    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='billing_receipts')
    account = models.ForeignKey(Account, on_delete=models.PROTECT,
                                related_name='receipts')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, null=True,
                                blank=True, related_name='billing_receipts')
    non_project = models.BooleanField('显式非项目', default=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, blank=True,
                                related_name='receipts')
    amount_cents = models.BigIntegerField('收款金额（分）')
    received_on = models.DateField('收款日期')
    counterparty = models.CharField('客户/往来方', max_length=120, blank=True)
    reference = models.CharField('来源编号', max_length=120, blank=True)
    note = models.CharField('备注', max_length=240, blank=True)
    reverses = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='refunds')
    reason = models.CharField('退款原因', max_length=240, blank=True)
    token = models.UUIDField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_on', '-id']
        constraints = [
            models.CheckConstraint(
                condition=(Q(project__isnull=False, non_project=False)
                           | Q(project__isnull=True, non_project=True)),
                name='billing_receipt_project_or_nonproject'),
            models.CheckConstraint(
                condition=Q(amount_cents__gte=-MAX_MONEY, amount_cents__lte=MAX_MONEY)
                & ~Q(amount_cents=0), name='billing_receipt_amount_bound'),
            models.CheckConstraint(
                condition=Q(reverses__isnull=True, amount_cents__gt=0)
                | Q(reverses__isnull=False, amount_cents__lt=0),
                name='billing_receipt_append_sign'),
            models.UniqueConstraint(fields=['organization', 'reference'],
                                    condition=Q(reverses__isnull=True),
                                    name='billing_receipt_org_reference'),
        ]

    @property
    def is_refund(self):
        return self.reverses_id is not None

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('开票与收款记录只追加，不允许删除')
