import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.imports import HEADERS

TEMPLATE_LABELS = {'time': '工时', 'expense': '费用', 'plan': '现金计划'}


class ImportPreviewForm(forms.Form):
    kind = forms.ChoiceField(
        label='导入类型', required=False,
        choices=[('', '自动识别')] + [(kind, TEMPLATE_LABELS.get(kind, kind))
                                     for kind in HEADERS])
    file = forms.FileField(label='CSV文件')

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > 1024 * 1024:
            raise ValidationError('CSV文件不能超过1MB')
        return upload


class ImportSubmitForm(forms.Form):
    """Commit inputs only: no amount/status/approver/ledger fields are ever accepted."""

    preview_ref = forms.UUIDField(label='预览标识')
    rows_digest = forms.CharField(label='预览行摘要', max_length=64)
    precheck_digest = forms.CharField(label='期间预检摘要', max_length=64)
    precheck_as_of_date = forms.DateField(
        label='预检基准日', widget=forms.DateInput(attrs={'type': 'date'}))
    period = forms.CharField(label='导入期间（YYYY-MM）', max_length=7)
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(label='预览版本', min_value=1)
    dry_run = forms.BooleanField(label='仅预演（不写收据与业务事实）', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        if not self.is_bound:
            today = timezone.localdate()
            self.fields['period'].initial = today.strftime('%Y-%m')
            self.fields['precheck_as_of_date'].initial = today
            self.fields['dry_run'].initial = True
            self.fields['request_token'].initial = uuid.uuid4()

    def clean(self):
        data = super().clean()
        if set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise ValidationError('包含不允许的字段，请刷新后重试')
        for field in ('rows_digest', 'precheck_digest'):
            value = (data.get(field) or '').strip().lower()
            if value and (len(value) != 64
                          or any(char not in '0123456789abcdef' for char in value)):
                self.add_error(field, '摘要须为64位十六进制sha256')
        period = (data.get('period') or '').strip()
        if period and (len(period) != 7 or period[4] != '-'
                       or not period[:4].isdigit() or not period[5:].isdigit()
                       or not 1 <= int(period[5:]) <= 12):
            self.add_error('period', '期间须为有效的年月（YYYY-MM）')
        return data
