"""Private clearance services: posted cash partially clears one active receivable open item.

No accounting event, ledger posting, revenue, cost or source consumption is written. Cash facts
are only read through the root-owned named interfaces in the cash plugin.
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
from core.models import AccountingPeriod, DomainEvent, Project
from core.plugin_runtime import is_enabled
from core.services import audit

from .models import Clearance, ClearanceReceipt, Offset, OpenItem
from .receivable_services import (_business_date, _digest, _dry, _fail, _fresh, _identifier,
                                  _locked, _text, _version)

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
CLEARED_TOPIC = 'settlement.cleared.v1'
REVERSED_TOPIC = 'settlement.clearance_reversed.v1'
CREATED_ACTION = 'settlement.clearance.created'
REVERSED_ACTION = 'settlement.clearance.reversed'
MAX_CENTS = 10 ** 12


# ---------- guards ----------
def _require_cash(member):
    if not is_enabled(member.organization, 'cash'):
        raise PermissionDenied('现金事实模块已停用，不能办理核销')


def _period(member, day):
    if type(day) is not date:
        _fail('核销日期须为有效的年月日')
    if day > timezone.localdate():
        _fail('核销日期不能在未来')
    if member.organization.locked_through and day <= member.organization.locked_through:
        _fail('核销日期所在期间已锁账，不能补记')
    row = AccountingPeriod.objects.filter(organization=member.organization,
                                          period=day.strftime('%Y-%m')).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('核销日期所在会计期间已关闭')


def _amount(value):
    if type(value) is not int or not 0 < value <= MAX_CENTS:
        _fail('核销金额须为大于0且不超过100亿元的整数分')
    return value


def _cash_ref(value):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('现金流水来源无效')
    if value['namespace'] != 'cash' or value['kind'] != 'cash_transaction':
        _fail('现金流水来源无效')
    if type(value['version']) is not int:
        _fail('现金流水来源版本无效')
    return {'namespace': 'cash', 'kind': 'cash_transaction',
            'id': str(_identifier(value['id'], '现金流水来源无效')), 'version': value['version']}


# ---------- receipts ----------
def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token, '请求标识无效')
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = ClearanceReceipt.objects.filter(organization=member.organization,
                                          request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, digest, row
    return token, digest, None


def _receipt_row(member, receipt):
    result = receipt.result or {}
    try:
        return Clearance.objects.select_related('open_item__project').get(
            organization=member.organization, pk=result['id'])
    except (KeyError, ValueError, TypeError, Clearance.DoesNotExist):
        raise PermissionDenied('原核销记录不存在或无权访问')


def _record(member, action, token, digest, row):
    ClearanceReceipt.objects.create(organization=member.organization, request_token=token,
                                    action=action, input_digest=digest,
                                    result=row.clearance_ref, created_by=member)


# ---------- reads ----------
def _get_open_item(member, open_item_id, *, lock=False):
    rows = OpenItem.objects.select_related('project')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        item = rows.get(organization=member.organization,
                        pk=_identifier(open_item_id, '未清项不存在或无权访问'))
    except OpenItem.DoesNotExist:
        raise PermissionDenied('未清项不存在或无权访问')
    require_project(member, item.project)
    if member.role == 'pm' and item.project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看自己管理项目的未清项')
    return item


def _get_clearance(member, clearance_id, *, lock=False):
    rows = Clearance.objects.select_related('open_item__project')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        row = rows.get(organization=member.organization,
                       pk=_identifier(clearance_id, '核销记录不存在或无权访问'))
    except Clearance.DoesNotExist:
        raise PermissionDenied('核销记录不存在或无权访问')
    require_project(member, row.open_item.project)
    if member.role == 'pm' and row.open_item.project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看自己管理项目的核销记录')
    return row


def _open_cleared(open_item):
    return Clearance.objects.filter(organization_id=open_item.organization_id,
                                    open_item=open_item, status='active').aggregate(
        total=Sum('cleared_cents'))['total'] or 0


def _cash_cleared(member, cash_transaction_id):
    return Clearance.objects.filter(organization=member.organization,
                                    cash_transaction_id=cash_transaction_id,
                                    status='active').aggregate(
        total=Sum('cleared_cents'))['total'] or 0


def _open_offset(open_item):
    return Offset.objects.filter(organization_id=open_item.organization_id,
                                 open_item=open_item, status='active').aggregate(
        total=Sum('offset_cents'))['total'] or 0


def _cash_offset(member, cash_transaction_id):
    return Offset.objects.filter(organization=member.organization,
                                 advance_cash_transaction_id=cash_transaction_id,
                                 status='active').aggregate(
        total=Sum('offset_cents'))['total'] or 0


def _advance_classified(member, cash_transaction_id):
    """PH-239 named read: an advance-classified cash fact is reserved for Offset."""
    from pmo_plugins.cash import classification_services as cash_classification
    rows = cash_classification.classification_list(
        member, cash_transaction_id=cash_transaction_id)
    return any(row['classification'] == 'advance' and row['status'] == 'active'
               for row in rows)


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _clearance_projection(row):
    return {
        'id': str(row.pk), 'clearance_ref': row.clearance_ref,
        'organization_id': str(row.organization_id),
        'open_item_id': str(row.open_item_id), 'open_item_ref': row.open_item.item_ref,
        'project_id': str(row.open_item.project_id),
        'cash_transaction_id': str(row.cash_transaction_id),
        'cash_transaction_ref': deepcopy(row.cash_transaction_ref),
        'cleared_cents': row.cleared_cents, 'cleared_display': _yuan(row.cleared_cents),
        'occurred_on': row.occurred_on.isoformat(), 'status': row.status,
        'status_display': row.get_status_display(), 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
    }


# ---------- writes ----------
@transaction.atomic
def create_clearance(member, *, open_item_id, cash_transaction_ref, cleared_cents,
                     occurred_on=None, request_token, dry_run=True):
    """Partially clear one active receivable open item with posted incoming cash."""
    _dry(dry_run)
    member = _locked(member)
    _require_cash(member)
    open_item_id = _identifier(open_item_id, '未清项不存在或无权访问')
    ref = _cash_ref(cash_transaction_ref)
    cleared = _amount(cleared_cents)
    occurred = None if occurred_on is None else _business_date(occurred_on)
    payload = {'open_item_id': str(open_item_id), 'cash_transaction_ref': ref,
               'cleared_cents': cleared,
               'occurred_on': occurred.isoformat() if occurred else None}
    token, digest, receipt = _receipt(member, 'clearance.create', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt)
    item = _get_open_item(member, open_item_id, lock=True)
    if item.status != 'active' or item.direction != 'receivable':
        _fail('仅可核销有效的应收未清项')
    from pmo_plugins.cash.transaction_services import posted_cash_for_clearance
    cash = posted_cash_for_clearance(member, ref)
    if cash['direction'] != 'in':
        _fail('仅收入类已过账现金可用于核销')
    effective = cash.get('effective_counterparty') or {}
    if effective.get('kind') != 'counterparty' or not effective.get('id'):
        _fail('现金流水尚未识别有效往来方，不能核销')
    if str(item.counterparty_id) != str(effective['id']):
        _fail('现金流水与未清项不是同一往来方')
    if _advance_classified(member, cash['id']):
        _fail('预收/预付分类的现金已预留给抵扣，不能用于核销')
    day = cash['transaction_date']
    if type(day) is not date:
        day = _business_date(day)
    if occurred is not None and occurred != day:
        _fail('核销日期必须与现金流水日期一致')
    _period(member, day)
    remaining_open = item.gross_cents - _open_cleared(item) - _open_offset(item)
    cash_id = _identifier(cash['id'], '现金流水标识无效')
    # PH-240 joint coverage: a bank fact can be refunded/returned and cleared, but the sum
    # must never exceed its posted gross. The cash owner exposes the read-only refund
    # coverage so both write paths use the same derived ceiling.
    from pmo_plugins.cash.refund_services import refund_coverage_for_cash
    refund_total = refund_coverage_for_cash(member, cash['transaction_ref'])['total_cents']
    remaining_cash = (cash['gross_cents'] - _cash_cleared(member, cash_id)
                      - _cash_offset(member, cash_id) - refund_total)
    if remaining_open <= 0 or remaining_cash <= 0:
        _fail('该未清项或现金流水已无未核销余额')
    if cleared > min(remaining_open, remaining_cash):
        _fail('核销金额超过现金未核销余额与未清项剩余额的较小值')
    clearance = Clearance(organization=member.organization, open_item=item,
                          cash_transaction_id=cash_id,
                          cash_transaction_ref=deepcopy(cash['transaction_ref']),
                          cleared_cents=cleared, occurred_on=day, status='active',
                          version=1, created_by=member)
    if dry_run:
        return clearance
    try:
        with transaction.atomic():
            clearance.save()
        # Recheck under the company lock so no half write can leave a negative balance.
        if (_open_cleared(item) + _open_offset(item) > item.gross_cents
                or (_cash_cleared(member, cash_id) + _cash_offset(member, cash_id)
                    + refund_total) > cash['gross_cents']):
            _fail('核销后余额为负，已拒绝本次核销')
    except IntegrityError:
        _fail('核销记录写入冲突，请刷新后重试')
    ref_out = clearance.clearance_ref
    audit(member, CREATED_ACTION, clearance,
          {'open_item_ref': item.item_ref, 'cash_transaction_ref':
           deepcopy(clearance.cash_transaction_ref), 'cleared_cents': cleared,
           'occurred_on': day.isoformat()})
    event_source = audit(member, 'plugin.settlement.clearance.created', clearance,
                         {'clearance_ref': ref_out})
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=event_source,
        topic=CLEARED_TOPIC,
        data={'event_id': str(event_id), 'topic': CLEARED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref_out,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref_out], 'posting_event_refs': []}})
    _record(member, 'clearance.create', token, digest, clearance)
    return clearance


@transaction.atomic
def reverse_clearance(member, clearance_id, *, reason, request_token, expected_version,
                      dry_run=True):
    """Single active->reversed transition; amounts and references stay untouched."""
    _dry(dry_run)
    member = _locked(member)
    _require_cash(member)
    clearance_id = _identifier(clearance_id, '核销记录不存在或无权访问')
    version = _version(expected_version)
    reason = _text(reason, 1000, '请填写1至1000字的撤核原因')
    payload = {'clearance_id': str(clearance_id), 'expected_version': version,
               'reason': reason}
    token, digest, receipt = _receipt(member, 'clearance.reverse', request_token, payload,
                                      dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt)
    row = _get_clearance(member, clearance_id, lock=True)
    if row.version != version:
        _fail('核销记录版本已变化，请刷新后重试')
    if row.status != 'active':
        _fail('仅可撤核有效核销记录；已撤核不可重复撤核')
    if dry_run:
        row.status = 'reversed'
        return row
    updated = Clearance.objects.filter(pk=row.pk, organization=member.organization,
                                       version=version, status='active').update(
        status='reversed')
    if updated != 1:
        _fail('核销记录已被其他人撤核，请刷新后重试')
    row.refresh_from_db()
    ref_out = row.clearance_ref
    audit(member, REVERSED_ACTION, row,
          {'reason': reason, 'open_item_ref': row.open_item.item_ref,
           'cleared_cents': row.cleared_cents})
    event_source = audit(member, 'plugin.settlement.clearance.reversed', row,
                         {'clearance_ref': ref_out})
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=event_source,
        topic=REVERSED_TOPIC,
        data={'event_id': str(event_id), 'topic': REVERSED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref_out,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref_out], 'posting_event_refs': []}})
    _record(member, 'clearance.reverse', token, digest, row)
    return row


# ---------- reads exposed to callers ----------
def clearance_snapshot(member, clearance_id):
    member = _fresh(member, check_module=False)
    return _clearance_projection(_get_clearance(member, clearance_id))


def open_item_balances(member, open_item_id):
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
        'offset_display': _yuan(offset_total),
        'remaining_display': _yuan(remaining),
        'clearances': [_clearance_projection(row) for row in item.clearances.select_related(
            'open_item__project').order_by('created_at', 'id')],
    }


def clearance_list(member, *, project_id=None, open_item_id=None):
    member = _fresh(member, check_module=False)
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    rows = Clearance.objects.filter(organization=member.organization,
                                    open_item__project__in=projects).select_related(
        'open_item__project')
    if project_id is not None:
        try:
            project = Project.objects.get(organization=member.organization, pk=project_id)
        except (Project.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied('项目不存在或无权访问')
        require_project(member, project)
        if member.role == 'pm' and project.manager_id != member.pk:
            raise PermissionDenied('项目经理只能查看自己管理项目的核销记录')
        rows = rows.filter(open_item__project=project)
    if open_item_id is not None:
        rows = rows.filter(open_item=_get_open_item(member, open_item_id))
    return [_clearance_projection(row) for row in rows.order_by('-created_at', 'id')]


def cash_candidates_for_open_item(member, open_item_id):
    """Settlement wrapper for the UI: posted incoming cash of the open item's counterparty."""
    member = _fresh(member, check_module=False)
    item = _get_open_item(member, open_item_id)
    if member.role == 'pm':
        # Read-only PM pages keep balances/history; the cash candidate read interface is
        # owner/finance/auditor only, so PM gets an empty candidate list (create is hidden).
        return []
    if not item.counterparty_id:
        return []
    from pmo_plugins.cash.transaction_services import posted_cash_candidates
    candidates = posted_cash_candidates(member, counterparty_id=item.counterparty_id,
                                        as_of=timezone.localdate())
    result = []
    from pmo_plugins.cash.refund_services import refund_coverage_for_cash
    for candidate in candidates:
        cash_id = _identifier(candidate['id'], '现金流水标识无效')
        cleared = _cash_cleared(member, cash_id)
        refund_total = refund_coverage_for_cash(
            member, candidate['transaction_ref'])['total_cents']
        offset_total = _cash_offset(member, cash_id)
        available = max(0, candidate['gross_cents'] - cleared - refund_total - offset_total)
        result.append({**candidate, 'cleared_cents': cleared,
                       'refunded_cents': refund_total, 'offset_cents': offset_total,
                       'available_cents': available,
                       'available_display': _yuan(available)})
    return result


