"""PH-281 read-only membership / scope / authorization audit operations page."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render

from .access import current_membership, require_member
from . import identity_read_services as identity
from .identity_forms import IdentityAccessFilterForm

ALLOWED_PARAMS = {'as_of', 'member', 'since', 'page', 'page_size'}


def _workspace_view(fn):
    @login_required
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        request.member = current_membership(request)
        require_member(request.member)
        return fn(request, *args, **kwargs)
    return wrapped


@_workspace_view
def identity_access(request):
    """Read-only history page: no write form, no audit, no token secrets."""
    member = request.member
    error = None
    form = IdentityAccessFilterForm(request.GET)
    if set(request.GET) - ALLOWED_PARAMS:
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = '；'.join(sum((list(errors) for errors in form.errors.values()), [])) or '筛选条件无效'
    snapshot = audit = issues = None
    if error is None:
        try:
            snapshot = identity.member_access_snapshot(
                member, as_of=form.cleaned_data.get('as_of'),
                page=form.cleaned_data.get('page') or 1,
                page_size=form.cleaned_data.get('page_size') or 50)
            audit = identity.member_access_audit(
                member, member_id=form.cleaned_data.get('member'),
                since=form.cleaned_data.get('since'),
                as_of=form.cleaned_data.get('as_of'))
            issues = identity.member_access_issues(
                member, as_of=form.cleaned_data.get('as_of'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
        except PermissionDenied:
            raise
    return render(request, 'core/identity_access.html', {
        'nav': 'settings', 'snapshot': snapshot, 'audit': audit, 'issues': issues,
        'form': form, 'error': error,
        'page_url': '/modules/delivery/identity-access/',
    }, status=400 if error else 200)


def identity_access_entry(context, payload):
    """Plugin-route adapter: the SDK passes an EntryContext, the page reads request.GET."""
    return identity_access(context.request)
