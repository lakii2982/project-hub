import uuid

from django import forms


class TaxAdjustmentForm(forms.Form):
    qualification = forms.UUIDField()
    request_token = forms.UUIDField(required=False)
    reason = forms.CharField(required=False, max_length=500)


class TaxReversalForm(forms.Form):
    batch = forms.UUIDField()
    request_token = forms.UUIDField(required=False)
    reason = forms.CharField(max_length=500)
