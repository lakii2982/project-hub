"""PH-304 authorized read-only CSV export of delivered reconciliation worksheets.

Composes the existing PH-291/PH-283/PH-288/PH-280 read services and renders a UTF-8 BOM
CSV with refs, as_of/period, generation time, row count and a checksum row. Strictly
read-only: no business table write, no migration, no canonical event and no generic audit.
Idempotency uses a process-local request_token map (no reusable receipt table exists and
this slice must not create one); the limitation is disclosed in the report.
"""
import csv
import hashlib
import io
import threading
import uuid
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import require_member
from core.models import Membership

DATASETS = ('ops_overview', 'settlement_coverage', 'cash_bridge', 'fee_pairing')
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
PM_DATASETS = {'ops_overview', 'settlement_coverage', 'fee_pairing'}
DATASET_LABELS = {
    'ops_overview': '跨域核对总览',
    'settlement_coverage': '结算未清/核销/退款覆盖',
    'cash_bridge': '现金分类/桥接/退款/互转',
    'fee_pairing': '手续费批准/成本/净额配对',
}
_LOCK = threading.Lock()
_RECEIPTS = {}


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以导出核对底稿')
    return fresh


def _date(value):
    if value in (None, ''):
        return None
    if type(value) is date:
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('as_of 须为YYYY-MM-DD日期')


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


def _token(value):
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ValidationError('request_token 无效')


def _digest(dataset, as_of, period):
    return hashlib.sha256(
        f'{dataset}|{as_of or ""}|{period or ""}'.encode('utf-8')).hexdigest()


def _ref_id(value):
    if isinstance(value, dict):
        return str(value.get('id', ''))
    return str(value or '')


def _money(value):
    return '' if value is None else str(int(value))


def _ops_rows(member, as_of, period):
    from . import operations_read_services as overview
    data = overview.ops_reconciliation_overview(member, as_of=as_of, period=period)
    rows = []
    for section in data['sections']:
        for item in section.get('count_rows', []):
            rows.append(['ops_overview', section['key'], 'count', item['key'],
                         item['label'], _money(item['value']), len(section.get('issues') or [])])
        for item in section.get('total_rows', []):
            rows.append(['ops_overview', section['key'], 'total', item['key'],
                         item['label'], _money(item['value']), len(section.get('issues') or [])])
    return rows


def _settlement_rows(member, as_of, period):
    from pmo_plugins.settlement import coverage_read_services as coverage
    data = coverage.open_item_coverage_snapshot(member, as_of=as_of)
    rows = []
    for item in data['items']:
        rows.append(['settlement_coverage', _ref_id(item['open_item_ref']),
                     item['project_name'], item['partition_kind'],
                     _money(item['gross_cents']), _money(item['cleared_cents']),
                     _money(item['offset_cents']), _money(item['refunded_coverage_cents']),
                     _money(item['returned_coverage_cents']),
                     _money(item['remaining_cents']), item['due_date'],
                     len(item['clearances'])])
    return rows


def _cash_rows(member, as_of, period):
    from pmo_plugins.cash import reconciliation_services as reconciliation
    data = reconciliation.cash_bridge_snapshot(member, as_of=as_of)
    rows = []
    for item in data['transactions']:
        rows.append(['cash_bridge', _ref_id(item['transaction_ref']),
                     item['transaction_date'], item['direction'],
                     _money(item['gross_cents']), item['account_name'],
                     (item['classification'] or {}).get('classification', ''),
                     _money((item['accounting_event'] or {}).get('amount_cents')),
                     len(item['postings']),
                     _money((item['claim'] or {}).get('net_effect_cents')),
                     item['refunds']['count'], item['bank_returns']['count'],
                     (item['internal_transfer'] or {}).get('status', '')])
    return rows


def _fee_rows(member, as_of, period):
    from pmo_plugins.costing import bank_charge_pairing_services as pairing
    rows = []
    for item in pairing.bank_charge_pairing(member, as_of=as_of):
        cost = item.get('cost') or {}
        net_fee = item.get('net_fee') or {}
        rows.append(['fee_pairing', _ref_id(item['charge_ref']), item['status'],
                     _money(item.get('fee_gross_cents')),
                     _ref_id(item.get('cash_transaction_ref')),
                     _money(item.get('posted_net_cents')),
                     _money(cost.get('cost_amount_cents')),
                     _money(net_fee.get('approved_fee_cents')),
                     len(item.get('issues') or [])])
    return rows


