"""Business inputs: percentages are exact decimal basis points, never prices."""
import uuid
from decimal import Decimal
from django import forms
from django.forms import formset_factory
from core.access import visible_projects
from pmo_plugins.contracts.models import Contact, ContractLine


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


class LineChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.contract.project.name} · {obj.contract.contract_code} · 第{obj.line_no}行'


class AcceptanceForm(StrictForm):
    acceptance_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    line = LineChoice(label='已批准固定节点合同', queryset=ContractLine.objects.none())
    contact = forms.ModelChoiceField(label='客户联系人', queryset=Contact.objects.none())
    confirmed_on = forms.DateField(label='客户确认日期', widget=forms.DateInput(attrs={'type': 'date'}))
    channel = forms.CharField(label='确认渠道', max_length=100)
    reference = forms.CharField(label='客户依据编号', max_length=200)
    content = forms.CharField(label='客户确认内容', max_length=2000, widget=forms.Textarea)
    replaces_acceptance_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, member, line=None, **kwargs):
        super().__init__(*args, **kwargs)
        projects = visible_projects(member)
        if member.role == 'pm':
            projects = projects.filter(manager=member)
        self.fields['line'].queryset = ContractLine.objects.filter(organization=member.organization, status='approved', contract_version__status='approved', contract_version__contract__project__in=projects, contract_version__contract__status='active', policy_selection__pricing_configuration__template='fixed_milestone').distinct()
        if line:
            self.fields['contact'].queryset = Contact.objects.filter(organization=member.organization, party_id=line.contract.party_id, status='active')


class CoverageForm(forms.Form):
    milestone_id = forms.ChoiceField(label='合同节点')
    from_percent = forms.DecimalField(label='起始覆盖（%）', max_digits=5, decimal_places=2, min_value=Decimal('0'), max_value=Decimal('100'), initial=0)
    to_percent = forms.DecimalField(label='结束覆盖（%）', max_digits=5, decimal_places=2, min_value=Decimal('0'), max_value=Decimal('100'), initial=100)

    def clean(self):
        data = super().clean()
        if data.get('from_percent') is not None and data.get('to_percent') is not None and data['from_percent'] >= data['to_percent']:
            raise forms.ValidationError('结束覆盖必须大于起始覆盖')
        return data


CoverageFormSet = formset_factory(CoverageForm, extra=0, can_delete=True, min_num=1, validate_min=True, max_num=1000, validate_max=True, absolute_max=1000)


class ActionForm(StrictForm):
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ReasonForm(ActionForm):
    reason = forms.CharField(label='核对意见 / 操作原因', max_length=500, widget=forms.Textarea)


class ReviewForm(ReasonForm):
    decision = forms.ChoiceField(label='独立核对结论', choices=[('approve', '确认验收'), ('return', '退回修改'), ('reject', '拒绝确认')])


class OpsOverviewFilterForm(forms.Form):
    """Read-only filter for the cross-domain operations reconciliation overview."""
    as_of = forms.DateField(label='快照日期', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    period = forms.CharField(label='期间（YYYY-MM）', required=False, max_length=7)

    def clean_period(self):
        value = (self.cleaned_data.get('period') or '').strip()
        if value and (len(value) != 7 or value[4] != '-' or not value[:4].isdigit()
                      or not value[5:].isdigit() or not 1 <= int(value[5:]) <= 12):
            raise forms.ValidationError('期间须为有效的年月（YYYY-MM）')
        return value or None
