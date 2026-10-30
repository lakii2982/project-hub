"""PH-241 private Offset services: a customer advance partially offsets one receivable item.

Offset is an append-only settlement fact. It writes no accounting event, ledger posting,
revenue, cost or source consumption; the advance cash fact is only read through the cash
plugin's root-owned named read and the PH-239 classification facts. There is no canonical
offset event in the frozen contract, so only a generic audit row is written and the relation
is derived from the Offset fact itself.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import require_project, visible_projects
from core.models import Project
from core.plugin_runtime import is_enabled
from core.services import audit

from .models import Offset, OpenItem, SettlementReceipt
from .receivable_services import (_business_date, _digest, _dry, _fail, _fresh, _identifier,
                                  _locked, _version)
from .clearance_services import (_cash_cleared, _cash_offset, _get_open_item, _open_cleared,
                                 _open_offset, _period, _yuan)

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
CREATED_ACTION = 'settlement.offset.created'
MAX_CENTS = 10 ** 12


# ---------- guards ----------
def _require_modules(member):
    if not is_enabled(member.organization, 'cash'):
        raise PermissionDenied('现金事实模块已停用，不能办理预收抵扣')
    if not is_enabled(member.organization, 'settlement'):
        raise PermissionDenied('结算模块已停用，不能办理预收抵扣')


def _amount(value):
    if type(value) is not int or not 0 < value <= MAX_CENTS:
        _fail('抵扣金额须为大于0且不超过100亿元的整数分')
    return value


def _advance_ref(value):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('预收现金来源无效')
    if value['namespace'] != 'cash' or value['kind'] != 'cash_transaction':
        _fail('预收现金来源无效')
    if type(value['version']) is not int:
        _fail('预收现金来源版本无效')
    return {'namespace': 'cash', 'kind': 'cash_transaction',
            'id': str(_identifier(value['id'], '预收现金来源无效')), 'version': value['version']}


# ---------- receipts (reuse SettlementReceipt) ----------
def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token, '请求标识无效')
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = SettlementReceipt.objects.filter(organization=member.organization,
                                           request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, digest, row
    return token, digest, None


def _receipt_row(member, receipt):
    result = receipt.result or {}
    try:
        return Offset.objects.select_related('open_item__project').get(
            organization=member.organization, pk=result['id'])
    except (KeyError, ValueError, TypeError, Offset.DoesNotExist):
        raise PermissionDenied('原抵扣记录不存在或无权访问')


def _record(member, action, token, digest, row):
    SettlementReceipt.objects.create(organization=member.organization, request_token=token,
                                     action=action, input_digest=digest,
                                     result=row.offset_ref, created_by=member)


# ---------- reads ----------
def _get_offset(member, offset_id):
    rows = Offset.objects.select_related('open_item__project')
    try:
        row = rows.get(organization=member.organization,
                       pk=_identifier(offset_id, '抵扣记录不存在或无权访问'))
    except Offset.DoesNotExist:
        raise PermissionDenied('抵扣记录不存在或无权访问')
    require_project(member, row.open_item.project)
    if member.role == 'pm' and row.open_item.project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看自己管理项目的抵扣记录')
    return row


def _offset_projection(row):
    return {
        'id': str(row.pk), 'offset_ref': row.offset_ref,
        'organization_id': str(row.organization_id),
        'project_id': str(row.project_id),
        'open_item_id': str(row.open_item_id), 'open_item_ref': row.open_item.item_ref,
        'advance_cash_transaction_id': str(row.advance_cash_transaction_id),
        'advance_ref': deepcopy(row.advance_ref),
        'offset_cents': row.offset_cents, 'offset_display': _yuan(row.offset_cents),
        'occurred_on': row.occurred_on.isoformat(), 'status': row.status,
        'status_display': {'active': '有效'}.get(row.status, row.status),
        'version': row.version, 'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
    }


def offset_snapshot(member, offset_id):
    member = _fresh(member, check_module=False)
    return _offset_projection(_get_offset(member, offset_id))


def offset_list(member, *, project_id=None, open_item_id=None,
                advance_cash_transaction_id=None):
    member = _fresh(member, check_module=False)
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    rows = Offset.objects.filter(organization=member.organization,
                                 open_item__project__in=projects).select_related(
        'open_item__project')
    if project_id is not None:
        try:
            project = Project.objects.get(organization=member.organization, pk=project_id)
        except (Project.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied('项目不存在或无权访问')
        require_project(member, project)
        if member.role == 'pm' and project.manager_id != member.pk:
            raise PermissionDenied('项目经理只能查看自己管理项目的抵扣记录')
        rows = rows.filter(open_item__project=project)
    if open_item_id is not None:
        rows = rows.filter(open_item=_get_open_item(member, open_item_id))
    if advance_cash_transaction_id is not None:
        rows = rows.filter(advance_cash_transaction_id=_identifier(
            advance_cash_transaction_id, '预收现金标识无效'))
    return [_offset_projection(row) for row in rows.order_by('-created_at', 'id')]


def open_item_offset_snapshot(member, open_item_id):
    """Open item balances with cleared/offset/remaining plus the offset history."""
    member = _fresh(member, check_module=False)
    item = _get_open_item(member, open_item_id)
    cleared = _open_cleared(item)
    offset_total = _open_offset(item)
    remaining = max(0, item.gross_cents - cleared - offset_total)
    return {
        'open_item_id': str(item.pk), 'open_item_ref': item.item_ref,
        'project_id': str(item.project_id), 'gross_cents': item.gross_cents,
        'cleared_cents': cleared, 'offset_cents': offset_total,
        'remaining_cents': remaining,
        'gross_display': _yuan(item.gross_cents), 'cleared_display': _yuan(cleared),
        'offset_display': _yuan(offset_total), 'remaining_display': _yuan(remaining),
        'offsets': [_offset_projection(row) for row in item.offsets.select_related(
            'open_item__project').order_by('created_at', 'id')],
    }


def advance_offset_snapshot(member, advance_ref):
    """Read-only advance availability: gross − active cleared − active offsets."""
    member = _fresh(member, check_module=False)
    from pmo_plugins.cash.transaction_services import posted_advance_for_offset
    cash = posted_advance_for_offset(member, advance_ref)
    cash_id = _identifier(cash['id'], '预收现金标识无效')
    cleared = _cash_cleared(member, cash_id)
    offset_total = _cash_offset(member, cash_id)
    from pmo_plugins.cash.refund_services import refund_coverage_for_cash
    refund_total = refund_coverage_for_cash(member, cash['transaction_ref'])['total_cents']
    available = max(0, cash['gross_cents'] - cleared - offset_total - refund_total)
    rows = Offset.objects.filter(organization=member.organization,
                                 advance_cash_transaction_id=cash_id,
                                 status='active').select_related('open_item__project')
    return {'advance_ref': deepcopy(cash['transaction_ref']),
            'advance_cash_transaction_id': str(cash_id),
            'classification_ref': cash['classification_ref'],
            'counterparty_id': (cash['effective_counterparty'] or {}).get('id'),
            'transaction_date': (cash['transaction_date'].isoformat()
                                 if isinstance(cash['transaction_date'], date)
                                 else cash['transaction_date']),
            'gross_cents': cash['gross_cents'],
            'cleared_cents': cleared, 'offset_cents': offset_total,
            'refunded_cents': refund_total,
            'available_cents': available,
            'gross_display': _yuan(cash['gross_cents']),
            'cleared_display': _yuan(cleared), 'offset_display': _yuan(offset_total),
            'available_display': _yuan(available),
            'offsets': [_offset_projection(row) for row in rows.order_by('created_at', 'id')]}


# ---------- write ----------
@transaction.atomic
def create_offset(member, *, open_item_id, advance_ref, offset_cents, occurred_on=None,
                  request_token, dry_run=True):
    """Partially offset one active receivable open item with a posted advance cash fact."""
    _dry(dry_run)
    member = _locked(member)
    _require_modules(member)
    if member.role not in WRITE_ROLES:
        raise PermissionDenied('仅公司负责人或财务可以办理预收抵扣')
    open_item_id = _identifier(open_item_id, '未清项不存在或无权访问')
    ref = _advance_ref(advance_ref)
    amount = _amount(offset_cents)
    occurred = None if occurred_on is None else _business_date(occurred_on)
    payload = {'open_item_id': str(open_item_id), 'advance_ref': ref,
               'offset_cents': amount,
               'occurred_on': occurred.isoformat() if occurred else None}
    token, digest, receipt = _receipt(member, 'offset.create', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt)
    item = _get_open_item(member, open_item_id, lock=True)
    if item.status != 'active' or item.direction != 'receivable':
        _fail('仅可抵扣有效的应收未清项')
    from pmo_plugins.cash.transaction_services import posted_advance_for_offset
    cash = posted_advance_for_offset(member, ref)
    effective = cash.get('effective_counterparty') or {}
    if effective.get('kind') != 'counterparty' or not effective.get('id'):
        _fail('预收现金尚未识别有效往来方，不能抵扣')
    if str(item.counterparty_id) != str(effective['id']):
        _fail('预收现金与未清项不是同一往来方')
    day = cash['transaction_date']
    if type(day) is not date:
        day = _business_date(day)
    if occurred is not None and occurred != day:
        _fail('抵扣日期必须与预收现金流水日期一致')
    _period(member, day)
    cash_id = _identifier(cash['id'], '预收现金标识无效')
    if _cash_cleared(member, cash_id) > 0:
        _fail('该预收现金已有有效核销，不能同时用于抵扣')
    from pmo_plugins.cash.refund_services import refund_coverage_for_cash
    refund_total = refund_coverage_for_cash(member, cash['transaction_ref'])['total_cents']
    advance_available = (cash['gross_cents'] - _cash_cleared(member, cash_id)
                         - _cash_offset(member, cash_id) - refund_total)
    item_remaining = (item.gross_cents - _open_cleared(item) - _open_offset(item))
    if advance_available <= 0 or item_remaining <= 0:
        _fail('该预收现金或未清项已无可抵扣余额')
    if amount > min(advance_available, item_remaining):
        _fail('抵扣金额超过预收可用额与未清项剩余额的较小值')
    offset = Offset(organization=member.organization, project=item.project,
                    open_item=item, advance_cash_transaction_id=cash_id,
                    advance_ref=deepcopy(cash['transaction_ref']),
                    offset_cents=amount, occurred_on=day, status='active', version=1,
                    created_by=member)
    if dry_run:
        return offset
    try:
        with transaction.atomic():
            offset.save()
        # Recheck under the company lock so no half write can leave a negative balance.
        if (_open_cleared(item) + _open_offset(item) > item.gross_cents
                or (_cash_cleared(member, cash_id) + _cash_offset(member, cash_id)
                    + refund_total) > cash['gross_cents']):
            _fail('抵扣后余额为负，已拒绝本次抵扣')
    except IntegrityError:
        _fail('抵扣记录写入冲突，请刷新后重试')
    audit(member, CREATED_ACTION, offset,
          {'open_item_ref': item.item_ref, 'advance_ref': deepcopy(offset.advance_ref),
           'offset_cents': amount, 'occurred_on': day.isoformat()})
    _record(member, 'offset.create', token, digest, offset)
    return offset
