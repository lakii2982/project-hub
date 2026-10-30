from django import forms
from django.core.exceptions import ValidationError

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
