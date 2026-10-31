"""PH-252 invoicing write/read services: record, allocate, reverse (no revenue/cash writes)."""
from datetime import date
from decimal import Decimal
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import require_member, require_project
from core.models import AccountingPeriod, AuditEvent, DomainEvent, Membership
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import (Invoice, InvoiceAllocation, InvoiceLine, InvoiceReversal, TaxQualification)

MODULE = 'invoicing'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
DIRECTIONS = {'sales', 'purchase'}
RECORD_TOPIC = 'invoicing.recorded.v1'
ALLOCATE_TOPIC = 'invoicing.lines_allocated.v1'
REVERSE_TOPIC = 'invoicing.reversed.v1'
MAX_CENTS = 10 ** 12


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看发票')
    if write and not is_enabled(fresh.organization, MODULE):
        raise PermissionDenied('发票模块已停用，不能办理')
    return fresh


def _period(member, day):
    if type(day) is not date or day > timezone.localdate():
        _fail('发票日期须为有效日期且不得未来')
    if member.organization.locked_through and day <= member.organization.locked_through:
        _fail('发票日所在原期间已锁，不能换开放月绕过')
    period = day.strftime('%Y-%m')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=period).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('发票日所在会计期间已关闭')
    return period


def _int(value, label, *, positive=False, signed=False):
    if type(value) is not int or type(value) is bool:
        _fail(f'{label}须为整数分')
    if positive and not 0 < value <= MAX_CENTS:
        _fail(f'{label}须大于0且不超过100亿元')
    if not positive and signed and not -MAX_CENTS <= value <= MAX_CENTS:
        _fail(f'{label}超出范围')
    if not positive and not signed and not 0 <= value <= MAX_CENTS:
        _fail(f'{label}超出范围')
    return value


def _split(gross, net, tax, label):
    _int(gross, label, positive=True)
    _int(net, label + '不含税', positive=False)
    _int(tax, label + '税额', positive=False)
    if gross != net + tax:
        _fail(f'{label}含税金额必须等于不含税金额加税额')
    return gross, net, tax


def _ref(value, label, namespace, kind):
    if (not isinstance(value, dict)
            or set(value) != {'namespace', 'kind', 'id', 'version'}
            or value['namespace'] != namespace or value['kind'] != kind
            or type(value['version']) is not int):
        _fail(f'{label}来源无效')
    return value


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _receipt(member, action, token, digest, dry_run):
    if type(token) is not uuid.UUID:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            _fail('request_token 无效')
    existing = AuditEvent.objects.filter(
        organization=member.organization, action=action,
        payload__request_token=str(token)).order_by('-id').first()
    if existing is not None:
        if existing.payload.get('input_digest') != digest:
            _fail('同一 request_token 的发票输入已改变，请使用新的令牌')
        return existing.payload.get('result'), token
    if dry_run:
        return None, token
    return None, token


def _canonical(member, topic, source_ref, changed_refs, anchor, payload):
    source = audit(member, 'plugin.' + topic.replace('.v1', ''), anchor, payload)
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=source, topic=topic,
        data={'event_id': str(event_id), 'topic': topic, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': source_ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': changed_refs, 'posting_event_refs': []}})
    return source


def _record_audit(member, action, obj, token, digest, result, payload):
    return audit(member, action, obj, {**payload, 'request_token': str(token),
                                       'input_digest': digest, 'result': result})


def _invoice_projection(row):
    reversals = list(row.reversals.order_by('created_at', 'id'))
    reversed_gross = sum(-item.gross_cents for item in reversals)
    lines = []
    for line in row.lines.order_by('line_no'):
        allocations = [{'ref': item.allocation_ref,
                        'business_source_ref': item.business_source_ref,
                        'gross_cents': item.gross_cents, 'net_cents': item.net_cents,
                        'tax_cents': item.tax_cents, 'status': item.status}
                       for item in line.allocations.order_by('created_at', 'id')]
        lines.append({'ref': line.line_ref, 'line_no': line.line_no,
                      'gross_cents': line.gross_cents, 'net_cents': line.net_cents,
                      'tax_cents': line.tax_cents, 'status': line.status,
                      'allocations': allocations,
                      'allocated_gross_cents': sum(item.gross_cents for item in line.allocations.all())})
    return {
        'id': str(row.pk), 'invoice_ref': row.invoice_ref, 'organization_id': str(row.organization_id),
        'direction': row.direction, 'counterparty_id': str(row.counterparty_id),
        'invoice_number': row.invoice_number, 'invoice_date': row.invoice_date.isoformat(),
        'gross_cents': row.gross_cents, 'net_cents': row.net_cents, 'tax_cents': row.tax_cents,
        'status': row.status, 'version': row.version,
        'remaining_gross_cents': row.gross_cents - reversed_gross,
        'lines': lines,
        'reversals': [{'ref': item.reversal_ref, 'reason': item.reason,
                       'reversal_date': item.reversal_date.isoformat(),
                       'gross_cents': item.gross_cents, 'net_cents': item.net_cents,
                       'tax_cents': item.tax_cents,
                       'line_reversals': item.line_reversals,
                       'allocation_reversals': item.allocation_reversals}
                      for item in reversals],
    }


