"""Browser forms for the time billable-confirmation owner; business rules stay in services."""
import json
import uuid

from django import forms
from django.utils import timezone

from . import services


class BillableConfirmationForm(forms.Form):
    confirmation_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    timesheet_id = forms.ChoiceField(label='已批准工时')
    contract_line_id = forms.ChoiceField(label='客户确认工时合同条款')
    confirmed_minutes = forms.IntegerField(label='客户确认分钟', min_value=1, max_value=1440)
    confirmed_on = forms.DateField(label='确认日期', widget=forms.DateInput(attrs={'type': 'date'}))
    evidence = forms.CharField(label='客户确认证据（JSON 清单）', widget=forms.Textarea(attrs={'rows': 3}),
                               help_text='至少一条证据，例如 [{"kind":"customer_email","reference":"客户邮件 2026-10-31"}]')

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        timesheets = services.approved_timesheet_choices(member) if member else []
        lines = services.contract_line_choices(member) if member else []
        self.fields['timesheet_id'].choices = [
            (str(row.pk), f'{row.work_date} · 项目{row.project.code} · {row.employee.name} · {row.minutes}分钟')
            for row in timesheets]
        self.fields['contract_line_id'].choices = [(row['id'], row['label']) for row in lines]
        if not self.is_bound:
            self.fields['request_token'].initial = uuid.uuid4()
            self.fields['confirmed_on'].initial = timezone.localdate()

    def clean_evidence(self):
        raw = self.cleaned_data['evidence']
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise forms.ValidationError('证据须为 JSON 清单')
        if not isinstance(value, list):
            raise forms.ValidationError('证据须为 JSON 清单')
        return value


class ConfirmForm(forms.Form):
    confirmation_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)
