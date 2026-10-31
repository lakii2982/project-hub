import uuid

from django import forms


class PeriodCloseForm(forms.Form):
    period = forms.CharField(max_length=7)
    expected_version = forms.IntegerField(min_value=1)
    reason = forms.CharField(required=False, max_length=500)
    request_token = forms.UUIDField(required=False)


class ProjectCloseForm(forms.Form):
    project = forms.IntegerField(min_value=1)
    expected_version = forms.IntegerField(min_value=1)
    allow_open_period = forms.BooleanField(required=False)
    reason = forms.CharField(required=False, max_length=500)
    request_token = forms.UUIDField(required=False)


class ReopenForm(forms.Form):
    period = forms.CharField(max_length=7, required=False)
    project = forms.IntegerField(min_value=1, required=False)
    expected_version = forms.IntegerField(min_value=1)
    reason = forms.CharField(max_length=500)
    request_token = forms.UUIDField(required=False)

    def clean(self):
        data = super().clean()
        if not data.get('period') and not data.get('project'):
            raise forms.ValidationError('须指定期间或项目')
        return data
