"""Business inputs for customer settlement; every amount is server-derived and read-only."""
import uuid
from decimal import Decimal

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import visible_projects

TEMPLATE_LABELS = {'fixed_milestone': '固定节点', 'fixed_service_period': '固定服务期'}
TERM_LABELS = {'due': '到期款', 'retention': '质保款'}


def yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def settlement_line_choices(member):
    """Only approved lines whose frozen terms cover the full tax-inclusive amount can be settled."""
    from pmo_plugins.contracts.approval_services import (approved_line_choices,
                                                         approved_terms_snapshot)
    result = []
    for snapshot in approved_line_choices(member):
        template = (snapshot['pricing_configuration'] or {}).get('template')
        if template not in TEMPLATE_LABELS:
            continue
        try:
            approved_terms_snapshot(member, snapshot['line_ref'], timezone.localdate())
        except (ValidationError, PermissionDenied):
            continue
        result.append((str(snapshot['line_id']),
                       f'{snapshot["contract_code"]} · 第 {snapshot["line_no"]} 行 · '
                       f'{TEMPLATE_LABELS[template]} · 含税 {yuan(snapshot["gross_cents"])} 元'))
    return result


def term_key(term):
    return f"{term['kind']}:{term['id']}"


def term_choices(terms_snapshot):
    return [(term_key(term),
             f'{TERM_LABELS.get(term["kind"], term["kind"])} · 含税 {yuan(term["gross_cents"])} 元 · '
             f'{"到期 " + term["due_date"] if term.get("due_date") else "缺少到期/释放日"}'
             + (f' · 释放条件：{term["release_condition"]}' if term.get('release_condition') else ''))
            for term in terms_snapshot['terms']]


class StrictForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        if not self.is_bound and 'request_token' in self.fields:
            self.fields['request_token'].initial = uuid.uuid4()

    def clean(self):
        result = super().clean()
        if set(self.data) - set(self.fields) - {'csrfmiddlewaretoken'}:
            raise forms.ValidationError('包含不允许的字段，请刷新后重试')
        return result


class SettlementForm(StrictForm):
    settlement_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    line = forms.ChoiceField(label='已批准合同明细', choices=())
    terms = forms.MultipleChoiceField(label='结算条款行', choices=(),
                                      widget=forms.CheckboxSelectMultiple)
    settled_on = forms.DateField(label='结算日期', widget=forms.DateInput(attrs={'type': 'date'}))
    evidence = forms.CharField(label='结算依据', max_length=2000, widget=forms.Textarea,
                               help_text='登记真实结算依据编号或说明；金额与条款由服务端按批准条款派生')
    request_token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, member, line_choices=None, term_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['line'].choices = line_choices or []
        self.fields['terms'].choices = term_choices or []
        if not self.is_bound:
            self.fields['settled_on'].initial = timezone.localdate()

    def clean_terms(self):
        values = self.cleaned_data.get('terms') or []
        if not values:
            raise forms.ValidationError('请至少选择一个完整条款行')
        if len(values) != len(set(values)):
            raise forms.ValidationError('条款行不能重复选择')
        return values

    def clean(self):
        data = super().clean()
        if data.get('settled_on') and data['settled_on'] > timezone.localdate():
            self.add_error('settled_on', '结算日期不能在未来')
        if not data.get('line'):
            self.add_error('line', '请选择已批准合同明细')
        return data


class ActionForm(StrictForm):
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    expected_version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ReasonForm(ActionForm):
    reason = forms.CharField(label='核对意见 / 操作原因', max_length=500, widget=forms.Textarea)


class ReviewForm(ReasonForm):
    decision = forms.ChoiceField(label='独立核对结论',
                                 choices=[('approve', '批准结算'), ('return', '退回修改'),
                                          ('reject', '拒绝批准')])


class SettlementListFilterForm(forms.Form):
    project = forms.ChoiceField(label='项目', required=False, choices=())
    status = forms.ChoiceField(label='状态', required=False, choices=(
        ('', '全部状态'), ('draft', '草稿'), ('submitted', '待独立批准'),
        ('active', '已生效'), ('withdrawn', '已撤回')))

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].choices = [('', '全部项目')] + [
            (str(project.pk), f'{project.code} · {project.name}')
            for project in visible_projects(member)]
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class AgingFilterForm(forms.Form):
    project = forms.ChoiceField(label='项目', required=False, choices=())
    as_of = forms.DateField(label='账龄基准日', required=False,
                            widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].choices = [('', '全部项目')] + [
            (str(project.pk), f'{project.code} · {project.name}')
            for project in visible_projects(member)]
        if not self.is_bound:
            self.fields['as_of'].initial = timezone.localdate()
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
