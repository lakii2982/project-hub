"""Q-36 expense entry forms; all money parsing stays Decimal/ROUND_HALF_UP."""
import uuid
from decimal import Decimal
from pathlib import Path

from django import forms
from django.utils import timezone

from core.forms import CATEGORIES, BaseForm
from core.models import Project
from pmo_plugins.billing.models import Account

from .services import (ATTACHMENT_MAX_BYTES, ATTACHMENT_SUFFIXES, to_cents,
                       writable_projects)

DATE = {"type": "date"}
MONEY = {"min": "0.01", "step": "0.01", "inputmode": "decimal"}


class ExpenseFields(BaseForm):
    category = forms.ChoiceField(label="费用类别", choices=CATEGORIES)
    amount = forms.DecimalField(label="支出金额（元，含采购/外包）", max_digits=14,
                                decimal_places=2, min_value=Decimal("0.01"),
                                widget=forms.NumberInput(attrs=MONEY))
    occurred_on = forms.DateField(label="发生日期", initial=timezone.localdate,
                                  widget=forms.DateInput(attrs=DATE))
    expected_pay_on = forms.DateField(
        label="预计付款日（未填按发生日期）", required=False,
        widget=forms.DateInput(attrs=DATE),
        help_text="只影响未付金额进入未来 8 周计划的日期，不影响成本日期")
    counterparty = forms.CharField(label="供应商/往来方", max_length=120, required=False)
    note = forms.CharField(label="费用说明", max_length=8000, widget=forms.Textarea,
                           help_text="说明采购/外包内容与依据；附件可选")
    attachment = forms.FileField(label="附件（可选）", required=False,
                                 help_text="PDF、图片、文本或 Office 文件，10MB以内")

    def clean(self):
        data = super().clean()
        self._clean_attachment()
        return data

    def _clean_attachment(self):
        uploaded = self.cleaned_data.get("attachment")
        if not uploaded:
            return
        suffix = Path(getattr(uploaded, "name", "") or "").suffix.lower()
        if uploaded.size > ATTACHMENT_MAX_BYTES or suffix not in ATTACHMENT_SUFFIXES:
            self.add_error("attachment", "附件须为10MB以内的PDF、图片、文本或Office凭证")

    def amount_cents(self):
        return to_cents(self.cleaned_data["amount"])


class ExpenseForm(ExpenseFields):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label="所属项目",
                                     empty_label="请选择项目")
    submission_token = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, member=None, project=None, **kwargs):
        super().__init__(*args, member=member, **kwargs)
        projects = writable_projects(member)
        self.fields["project"].queryset = projects.order_by("code")
        if project is not None:
            self.fields["project"].initial = project.pk
            self.fields["project"].queryset = projects.filter(pk=project.pk)

    def clean(self):
        data = super().clean()
        project = data.get("project")
        if project is not None and project.stage in {"closed", "cancelled", "on_hold"}:
            self.add_error("project", "项目已结束或暂停；登记费用前请由负责人恢复项目")
        return data


class CorrectionForm(ExpenseFields):
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["note"].required = True


class PaymentForm(BaseForm):
    account = forms.ModelChoiceField(
        queryset=Account.objects.none(), label="资金账户", empty_label="请选择资金账户",
        help_text="付款从该账户余额扣减（余额 = 期初 + 收款 − 付款）。")
    amount = forms.DecimalField(label="本次付款金额（元）", max_digits=14,
                                decimal_places=2, min_value=Decimal("0.01"),
                                widget=forms.NumberInput(attrs=MONEY))
    paid_on = forms.DateField(label="实际付款日期", initial=timezone.localdate,
                              widget=forms.DateInput(attrs=DATE))
    note = forms.CharField(label="付款说明", max_length=240, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))
    submission_token = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, member=member, **kwargs)
        from .services import payment_accounts
        accounts = payment_accounts(member) if member is not None else Account.objects.none()
        self.fields["account"].queryset = accounts.order_by("id")
        if not accounts.exists():
            self.fields["account"].help_text = "还没有资金账户；请先在现金页新建账户再登记付款。"

    def amount_cents(self):
        return to_cents(self.cleaned_data["amount"])
