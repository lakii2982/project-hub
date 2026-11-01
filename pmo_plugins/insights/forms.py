import uuid

from django import forms


def _valid_period(value):
    if value and (len(value) != 7 or value[4] != '-' or not value[:4].isdigit()
                  or not value[5:].isdigit() or not 1 <= int(value[5:]) <= 12):
        raise forms.ValidationError('期间须为有效的年月（YYYY-MM）')
    return value or None


class PeriodPrecheckFilterForm(forms.Form):
    """只读筛选：期间预检快照（period.precheck.snapshot/v1）。"""
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    period = forms.CharField(label='期间（YYYY-MM）', required=False, max_length=7)
    project = forms.IntegerField(label='项目 ID', required=False, min_value=1)

    def clean_period(self):
        return _valid_period((self.cleaned_data.get('period') or '').strip())


class ReconciliationFilterForm(forms.Form):
    """只读筛选：核对差异单 canonical 投影。"""
    period = forms.CharField(label='期间', required=False, max_length=7)
    status = forms.ChoiceField(label='状态', required=False, choices=(
        ('', '全部'), ('draft', '草稿'), ('pending', '待确认'),
        ('approved', '已确认'), ('rejected', '已驳回'),
        ('superseded', '已替代'), ('closed', '已结案')))
    owner = forms.IntegerField(label='责任人成员 ID', required=False, min_value=1)
    project = forms.IntegerField(label='项目 ID', required=False, min_value=1)
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))

    def clean_period(self):
        return _valid_period((self.cleaned_data.get('period') or '').strip())


class ReconciliationRefreshForm(forms.Form):
    """刷新差异单（insights.reconciliation.refresh/v1）；默认预演。"""
    period = forms.CharField(label='期间（YYYY-MM）', max_length=7)
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))
    request_token = forms.UUIDField(widget=forms.HiddenInput())
    confirm = forms.BooleanField(label='确认写入差异单', required=False)

    def clean_period(self):
        return _valid_period((self.cleaned_data.get('period') or '').strip())


class ReconciliationUpdateForm(forms.Form):
    """差异单状态更新（insights.reconciliation.case.update/v1）；默认预演。"""
    case_id = forms.UUIDField(label='差异单 ID', widget=forms.HiddenInput())
    decision = forms.ChoiceField(label='变更', choices=(
        ('pending', '提交待确认'), ('approved', '确认通过'),
        ('rejected', '驳回（终态）'), ('closed', '结案')))
    note = forms.CharField(label='处理说明', required=False, max_length=2000,
                           widget=forms.Textarea(attrs={'rows': 3}))
    expected_version = forms.IntegerField(label='期望版本', min_value=1,
                                          widget=forms.HiddenInput())
    request_token = forms.UUIDField(widget=forms.HiddenInput())
    confirm = forms.BooleanField(label='确认写入状态', required=False)


class WorkbenchFilterForm(forms.Form):
    """只读项目工作台筛选。"""
    project = forms.IntegerField(label='项目 ID', min_value=1)
    as_of = forms.DateField(label='基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))


def new_token():
    return uuid.uuid4()