COLUMNS = {
    'ops_overview': ['dataset', 'section', 'kind', 'key', 'label', 'value',
                     'issue_count'],
    'settlement_coverage': ['dataset', 'open_item_ref', 'project', 'partition_kind',
                            'gross_cents', 'cleared_cents', 'offset_cents',
                            'refunded_cents', 'returned_cents', 'remaining_cents',
                            'due_date', 'clearance_count'],
    'cash_bridge': ['dataset', 'cash_transaction_ref', 'transaction_date', 'direction',
                    'gross_cents', 'account', 'classification', 'event_amount_cents',
                    'posting_count', 'claim_net_cents', 'refund_count', 'return_count',
                    'internal_transfer_status'],
    'fee_pairing': ['dataset', 'charge_ref', 'status', 'gross_cents',
                    'cash_transaction_ref', 'cash_gross_cents', 'cost_amount_cents',
                    'net_cents', 'issue_count'],
}
ROW_BUILDERS = {
    'ops_overview': _ops_rows,
    'settlement_coverage': _settlement_rows,
    'cash_bridge': _cash_rows,
    'fee_pairing': _fee_rows,
}


def _build(dataset, member, as_of, period):
    if dataset not in DATASETS:
        raise ValidationError('不支持的导出数据集')
    if member.role == 'pm' and dataset not in PM_DATASETS:
        raise PermissionDenied('项目经理不能导出无项目归属的现金核对底稿')
    day = _date(as_of)
    month = _month(period)
    rows = ROW_BUILDERS[dataset](member, day.isoformat() if day else None, month)
    return day, month, rows


def _render(dataset, rows, *, as_of, period, generated_at):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    columns = COLUMNS[dataset]
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    body = buffer.getvalue()
    checksum = hashlib.sha256(
        (body + f'|{len(rows)}|{as_of or ""}|{period or ""}').encode('utf-8')).hexdigest()
    buffer.write('\r\n')
    writer.writerow(['#checksum', 'dataset', dataset, 'as_of', as_of or '',
                     'period', period or '', 'row_count', len(rows),
                     'checksum', checksum, 'generated_at', generated_at])
    text = '\ufeff' + buffer.getvalue()
    return text, checksum, columns


def export_reconciliation_worksheets(member, *, dataset, as_of=None, period=None,
                                     request_token, dry_run=True):
    """Return a plan (dry_run) or a rendered UTF-8 BOM CSV worksheet; no writes."""
    member = _fresh(member)
    if type(dry_run) is not bool:
        raise ValidationError('dry_run 必须为布尔值')
    dataset = str(dataset or '')
    token = _token(request_token)
    day, month, rows = _build(dataset, member, as_of, period)
    digest = _digest(dataset, day.isoformat() if day else None, month)
    key = (member.organization_id, str(token))
    with _LOCK:
        stored = _RECEIPTS.get(key)
        if stored is not None and stored['digest'] != digest:
            raise ValidationError('同一 request_token 的导出输入已改变，请使用新的令牌')
        if stored is not None:
            if dry_run and stored.get('plan') is not None:
                return stored['plan']
            if not dry_run and stored.get('result') is not None:
                return stored['result']
    generated_at = datetime.now(dt_timezone.utc).replace(microsecond=0).isoformat()
    columns = COLUMNS[dataset]
    plan = {
        'dataset': dataset, 'dataset_label': DATASET_LABELS[dataset],
        'as_of': day.isoformat() if day else None, 'period': month,
        'columns': columns, 'row_count': len(rows), 'dry_run': True,
        'content_id': None, 'generated_at': None, 'token': str(token),
        'checksum_row': ['#checksum', 'dataset', dataset,
                         'as_of', day.isoformat() if day else '',
                         'period', month or '', 'row_count', len(rows)],
    }
    if dry_run:
        with _LOCK:
            cached = _RECEIPTS.get(key) or {'digest': digest}
            cached['plan'] = plan
            _RECEIPTS[key] = cached
        return plan
    text, checksum, columns = _render(
        dataset, rows, as_of=day.isoformat() if day else None, period=month,
        generated_at=generated_at)
    result = {
        'dataset': dataset, 'dataset_label': DATASET_LABELS[dataset],
        'as_of': day.isoformat() if day else None, 'period': month,
        'columns': columns, 'row_count': len(rows), 'dry_run': False,
        'content_id': checksum, 'generated_at': generated_at,
        'csv': text,
        'filename': f'{dataset}-{(day or timezone.localdate()).isoformat()}.csv',
        'checksum_row': ['#checksum', 'dataset', dataset,
                         'as_of', day.isoformat() if day else '',
                         'period', month or '', 'row_count', len(rows),
                         'checksum', checksum, 'generated_at', generated_at],
    }
    with _LOCK:
        cached = _RECEIPTS.get(key) or {'digest': digest}
        cached['result'] = result
        cached['plan'] = plan
        _RECEIPTS[key] = cached
    return result


def export_dataset_options():
    return [{'key': key, 'label': DATASET_LABELS[key]} for key in DATASETS]
