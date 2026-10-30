"""Business inputs for service-period confirmation; prices and months stay server-derived."""
import uuid

from django import forms

from core.access import visible_projects
from pmo_plugins.contracts.models import Contact, ContractLine

from .forms import ActionForm, LineChoice, ReasonForm, ReviewForm, StrictForm


class ServiceConfirmationForm(StrictForm):
    confirmation_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    line = LineChoice(label='已批准固定服务期合同', queryset=ContractLine.objects.none())
    contact = forms.ModelChoiceField(label='客户联系人', queryset=Contact.objects.none())
    confirmed_on = forms.DateField(label='客户确认日期', widget=forms.DateInput(attrs={'type': 'date'}))
    coverage_start = forms.DateField(label='服务覆盖起始日', widget=forms.DateInput(attrs={'type': 'date'}))
    coverage_end = forms.DateField(label='服务覆盖结束日', widget=forms.DateInput(attrs={'type': 'date'}))
    channel = forms.CharField(label='确认渠道', max_length=100)
    reference = forms.CharField(label='客户依据编号', max_length=200)
    content = forms.CharField(label='客户确认内容', max_length=2000, widget=forms.Textarea)
    replaces_confirmation_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, member, line=None, **kwargs):
        super().__init__(*args, **kwargs)
        projects = visible_projects(member)
        if member.role == 'pm':
            projects = projects.filter(manager=member)
        self.fields['line'].queryset = ContractLine.objects.filter(
            organization=member.organization, status='approved',
            contract_version__status='approved', contract_version__contract__project__in=projects,
            contract_version__contract__status='active',
            policy_selection__pricing_configuration__template='fixed_service_period').distinct()
        if line:
            self.fields['contact'].queryset = Contact.objects.filter(
                organization=member.organization, party_id=line.contract.party_id, status='active')

    def clean(self):
        data = super().clean()
        start, end, confirmed = data.get('coverage_start'), data.get('coverage_end'), data.get('confirmed_on')
        if start and end and start > end:
            self.add_error('coverage_end', '服务覆盖结束日不能早于起始日')
        if end and confirmed and end > confirmed:
            self.add_error('confirmed_on', '客户确认日期不能早于服务覆盖结束日')
        return data


class RecognizeMonthForm(ActionForm):
    service_month = forms.CharField(label='服务月', max_length=7, widget=forms.HiddenInput)


class ReverseIncomeForm(ReasonForm):
    recognition_id = forms.UUIDField(widget=forms.HiddenInput)
