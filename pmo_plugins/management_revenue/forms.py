"""Read-only filters for the management-revenue pairing snapshot page."""
from django import forms

from core.access import visible_projects
from core.models import Project


class RevenuePairingFilterForm(forms.Form):
    project = forms.ModelChoiceField(label='项目', required=False,
                                     queryset=Project.objects.none())
    period = forms.CharField(label='期间（YYYY-MM）', required=False, max_length=7)
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if member is None:
            return
        rows = visible_projects(member)
        if member.role == 'pm':
            rows = rows.filter(manager=member)
        self.fields['project'].queryset = rows.order_by('code', 'id')
