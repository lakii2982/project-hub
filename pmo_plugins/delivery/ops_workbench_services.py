"""PH-309 read-only operations reconciliation workbench.

Aggregates the delivered read-only reconciliation services into partition summaries with
issue counts, key totals, missing markers and drill links. No business fact, table,
migration, canonical event, receipt or audit write is performed; no business rule is
re-derived here.
"""
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import require_member
from core.models import Membership

READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
PARTITION_ORDER = ('ops_overview', 'settlement_coverage', 'cash_bridge',
                   'fee_pairing', 'identity_access')
PARTITION_LABELS = {
    'ops_overview': '跨域核对总览',
    'settlement_coverage': '结算未清/核销/退款覆盖',
    'cash_bridge': '现金分类/桥接/退款/互转',
    'fee_pairing': '手续费批准/成本/净额配对',
    'identity_access': '成员/项目范围与授权审计',
}
EXPORT_DATASETS = {
    'ops_overview': 'ops_overview',
    'settlement_coverage': 'settlement_coverage',
    'cash_bridge': 'cash_bridge',
    'fee_pairing': 'fee_pairing',
}
DRILL_URLS = {
    'ops_overview': '/modules/delivery/ops-reconciliation-overview/',
    'settlement_coverage': '/modules/settlement/settlement-coverage-issues/',
    'cash_bridge': '/modules/cash/cash-bridge-issues/',
    'fee_pairing': '/modules/costing/bank-charge-pairing/',
    'identity_access': '/modules/delivery/identity-access/',
}


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务、审阅账户或项目经理可以查看运营核对工作台')
    return fresh


def _date(value):
    if value in (None, ''):
        return None
    if type(value) is date:
        return value
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('as_of 须为YYYY-MM-DD日期')
    if parsed > timezone.localdate():
        raise ValidationError('as_of 不能在未来')
    return parsed


def _month(value):
    if value in (None, ''):
        return None
    text = str(value)
    try:
        year, month = text.split('-')
        start = date(int(year), int(month), 1)
    except (TypeError, ValueError):
        raise ValidationError('period 须为YYYY-MM月份')
    return start.strftime('%Y-%m')


def _partition(key, *, issue_count, metrics, missing=None, available=True,
               as_of=None, period=None):
    dataset = EXPORT_DATASETS.get(key) if available else None
    export = None
    export_missing = []
    if dataset is None:
        if key == 'identity_access':
            export_missing.append('export_dataset_not_implemented')
        elif not available:
            export_missing.append('permission')
    else:
        export = {'dataset': dataset, 'as_of': as_of, 'period': period}
    return {'key': key, 'label': PARTITION_LABELS[key],
            'issue_count': issue_count, 'metrics': metrics,
            'missing': list(missing or []), 'available': available,
            'drill_url': DRILL_URLS[key],
            'export': export, 'export_missing': export_missing}


def _ops_partition(member, as_of, period):
    from . import operations_read_services as overview
    data = overview.ops_reconciliation_overview(member, as_of=as_of, period=period)
    metrics = {
        'section_count': len(data['sections']),
        'issue_count': data['issue_count'],
        'missing_count': len(data['missing']),
    }
    return _partition('ops_overview', issue_count=data['issue_count'],
                      metrics=metrics, missing=data['missing'],
                      as_of=as_of, period=period)


def _settlement_partition(member, as_of, period):
    from pmo_plugins.settlement import coverage_read_services as coverage
    data = coverage.open_item_coverage_issues(member, as_of=as_of)
    summary = data['summary']
    metrics = {'open_item_count': summary['open_item_count'],
               'remaining_cents': summary['remaining_cents'],
               'issue_count': data['issue_count'],
               'offset_implemented': data['offset_implemented']}
    return _partition('settlement_coverage', issue_count=data['issue_count'],
                      metrics=metrics, as_of=as_of, period=period)


def _cash_partition(member, as_of, period):
    from pmo_plugins.cash import reconciliation_services as reconciliation
    data = reconciliation.cash_bridge_issues(member, as_of=as_of)
    summary = data['summary']
    metrics = {'posted_cash_count': summary['posted_cash_count'],
               'classified_count': summary['classified_count'],
               'unclassified_count': summary['unclassified_count'],
               'issue_count': data['issue_count'],
               'offset_implemented': data['offset_implemented']}
    return _partition('cash_bridge', issue_count=data['issue_count'],
                      metrics=metrics, as_of=as_of, period=period)


def _fee_partition(member, as_of, period):
    from pmo_plugins.costing import bank_charge_pairing_services as pairing
    issues = pairing.bank_charge_pairing_issues(member, as_of=as_of)
    rows = pairing.bank_charge_pairing(member, as_of=as_of)
    metrics = {'charge_count': len(rows), 'issue_count': len(issues)}
    return _partition('fee_pairing', issue_count=len(issues), metrics=metrics,
                      as_of=as_of, period=period)


def _identity_partition(member, as_of, period):
    from core import identity_read_services as identity
    issues = identity.member_access_issues(member, as_of=as_of)
    snapshot = identity.member_access_snapshot(
        member, as_of=as_of, page=1, page_size=1)
    metrics = {'member_count': snapshot['total'],
               'scope': snapshot['scope'],
               'issue_count': len(issues)}
    return _partition('identity_access', issue_count=len(issues), metrics=metrics,
                      as_of=as_of, period=period)


PARTITIONS = {
    'ops_overview': _ops_partition,
    'settlement_coverage': _settlement_partition,
    'cash_bridge': _cash_partition,
    'fee_pairing': _fee_partition,
    'identity_access': _identity_partition,
}


def ops_workbench_summary(member, *, as_of=None, period=None):
    """Read-only partition summary + issue counts + drill links; no writes."""
    member = _fresh(member)
    day = _date(as_of)
    month = _month(period)
    partitions, missing = [], []
    for key in PARTITION_ORDER:
        try:
            partition = PARTITIONS[key](member, day.isoformat() if day else None, month)
        except PermissionDenied:
            partition = _partition(
                key, issue_count=None, metrics={}, missing=['permission'],
                available=False, as_of=day.isoformat() if day else None,
                period=month)
        partitions.append(partition)
        missing.extend(partition['missing'])
    total_issues = sum(item['issue_count'] or 0 for item in partitions)
    return {'as_of': (day or timezone.localdate()).isoformat(),
            'period': month,
            'role': member.role,
            'scope': 'managed_projects' if member.role == 'pm' else 'company',
            'partitions': partitions,
            'issue_count': total_issues,
            'missing': sorted(set(missing)),
            'estimate_label': '只读聚合工作台；分区指标来自各 owner 只读接口，缺失以 null+missing 表示'}
