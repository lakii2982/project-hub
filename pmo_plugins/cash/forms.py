"""Business inputs for cash facts; amounts become integer cents only on the server side."""
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.utils import timezone

MAX_CENTS = 10 ** 12


def amount_cents(value):
    """Exact yuan->cents conversion; bool/float are rejected by the caller's field contract."""
    if value is None:
        return None
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or amount > Decimal(MAX_CENTS) / 100:
        raise forms.ValidationError('金额须为非负数字，最多两位小数，且不超过100亿元')
    if amount.as_tuple().exponent < -2:
        raise forms.ValidationError('金额最多两位小数')
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class StrictForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        if not self.is_bound and 'request_token' in self.fields:
            self.fields['request_token'].initial = uuid.uuid4()

    def clean(self):
        result = super().clean()
        if set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise forms.ValidationError('包含不允许的字段，请刷新后重试')
        return result


class FundingAccountForm(StrictForm):
    account_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    name = forms.CharField(label='账户名称', max_length=120)
    masked_identifier = forms.CharField(label='脱敏账号', max_length=64, required=False,
                                        help_text='只登记脱敏账号，最多64字；不要填写完整卡号')
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class CashTransactionForm(StrictForm):
    transaction_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    funding_account = forms.ChoiceField(label='银行账户', choices=())
    transaction_date = forms.DateField(label='交易日期', widget=forms.DateInput(attrs={'type': 'date'}))
    direction = forms.ChoiceField(label='方向', choices=(('in', '收入'), ('out', '支出')))
    amount = forms.DecimalField(label='金额（元）', max_digits=14, decimal_places=2,
                                min_value=Decimal('0.01'),
                                widget=forms.NumberInput(attrs={'step': '0.01', 'inputmode': 'decimal'}))
    external_source_key = forms.CharField(label='银行流水来源编号', max_length=200,
                                          help_text='组织+账户内唯一；同一来源编号重复提交保持幂等')
    payee_kind = forms.ChoiceField(label='收款方识别', choices=(
        ('unidentified', '未识别'), ('counterparty', '客户/供应商'), ('own_account', '本公司账户')))
    counterparty = forms.ChoiceField(label='客户/供应商', required=False, choices=())
    own_account = forms.ChoiceField(label='本公司收款账户', required=False, choices=())
    evidence = forms.CharField(label='银行证据/依据编号', max_length=2000, widget=forms.Textarea,
                               help_text='登记真实银行回单/流水依据编号或说明，至少一条')
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, accounts=None, counterparties=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['funding_account'].choices = accounts or []
        self.fields['counterparty'].choices = [('', '请选择')] + list(counterparties or [])
        self.fields['own_account'].choices = [('', '请选择')] + list(accounts or [])
        if not self.is_bound:
            self.fields['transaction_date'].initial = timezone.localdate()

    def clean(self):
        data = super().clean()
        kind = data.get('payee_kind')
        if kind == 'counterparty' and not data.get('counterparty'):
            self.add_error('counterparty', '选择客户/供应商收款方时必须指定真实往来方')
        if kind == 'own_account' and not data.get('own_account'):
            self.add_error('own_account', '选择本公司账户收款方时必须指定账户')
        if data.get('transaction_date') and data['transaction_date'] > timezone.localdate():
            self.add_error('transaction_date', '交易日期不能在未来')
        return data


class IdentifyPartyForm(StrictForm):
    transaction_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.CharField(widget=forms.HiddenInput, initial='identify')
    payee_kind = forms.ChoiceField(label='识别类型', choices=(
        ('counterparty', '客户/供应商'), ('employee', '员工'), ('own_account', '本公司账户')))
    counterparty = forms.ChoiceField(label='客户/供应商', required=False, choices=())
    employee = forms.ChoiceField(label='员工', required=False, choices=())
    own_account = forms.ChoiceField(label='本公司账户', required=False, choices=())
    evidence = forms.CharField(label='匹配证据/依据编号', max_length=2000, widget=forms.Textarea)
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, counterparties=None, employees=None, accounts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['counterparty'].choices = [('', '请选择')] + list(counterparties or [])
        self.fields['employee'].choices = [('', '请选择')] + list(employees or [])
        self.fields['own_account'].choices = [('', '请选择')] + list(accounts or [])

    def clean(self):
        data = super().clean()
        kind = data.get('payee_kind')
        if kind not in {'counterparty', 'employee', 'own_account'}:
            self.add_error('payee_kind', '识别收款方时必须选择识别类型')
        elif not data.get(kind):
            self.add_error(kind, '请选择真实且同企业的识别对象')
        return data


class ReversePartyForm(StrictForm):
    transaction_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.CharField(widget=forms.HiddenInput, initial='reverse')
    original_match_ref = forms.ChoiceField(label='原匹配记录', choices=())
    reason = forms.CharField(label='反向匹配原因', max_length=1000, widget=forms.Textarea)
    evidence = forms.CharField(label='反向匹配证据/依据编号', max_length=2000, widget=forms.Textarea)
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, matches=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['original_match_ref'].choices = [('', '请选择原匹配记录')] + list(matches or [])


class FundingAccountListFilterForm(forms.Form):
    status = forms.ChoiceField(label='状态', required=False, choices=(
        ('', '全部状态'), ('draft', '草稿'), ('active', '已激活')))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class CashTransactionListFilterForm(forms.Form):
    funding_account = forms.ChoiceField(label='银行账户', required=False, choices=())
    direction = forms.ChoiceField(label='方向', required=False, choices=(
        ('', '全部方向'), ('in', '收入'), ('out', '支出')))
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, accounts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['funding_account'].choices = [('', '全部账户')] + list(accounts or [])
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')