COVERAGE_READ_ROLES = {'owner', 'finance', 'auditor'}


def clearance_coverage_for_cash(member, cash_transaction_ref):
    """Cross-owner read-only clearance coverage of one posted bank fact (PH-240).

    Cash refund/return coverage consumes this named read; PH-241 fills the reserved offset
    slot so a cash fact's cleared + offset coverage is derived from the same named interface.
    """
    from pmo_plugins.cash.models import CashTransaction
    member = _fresh(member, check_module=False)
    if member.role not in COVERAGE_READ_ROLES:
        raise PermissionDenied('无权读取现金核销覆盖')
    if (not isinstance(cash_transaction_ref, dict)
            or set(cash_transaction_ref) != {'namespace', 'kind', 'id', 'version'}):
        _fail('现金流水来源无效')
    if (cash_transaction_ref['namespace'] != 'cash'
            or cash_transaction_ref['kind'] != 'cash_transaction'
            or type(cash_transaction_ref['version']) is not int):
        _fail('现金流水来源无效')
    try:
        cash = CashTransaction.objects.get(
            organization=member.organization,
            pk=_identifier(cash_transaction_ref['id'], '现金流水不存在或无权访问'))
    except CashTransaction.DoesNotExist:
        raise PermissionDenied('现金流水不存在或无权访问')
    if cash.version != cash_transaction_ref['version']:
        _fail('现金流水版本已变化，请刷新后重试')
    if cash.status != 'posted':
        _fail('仅已过账现金有核销覆盖')
    cleared = Clearance.objects.filter(organization=member.organization,
                                       cash_transaction_id=cash.pk,
                                       status='active').aggregate(
        total=Sum('cleared_cents'))['total'] or 0
    offset_total = Offset.objects.filter(organization=member.organization,
                                         advance_cash_transaction_id=cash.pk,
                                         status='active').aggregate(
        total=Sum('offset_cents'))['total'] or 0
    return {'cash_transaction_ref': cash.transaction_ref,
            'posted_gross_cents': cash.gross_cents, 'cleared_cents': cleared,
            'offset_cents': offset_total, 'total_cents': cleared + offset_total,
            'offset_implemented': True}
