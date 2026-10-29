import uuid
from decimal import Decimal
from django import forms
from django.forms import formset_factory
from django.utils import timezone
from core.models import Milestone
from .party_forms import StrictForm


def day(label, required=True):
    return forms.DateField(label=label, required=required, widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))


def money(label, required=True):
    return forms.DecimalField(label=label + '（元）', required=required, min_value=Decimal('0'), max_value=Decimal('10000000000'), max_digits=13, decimal_places=2)


class VersionForm(StrictForm):
    contract_id = forms.UUIDField(widget=forms.HiddenInput)
    version_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    effective_on = day('首次生效日期')
    contract_reference = forms.CharField(label='人工合同依据', max_length=240, help_text='填写合同编号、签署依据或经办记录；本次不上传附件。')


class MoneyForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Persisted candidate initials are cents; only unbound browser fields display yuan.
        if not self.is_bound:
            for name in self.fields:
                if name.endswith('_cents') and self.initial.get(name) is not None:
                    self.initial[name] = Decimal(self.initial[name]) / Decimal(100)

    def clean(self):
        data = super().clean()
        for name, value in list(data.items()):
            if name.endswith('_cents') and isinstance(value, Decimal):
                data[name] = int(value * Decimal(100))
        return data


class LineForm(MoneyForm):
    line_no = forms.IntegerField(label='明细行号', min_value=1)
    template = forms.ChoiceField(label='收费模板', choices=[('', '选择收费模板'), ('fixed_milestone', '固定价节点'), ('confirmed_time', '客户确认工时'), ('fixed_service_period', '固定服务期')])
    net_cents = money('净额')
    tax_cents = money('税额')
    gross_cents = money('含税额')
    net_cap_cents = money('累计净额上限')
    time_unit = forms.ChoiceField(label='计时单位', required=False, choices=[('', '计时模板填写'), ('hour', '小时'), ('workday', '人天')])
    unit_minutes = forms.IntegerField(label='每单位分钟数', min_value=1, max_value=1440, required=False)
    unit_price_cents = money('单位价', False)
    service_start = day('服务起日', False)
    service_end = day('服务止日', False)
    period_unit = forms.ChoiceField(label='服务期间单位', required=False, choices=[('', '服务模板填写'), ('calendar_month', '自然月'), ('calendar_day', '自然日')])
    partial_period_rule = forms.ChoiceField(label='部分期间规则', required=False, choices=[('', '服务模板填写'), ('no_partial', '不允许部分期间'), ('calendar_days', '已明确按自然日')])


class MilestoneForm(MoneyForm):
    line_no = forms.IntegerField(label='对应明细行号', min_value=1)
    milestone_id = forms.ModelChoiceField(label='已有项目节点', queryset=Milestone.objects.none())
    allocated_net_cents = money('节点分配净额')

    def __init__(self, *args, project_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['milestone_id'].queryset = Milestone.objects.filter(project_id=project_id)


class ScheduleForm(MoneyForm):
    line_no = forms.IntegerField(label='对应明细行号', min_value=1)
    coverage_start = day('计价区间起日')
    coverage_end = day('计价区间止日')
    allocated_net_cents = money('期间分配净额')


class PaymentForm(MoneyForm):
    line_no = forms.IntegerField(label='对应明细行号', min_value=1)
    key = forms.CharField(label='付款条款标识', max_length=80)
    condition_type = forms.ChoiceField(label='付款条件', choices=[('', '选择条件'), ('unconditional', '无条件'), ('conditional', '附条件'), ('warranty', '质保款')])
    gross_cents = money('付款含税额')
    due_date = day('到期日', False)
    release_condition = forms.CharField(label='释放条件', max_length=500, required=False)


class WarrantyForm(MoneyForm):
    line_no = forms.IntegerField(label='对应明细行号', min_value=1)
    payment_key = forms.CharField(label='所属质保付款条款标识', max_length=80)
    gross_cents = money('价款内质保金额')
    release_condition = forms.CharField(label='质保释放条件', max_length=500)
    release_on = day('释放日期', False)


class ActionForm(StrictForm):
    version_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)


class ReviewForm(ActionForm):
    decision = forms.ChoiceField(label='独立审批决定', choices=[('approve', '批准合同及配置规则'), ('reject', '拒绝并保留本版')])
    review_note = forms.CharField(label='审批说明', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}))


SECTIONS = [('lines', LineForm, '收费明细'), ('milestones', MilestoneForm, '固定价节点分配'),
            ('schedules', ScheduleForm, '服务期间分配'), ('payments', PaymentForm, '付款条件'), ('warranties', WarrantyForm, '价款内质保')]


def build_formsets(contract, data=None, content=None):
    initial = {name: [] for name, _, _ in SECTIONS}
    for line in (content or {}).get('lines', []):
        p = line['pricing_configuration']
        initial['lines'].append({**{k: v for k, v in line.items() if k not in {'pricing_configuration', 'payment_terms', 'warranty_terms'}},
                                **{k: v for k, v in p.items() if k not in {'milestones', 'service_schedule'}}})
        for key, values in [('milestones', p.get('milestones', [])), ('schedules', p.get('service_schedule', [])),
                            ('payments', line.get('payment_terms', [])), ('warranties', line.get('warranty_terms', []))]:
            initial[key].extend({'line_no': line['line_no'], **item} for item in values)
    sections = []
    for name, klass, title in SECTIONS:
        factory = formset_factory(klass, extra=2, max_num=1000, validate_max=True, can_delete=True)
        kwargs = {'form_kwargs': {'project_id': contract.project_id}} if name == 'milestones' else {}
        sections.append((name, title, factory(data, prefix=name, initial=initial[name], **kwargs)))
    return sections


def candidate_lines(sections):
    values = {name: [form.cleaned_data for form in group if form.cleaned_data and not form.cleaned_data.get('DELETE')]
              for name, _, group in sections}
    numbers = [line['line_no'] for line in values['lines']]
    if any(item['line_no'] not in numbers for name in ('milestones', 'schedules', 'payments', 'warranties') for item in values[name]):
        raise forms.ValidationError('分配或条款引用了不存在的明细行号')
    result = []
    for line in values['lines']:
        number, template = line['line_no'], line['template']
        config = {'template': template, 'net_cap_cents': line['net_cap_cents']}
        select = lambda key: [{k: (v.pk if key == 'milestones' and k == 'milestone_id' else v)
                              for k, v in item.items() if k not in {'line_no', 'DELETE'}}
                             for item in values[key] if item['line_no'] == number]
        if template == 'fixed_milestone':
            config['milestones'] = select('milestones')
        elif template == 'confirmed_time':
            config.update({k: line[k] for k in ('time_unit', 'unit_minutes', 'unit_price_cents')})
        else:
            config.update({k: line[k] for k in ('service_start', 'service_end', 'period_unit', 'partial_period_rule')})
            config['service_schedule'] = select('schedules')
        result.append({k: line[k] for k in ('line_no', 'net_cents', 'tax_cents', 'gross_cents')} |
                      {'pricing_configuration': config, 'payment_terms': select('payments'), 'warranty_terms': select('warranties')})
    return result
