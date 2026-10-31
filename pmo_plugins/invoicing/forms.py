import json

from django import forms

from pmo_plugins.contracts.models import Counterparty


class InvoiceForm(forms.Form):
    direction = forms.ChoiceField(choices=[('sales', '销项'), ('purchase', '进项')])
    counterparty = forms.ModelChoiceField(queryset=Counterparty.objects.none())
    invoice_number = forms.CharField(max_length=80)
    invoice_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    gross_cents = forms.IntegerField(min_value=1)
    net_cents = forms.IntegerField(min_value=0)
    tax_cents = forms.IntegerField(min_value=0)
    evidence = forms.CharField(required=False, widget=forms.Textarea)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if member is not None:
            self.fields['counterparty'].queryset = Counterparty.objects.filter(
                organization=member.organization, status='active').order_by('display_name')

    def clean_evidence(self):
        value = (self.cleaned_data.get('evidence') or '').strip()
        if not value:
            return [{'kind': 'invoice_document', 'evidence_id': 'manual'}]
        try:
            parsed = json.loads(value)
        except ValueError:
            raise forms.ValidationError('证据须为JSON数组')
        if not isinstance(parsed, list) or not parsed:
            raise forms.ValidationError('证据须为非空JSON数组')
        return parsed


class AllocateForm(forms.Form):
    invoice_id = forms.UUIDField()
    expected_version = forms.IntegerField(min_value=1)
    lines = forms.CharField(widget=forms.Textarea)

    def clean_lines(self):
        try:
            value = json.loads(self.cleaned_data['lines'])
        except (TypeError, ValueError):
            raise forms.ValidationError('发票行须为JSON数组')
        if not isinstance(value, list) or not value:
            raise forms.ValidationError('发票行须为非空JSON数组')
        return value


class ReverseForm(forms.Form):
    invoice_id = forms.UUIDField()
    expected_version = forms.IntegerField(min_value=1)
    reason = forms.CharField(max_length=500)
    reversal_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    line_reversals = forms.CharField(required=False, widget=forms.Textarea)
    allocation_reversals = forms.CharField(required=False, widget=forms.Textarea)

    def _json(self, name):
        raw = (self.cleaned_data.get(name) or '').strip()
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            raise forms.ValidationError('须为JSON数组')
        if not isinstance(value, list):
            raise forms.ValidationError('须为JSON数组')
        return value

    def clean_line_reversals(self):
        return self._json('line_reversals')

    def clean_allocation_reversals(self):
        return self._json('allocation_reversals')


class InvoiceReconciliationFilterForm(forms.Form):
    """Read-only filters for the PH-325 invoice pairing page."""

    project = forms.CharField(required=False)
    as_of = forms.DateField(required=False)
    invoice = forms.CharField(required=False)

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = member
        if member is not None:
            from core.access import visible_projects
            from core.models import Project
            projects = (visible_projects(member) if member.role == 'pm'
                        else Project.objects.filter(organization=member.organization))
            self.fields['project'] = forms.ModelChoiceField(
                queryset=projects.order_by('name'), required=False,
                label='项目')
