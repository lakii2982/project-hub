"""期间预检只读页面（PH-354）；零写、复用 owner 只读服务。"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render

from . import precheck_services
from .forms import PeriodPrecheckFilterForm


def period_precheck_page(context, payload):
    member = context.member
    allowed = {'as_of', 'period', 'project'}
    form = PeriodPrecheckFilterForm(payload)
    error = None
    if set(payload or ()) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = '；'.join(form.errors.get('__all__', [])) or '筛选条件无效'
    data = None
    if error is None:
        try:
            data = precheck_services.period_precheck_snapshot(
                member, project_id=form.cleaned_data.get('project'),
                period=form.cleaned_data.get('period'),
                as_of=form.cleaned_data.get('as_of'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
        except PermissionDenied:
            raise
    return render(context.request, 'insights/precheck.html', {
        'nav': 'insights', 'form': form, 'data': data, 'error': error,
        'page_url': '/modules/insights/period-precheck/'},
        status=400 if error else 200)
