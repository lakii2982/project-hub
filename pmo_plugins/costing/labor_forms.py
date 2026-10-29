"""Typed browser inputs for monthly labor; no client accounting snapshots."""
import uuid
from decimal import Decimal
from django import forms
from django.forms import formset_factory
from core.models import Employee


class StrictForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault('class', 'form-control')
        if not self.is_bound and 'request_token' in self.fields:
            self.fields['request_token'].initial = uuid.uuid4()

    def clean(self):
        data = super().clean()
        if set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise forms.ValidationError('包含不允许的字段，请刷新后重试')
        return data


class ActionForm(StrictForm):
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ReasonForm(ActionForm):
    reason = forms.CharField(label='撤销原因', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))
    confirmed = forms.BooleanField(label='我已核对原始来源，确认完整撤销此项操作')


class SalaryForm(StrictForm):
    pool_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    employee = forms.ModelChoiceField(label='员工', queryset=Employee.objects.none())
    period = forms.RegexField(label='工资月份', regex=r'^\d{4}-(0[1-9]|1[0-2])$', help_text='格式：2026-10')
    covered_through = forms.DateField(label='已发生覆盖截止日', widget=forms.DateInput(attrs={'type': 'date'}))
    valuation_state = forms.ChoiceField(label='发生金额口径', choices=[('incurred_provisional', '已发生暂估'), ('reconciled_actual', '已核实实际')])
    source_evidence = forms.CharField(label='发生依据说明', max_length=500, widget=forms.Textarea(attrs={'rows': 3}), help_text='填写真实工资构成及发生依据；内部批准不代表工资已支付。')
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = Employee.objects.filter(organization=member.organization, active=True).order_by('name', 'pk')


class ComponentForm(forms.Form):
    code = forms.CharField(label='工资构成名称', max_length=80)
    amount = forms.DecimalField(label='已发生金额（元）', max_digits=13, decimal_places=2, min_value=Decimal('0'), max_value=Decimal('10000000000.00'))


ComponentFormSet = formset_factory(ComponentForm, extra=3, min_num=1, validate_min=True, max_num=30, validate_max=True, absolute_max=30)


class CapacityForm(ActionForm):
    denominator_minutes = forms.IntegerField(label='覆盖期有效产能（分钟）', min_value=1, max_value=1000000)
    evidence = forms.CharField(label='容量核对依据', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class CutoverForm(StrictForm):
    effective_from = forms.DateField(label='人工模式启用月首', widget=forms.DateInput(attrs={'type': 'date'}), help_text='必须为无旧模式已批成本或待批投入冲突的月首。未来启用安排不允许提前发生工资或工时。')
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class ReviewForm(ActionForm):
    review_note = forms.CharField(label='核对意见', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))


class AllocateForm(ActionForm):
    capacity_policy_id = forms.UUIDField(label='已批准容量政策', widget=forms.Select)
