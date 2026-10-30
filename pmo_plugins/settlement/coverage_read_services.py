"""PH-283 read-only receivable coverage reconciliation: open-item remaining vs active
clearances vs refund/return coverage and cross-owner cash references.

Strictly read-only: derives projections from existing settlement/cash owner facts, writes no
clearance/refund/cash/ledger/audit fact, adds no table/migration and does not implement Offset
(PH-241 is in_review, so offset_implemented stays False and offset_cents is displayed as 0).
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.utils import timezone

from core.access import require_project, visible_projects
from core.models import Project

from .models import Clearance, OpenItem
from .receivable_services import _fresh

READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
MAX_PAGE = 200


def _day(as_of):
    if as_of is None:
        return timezone.localdate()
    if isinstance(as_of, date):
        return as_of
    try:
        return date.fromisoformat(str(as_of))
    except (TypeError, ValueError):
        raise ValidationError('as_of 须为YYYY-MM-DD日期')


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _ref(value, kind):
    return {'namespace': 'settlement', 'kind': kind, 'id': str(value.pk),
            'version': getattr(value, 'version', 1)}


def _issue(issues, code, item, **details):
    entry = {'issue_code': code, 'open_item_ref': item.item_ref,
             'open_item_id': str(item.pk),
             'project_id': str(item.project_id),
             'expected': details.pop('expected', None),
             'actual': details.pop('actual', None)}
    entry.update(details)
    issues.append(entry)


def _visible_items(member, *, project_id=None):
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    rows = OpenItem.objects.filter(
        organization_id=member.organization_id, project__in=projects).select_related(
        'project', 'counterparty', 'settlement')
    if project_id is not None:
        try:
            project = Project.objects.get(
                organization_id=member.organization_id, pk=project_id)
        except (Project.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied('项目不存在或无权访问')
        require_project(member, project)
        if member.role == 'pm' and project.manager_id != member.pk:
            raise PermissionDenied('项目经理只能查看自己管理项目的未清覆盖')
        rows = rows.filter(project=project)
    return rows


def _cash_for_clearances(clearances):
    from pmo_plugins.cash.models import CashTransaction
    ids = {row.cash_transaction_id for row in clearances}
    if not ids:
        return {}
    return {row.pk: row for row in CashTransaction.objects.filter(pk__in=ids)}


def _refund_totals(cash_ids):
    from pmo_plugins.cash.models import BankReturn, Refund
    if not cash_ids:
        return {}, {}
    refunded = {row['original_cash_transaction_id']: row['total'] for row in
                Refund.objects.filter(original_cash_transaction_id__in=cash_ids).values(
                    'original_cash_transaction_id').annotate(total=Sum('gross_cents'))}
    returned = {row['original_cash_transaction_id']: row['total'] for row in
                BankReturn.objects.filter(original_cash_transaction_id__in=cash_ids).values(
                    'original_cash_transaction_id').annotate(total=Sum('gross_cents'))}
    return refunded, returned


def _item_bundle(item):
    clearances = list(Clearance.objects.filter(
        organization_id=item.organization_id, open_item=item).select_related(
        'open_item__project').order_by('created_at', 'id'))
    active = [row for row in clearances if row.status == 'active']
    cash = _cash_for_clearances(clearances)
    refunded, returned = _refund_totals(set(cash))
    cleared = sum(row.cleared_cents for row in active)
    # PH-241 Offset is in_review: never read the Offset table, display 0 / not implemented.
    offset = 0
    refunded_total = sum(refunded.get(row.cash_transaction_id, 0) for row in active)
    returned_total = sum(returned.get(row.cash_transaction_id, 0) for row in active)
    # Coverage remaining deducts refund/return coverage as well as clearances (spec/Lead).
    remaining = item.gross_cents - cleared - offset - refunded_total - returned_total
    return {'clearances': clearances, 'active': active, 'cash': cash,
            'cleared': cleared, 'offset': offset, 'remaining': remaining,
            'refunded': refunded_total, 'returned': returned_total}


def _item_issues(item, bundle):
    issues = []
    cleared = bundle['cleared']
    remaining = bundle['remaining']
    if cleared > item.gross_cents:
        _issue(issues, 'cleared_over_gross', item,
               expected={'max_cents': item.gross_cents}, actual=cleared)
    if remaining < 0:
        _issue(issues, 'remaining_negative', item,
               expected={'min_cents': 0}, actual=remaining)
    # The model property ignores refund/return coverage; adjust it before comparing with the
    # new coverage口径 so a refund does not create a false mismatch.
    adjusted_model_remaining = max(
        0, item.remaining_cents - bundle['refunded'] - bundle['returned'])
    if adjusted_model_remaining != max(0, remaining):
        _issue(issues, 'remaining_mismatch', item,
               expected={'remaining_cents': max(0, remaining)},
               actual=adjusted_model_remaining)
    seen = {}
    for row in bundle['active']:
        # Canonical identity of an active clearance is the (open item, cash fact) pair:
        # a second active clearance for the same pair is a duplicate even when its amount
        # or occurred_on differs.
        key = (row.open_item_id, row.cash_transaction_id)
        seen[key] = seen.get(key, 0) + 1
        if row.organization_id != item.organization_id:
            _issue(issues, 'clearance_cross_org', item,
                   clearance_ref=row.clearance_ref)
        cash = bundle['cash'].get(row.cash_transaction_id)
        if cash is None:
            _issue(issues, 'clearance_cash_missing', item,
                   clearance_ref=row.clearance_ref,
                   cash_transaction_ref=row.cash_transaction_ref)
            continue
        if cash.organization_id != item.organization_id:
            _issue(issues, 'clearance_cash_cross_org', item,
                   clearance_ref=row.clearance_ref,
                   cash_transaction_ref=row.cash_transaction_ref)
        if cash.status != 'posted':
            _issue(issues, 'clearance_cash_not_posted', item,
                   clearance_ref=row.clearance_ref,
                   cash_transaction_ref=row.cash_transaction_ref)
        if cash.direction != 'in':
            _issue(issues, 'clearance_cash_direction', item,
                   clearance_ref=row.clearance_ref,
                   cash_transaction_ref=row.cash_transaction_ref)
        if (row.cash_transaction_ref.get('id') != str(cash.pk)
                or row.cash_transaction_ref.get('version') != cash.version):
            _issue(issues, 'clearance_cash_ref_mismatch', item,
                   clearance_ref=row.clearance_ref,
                   expected={'id': str(cash.pk), 'version': cash.version},
                   actual=row.cash_transaction_ref)
        refunded = bundle['refunded'] if cash.pk == row.cash_transaction_id else 0
        refunded_total = sum(
            value for key, value in _refund_totals({cash.pk})[0].items())
        returned_total = sum(
            value for key, value in _refund_totals({cash.pk})[1].items())
        cash_cleared = sum(other.cleared_cents for other in bundle['active']
                           if other.cash_transaction_id == cash.pk)
        if cash_cleared + refunded_total + returned_total > cash.gross_cents:
            _issue(issues, 'cash_coverage_over_gross', item,
                   clearance_ref=row.clearance_ref,
                   cash_transaction_ref=row.cash_transaction_ref,
                   expected={'max_cents': cash.gross_cents},
                   actual=cash_cleared + refunded_total + returned_total)
    for key, count in seen.items():
        if count > 1:
            _issue(issues, 'clearance_duplicate_active', item,
                   expected={'count': 1}, actual=count,
                   open_item_id=str(key[0]), cash_transaction_id=str(key[1]))
    return issues


def _item_projection(item, bundle):
    return {
        'open_item_ref': item.item_ref,
        'project_id': str(item.project_id),
        'project_ref': {'namespace': 'core', 'kind': 'project',
                        'id': str(item.project_id), 'version': 1},
        'project_name': item.project.name,
        'counterparty_ref': {'namespace': 'contracts', 'kind': 'counterparty',
                             'id': str(item.counterparty_id), 'version': 1},
        'partition_kind': item.partition_kind,
        'direction': item.direction,
        'status': item.status,
        'gross_cents': item.gross_cents,
        'cleared_cents': bundle['cleared'],
        'offset_cents': 0,
        'offset_implemented': False,
        'refunded_coverage_cents': bundle['refunded'],
        'returned_coverage_cents': bundle['returned'],
        'remaining_cents': max(0, bundle['remaining']),
        'gross_display': _yuan(item.gross_cents),
        'cleared_display': _yuan(bundle['cleared']),
        'remaining_display': _yuan(max(0, bundle['remaining'])),
        'due_date': item.due_date.isoformat(),
        'occurred_on': item.occurred_on.isoformat(),
        'clearances': [{
            'ref': row.clearance_ref,
            'cash_transaction_ref': row.cash_transaction_ref,
            'cleared_cents': row.cleared_cents,
            'occurred_on': row.occurred_on.isoformat(),
            'status': row.status,
            'version': row.version,
            'cash_org_matches': (
                row.cash_transaction_id in bundle['cash']
                and bundle['cash'][row.cash_transaction_id].organization_id
                == item.organization_id),
        } for row in bundle['clearances']],
    }


def open_item_coverage_snapshot(member, *, as_of=None, project_id=None):
    """Read-only open-item remaining/clearance/refund coverage snapshot (max 200 items)."""
    member = _fresh(member, check_module=False)
    day = _day(as_of)
    items = list(_visible_items(member, project_id=project_id).filter(
        occurred_on__lte=day).order_by('due_date', 'created_at', 'pk')[:MAX_PAGE])
    rows, issues = [], []
    for item in items:
        bundle = _item_bundle(item)
        issues.extend(_item_issues(item, bundle))
        rows.append(_item_projection(item, bundle))
    return {
        'as_of': day.isoformat(),
        'scope': 'company' if member.role != 'pm' else 'managed_projects',
        'offset_implemented': False,
        'summary': {
            'open_item_count': len(rows),
            'gross_cents': sum(row['gross_cents'] for row in rows),
            'cleared_cents': sum(row['cleared_cents'] for row in rows),
            'offset_cents': 0,
            'refunded_coverage_cents': sum(
                row['refunded_coverage_cents'] for row in rows),
            'returned_coverage_cents': sum(
                row['returned_coverage_cents'] for row in rows),
            'remaining_cents': sum(row['remaining_cents'] for row in rows),
            'issue_count': len(issues),
        },
        'items': rows,
        'issues': issues,
        'estimate_label': '只读未清覆盖快照；remaining 为服务端派生，非可编辑余额',
    }


def open_item_coverage_issues(member, *, as_of=None, project_id=None):
    """Read-only issue list derived from the same open-item coverage rules."""
    snapshot = open_item_coverage_snapshot(member, as_of=as_of, project_id=project_id)
    return {'as_of': snapshot['as_of'], 'scope': snapshot['scope'],
            'offset_implemented': False,
            'issue_count': len(snapshot['issues']),
            'issues': snapshot['issues'],
            'summary': snapshot['summary'],
            'estimate_label': snapshot['estimate_label']}
