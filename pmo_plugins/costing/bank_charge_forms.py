"""Typed browser inputs for bank-fee drafts; no client accounting or approval fields."""
from decimal import Decimal
import uuid

from django import forms
from pmo_plugins.contracts.models import Counterparty
from core.models import Project


MAX_YUAN = Decimal('10000000000.00')


class BankChargeStrictForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault('class', 'form-control')
        if not self.is_bound and 'request_token' in self.fields:
            self.fields['request_token'].initial = uuid.uuid4()

    def clean(self):
        data = super().clean()
        if set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise forms.ValidationError('包含不允许的字段，请刷新后重试')
        return data


class BankChargeForm(BankChargeStrictForm):
    charge_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    cash_transaction_id = forms.UUIDField(label='已过账收款流水标识')
    gross = forms.DecimalField(label='手续费金额（元）', max_digits=13, decimal_places=2,
                               min_value=Decimal('0.01'), max_value=MAX_YUAN,
                               help_text='正数、最多两位小数；不得超过该现金流水已过账金额。')
    tax = forms.DecimalField(label='票面税额（元，可留空）', max_digits=13, decimal_places=2,
                             min_value=Decimal('0.00'), max_value=MAX_YUAN, required=False,
                             help_text='未知税额保持留空，不以0冒充。')
    counterparty = forms.ModelChoiceField(label='手续费收取方（可留空）',
                                          queryset=Counterparty.objects.none(), required=False,
                                          help_text='银行或手续费供应商；与收款客户不是同一概念。')
    project = forms.ModelChoiceField(label='项目范围', queryset=Project.objects.none())
    scope_kind = forms.ChoiceField(label='范围类型', choices=[('project', '项目')],
                                   initial='project', widget=forms.HiddenInput)
    fee_bearer = forms.ChoiceField(label='承担方', choices=[('company', '公司承担')],
                                   initial='company', widget=forms.HiddenInput)
    occurred_on = forms.DateField(label='手续费发生日（可留空，默认流水日期）', required=False,
                                  widget=forms.DateInput(attrs={'type': 'date'}))
    posting_period = forms.RegexField(label='过账期间（可留空）',
                                      regex=r'^\d{4}-(0[1-9]|1[0-2])$', required=False,
                                      help_text='格式：2026-10；必须与流水所属月份一致。')
    evidence = forms.CharField(label='真实依据', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}),
                               help_text='支持附件引用或1至2000字依据说明；本片不代表已批准或已过账。')
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['counterparty'].queryset = Counterparty.objects.filter(
            organization=member.organization).order_by('display_name', 'pk')
        self.fields['project'].queryset = Project.objects.filter(
            organization=member.organization).order_by('pk')

    @staticmethod
    def amount_cents(value):
        return int(value * 100)
