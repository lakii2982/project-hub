"""PH-281 read-only filters for the identity/authorization operations view."""
from django import forms


class IdentityAccessFilterForm(forms.Form):
    as_of = forms.DateField(label='快照日期', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    member = forms.IntegerField(label='成员', required=False, min_value=1)
    since = forms.DateField(label='审计起始日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    page = forms.IntegerField(label='页码', required=False, min_value=1, initial=1)
    page_size = forms.IntegerField(label='每页', required=False, min_value=1, max_value=200,
                                   initial=50)
