import uuid

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