def invoice_snapshot(member, invoice_id):
    member = _fresh(member)
    try:
        row = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    return _invoice_projection(row)


def invoice_list(member, *, direction=None):
    member = _fresh(member)
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    rows = Invoice.objects.filter(organization=member.organization).select_related('counterparty')
    if direction is not None:
        if direction not in DIRECTIONS:
            _fail('发票方向无效')
        rows = rows.filter(direction=direction)
    return [_invoice_projection(row) for row in rows.order_by('-invoice_date', '-created_at', 'pk')]


def _source_quota(member, ref, invoice):
    if not isinstance(ref, dict) or set(ref) != {'namespace', 'kind', 'id', 'version'}:
        _fail('业务来源引用无效')
    if invoice.direction != 'sales':
        _fail('采购发票的业务来源分配本片未支持')
    kind = (ref['namespace'], ref['kind'])
    if kind == ('contracts', 'contract_line'):
        from pmo_plugins.contracts.approval_services import approved_line_snapshot
        snapshot = approved_line_snapshot(member, ref, invoice.invoice_date)
        if str(snapshot['counterparty_id']) != str(invoice.counterparty_id):
            _fail('发票往来方与合同来源不一致')
        quota = snapshot['gross_cents']
    elif kind == ('settlement', 'open_item'):
        from pmo_plugins.settlement.models import OpenItem
        try:
            item = OpenItem.objects.select_related('project').get(
                organization=member.organization, pk=ref['id'])
        except (OpenItem.DoesNotExist, ValueError):
            raise PermissionDenied('未清项不存在或无权访问')
        require_project(member, item.project)
        if str(item.counterparty_id) != str(invoice.counterparty_id):
            _fail('发票往来方与未清项来源不一致')
        quota = item.gross_cents
    elif kind == ('settlement', 'settlement'):
        from pmo_plugins.settlement.receivable_services import settlement_snapshot
        snapshot = settlement_snapshot(member, ref['id'])
        if str(snapshot['counterparty_id']) != str(invoice.counterparty_id):
            _fail('发票往来方与结算来源不一致')
        quota = sum(item.gross_cents for item in snapshot['settlement'].items.all())
    else:
        _fail('本片仅支持合同收费明细/结算未清项/结算来源分配')
    used = InvoiceAllocation.objects.filter(
        organization=member.organization, business_source_ref=ref,
        status='active').aggregate(total=Sum('gross_cents'))['total'] or 0
    return quota, used


