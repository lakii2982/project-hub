"""PH-371 tax-qualification -> costing open-period cost adjustment.

Only a confirmed, not_recoverable TaxQualification creates an append-only costing
adjustment batch. Invoicing facts are referenced, never modified; no ledger,
revenue, cash or open-item fact is written. Recoverable/pending qualifications
produce no cost. Reversal is a negative append-only batch.
"""
import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_member
from core.models import AccountingPeriod, AuditEvent, Membership
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import (SharedCostAllocationBatch, SharedCostAllocationLine,
                     SharedCostAllocationReceipt)
from pmo_plugins.invoicing.models import (Invoice, InvoiceAllocation, TaxQualification)

MODULE = 'costing'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
ACTION = 'tax.cost.adjust'
REVERSE_ACTION = 'tax.cost.reverse'
POOL_KIND = 'tax_not_recoverable'
DRIVER = 'fixed_weights'
ALGORITHM = 'tax_not_recoverable_cost_adjust_v1'


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看税额成本调整')
    if write and not is_enabled(fresh.organization, MODULE):
        raise PermissionDenied('成本模块已停用，不能办理税额成本调整')
    return fresh


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _period(member, day):
    if type(day) is not date or day > timezone.localdate():
        _fail('调整日期须为有效日期且不得未来')
    if member.organization.locked_through and day <= member.organization.locked_through:
        _fail('调整日所在原期间已锁，不能换开放月绕过')
    period = day.strftime('%Y-%m')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=period).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('调整日所在会计期间已关闭')
    return period


def _qualification(member, qualification_id):
    try:
        row = TaxQualification.objects.get(organization=member.organization, pk=qualification_id)
    except (TaxQualification.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('税额资格不存在或无权访问')
    return row


def _receipt(member, action, token, digest, dry_run):
    if type(token) is not uuid.UUID:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            _fail('request_token 无效')
    existing = SharedCostAllocationReceipt.objects.filter(
        organization=member.organization, request_token=token).first()
    if existing is not None:
        if existing.action != action or existing.input_digest != digest:
            _fail('同一 request_token 的税额成本调整输入已改变，请使用新的令牌')
        return existing, token
    if dry_run:
        return None, token
    return None, token


def _record(member, action, token, digest, result):
    return SharedCostAllocationReceipt.objects.create(
        organization=member.organization, request_token=token, action=action,
        input_digest=digest, result=result, created_by=member)


def _project_weights(invoice, amount):
    rows = list(InvoiceAllocation.objects.filter(
        invoice_line__invoice=invoice, status='active').select_related(
        'invoice_line__invoice'))
    weights = {}
    for row in rows:
        source = row.business_source_ref or {}
        if source.get('namespace') == 'contracts' and source.get('kind') == 'contract_line':
            from pmo_plugins.contracts.models import ContractLine
            line = ContractLine.objects.filter(pk=source.get('id')).select_related(
                'contract_version__contract').first()
            if line is None:
                continue
            project_id = str(line.contract_version.contract.project_id)
            weights[project_id] = weights.get(project_id, 0) + row.gross_cents
    total = sum(weights.values())
    if not total:
        return {}, []
    allocated = []
    remaining = amount
    keys = sorted(weights)
    for index, key in enumerate(keys):
        if index == len(keys) - 1:
            cents = remaining
        else:
            cents = int((Decimal(amount) * Decimal(weights[key]) / Decimal(total)).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP))
        remaining -= cents
        allocated.append((key, cents, weights[key]))
    basis = [max(1, int((Decimal(weights[key]) * 10000 / Decimal(total)).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))) for key in keys]
    # absorb basis-point rounding on the last project so total is exactly 10000
    basis[-1] += 10000 - sum(basis)
    return {key: basis[index] for index, key in enumerate(keys)}, allocated


