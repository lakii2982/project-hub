"""Notification inbox browser handlers; only personal state POSTs are allowed."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix
from django.views.decorators.http import require_GET, require_POST

from . import notification_services as services
from .notification_forms import NotificationActionForm, NotificationFilterForm
from .views import workspace_view


def _render(request, form, *, error='', status=200):
    snapshot = [] if error else services.notification_snapshot(
        request.member, status=form.cleaned_data.get('status') if form.is_bound else None,
        include_ignored=form.cleaned_data.get('status') in {'ignored', 'all'}
        if form.is_bound else False)
    return render(request, 'core/notifications.html', {
        'nav': 'notifications', 'form': form, 'notifications': snapshot,
        'error': error, 'script_prefix': get_script_prefix(),
    }, status=status)


@workspace_view
@require_GET
def notification_list(request):
    form = NotificationFilterForm(request.GET or None)
    if set(request.GET) - {'status'}:
        return _render(request, form, error='包含不允许的查询参数，请刷新后重试', status=400)
    if form.is_bound and not form.is_valid():
        error = '；'.join(message for errors in form.errors.values() for message in errors)
        return _render(request, form, error=error, status=400)
    return _render(request, form)


@workspace_view
@require_POST
def notification_sync(request):
    form = NotificationActionForm(request.POST)
    if not form.is_valid():
        list_form = NotificationFilterForm()
        return _render(request, list_form, error='包含不允许的字段，请刷新后重试', status=400)
    result = services.sync_notifications(request.member)
    messages.success(request, f'提醒已同步，新增 {result["created"]} 条；外部发送 0')
    return redirect('notification-list')


@workspace_view
@require_POST
def notification_mark(request, pk, action):
    form = NotificationActionForm(request.POST)
    if not form.is_valid():
        return _render(request, NotificationFilterForm(),
                       error='包含不允许的字段，请刷新后重试', status=400)
    try:
        services.mark_notification(request.member, pk, action=action)
    except ValidationError as exc:
        return _render(request, NotificationFilterForm(),
                       error='；'.join(exc.messages), status=400)
    except PermissionDenied:
        raise
    messages.success(request, '已标记为已读' if action == 'read' else '已忽略；可从历史筛选恢复查看')
    return redirect('notification-list')