@transaction.atomic
def record_invoice(member, *, direction, counterparty_id, invoice_number, invoice_date,
                   gross_cents, net_cents, tax_cents, evidence, request_token, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    if direction not in DIRECTIONS:
        _fail('发票方向无效')
    number = str(invoice_number or '').strip()
    if not 1 <= len(number) <= 80:
        _fail('发票号码须为1至80字符')
    _split(gross_cents, net_cents, tax_cents, '发票')
    try:
        day = date.fromisoformat(str(invoice_date))
    except (TypeError, ValueError):
        _fail('发票日期须为YYYY-MM-DD')
    _period(member, day)
    from pmo_plugins.contracts.models import Counterparty
    try:
        counterparty = Counterparty.objects.get(organization=member.organization, pk=counterparty_id)
    except (Counterparty.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('往来方不存在或无权访问')
    payload = {'direction': direction, 'counterparty_id': str(counterparty.pk),
               'invoice_number': number, 'invoice_date': day.isoformat(),
               'gross_cents': gross_cents, 'net_cents': net_cents, 'tax_cents': tax_cents}
    digest = _digest(payload)
    receipt, token = _receipt(member, 'plugin.invoicing.invoice.recorded', request_token,
                              digest, dry_run)
    if receipt is not None:
        return receipt
    if Invoice.objects.filter(organization=member.organization, direction=direction,
                              invoice_number=number).exists():
        _fail('同一方向发票号码已存在')
    if dry_run:
        preview = Invoice(organization=member.organization, direction=direction,
                          counterparty=counterparty, invoice_number=number, invoice_date=day,
                          gross_cents=gross_cents, net_cents=net_cents, tax_cents=tax_cents,
                          created_by=member)
        return {'dry_run': True, 'invoice_ref': {'namespace': 'invoicing', 'kind': 'invoice',
                                                'id': 'preview', 'version': 1},
                'direction': direction, 'gross_cents': gross_cents}
    row = Invoice.objects.create(
        organization=member.organization, direction=direction, counterparty=counterparty,
        invoice_number=number, invoice_date=day, gross_cents=gross_cents,
        net_cents=net_cents, tax_cents=tax_cents, created_by=member)
    result = {'invoice_ref': row.invoice_ref, 'status': row.status, 'version': row.version,
              'direction': direction, 'gross_cents': gross_cents}
    _canonical(member, RECORD_TOPIC, row.invoice_ref, [row.invoice_ref], row, result)
    _record_audit(member, 'plugin.invoicing.invoice.recorded', row, token, digest, result, payload)
    return result


@transaction.atomic
def allocate_invoice_lines(member, *, invoice_id, lines, expected_version, occurred_on=None,
                           posting_period=None, request_token, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        invoice = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if invoice.status != 'active':
        _fail('仅 active 发票可以分配行')
    if type(expected_version) is not int or expected_version != invoice.version:
        _fail('发票版本已变化，请刷新后重试')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 1000:
        _fail('发票行须为1至1000行')
    seen, total = set(), 0
    for line in lines:
        if not isinstance(line, dict) or set(line) != {'line_no', 'gross_cents', 'net_cents',
                                                       'tax_cents', 'allocations'}:
            _fail('发票行字段无效')
        _int(line['line_no'], '行号', positive=True)
        if line['line_no'] in seen:
            _fail('发票行号重复')
        seen.add(line['line_no'])
        gross, net, tax = _split(line['gross_cents'], line['net_cents'], line['tax_cents'], '发票行')
        allocations = line['allocations']
        if not isinstance(allocations, list) or not 1 <= len(allocations) <= 1000:
            _fail('每行须有1至1000个分配')
        sum_gross = sum_net = sum_tax = 0
        for allocation in allocations:
            if not isinstance(allocation, dict) or set(allocation) != {
                    'business_source_ref', 'gross_cents', 'net_cents', 'tax_cents'}:
                _fail('分配合字段无效')
            ag, an, at = _split(allocation['gross_cents'], allocation['net_cents'],
                                allocation['tax_cents'], '分配')
            sum_gross += ag
            sum_net += an
            sum_tax += at
        if (sum_gross, sum_net, sum_tax) != (gross, net, tax):
            _fail('分配金额合计必须等于发票行金额')
        total += gross
    if total != invoice.gross_cents:
        _fail('发票行合计必须等于发票头金额')
    payload = {'invoice_id': str(invoice.pk), 'expected_version': expected_version,
               'lines': lines}
    digest = _digest(payload)
    receipt, token = _receipt(member, 'plugin.invoicing.invoice.allocated', request_token,
                              digest, dry_run)
    if receipt is not None:
        return receipt
    if dry_run:
        return {'dry_run': True, 'invoice_ref': invoice.invoice_ref,
                'line_count': len(lines), 'gross_cents': total}
    existing_lines = invoice.lines.count()
    if existing_lines:
        _fail('发票行已分配，不能重复分配')
    created_lines, created_allocations = [], []
    for line in lines:
        line_row = InvoiceLine.objects.create(
            organization=member.organization, invoice=invoice, line_no=line['line_no'],
            gross_cents=line['gross_cents'], net_cents=line['net_cents'],
            tax_cents=line['tax_cents'])
        created_lines.append(line_row)
        for allocation in line['allocations']:
            quota, used = _source_quota(member, allocation['business_source_ref'], invoice)
            if used + allocation['gross_cents'] > quota:
                _fail('业务来源可分配额度不足')
            row = InvoiceAllocation.objects.create(
                organization=member.organization, invoice_line=line_row,
                business_source_ref=allocation['business_source_ref'],
                gross_cents=allocation['gross_cents'], net_cents=allocation['net_cents'],
                tax_cents=allocation['tax_cents'], created_by=member)
            created_allocations.append(row)
    result = {'invoice_ref': invoice.invoice_ref,
              'line_refs': [row.line_ref for row in created_lines],
              'allocation_refs': [row.allocation_ref for row in created_allocations],
              'gross_cents': total}
    _canonical(member, ALLOCATE_TOPIC, invoice.invoice_ref,
               [invoice.invoice_ref] + [row.line_ref for row in created_lines]
               + [row.allocation_ref for row in created_allocations],
               created_allocations[0], result)
    _record_audit(member, 'plugin.invoicing.invoice.allocated', created_allocations[0],
                  token, digest, result, payload)
    return result


@transaction.atomic
def reverse_invoice(member, *, invoice_id, reason, reversal_date, line_reversals=None,
                    allocation_reversals=None, request_token, expected_version, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        invoice = Invoice.objects.get(organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if invoice.status != 'active':
        _fail('仅 active 发票可以红冲')
    if type(expected_version) is not int or expected_version != invoice.version:
        _fail('发票版本已变化，请刷新后重试')
    text = str(reason or '').strip()
    if not 1 <= len(text) <= 500:
        _fail('红冲原因须为1至500字符')
    try:
        day = date.fromisoformat(str(reversal_date))
    except (TypeError, ValueError):
        _fail('红冲日期须为YYYY-MM-DD')
    _period(member, day)
    reversed_gross = sum(-row.gross_cents for row in invoice.reversals.all())
    remaining = invoice.gross_cents - reversed_gross
    if remaining <= 0:
        _fail('发票已全额红冲')
    lines = list(invoice.lines.order_by('line_no'))
    allocations = list(InvoiceAllocation.objects.filter(
        organization=member.organization, invoice_line__invoice=invoice, status='active'))
    line_map = {str(line.pk): line for line in lines}
    allocation_map = {str(row.pk): row for row in allocations}
    prior_line, prior_allocation = {}, {}
    for row in invoice.reversals.all():
        for item in row.line_reversals or []:
            ref = item.get('original_line_ref') or {}
            used = prior_line.setdefault(ref.get('id'), [0, 0, 0])
            used[0] += abs(item.get('gross_cents', 0))
            used[1] += abs(item.get('net_cents', 0))
            used[2] += abs(item.get('tax_cents', 0))
        for item in row.allocation_reversals or []:
            ref = item.get('original_allocation_ref') or {}
            used = prior_allocation.setdefault(ref.get('id'), [0, 0, 0])
            used[0] += abs(item.get('gross_cents', 0))
            used[1] += abs(item.get('net_cents', 0))
            used[2] += abs(item.get('tax_cents', 0))
    if (line_reversals is None) != (allocation_reversals is None):
        _fail('红冲须同时提供行与分配明细，或均留空表示全额')
    if line_reversals is None:
        line_reversals = []
        for line in lines:
            used = prior_line.get(str(line.pk), [0, 0, 0])
            if line.gross_cents - used[0] > 0:
                line_reversals.append({'original_line_ref': line.line_ref,
                                       'gross_cents': -(line.gross_cents - used[0]),
                                       'net_cents': -(line.net_cents - used[1]),
                                       'tax_cents': -(line.tax_cents - used[2])})
    if allocation_reversals is None:
        allocation_reversals = []
        for row in allocations:
            used = prior_allocation.get(str(row.pk), [0, 0, 0])
            if row.gross_cents - used[0] > 0:
                allocation_reversals.append({
                    'original_allocation_ref': row.allocation_ref,
                    'original_line_ref': row.invoice_line.line_ref,
                    'business_source_ref': row.business_source_ref,
                    'gross_cents': -(row.gross_cents - used[0]),
                    'net_cents': -(row.net_cents - used[1]),
                    'tax_cents': -(row.tax_cents - used[2])})
    if not isinstance(line_reversals, list) or not isinstance(allocation_reversals, list):
        _fail('红冲明细须为数组')
    seen_lines = set()
    for item in line_reversals:
        if not isinstance(item, dict) or set(item) != {
                'original_line_ref', 'gross_cents', 'net_cents', 'tax_cents'}:
            _fail('行红冲字段无效')
        ref = item['original_line_ref']
        if (not isinstance(ref, dict) or set(ref) != {'namespace', 'kind', 'id', 'version'}
                or ref.get('namespace') != 'invoicing' or ref.get('kind') != 'invoice_line'):
            _fail('行红冲引用无效')
        line = line_map.get(str(ref.get('id')))
        if line is None:
            _fail('行红冲引用不属于本发票')
        if str(ref.get('id')) in seen_lines:
            _fail('同一发票行不能重复红冲')
        seen_lines.add(str(ref.get('id')))
        gross = _int(item['gross_cents'], '行红冲含税', signed=True)
        net = _int(item['net_cents'], '行红冲不含税', signed=True)
        tax = _int(item['tax_cents'], '行红冲税额', signed=True)
        if gross >= 0 or gross - net - tax != 0:
            _fail('行红冲金额须为负值且含税=不含税+税额')
        used = prior_line.get(str(line.pk), [0, 0, 0])
        if (abs(gross) > line.gross_cents - used[0]
                or abs(net) > line.net_cents - used[1]
                or abs(tax) > line.tax_cents - used[2]):
            _fail('行红冲金额超过该行未冲销余额')
    seen_allocations = set()
    for item in allocation_reversals:
        if not isinstance(item, dict) or set(item) != {
                'original_allocation_ref', 'original_line_ref', 'business_source_ref',
                'gross_cents', 'net_cents', 'tax_cents'}:
            _fail('分配红冲字段无效')
        ref = item['original_allocation_ref']
        if (not isinstance(ref, dict) or set(ref) != {'namespace', 'kind', 'id', 'version'}
                or ref.get('namespace') != 'invoicing'
                or ref.get('kind') != 'invoice_allocation'):
            _fail('分配红冲引用无效')
        row = allocation_map.get(str(ref.get('id')))
        if row is None:
            _fail('分配红冲引用不属于本发票')
        if str(ref.get('id')) in seen_allocations:
            _fail('同一分配不能重复红冲')
        seen_allocations.add(str(ref.get('id')))
        if str((item['original_line_ref'] or {}).get('id')) != str(row.invoice_line_id):
            _fail('分配红冲的行引用不匹配')
        if item['business_source_ref'] != row.business_source_ref:
            _fail('分配红冲的业务来源不匹配')
        gross = _int(item['gross_cents'], '分配红冲含税', signed=True)
        net = _int(item['net_cents'], '分配红冲不含税', signed=True)
        tax = _int(item['tax_cents'], '分配红冲税额', signed=True)
        if gross >= 0 or gross - net - tax != 0:
            _fail('分配红冲金额须为负值且含税=不含税+税额')
        used = prior_allocation.get(str(row.pk), [0, 0, 0])
        if (abs(gross) > row.gross_cents - used[0]
                or abs(net) > row.net_cents - used[1]
                or abs(tax) > row.tax_cents - used[2]):
            _fail('分配红冲金额超过该分配未冲销余额')
    total_gross = sum(item['gross_cents'] for item in line_reversals)
    total_net = sum(item['net_cents'] for item in line_reversals)
    total_tax = sum(item['tax_cents'] for item in line_reversals)
    if total_gross >= 0 or total_gross - total_net - total_tax != 0:
        _fail('红冲金额须为负值且含税=不含税+税额')
    if abs(total_gross) > remaining:
        _fail('红冲金额超过原票未冲销余额')
    alloc_gross = sum(item['gross_cents'] for item in allocation_reversals)
    alloc_net = sum(item['net_cents'] for item in allocation_reversals)
    alloc_tax = sum(item['tax_cents'] for item in allocation_reversals)
    if (alloc_gross, alloc_net, alloc_tax) != (total_gross, total_net, total_tax):
        _fail('行红冲与分配红冲金额不一致')
    payload = {'invoice_id': str(invoice.pk), 'expected_version': expected_version,
               'reason': text, 'reversal_date': day.isoformat(),
               'line_reversals': line_reversals, 'allocation_reversals': allocation_reversals}
    digest = _digest(payload)
    receipt, token = _receipt(member, 'plugin.invoicing.invoice.reversed', request_token,
                              digest, dry_run)
    if receipt is not None:
        return receipt
    if dry_run:
        return {'dry_run': True, 'invoice_ref': invoice.invoice_ref,
                'gross_cents': total_gross, 'full': abs(total_gross) == remaining}
    row = InvoiceReversal.objects.create(
        organization=member.organization, invoice=invoice, reason=text, reversal_date=day,
        line_reversals=line_reversals, allocation_reversals=allocation_reversals,
        gross_cents=total_gross, net_cents=total_net, tax_cents=total_tax, created_by=member)
    full = abs(total_gross) == remaining
    if full:
        invoice.status = 'reversed'
        invoice.save(update_fields=['status'])
    result = {'invoice_ref': invoice.invoice_ref, 'reversal_ref': row.reversal_ref,
              'gross_cents': total_gross, 'net_cents': total_net, 'tax_cents': total_tax,
              'full': full, 'status': invoice.status}
    _canonical(member, REVERSE_TOPIC, invoice.invoice_ref,
               [row.reversal_ref, invoice.invoice_ref], row, result)
    _record_audit(member, 'plugin.invoicing.invoice.reversed', row, token, digest, result, payload)
    return result


# ---------------------------------------------------------------------------
# PH-320: tax qualification confirmation, cross-owner reference links and
# read-only tax/orchestration projection. Reuses PH-252 models/services; does
# not reimplement record/allocate/reverse and never writes revenue/cash/ledger.
# ---------------------------------------------------------------------------

TAX_ACTIONS = {
    'opened': 'plugin.invoicing.tax_qualification_opened',
    'confirmed': 'plugin.invoicing.tax_qualification_confirmed',
    'superseded': 'plugin.invoicing.tax_qualification_superseded',
    'linked': 'plugin.invoicing.orchestration_linked',
}
LINK_KINDS = {'revenue_reversal', 'cash_refund', 'cash_bank_return',
              'settlement_adjustment'}
QUALIFICATION_STATUSES = {'recoverable', 'not_recoverable'}


def _json_list(value, label):
    if not isinstance(value, list) or not value or len(value) > 1000:
        _fail(f'{label}须为非空JSON数组且不超过1000项')
    for item in value:
        if not isinstance(item, dict) or not item:
            _fail(f'{label}项须为非空对象')
    return value


def _source_invoice(member, source_ref):
    """Resolve an invoicing source ref to its invoice, with tenant validation."""
    if not isinstance(source_ref, dict) or set(source_ref) != {'namespace', 'kind', 'id', 'version'}:
        _fail('税额来源引用无效')
    if source_ref.get('namespace') != 'invoicing' or type(source_ref.get('version')) is not int:
        _fail('税额来源引用无效')
    kind, raw_id = source_ref.get('kind'), source_ref.get('id')
    try:
        if kind == 'invoice':
            row = Invoice.objects.get(organization=member.organization, pk=raw_id)
            return row, 'invoice', row.tax_cents
        if kind == 'invoice_line':
            row = InvoiceLine.objects.select_related('invoice').get(
                organization=member.organization, pk=raw_id)
            return row.invoice, 'invoice_line', row.tax_cents
        if kind == 'invoice_allocation':
            row = InvoiceAllocation.objects.select_related('invoice_line__invoice').get(
                organization=member.organization, pk=raw_id)
            return row.invoice_line.invoice, 'invoice_allocation', row.tax_cents
        if kind == 'invoice_reversal':
            row = InvoiceReversal.objects.select_related('invoice').get(
                organization=member.organization, pk=raw_id)
            return row.invoice, 'invoice_reversal', abs(row.tax_cents)
    except (Invoice.DoesNotExist, InvoiceLine.DoesNotExist,
            InvoiceAllocation.DoesNotExist, InvoiceReversal.DoesNotExist,
            ValueError, TypeError):
        raise PermissionDenied('税额来源发票不存在或无权访问')
    _fail('税额来源类型未支持')


def _qualification_ref(row):
    return {'namespace': 'invoicing', 'kind': 'tax_qualification',
            'id': str(row.pk), 'version': row.version}


def _qualification_projection(row, *, source_ids=None):
    return {
        'ref': _qualification_ref(row), 'source_ref': row.source_ref,
        'tax_cents': row.tax_cents, 'status': row.status,
        'qualification_status': row.qualification_status,
        'decided_on': row.decided_on.isoformat() if row.decided_on else None,
        'evidence': row.evidence, 'version': row.version,
        'created_at': row.created_at.isoformat(),
        'effective': row.status == 'confirmed',
    }


@transaction.atomic
def open_tax_qualification(member, *, source_ref, tax_cents, evidence, request_token,
                           dry_run=False):
    """Create one pending tax-qualification fact for an existing invoicing source."""
    member = _fresh(member, write=True)
    lock_company(member)
    invoice, source_kind, source_tax = _source_invoice(member, source_ref)
    _int(tax_cents, '税额', positive=False)
    if tax_cents > source_tax:
        _fail('税额资格金额不得超过来源税额')
    evidence = _json_list(evidence, '证据')
    payload = {'source_ref': source_ref, 'tax_cents': tax_cents, 'evidence': evidence}
    digest = _digest(payload)
    receipt, token = _receipt(member, TAX_ACTIONS['opened'], request_token, digest, dry_run)
    if receipt is not None:
        return receipt
    if TaxQualification.objects.filter(
            organization=member.organization, source_ref=source_ref,
            status__in=('pending', 'confirmed')).exists():
        _fail('同一来源已有待确认或已确认税额资格')
    if dry_run:
        return {'dry_run': True, 'source_ref': source_ref, 'tax_cents': tax_cents,
                'status': 'pending'}
    row = TaxQualification.objects.create(
        organization=member.organization, source_ref=source_ref, tax_cents=tax_cents,
        status='pending', qualification_status='unknown', evidence=evidence)
    result = {'qualification_ref': _qualification_ref(row), 'source_ref': source_ref,
              'tax_cents': tax_cents, 'status': 'pending', 'version': row.version,
              'source_kind': source_kind, 'invoice_ref': invoice.invoice_ref}
    _record_audit(member, TAX_ACTIONS['opened'], row, token, digest, result, payload)
    return result


@transaction.atomic
def confirm_tax_qualification(member, qualification_id, *, qualification_status,
                              decided_on, evidence, request_token, expected_version,
                              dry_run=False):
    """pending -> confirmed (recoverable/not_recoverable), append-only transition."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        row = TaxQualification.objects.select_for_update().get(
            organization=member.organization, pk=qualification_id)
    except (TaxQualification.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('税额资格不存在或无权访问')
    if type(expected_version) is not int or expected_version != row.version:
        _fail('税额资格版本已变化，请刷新后重试')
    if row.status != 'pending' or row.qualification_status != 'unknown':
        _fail('仅待确认税额资格可以确认')
    if qualification_status not in QUALIFICATION_STATUSES:
        _fail('税额资格确认结果无效')
    try:
        day = date.fromisoformat(str(decided_on))
    except (TypeError, ValueError):
        _fail('确认日期须为YYYY-MM-DD')
    _period(member, day)
    evidence = _json_list(evidence, '证据')
    if TaxQualification.objects.filter(
            organization=member.organization, source_ref=row.source_ref,
            status='confirmed').exclude(pk=row.pk).exists():
        _fail('同一来源已有有效税额资格，请使用更正/取代流程')
    payload = {'qualification_id': str(row.pk), 'qualification_status': qualification_status,
               'decided_on': day.isoformat(), 'evidence': evidence,
               'expected_version': expected_version}
    digest = _digest(payload)
    receipt, token = _receipt(member, TAX_ACTIONS['confirmed'], request_token, digest, dry_run)
    if receipt is not None:
        return receipt
    if dry_run:
        return {'dry_run': True, 'qualification_ref': _qualification_ref(row),
                'status': 'confirmed', 'qualification_status': qualification_status,
                'decided_on': day.isoformat()}
    updated = TaxQualification.objects.filter(
        pk=row.pk, organization=member.organization, status='pending',
        version=row.version).update(
        status='confirmed', qualification_status=qualification_status,
        decided_on=day, evidence=evidence, version=row.version + 1)
    if updated != 1:
        _fail('税额资格确认并发冲突，请刷新后重试')
    row.refresh_from_db()
    result = {'qualification_ref': _qualification_ref(row), 'source_ref': row.source_ref,
              'status': 'confirmed', 'qualification_status': qualification_status,
              'decided_on': day.isoformat(), 'version': row.version}
    _record_audit(member, TAX_ACTIONS['confirmed'], row, token, digest, result, payload)
    return result


@transaction.atomic
def supersede_tax_qualification(member, qualification_id, *, qualification_status,
                                decided_on, evidence, request_token, expected_version,
                                dry_run=False):
    """Append a replacement confirmed fact and mark the old one superseded."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        old = TaxQualification.objects.select_for_update().get(
            organization=member.organization, pk=qualification_id)
    except (TaxQualification.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('税额资格不存在或无权访问')
    if type(expected_version) is not int or expected_version != old.version:
        _fail('税额资格版本已变化，请刷新后重试')
    if old.status != 'confirmed':
        _fail('仅已确认税额资格可以更正/取代')
    if qualification_status not in QUALIFICATION_STATUSES:
        _fail('税额资格确认结果无效')
    try:
        day = date.fromisoformat(str(decided_on))
    except (TypeError, ValueError):
        _fail('确认日期须为YYYY-MM-DD')
    _period(member, day)
    evidence = _json_list(evidence, '证据')
    payload = {'qualification_id': str(old.pk), 'qualification_status': qualification_status,
               'decided_on': day.isoformat(), 'evidence': evidence,
               'expected_version': expected_version, 'supersedes': _qualification_ref(old)}
    digest = _digest(payload)
    receipt, token = _receipt(member, TAX_ACTIONS['superseded'], request_token, digest, dry_run)
    if receipt is not None:
        return receipt
    if dry_run:
        return {'dry_run': True, 'supersedes': _qualification_ref(old),
                'qualification_status': qualification_status, 'decided_on': day.isoformat()}
    updated = TaxQualification.objects.filter(
        pk=old.pk, organization=member.organization, status='confirmed',
        version=old.version).update(status='superseded', version=old.version + 1)
    if updated != 1:
        _fail('税额资格取代并发冲突，请刷新后重试')
    replacement = TaxQualification.objects.create(
        organization=member.organization, source_ref=old.source_ref,
        tax_cents=old.tax_cents, status='confirmed',
        qualification_status=qualification_status, decided_on=day,
        evidence=evidence + [{'kind': 'tax_supersedes', 'supersedes': _qualification_ref(old)}])
    result = {'qualification_ref': _qualification_ref(replacement),
              'supersedes': _qualification_ref(old), 'source_ref': replacement.source_ref,
              'status': 'confirmed', 'qualification_status': qualification_status,
              'decided_on': day.isoformat(), 'version': replacement.version}
    _record_audit(member, TAX_ACTIONS['superseded'], replacement, token, digest, result, payload)
    return result


def _owner_ref_value(member, link_kind, owner_ref):
    if (not isinstance(owner_ref, dict)
            or set(owner_ref) != {'namespace', 'kind', 'id', 'version'}):
        _fail('跨 owner 引用无效')
    namespace, kind, raw_id = owner_ref.get('namespace'), owner_ref.get('kind'), owner_ref.get('id')
    try:
        if link_kind == 'revenue_reversal':
            from pmo_plugins.management_revenue.models import RevenueAdjustment
            row = RevenueAdjustment.objects.get(
                organization=member.organization, pk=raw_id, status='posted')
        elif link_kind == 'cash_refund':
            from pmo_plugins.cash.models import Refund
            row = Refund.objects.get(organization=member.organization, pk=raw_id, status='active')
        elif link_kind == 'cash_bank_return':
            from pmo_plugins.cash.models import BankReturn
            row = BankReturn.objects.get(organization=member.organization, pk=raw_id,
                                         status='active')
        else:
            if kind == 'clearance':
                from pmo_plugins.settlement.models import Clearance
                row = Clearance.objects.get(organization=member.organization, pk=raw_id)
            elif kind == 'open_item':
                from pmo_plugins.settlement.models import OpenItem
                row = OpenItem.objects.get(organization=member.organization, pk=raw_id)
            else:
                from pmo_plugins.settlement.models import Settlement
                row = Settlement.objects.get(organization=member.organization, pk=raw_id)
    except Exception:
        _fail('跨 owner 引用不存在、不可读或不属于本公司')
    return row


@transaction.atomic
def link_invoice_orchestration(member, *, invoice_id, link_kind, owner_ref, evidence,
                               request_token, expected_version, reversal_id=None, dry_run=False):
    """Record an append-only reference to an owner fact after that owner service acted.

    This service does not mutate revenue/cash/settlement and does not claim a
    single global transaction; it validates the target, records the link in the
    invoicing audit stream, and the read projection exposes the reference chain.
    """
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        invoice = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if type(expected_version) is not int or expected_version != invoice.version:
        _fail('发票版本已变化，请刷新后重试')
    if link_kind not in LINK_KINDS:
        _fail('跨 owner 编排类型无效')
    reversal = None
    if reversal_id is not None:
        try:
            reversal = InvoiceReversal.objects.get(
                organization=member.organization, invoice=invoice, pk=reversal_id)
        except (InvoiceReversal.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied('发票红冲不存在或不属于该发票')
    _owner_ref_value(member, link_kind, owner_ref)
    if not isinstance(evidence, list) or not evidence:
        _fail('编排证据须为非空JSON数组')
    payload = {'invoice_id': str(invoice.pk), 'reversal_id': str(reversal.pk) if reversal else None,
               'link_kind': link_kind, 'owner_ref': owner_ref, 'evidence': evidence,
               'expected_version': expected_version}
    digest = _digest(payload)
    receipt, token = _receipt(member, TAX_ACTIONS['linked'], request_token, digest, dry_run)
    if receipt is not None:
        return receipt
    existing = AuditEvent.objects.filter(
        organization=member.organization, action=TAX_ACTIONS['linked'],
        payload__invoice_ref__id=str(invoice.pk),
        payload__link_kind=link_kind,
        payload__owner_ref__id=str(owner_ref['id'])).exists()
    if existing:
        _fail('该发票与 owner 事实的编排引用已存在')
    if dry_run:
        return {'dry_run': True, 'invoice_ref': invoice.invoice_ref,
                'reversal_ref': reversal.reversal_ref if reversal else None,
                'link_kind': link_kind, 'owner_ref': owner_ref}
    result = {'invoice_ref': invoice.invoice_ref,
              'reversal_ref': reversal.reversal_ref if reversal else None,
              'link_kind': link_kind, 'owner_ref': owner_ref, 'status': 'linked'}
    _record_audit(member, TAX_ACTIONS['linked'], invoice, token, digest, result,
                  {**payload, 'invoice_ref': invoice.invoice_ref,
                   'reversal_ref': result['reversal_ref']})
    return result


def _invoice_tax_qualifications(member, invoice):
    source_ids = {str(invoice.pk)}
    source_ids.update(str(line.pk) for line in invoice.lines.all())
    source_ids.update(str(item.pk) for item in invoice.reversals.all())
    rows = []
    for row in TaxQualification.objects.filter(
            organization=member.organization).order_by('created_at', 'id'):
        source = row.source_ref or {}
        if source.get('namespace') == 'invoicing' and str(source.get('id')) in source_ids:
            rows.append(row)
    return rows


def _orchestration_links(member, invoice):
    links = []
    for event in AuditEvent.objects.filter(
            organization=member.organization,
            action=TAX_ACTIONS['linked']).order_by('-id')[:500]:
        payload = event.payload or {}
        invoice_ref = payload.get('invoice_ref') or {}
        ref = payload.get('invoice_id')
        if str(invoice_ref.get('id') or ref) != str(invoice.pk):
            continue
        owner_ref = payload.get('owner_ref') or {}
        exists = True
        try:
            _owner_ref_value(member, payload.get('link_kind'), owner_ref)
        except (ValidationError, PermissionDenied):
            exists = False
        links.append({'link_kind': payload.get('link_kind'),
                      'owner_ref': owner_ref, 'exists': exists,
                      'reversal_ref': payload.get('reversal_ref'),
                      'recorded_at': event.created_at.isoformat(),
                      'actor_id': str(event.actor_id or '')})
    return links


def invoice_tax_snapshot(member, invoice_id):
    member = _fresh(member)
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    try:
        invoice = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    qualifications = _invoice_tax_qualifications(member, invoice)
    links = _orchestration_links(member, invoice)
    confirmed = [row for row in qualifications if row.status == 'confirmed']
    effective = confirmed[-1] if confirmed else None
    issues = []
    if invoice.tax_cents > 0 and not qualifications:
        issues.append({'issue_code': 'tax_qualification_missing',
                       'refs': [{'invoice_ref': invoice.invoice_ref}],
                       'expected': 'pending/confirmed tax qualification',
                       'actual': None})
    if any(row.status == 'pending' for row in qualifications):
        issues.append({'issue_code': 'tax_qualification_pending',
                       'refs': [{'qualification_ref': _qualification_ref(row)}
                                for row in qualifications if row.status == 'pending'],
                       'expected': 'confirmed qualification', 'actual': 'pending'})
    if any(row.status == 'superseded' for row in qualifications):
        issues.append({'issue_code': 'tax_qualification_superseded',
                       'refs': [{'qualification_ref': _qualification_ref(row)}
                                for row in qualifications if row.status == 'superseded'],
                       'expected': 'one effective confirmed qualification',
                       'actual': 'superseded history present'})
    for link in links:
        if not link['exists']:
            issues.append({'issue_code': 'orchestration_ref_missing',
                           'refs': [{'owner_ref': link['owner_ref']}],
                           'expected': 'readable owner fact', 'actual': 'missing'})
    if effective is not None and effective.qualification_status == 'not_recoverable':
        issues.append({'issue_code': 'not_recoverable_costing_review_required',
                       'refs': [{'qualification_ref': _qualification_ref(effective)}],
                       'expected': 'costing owner open-period correction/reference',
                       'actual': 'not_recoverable'})
    return {
        'invoice': _invoice_projection(invoice),
        'tax_qualifications': [_qualification_projection(row) for row in qualifications],
        'effective_qualification': _qualification_projection(effective) if effective else None,
        'orchestration_links': links,
        'issues': issues,
        'missing': [] if effective or invoice.tax_cents == 0 else ['effective_qualification'],
    }


def invoice_tax_list(member):
    member = _fresh(member)
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    rows = []
    for invoice in Invoice.objects.filter(
            organization=member.organization).select_related('counterparty').order_by(
            '-invoice_date', '-created_at')[:200]:
        qualification = invoice_tax_snapshot(member, invoice.pk)
        rows.append({'invoice': qualification['invoice'],
                     'effective_qualification': qualification['effective_qualification'],
                     'orchestration_link_count': len(qualification['orchestration_links']),
                     'issue_codes': [item['issue_code'] for item in qualification['issues']]})
    return rows
