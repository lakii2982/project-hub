import uuid
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.models import Project

from .models import Contract, Counterparty, normalize_display
from .party_forms import StrictForm


class ContractDraftForm(StrictForm):
    contract_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    project = forms.ModelChoiceField(label="所属项目", queryset=Project.objects.none())
    party = forms.ModelChoiceField(label="合同客户", queryset=Counterparty.objects.none())
    contract_code = forms.CharField(label="合同编号", max_length=80)
    signed_on = forms.DateField(
        label="签署日期（可选）",
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        help_text="不能填写未来日期；签署日期不代表合同已生效。",
    )
    request_token = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["project"].queryset = Project.objects.filter(organization=member.organization).order_by("code")
        self.fields["party"].queryset = Counterparty.objects.filter(
            organization=member.organization,
            status=Counterparty.Status.ACTIVE,
            active_from__lte=today,
        ).filter(Q(inactive_on__isnull=True) | Q(inactive_on__gte=today)).order_by("display_name")
        candidates = self.fields["party"].queryset
        self.fields["party"].queryset = candidates.filter(pk__in=[p.pk for p in candidates if "customer" in p.roles])
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()

    def clean_contract_code(self):
        return normalize_display(self.cleaned_data["contract_code"])

    def clean_signed_on(self):
        value = self.cleaned_data.get("signed_on")
        if value and value > timezone.localdate():
            raise forms.ValidationError("签署日期不能在未来")
        return value

    def business_values(self):
        data = self.cleaned_data
        return {
            "project_id": data["project"].pk,
            "party_id": data["party"].pk,
            "contract_code": data["contract_code"],
            "signed_on": data.get("signed_on"),
        }



class ContractChangeForm(StrictForm):
    contract_id = forms.UUIDField(widget=forms.HiddenInput)
    change_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    change_type = forms.ChoiceField(label='变更类型', choices=[
        ('add_scope', '新增范围'), ('reduce_scope', '缩减范围'), ('price_change', '价格变更'),
        ('extension', '延期'), ('termination', '终止')])
    requested_on = forms.DateField(label='变更生效日期', widget=forms.DateInput(attrs={'type': 'date'}))
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class ContractChangeDecisionForm(StrictForm):
    change_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    decision = forms.ChoiceField(label='独立决策', choices=[('approved', '批准变更'), ('rejected', '驳回变更'), ('withdrawn', '撤回变更')])
    note = forms.CharField(label='决策说明（驳回必填）', max_length=500, required=False, widget=forms.Textarea(attrs={'rows': 2}))
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class TermsSnapshotFilterForm(forms.Form):
    """Read-only filter for the approved terms snapshot page."""

    contract = forms.ModelChoiceField(label='合同', required=False, queryset=Contract.objects.none())
    as_of = forms.DateField(label='快照日期', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        rows = Contract.objects.filter(organization=member.organization) if member else Contract.objects.none()
        if member is not None and member.role == 'pm':
            from core.access import visible_projects
            rows = rows.filter(project__in=visible_projects(member))
        self.fields['contract'].queryset = rows.select_related('project').order_by('contract_code', 'id')


class FxRateForm(StrictForm):
    """Owner/finance input for an append-only FX rate snapshot."""

    quote_currency = forms.CharField(label='原币种（ISO 4217）', max_length=3)
    rate = forms.DecimalField(label='汇率（1 原币 = ? CNY）', max_digits=18,
                              decimal_places=8, min_value=Decimal('0.00000001'))
    effective_on = forms.DateField(label='生效日期', widget=forms.DateInput(attrs={'type': 'date'}))
    source = forms.ChoiceField(label='来源', choices=[('manual', '手工'), ('bank', '银行'),
                                                      ('provider', '供应商')])
    reference = forms.CharField(label='参考', max_length=240, required=False)

    def clean_quote_currency(self):
        value = (self.cleaned_data.get('quote_currency') or '').strip().upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise forms.ValidationError('原币种须为三位 ISO 4217 代码')
        if value == 'CNY':
            raise forms.ValidationError('原币种不能与记账本位币相同')
        return value

    def clean_effective_on(self):
        value = self.cleaned_data.get('effective_on')
        if value and value > timezone.localdate():
            raise forms.ValidationError('汇率生效日期不能在未来')
        return value
