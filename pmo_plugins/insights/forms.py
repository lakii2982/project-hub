from django import forms


class PeriodPrecheckFilterForm(forms.Form):
    """只读筛选：期间预检快照（period.precheck.snapshot/v1）。"""
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    period = forms.CharField(label='期间（YYYY-MM）', required=False, max_length=7)
    project = forms.IntegerField(label='项目 ID', required=False, min_value=1)

    def clean_period(self):
        value = (self.cleaned_data.get('period') or '').strip()
        if value and (len(value) != 7 or value[4] != '-' or not value[:4].isdigit()
                      or not value[5:].isdigit() or not 1 <= int(value[5:]) <= 12):
            raise forms.ValidationError('期间须为有效的年月（YYYY-MM）')
        return value or None
