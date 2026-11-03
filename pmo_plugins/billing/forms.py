"""Q-37 开票、收款与资金账户表单；只做输入形态校验，业务校验在服务层。"""
import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.models import Project
from .models import Account, Invoice
from .services import billing_projects, yuan_to_cents

DATE = {"type": "date"}
MONEY = {"min": "0", "step": "0.01", "inputmode": "decimal"}


class BillingForm(forms.Form):
    request_token = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, member=None, **kwargs):
        self.member = member
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()
            if "issued_on" in self.fields:
                self.fields["issued_on"].initial = timezone.localdate()
            if "received_on" in self.fields:
                self.fields["received_on"].initial = timezone.localdate()
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs.setdefault("class", "form-control")

    def token(self):
        return self.cleaned_data.get("request_token") or uuid.uuid4()


class AccountForm(BillingForm):
    name = forms.CharField(label="账户名称", max_length=80)
    kind = forms.ChoiceField(label="账户类型", choices=Account.Kind.choices,
                             initial=Account.Kind.BANK)
    opening_balance = forms.DecimalField(
        label="期初余额（元）", required=False, max_digits=14, decimal_places=2,
        min_value=Decimal("-10000000000"), max_value=Decimal("10000000000"),
        widget=forms.NumberInput(dict(MONEY, step="0.01")),
        help_text="可填负数；建立后余额只由收付款派生，不能直接改余额。")
    note = forms.CharField(label="备注", max_length=240, required=False)


class InvoiceForm(BillingForm):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label="所属项目", empty_label="请选择项目")
    number = forms.CharField(label="发票号码", max_length=60)
    amount = forms.DecimalField(label="价税合计（元）", max_digits=14, decimal_places=2,
                                min_value=Decimal("0.01"), widget=forms.NumberInput(MONEY))
    tax_rate = forms.DecimalField(
        label="税率（%，可选）", required=False, max_digits=5, decimal_places=2,
        min_value=Decimal("0"), max_value=Decimal("100"),
        widget=forms.NumberInput({"min": "0", "max": "100", "step": "0.01", "inputmode": "decimal"}),
        help_text="留空表示未指定税率，系统不猜测税额；填写后按价税合计反算税额（ROUND_HALF_UP）。")
    issued_on = forms.DateField(label="开票日期", widget=forms.DateInput(DATE))
    contract_reference = forms.CharField(
        label="合同或立项依据（可选）", max_length=120, required=False,
        help_text="留空沿用项目已登记的合同或立项依据；用于收款与合同关联一致。")
    note = forms.CharField(label="备注", max_length=240, required=False)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, member=member, **kwargs)
        if member is not None:
            self.fields["project"].queryset = billing_projects(member).exclude(
                stage="cancelled").select_related("manager__user")

    def clean_tax_rate(self):
        rate = self.cleaned_data.get("tax_rate")
        if rate is None:
            return None
        basis_points = rate * 100
        if basis_points != basis_points.to_integral_value():
            raise forms.ValidationError("税率最多两位小数")
        return int(basis_points)


class ReceiptForm(BillingForm):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), label="所属项目", required=False,
        empty_label="请选择项目（或勾选非项目收款）", help_text="收款必须归属项目；非项目收款请勾选下方“非项目收款”。")
    non_project = forms.BooleanField(label="非项目收款（显式不归属项目）", required=False)
    account = forms.ModelChoiceField(queryset=Account.objects.none(), label="资金账户")
    invoice = forms.ModelChoiceField(
        queryset=Invoice.objects.none(), label="关联发票（可选）", required=False,
        help_text="可部分收款；本次金额不能超过该发票未收金额。")
    amount = forms.DecimalField(label="收款金额（元）", max_digits=14, decimal_places=2,
                                min_value=Decimal("0.01"), widget=forms.NumberInput(MONEY))
    received_on = forms.DateField(label="收款日期", widget=forms.DateInput(DATE))
    counterparty = forms.CharField(label="客户/往来方（可选）", max_length=120, required=False,
                                   help_text="留空沿用项目客户名称。")
    reference = forms.CharField(
        label="银行流水号/来源编号", max_length=120,
        help_text="同一来源编号只能登记一次；重复提交会被拒绝。")
    note = forms.CharField(label="备注", max_length=240, required=False)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, member=member, **kwargs)
        if member is None:
            return
        if member.role != "owner":
            self.fields.pop("non_project")
        projects = billing_projects(member)
        self.fields["project"].queryset = projects.exclude(stage="cancelled").select_related("manager__user")
        self.fields["account"].queryset = Account.objects.filter(
            organization=member.organization, active=True)
        self.fields["account"].empty_label = "请选择资金账户"
        self.fields["invoice"].queryset = Invoice.objects.filter(
            organization=member.organization, project__in=projects,
            reverses__isnull=True, reversal__isnull=True).select_related("project").order_by("-issued_on", "-id")
        self.fields["invoice"].empty_label = "不关联发票（按项目收款）"
        self.fields["invoice"].label_from_instance = (
            lambda invoice: f"发票 {invoice.number} · {invoice.project.code} · ¥{invoice.amount_cents / 100:,.2f}")

    def clean(self):
        data = super().clean()
        project, non_project = data.get("project"), data.get("non_project", False)
        if non_project and project:
            self.add_error("project", "非项目收款不能再选择项目；请二选一")
        elif not non_project and not project:
            self.add_error("project", "请选择所属项目，或勾选“非项目收款”")
        if non_project and self.member is not None and self.member.role != "owner":
            self.add_error("non_project", "非项目收款须由公司负责人登记")
        invoice = data.get("invoice")
        if invoice and not project:
            self.add_error("invoice", "关联发票的收款必须归属项目")
        elif invoice and project and invoice.project_id != project.pk:
            self.add_error("invoice", "所选发票不属于该项目")
        return data


class VoidForm(BillingForm):
    reason = forms.CharField(label="作废原因", max_length=240, min_length=4,
                             widget=forms.Textarea(attrs={"rows": 2}))


class RefundForm(BillingForm):
    amount = forms.DecimalField(label="退款金额（元）", max_digits=14, decimal_places=2,
                                min_value=Decimal("0.01"), widget=forms.NumberInput(MONEY))
    reason = forms.CharField(label="退款原因", max_length=240, min_length=4,
                             widget=forms.Textarea(attrs={"rows": 2}))


def amount_cents(form, field="amount"):
    return yuan_to_cents(form.cleaned_data[field])
