"""PH-330 shared-cost allocation facts and net-zero cost reclassification.

Implements PH-305/PH-313: only posted nonproject costing cost postings form a
pool, weights are integer basis points summing to 10000 with deterministic
clamped half-up last-item absorption, allocation appends a net-zero accounting
event (source negative, project positive), reversal mirrors exactly. No second
ledger and no revenue/cash/settlement writes.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, LedgerPosting,
                         Membership, NonProjectDestination, Project, SourceConsumption)
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company, require_member
from pmo_plugins.costing.models import (SharedCostAllocationBatch,
                                        SharedCostAllocationLine,
                                        SharedCostAllocationReceipt)

MODULE = 'costing'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
DRIVERS = {'revenue_cents', 'approved_minutes', 'direct_cost_cents', 'fixed_weights'}
POOL_KINDS = {'labor_nonproject', 'labor_unallocated'}
ALGORITHM = 'basis_points_clamped_half_up_last_absorb_v1'
EVENT_ALLOC = 'costing.shared_cost.allocated'
EVENT_REVERSE = 'costing.shared_cost.reversed'


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(row)
    if row.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看共享费用分摊')
    if write and not is_enabled(row.organization, MODULE):
        raise PermissionDenied('成本模块已停用，不能办理分摊')
    return row


def _period(member, value):
    if not isinstance(value, str) or len(value) != 7 or value[4] != '-':
        _fail('分摊期间须为YYYY-MM')
    try:
        start = date.fromisoformat(value + '-01')
    except ValueError:
        _fail('分摊期间须为YYYY-MM')
    if start.strftime('%Y-%m') != value or start > timezone.localdate():
        _fail('分摊期间须为有效且不晚于本月的YYYY-MM')
    if member.organization.locked_through and start <= member.organization.locked_through:
        _fail('分摊期间已锁账')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=value).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('分摊期间已关闭')
    return start


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _canonical_refs(refs):
    return json.loads(json.dumps(refs, sort_keys=True, ensure_ascii=False, default=str))


def _pool_sources(member, period, pool_kind):
    start = date.fromisoformat(period + '-01')
    destination = {str(row.pk): row.code for row in NonProjectDestination.objects.filter(
        organization=member.organization, active=True)}
    rows = list(LedgerPosting.objects.select_related('accounting_event', 'reverses_posting').filter(
        organization=member.organization, book='cost', origin='current', status='posted',
        scope_kind='nonproject', accounting_event__posting_period=period,
        reverses_posting__isnull=True).exclude(
        accounting_event__source__kind='shared_cost_allocation').order_by('id'))
    reversed_ids = set(LedgerPosting.objects.filter(
        organization=member.organization, reverses_posting__in=rows,
        reverses_posting__isnull=False).values_list('reverses_posting_id', flat=True))
    sources = []
    for row in rows:
        if row.pk in reversed_ids:
            continue
        code = destination.get(row.scope_id)
        if code is None:
            continue
        if pool_kind == 'labor_unallocated':
            if code != 'UNALLOCATED':
                continue
        elif code == 'UNALLOCATED':
            continue
        sources.append({'posting_ref': {'namespace': 'core', 'kind': 'ledger_posting',
                                        'id': str(row.pk), 'version': row.version},
                        'amount_cents': row.amount_cents, 'scope_kind': 'nonproject',
                        'scope_id': row.scope_id})
    amount = sum(item['amount_cents'] for item in sources)
    return sources, amount


def _weights_from_values(member, values):
    positive = [(str(pk), int(value)) for pk, value in values.items() if int(value) > 0]
    if not positive:
        _fail('分摊驱动数据缺失或全为0，不能猜测权重')
    total = sum(value for _, value in positive)
    exact = [Decimal(value) * Decimal(10000) / Decimal(total) for _, value in positive]
    floors = [int(item) for item in exact]
    remaining = 10000 - sum(floors)
    order = sorted(range(len(positive)),
                   key=lambda i: (-(exact[i] - floors[i]), positive[i][0]))
    for index in order[:remaining]:
        floors[index] += 1
    return {positive[i][0]: floors[i] for i in range(len(positive))}


def _driver_weights(member, period, driver, fixed_weights):
    projects = {str(pk): pk for pk in Project.objects.filter(
        organization=member.organization).exclude(
        stage__in=(Project.Stage.CANCELLED,)).values_list('pk', flat=True)}
    if driver == 'fixed_weights':
        if not isinstance(fixed_weights, list) or not fixed_weights:
            _fail('固定权重须为非空列表')
        weights = {}
        for item in fixed_weights:
            if not isinstance(item, dict) or set(item) != {'target_kind', 'target_id', 'basis_points'}:
                _fail('固定权重项无效')
            if item['target_kind'] != 'project':
                _fail('固定权重目标仅支持项目')
            target_id = str(item['target_id'])
            if target_id not in projects:
                _fail('固定权重项目不存在、已取消或不属于本公司')
            bp = item['basis_points']
            if type(bp) is not int or not 1 <= bp <= 10000 or target_id in weights:
                _fail('固定权重须为1..10000的整数且目标不重复')
            weights[target_id] = bp
        if sum(weights.values()) != 10000:
            _fail('固定权重合计须为10000')
        return weights
    values = {}
    if driver == 'revenue_cents':
        from pmo_plugins.management_revenue.services import project_revenue_summary
        for project_id in projects.values():
            summary = project_revenue_summary(member, project_id, as_of=date.fromisoformat(period + '-01').isoformat())
            values[str(project_id)] = summary['management_revenue_cents']
    elif driver == 'approved_minutes':
        from pmo_plugins.time.models import BillableConfirmation
        from pmo_plugins.contracts.models import ContractLine
        rows = BillableConfirmation.objects.filter(
            organization=member.organization, status='confirmed').values(
            'contract_line_id').annotate(total=Sum('confirmed_minutes'))
        line_projects = {str(pk): str(project) for pk, project in ContractLine.objects.filter(
            organization=member.organization).values_list(
            'pk', 'contract_version__contract__project_id')}
        for row in rows:
            project_id = line_projects.get(str(row['contract_line_id']))
            if project_id in projects:
                values[project_id] = values.get(project_id, 0) + (row['total'] or 0)
    elif driver == 'direct_cost_cents':
        rows = LedgerPosting.objects.filter(
            organization=member.organization, book='cost', origin='current', status='posted',
            scope_kind='project', accounting_event__posting_period=period).values(
            'scope_id').annotate(total=Sum('amount_cents'))
        for row in rows:
            if row['scope_id'] in projects:
                values[row['scope_id']] = row['total'] or 0
    else:
        _fail('分摊驱动无效')
    return _weights_from_values(member, values)


def _allocate_amounts(amount, weights):
    if type(amount) is not int or amount < 0 or amount > 10**12:
        _fail('分摊金额须为非负整数分')
    targets = sorted(weights, key=lambda key: key)
    result, remaining = {}, amount
    for index, target in enumerate(targets):
        if index == len(targets) - 1:
            alloc = remaining
        else:
            exact = (Decimal(amount) * Decimal(weights[target]) / Decimal(10000))
            alloc = min(int(exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP)), remaining)
            remaining -= alloc
        result[target] = alloc
    return result


def _receipt(member, action, token, digest, dry_run):
    if type(token) is not uuid.UUID:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            _fail('request_token 无效')
    row = SharedCostAllocationReceipt.objects.filter(
        organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, row.result
    if dry_run:
        return token, None
    return token, None


def _post_reallocation(member, batch, *, reversal=False):
    event_id = uuid.uuid4()
    event_ref = {'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id), 'version': 1}
    claim = SourceConsumption.objects.create(
        organization=member.organization, source=batch.source_ref, source_id=batch.pk,
        consumer='costing_shared', coverage_key=f'shared_cost:{batch.pk}',
        amount_cents=abs(batch.amount_cents), event_ref=event_ref)
    event = AccountingEvent.objects.create(
        id=event_id, organization=member.organization, version=1, status='posted',
        source={'namespace': 'costing', 'kind': 'shared_cost_allocation',
                'id': str(batch.pk), 'version': 1},
        event_type=EVENT_REVERSE if reversal else EVENT_ALLOC,
        occurred_on=date.fromisoformat(batch.period + '-01'), posting_period=batch.period,
        algorithm_version=batch.algorithm_version, claim=claim, claim_effect_cents=0)
    index = 1
    for source in batch.source_refs:
        LedgerPosting.objects.create(
            organization=member.organization, accounting_event=event, version=1,
            status='posted', book='cost', origin='current',
            amount_cents=(source['amount_cents'] if reversal else -source['amount_cents']),
            scope_kind='nonproject', scope_id=source['scope_id'], line_index=index)
        index += 1
    for line in batch.lines.order_by('sequence'):
        LedgerPosting.objects.create(
            organization=member.organization, accounting_event=event, version=1,
            status='posted', book='cost', origin='current',
            amount_cents=line.allocated_cents,
            scope_kind='project', scope_id=line.target_id, line_index=index)
        index += 1
    if event.postings.aggregate(total=Sum('amount_cents'))['total'] != 0:
        _fail('分摊账本事件必须净额为0')
    return event


@transaction.atomic
def create_shared_cost_allocation_batch(member, *, period, pool_kind, driver,
                                        request_token, fixed_weights=None, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    start = _period(member, period)
    if pool_kind not in POOL_KINDS:
        _fail('费用池类型无效')
    if driver not in DRIVERS:
        _fail('分摊驱动无效')
    sources, amount = _pool_sources(member, period, pool_kind)
    if not sources:
        _fail('费用池缺少已过账 nonproject 来源')
    if amount <= 0:
        _fail('费用池金额缺失或非正，不能分摊')
    source_digest = _digest(_canonical_refs(sources))
    weights = _driver_weights(member, period, driver, fixed_weights)
    allocs = _allocate_amounts(amount, weights)
    source_refs = _canonical_refs(sources)
    pool_key = hashlib.sha256(json.dumps(
        {'org': str(member.organization_id), 'period': period, 'pool_kind': pool_kind,
         'source_digest': source_digest}, sort_keys=True).encode()).hexdigest()
    for active in SharedCostAllocationBatch.objects.filter(
            organization=member.organization, period=period, status='posted',
            kind='allocation').exclude(pool_key=pool_key):
        active_ids = {item['posting_ref']['id'] for item in active.source_refs}
        if active_ids & {item['posting_ref']['id'] for item in sources}:
            _fail('同一来源已属于另一个有效费用池批次')
    if SharedCostAllocationBatch.objects.filter(
            organization=member.organization, period=period, pool_key=pool_key,
            kind='allocation', status='posted').exists():
        _fail('该费用池期间已有有效分摊批次')
    payload = {'period': period, 'pool_kind': pool_kind, 'driver': driver,
               'amount_cents': amount, 'source_digest': source_digest,
               'weights': weights, 'algorithm_version': ALGORITHM}
    digest = _digest(payload)
    token, existing = _receipt(member, 'allocate', request_token, digest, dry_run)
    if existing is not None:
        return existing
    if dry_run:
        return {'dry_run': True, 'period': period, 'pool_kind': pool_kind, 'driver': driver,
                'amount_cents': amount, 'pool_key': pool_key, 'source_digest': source_digest,
                'algorithm_version': ALGORITHM,
                'allocations': [{'target_kind': 'project', 'target_id': key,
                                 'basis_points': weights[key], 'allocated_cents': allocs[key]}
                                for key in sorted(allocs)]}
    batch = SharedCostAllocationBatch.objects.create(
        organization=member.organization, period=period, pool_key=pool_key,
        pool_kind=pool_kind, driver=driver, kind='allocation', status='draft',
        amount_cents=amount, total_basis_points=10000, source_refs=source_refs,
        source_digest=source_digest, input_digest=digest, algorithm_version=ALGORITHM,
        created_by=member)
    for index, target in enumerate(sorted(allocs), start=1):
        SharedCostAllocationLine.objects.create(
            organization=member.organization, batch=batch, sequence=index,
            target_kind='project', target_id=target, basis_points=weights[target],
            allocated_cents=allocs[target])
    _post_reallocation(member, batch)
    batch.status = 'posted'
    batch.save()
    result = {'batch_ref': batch.source_ref, 'period': period, 'pool_kind': pool_kind,
              'driver': driver, 'amount_cents': amount, 'status': 'posted',
              'algorithm_version': ALGORITHM,
              'allocations': [{'target_kind': 'project',
                               'target_id': line.target_id,
                               'basis_points': line.basis_points,
                               'allocated_cents': line.allocated_cents}
                              for line in batch.lines.order_by('sequence')]}
    SharedCostAllocationReceipt.objects.create(
        organization=member.organization, request_token=token, action='allocate',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.shared_cost_allocated', batch, {
        'batch_ref': batch.source_ref, 'amount_cents': amount, 'pool_key': pool_key})
    return result


@transaction.atomic
def reverse_shared_cost_allocation_batch(member, batch_id, *, reason, request_token,
                                         expected_version=1, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        original = SharedCostAllocationBatch.objects.select_for_update().get(
            organization=member.organization, pk=batch_id, kind='allocation')
    except (SharedCostAllocationBatch.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('分摊批次不存在或无权访问')
    if original.status != 'posted':
        _fail('仅有效未冲销分摊批次可以冲销')
    if expected_version != 1:
        _fail('分摊批次版本冲突')
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        _fail('冲销原因须为1至500字')
    if SharedCostAllocationBatch.objects.filter(reverses_batch=original).exists():
        _fail('该分摊批次已冲销')
    payload = {'batch_id': str(original.pk), 'reason': reason.strip()}
    digest = _digest(payload)
    token, existing = _receipt(member, 'reverse', request_token, digest, dry_run)
    if existing is not None:
        return existing
    if dry_run:
        return {'dry_run': True, 'batch_ref': original.source_ref,
                'amount_cents': -original.amount_cents, 'kind': 'reversal'}
    reversal = SharedCostAllocationBatch.objects.create(
        organization=member.organization, period=original.period, pool_key=original.pool_key,
        pool_kind=original.pool_kind, driver=original.driver, kind='reversal', status='draft',
        amount_cents=-original.amount_cents, total_basis_points=10000,
        source_refs=original.source_refs, source_digest=original.source_digest,
        input_digest=digest, algorithm_version=original.algorithm_version,
        reverses_batch=original, reason=reason.strip(), created_by=member)
    for line in original.lines.order_by('sequence'):
        SharedCostAllocationLine.objects.create(
            organization=member.organization, batch=reversal, sequence=line.sequence,
            target_kind=line.target_kind, target_id=line.target_id,
            basis_points=line.basis_points, allocated_cents=-line.allocated_cents)
    _post_reallocation(member, reversal, reversal=True)
    reversal.status = 'posted'
    reversal.save()
    original.status = 'reversed'
    original.save()
    result = {'batch_ref': reversal.source_ref, 'reverses_batch': original.source_ref,
              'amount_cents': reversal.amount_cents, 'kind': 'reversal', 'status': 'posted'}
    SharedCostAllocationReceipt.objects.create(
        organization=member.organization, request_token=token, action='reverse',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.shared_cost_reversed', reversal, result)
    return result


def shared_cost_reconciliation(member, *, period, pool_kind=None, as_of=None):
    member = _fresh(member)
    if member.role == 'pm':
        raise PermissionDenied('项目经理无法查看公司共享费用池，请使用项目成本摘要')
    start = _period(member, period)
    kinds = [pool_kind] if pool_kind else sorted(POOL_KINDS)
    batches = SharedCostAllocationBatch.objects.filter(
        organization=member.organization, period=period).prefetch_related('lines')
    rows, issues = [], []
    for kind in kinds:
        sources, amount = _pool_sources(member, period, kind)
        active = batches.filter(pool_kind=kind, kind='allocation', status='posted').first()
        reversal = batches.filter(pool_kind=kind, kind='reversal', status='posted').first()
        allocated = sum(line.allocated_cents for line in (active.lines.all() if active else []))
        reversed_amount = abs(reversal.amount_cents) if reversal else 0
        unallocated = amount - allocated
        rows.append({'pool_kind': kind, 'pool_amount_cents': amount,
                     'allocated_cents': allocated if active else 0,
                     'unallocated_cents': unallocated,
                     'batch_ref': active.source_ref if active else None,
                     'reversal_ref': reversal.source_ref if reversal else None,
                     'reversed_cents': reversed_amount,
                     'source_count': len(sources),
                     'missing': [] if sources else ['pool_source']})
        if pool_kind and not sources:
            issues.append({'issue_code': 'pool_source_missing',
                           'refs': [{'pool_kind': kind}], 'expected': 'posted pool sources',
                           'actual': None})
        if active and allocated != amount:
            issues.append({'issue_code': 'pool_amount_mismatch',
                           'refs': [{'batch_ref': active.source_ref}],
                           'expected': amount, 'actual': allocated})
        if unallocated < 0:
            issues.append({'issue_code': 'over_allocated',
                           'refs': [{'pool_kind': kind}], 'expected': '>=0',
                           'actual': unallocated})
    return {'period': period, 'pools': rows, 'issues': issues,
            'missing': [] if any(row['missing'] == [] for row in rows) else ['pool_sources'],
            'algorithm_version': ALGORITHM}


def integrity_errors():
    """Read-only verifier branch for shared allocation facts (no writes)."""
    errors = []
    for batch in SharedCostAllocationBatch.objects.all():
        lines = list(batch.lines.order_by('sequence'))
        total = sum(line.allocated_cents for line in lines)
        if total != batch.amount_cents:
            errors.append(f'shared_allocation:{batch.pk}:line_total')
        if len(lines) != len({line.sequence for line in lines}):
            errors.append(f'shared_allocation:{batch.pk}:sequence')
        if sum(line.basis_points for line in lines) != batch.total_basis_points:
            errors.append(f'shared_allocation:{batch.pk}:basis_total')
        if batch.kind == 'allocation':
            if any(line.allocated_cents < 0 for line in lines):
                errors.append(f'shared_allocation:{batch.pk}:sign')
            if batch.amount_cents < 0:
                errors.append(f'shared_allocation:{batch.pk}:amount_sign')
        if batch.kind == 'reversal':
            if batch.reverses_batch is None or batch.amount_cents != -batch.reverses_batch.amount_cents:
                errors.append(f'shared_allocation:{batch.pk}:reversal_amount')
                continue
            original = {line.target_id: line for line in batch.reverses_batch.lines.all()}
            if len(original) != len(lines):
                errors.append(f'shared_allocation:{batch.pk}:reversal_lines')
            for line in lines:
                source = original.get(line.target_id)
                if (source is None or source.sequence != line.sequence
                        or source.basis_points != line.basis_points
                        or line.allocated_cents != -source.allocated_cents):
                    errors.append(f'shared_allocation:{batch.pk}:reversal_mirror:{line.target_id}')
        if batch.status == 'posted' and batch.kind == 'allocation':
            if SharedCostAllocationBatch.objects.filter(
                    organization_id=batch.organization_id, period=batch.period,
                    pool_key=batch.pool_key, kind='allocation', status='posted').exclude(
                    pk=batch.pk).exists():
                errors.append(f'shared_allocation:{batch.pk}:duplicate_active')
        event = AccountingEvent.objects.filter(
            organization_id=batch.organization_id,
            source__namespace='costing', source__kind='shared_cost_allocation',
            source__id=str(batch.pk)).first()
        if event is None:
            errors.append(f'shared_allocation:{batch.pk}:missing_event')
        elif event.postings.aggregate(total=Sum('amount_cents'))['total'] != 0:
            errors.append(f'shared_allocation:{batch.pk}:event_net_zero')
    return errors