@transaction.atomic
def adjust_tax_cost(member, qualification_id, *, request_token, dry_run=True):
    """confirmed/not_recoverable tax -> append-only costing adjustment batch."""
    member = _fresh(member, write=True)
    lock_company(member)
    row = _qualification(member, qualification_id)
    if row.status != 'confirmed' or row.qualification_status != 'not_recoverable':
        _fail('仅 confirmed/not_recoverable 税额资格可产生成本调整')
    if row.tax_cents <= 0:
        _fail('税额资格金额必须为正')
    source = row.source_ref or {}
    if source.get('namespace') != 'invoicing' or source.get('kind') != 'invoice':
        _fail('税额资格来源必须是发票')
    try:
        invoice = Invoice.objects.get(organization=member.organization, pk=source.get('id'))
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    day = row.decided_on or invoice.invoice_date
    period = _period(member, day)
    payload = {'qualification_id': str(row.pk), 'tax_cents': row.tax_cents,
               'period': period, 'decided_on': day.isoformat()}
    digest = _digest(payload)
    receipt, token = _receipt(member, ACTION, request_token, digest, dry_run)
    if receipt is not None:
        return receipt.result
    existing = SharedCostAllocationBatch.objects.filter(
        organization=member.organization, pool_key=f'tax:{row.pk}',
        kind='allocation', status='posted').first()
    if existing is not None:
        _fail('该税额资格已产生成本调整，不能重复调整')
    weights, allocated = _project_weights(invoice, row.tax_cents)
    if dry_run:
        return {'dry_run': True, 'qualification_ref': {'namespace': 'invoicing',
                                                       'kind': 'tax_qualification',
                                                       'id': str(row.pk), 'version': row.version},
                'period': period, 'amount_cents': row.tax_cents,
                'allocations': [{'project_id': key, 'basis_points': weights[key],
                                 'allocated_cents': cents} for key, cents, _ in allocated]}
    batch = SharedCostAllocationBatch.objects.create(
        organization=member.organization, period=period, pool_key=f'tax:{row.pk}',
        pool_kind=POOL_KIND, driver=DRIVER, kind='allocation', status='draft',
        amount_cents=row.tax_cents, total_basis_points=10000,
        source_refs=[{'namespace': 'invoicing', 'kind': 'tax_qualification',
                      'id': str(row.pk), 'version': row.version},
                     {'namespace': 'invoicing', 'kind': 'invoice',
                      'id': str(invoice.pk), 'version': invoice.version}],
        source_digest=_digest([source, invoice.invoice_ref]),
        input_digest=digest, algorithm_version=ALGORITHM, created_by=member)
    for index, (project_id, cents, _) in enumerate(allocated, start=1):
        SharedCostAllocationLine.objects.create(
            organization=member.organization, batch=batch, sequence=index,
            target_kind='project', target_id=project_id,
            basis_points=weights[project_id], allocated_cents=cents)
    batch.status = 'posted'
    batch.save()
    result = {'batch_ref': batch.source_ref, 'qualification_ref': {
        'namespace': 'invoicing', 'kind': 'tax_qualification', 'id': str(row.pk),
        'version': row.version}, 'period': period, 'amount_cents': row.tax_cents,
        'status': 'posted'}
    _record(member, ACTION, token, digest, result)
    audit(member, 'plugin.costing.tax_cost_adjusted', batch,
          {'batch_ref': batch.source_ref, 'qualification_ref': result['qualification_ref'],
           'period': period, 'amount_cents': row.tax_cents,
           'algorithm_version': ALGORITHM})
    return result


@transaction.atomic
def reverse_tax_cost_adjustment(member, batch_id, *, reason, request_token, dry_run=True):
    """Append-only negative reversal of one posted tax cost adjustment batch."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        batch = SharedCostAllocationBatch.objects.get(
            organization=member.organization, pk=batch_id, kind='allocation',
            pool_kind=POOL_KIND, status='posted')
    except (SharedCostAllocationBatch.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('税额成本调整不存在或无权访问')
    text = str(reason or '').strip()
    if not 1 <= len(text) <= 500:
        _fail('冲销原因须为1至500字符')
    if SharedCostAllocationBatch.objects.filter(
            organization=member.organization, reverses_batch=batch, kind='reversal').exists():
        _fail('该税额成本调整已冲销')
    payload = {'batch_id': str(batch.pk), 'amount_cents': -batch.amount_cents, 'reason': text}
    digest = _digest(payload)
    receipt, token = _receipt(member, REVERSE_ACTION, request_token, digest, dry_run)
    if receipt is not None:
        return receipt.result
    if dry_run:
        return {'dry_run': True, 'batch_ref': batch.source_ref,
                'amount_cents': -batch.amount_cents, 'period': batch.period}
    reversal = SharedCostAllocationBatch.objects.create(
        organization=member.organization, period=batch.period, pool_key=batch.pool_key,
        pool_kind=POOL_KIND, driver=DRIVER, kind='reversal', status='draft',
        amount_cents=-batch.amount_cents, total_basis_points=10000,
        source_refs=[batch.source_ref], source_digest=batch.source_digest,
        input_digest=digest, algorithm_version=ALGORITHM, reverses_batch=batch,
        reason=text, created_by=member)
    reversal.status = 'posted'
    reversal.save()
    result = {'reversal_ref': reversal.source_ref, 'batch_ref': batch.source_ref,
              'amount_cents': -batch.amount_cents, 'period': batch.period,
              'status': 'posted'}
    _record(member, REVERSE_ACTION, token, digest, result)
    audit(member, 'plugin.costing.tax_cost_reversed', reversal,
          {'reversal_ref': reversal.source_ref, 'batch_ref': batch.source_ref,
           'amount_cents': -batch.amount_cents, 'reason': text})
    return result


def tax_adjustment_list(member):
    member = _fresh(member)
    return [{'batch_ref': row.source_ref, 'period': row.period, 'kind': row.kind,
             'amount_cents': row.amount_cents, 'status': row.status,
             'reverses_batch': row.reverses_batch_id and str(row.reverses_batch_id)}
            for row in SharedCostAllocationBatch.objects.filter(
                organization=member.organization, pool_kind=POOL_KIND).order_by(
                'created_at', 'id')]
