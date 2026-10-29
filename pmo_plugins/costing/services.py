"""Monthly labor business services; all accounting writes are delegated to core."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from core.access import require_member, require_project, visible_projects
from core.models import (Membership, Employee, Timesheet, NonProjectDestination,
                         LaborCutover, AccountingEvent, DomainEvent)
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company
from .models import SalaryPool, CostPolicy, AllocationRun, AllocationTarget, CostEvent, LaborReceipt

METHOD = 'monthly_comprehensive_capacity_v1'
ALGORITHM = 'monthly_group_half_up_largest_remainder_v1'
MAX_CENTS = 10**12


def _fail(message):
    raise ValidationError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


def _fresh(member, write=False):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in ({'owner', 'finance'} if write else {'owner', 'finance', 'auditor'}):
        raise PermissionDenied('无权读取或办理工资池')
    if not is_enabled(member.organization, 'costing'):
        raise PermissionDenied('月度人工模块已停用')
    return member


def labor_reader(member):
    return _fresh(member)


def _locked(member):
    member = _fresh(member, True)
    lock_company(member)
    return _fresh(member, True)


def _text(value, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        _fail('请填写完整依据或原因，并遵守字段长度')
    return value.strip()


def _date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _fail('日期无效')


def _period(value):
    if not isinstance(value, str) or len(value) != 7:
        _fail('月份须为YYYY-MM')
    first = _date(value + '-01')
    return first.strftime('%Y-%m')


def _token(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail('请求标识无效，请刷新表单')


def _version(pool, expected):
    if type(expected) is not int or expected != pool.version:
        _fail('记录已变更，请刷新后重试')


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _money(value):
    if type(value) is not int or not 0 <= value <= MAX_CENTS:
        _fail('金额须为0至100亿元的整数分')
    return value


def _components(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        _fail('至少填写一项工资构成，最多1000项')
    result, used = [], set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {'component_code', 'amount_cents'}:
            _fail('构成只允许名称和整数分金额；批准证据由服务器生成')
        code = _text(item['component_code'], 100)
        if code.casefold() in used:
            _fail('工资构成名称不能重复')
        used.add(code.casefold())
        result.append({'component_code': code, 'amount_cents': _money(item['amount_cents'])})
    _money(sum(item['amount_cents'] for item in result))
    return sorted(result, key=lambda item: item['component_code'])


def _pool(member, pool_id):
    try:
        return SalaryPool.objects.select_related('employee__member', 'original_policy', 'recognition_event').get(
            organization=member.organization, pk=pool_id)
    except (SalaryPool.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('工资池不存在或无权访问')


def _employee(member, employee_id):
    try:
        employee = Employee.objects.select_related('member').get(pk=employee_id, organization=member.organization, active=True)
    except (Employee.DoesNotExist, TypeError, ValueError, ValidationError):
        _fail('员工不存在、跨公司或已停用')
    return employee


def _coverage(member, employee, period, covered):
    from core.labor_accounting import check_labor_period
    _employee(member, employee.pk)
    check_labor_period(member.organization, period, covered)
    first = _date(period + '-01')
    if covered.strftime('%Y-%m') != period or covered > timezone.localdate():
        _fail('发生截止须在本月且不得未来')
    if member.organization.locked_through and first <= member.organization.locked_through:
        _fail('工资覆盖整段触及已锁日期')
    cutover = LaborCutover.objects.filter(organization=member.organization, status='approved').first()
    if not cutover or first < cutover.effective_from:
        _fail('该月尚未批准启用月度人工模式')


def _independent(member, pool, proposer=None):
    ids = {pool.created_by_id, pool.submitted_by_id}
    if proposer is not None:
        ids.add(proposer)
    if member.pk in ids:
        _fail('制单、提交或容量提出人不能自行批准')
    employee = _employee(member, pool.employee_id)
    if employee.member_id and employee.member.user_id == member.user_id:
        _fail('工资业务本人不能自行确认或冲销')


def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token, digest = _token(request_token), _digest(payload)
    found = LaborReceipt.objects.filter(organization=member.organization, action=action, request_token=token).first()
    if found:
        if found.normalized_digest != digest:
            _fail('同一请求标识已用于不同输入')
        if dry_run:
            return token, digest, {'dry_run': True, 'action': action, 'existing': True, 'result': deepcopy(found.result_snapshot)}
        if found.result_ref['kind'] == 'allocation_run':
            return token, digest, AllocationRun.objects.get(pk=found.result_ref['id'], organization=member.organization)
        return token, digest, _pool(member, found.salary_pool_id)
    return token, digest, None


def _finish(member, action, token, digest, pool, result):
    kind = 'allocation_run' if isinstance(result, AllocationRun) else 'salary_pool'
    ref = {'namespace': 'costing', 'kind': kind, 'id': str(result.pk), 'version': result.version}
    LaborReceipt.objects.create(organization=member.organization, action=action, request_token=token,
        normalized_digest=digest, salary_pool=pool, result_ref=ref,
        result_snapshot={'source': ref, 'status': result.status})
    return result


def _preview(action, pool=None, **values):
    return {'dry_run': True, 'action': action, 'pool_id': str(pool.pk) if pool else None, **values}


def _active(pool):
    if pool.status not in {'approved', 'reconciled'} or not pool.recognition_event_id or pool.reversed_event_id:
        _fail('工资尚未确认或已完整冲销')
    snapshot = pool.approved_salary_snapshot
    if (not snapshot or snapshot.get('version') != pool.approved_source_version or
        str(snapshot.get('employee_id')) != str(pool.employee_id) or snapshot.get('period') != pool.period or
        snapshot.get('covered_through') != pool.covered_through.isoformat() or
        snapshot.get('valuation_state') != pool.valuation_state or not pool.original_policy_id or
        snapshot.get('policy_version') != pool.original_policy.policy_version):
        _fail('原批准工资来源或政策与不可变快照不一致')
    if AccountingEvent.objects.filter(reverses_event_id=pool.recognition_event_id).exists():
        _fail('原公司工资事件已反冲，不能继续分配')


def _event(member, audit_row, source, topic, accounting_event, changed):
    event_id = uuid.uuid4()
    DomainEvent.objects.create(event_id=event_id, organization=member.organization, source=audit_row, topic=topic,
        data={'event_id': str(event_id), 'topic': topic, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': source,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': changed, 'posting_event_refs': [
                  {'namespace': 'core', 'kind': 'accounting_event', 'id': str(accounting_event.pk), 'version': 1}]}})


def _ref(row, kind, namespace='costing'):
    return {'namespace': namespace, 'kind': kind, 'id': str(row.pk), 'version': row.version}


def get_salary_pool(member, pool_id):
    return _pool(labor_reader(member), pool_id)


def list_salary_pools(member, *, period=None, employee_id=None):
    member = labor_reader(member)
    rows = SalaryPool.objects.filter(organization=member.organization).select_related('employee')
    if period:
        rows = rows.filter(period=_period(period))
    if employee_id:
        employee = _employee(member, employee_id)
        rows = rows.filter(employee=employee)
    return rows


def get_labor_detail(member, pool_id):
    pool = get_salary_pool(member, pool_id)
    return {'pool': pool, 'policies': pool.policies.order_by('created_at'),
            'runs': pool.allocation_runs.prefetch_related('targets').order_by('created_at'),
            'active_run': pool.allocation_runs.filter(status='posted').first(),
            'events': pool.cost_events.select_related('core_accounting_event').order_by('created_at')}


@transaction.atomic
def save_salary_pool(member, *, employee_id, period, valuation_state, covered_through, components,
                     reference, request_token, pool_id=None, expected_version=None, dry_run=True):
    member = _locked(member)
    employee, period, covered = _employee(member, employee_id), _period(period), _date(covered_through)
    components, reference = _components(components), _text(reference)
    if valuation_state not in ('planned', 'incurred_provisional', 'reconciled_actual'):
        _fail('工资估值状态无效')
    _coverage(member, employee, period, covered)
    payload = {'employee': employee.pk, 'period': period, 'valuation': valuation_state, 'covered': covered,
        'components': components, 'reference': reference, 'pool': str(pool_id) if pool_id else None, 'expected': expected_version}
    token, digest, replay = _receipt(member, 'save', request_token, payload, dry_run)
    if replay is not None:
        return replay
    if pool_id:
        pool = _pool(member, pool_id)
        _version(pool, expected_version)
        if pool.status != 'draft' or pool.submitted_at:
            _fail('已提交或已批准工资池不可修改')
        if pool.employee_id != employee.pk or pool.period != period:
            _fail('不能修改工资池的员工或月份')
    else:
        if expected_version is not None or SalaryPool.objects.filter(organization=member.organization, employee=employee, period=period).exists():
            _fail('该员工月份已有原始工资池，请查看原来源；不能换请求重建')
        pool = SalaryPool(organization=member.organization, employee=employee, period=period, created_by=member)
    if dry_run:
        return _preview('save', pool if pool_id else None, total_cents=sum(i['amount_cents'] for i in components))
    if pool_id:
        pool.version += 1
    pool.draft_components, pool.reference = components, reference
    pool.covered_through, pool.valuation_state = covered, valuation_state
    pool.save()
    audit(member, 'costing.pool.saved', pool, {'version': pool.version})
    return _finish(member, 'save', token, digest, pool, pool)


@transaction.atomic
def submit_salary_pool(member, pool_id, *, expected_version, request_token, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id)
    token, digest, replay = _receipt(member, 'submit', request_token, {'pool': str(pool.pk), 'expected': expected_version}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version)
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    if pool.status != 'draft' or pool.submitted_at:
        _fail('工资池已提交或已处理')
    _components(pool.draft_components)
    if dry_run:
        return _preview('submit', pool)
    pool.submitted_by, pool.submitted_at = member, timezone.now()
    pool.version += 1; pool.save()
    audit(member, 'costing.pool.submitted', pool, {'version': pool.version})
    return _finish(member, 'submit', token, digest, pool, pool)


@transaction.atomic
def confirm_salary_pool(member, pool_id, *, expected_version, request_token, review_note, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id); note = _text(review_note)
    token, digest, replay = _receipt(member, 'confirm', request_token,
        {'pool': str(pool.pk), 'expected': expected_version, 'note': note}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version); _independent(member, pool)
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    if pool.status != 'draft' or not pool.submitted_at or pool.recognition_event_id:
        _fail('仅能独立确认已提交的首次工资发生额')
    if pool.valuation_state not in {'incurred_provisional', 'reconciled_actual'}:
        _fail('预计工资不能确认成实际成本')
    components = _components(pool.draft_components)
    if dry_run:
        return _preview('confirm', pool, total_cents=sum(i['amount_cents'] for i in components))
    approval = audit(member, 'plugin.costing.salary.confirmed', pool,
        {'components': components, 'reference': pool.reference, 'review_note': note,
         'period': pool.period, 'covered_through': pool.covered_through})
    evidence = {'kind': 'approval', 'evidence_id': str(approval.pk), 'sha256': approval.event_hash}
    approved_components = [{**item, 'evidence': [evidence]} for item in components]
    policy = CostPolicy.objects.create(organization=member.organization, employee=pool.employee, salary_pool=pool,
        period=pool.period, covered_through=pool.covered_through, component_snapshot=approved_components,
        capacity_confirmed=False, denominator_minutes=None, approved_by=member, approved_at=timezone.now(),
        approval_audit=approval, calendar_digest=_digest({'method': METHOD, 'components': components}))
    pool.status = 'reconciled' if pool.valuation_state == 'reconciled_actual' else 'approved'
    pool.version += 1; pool.approved_source_version = pool.version
    pool.original_policy, pool.approved_by, pool.approved_at, pool.approval_audit = policy, member, timezone.now(), approval
    pool.approved_salary_snapshot = {'id': str(pool.pk), 'version': pool.approved_source_version,
        'employee_id': str(pool.employee_id), 'period': pool.period, 'covered_through': pool.covered_through.isoformat(),
        'valuation_state': pool.valuation_state, 'salary_components': approved_components,
        'total_comprehensive_cost_cents': sum(i['amount_cents'] for i in components), 'policy_version': policy.policy_version,
        'reference': pool.reference, 'source_evidence': [evidence], 'approved_by_member_id': str(member.pk)}
    pool.save()
    from core.labor_accounting import post_salary_confirmation
    accounting = post_salary_confirmation(member, pool)
    pool.recognition_event = accounting; pool.save(update_fields=['recognition_event'])
    bridge = CostEvent.objects.create(organization=member.organization, salary_pool=pool, core_accounting_event=accounting)
    _event(member, approval, _ref(bridge, 'cost_event'), 'costing.cost_posted.v1', accounting,
           [pool.source_ref, _ref(bridge, 'cost_event'), _ref(policy, 'cost_policy')])
    return _finish(member, 'confirm', token, digest, pool, pool)


@transaction.atomic
def propose_capacity(member, pool_id, *, capacity_minutes, calendar_basis, expected_version, request_token, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id)
    if type(capacity_minutes) is not int or not 1 <= capacity_minutes <= 1000000:
        _fail('确认产能须为1至1000000整数分钟')
    basis = _text(calendar_basis)
    token, digest, replay = _receipt(member, 'capacity_propose', request_token,
        {'pool': str(pool.pk), 'expected': expected_version, 'capacity': capacity_minutes, 'basis': basis}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version); _active(pool)
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    if pool.allocation_runs.filter(status='posted').exists():
        _fail('先撤销有效分配，不能覆盖其产能依据')
    if dry_run:
        return _preview('capacity_propose', pool)
    pool.capacity_proposal = {'capacity_minutes': capacity_minutes, 'calendar_basis': basis,
                              'salary_source_version': pool.approved_source_version}
    pool.capacity_proposed_by = member; pool.version += 1; pool.save()
    audit(member, 'costing.capacity.proposed', pool, pool.capacity_proposal)
    return _finish(member, 'capacity_propose', token, digest, pool, pool)


@transaction.atomic
def confirm_capacity(member, pool_id, *, expected_version, request_token, review_note, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id); note = _text(review_note)
    token, digest, replay = _receipt(member, 'capacity_confirm', request_token,
        {'pool': str(pool.pk), 'expected': expected_version, 'note': note}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version); _active(pool)
    if member.pk == pool.capacity_proposed_by_id:
        _fail('容量提出人不能自行批准容量')
    employee = _employee(member, pool.employee_id)
    if employee.member_id and employee.member.user_id == member.user_id:
        _fail('真实绑定工资本人不能批准容量')
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    proposal = pool.capacity_proposal
    if not proposal or proposal.get('salary_source_version') != pool.approved_source_version:
        _fail('没有待独立确认的同源产能提议')
    if pool.allocation_runs.filter(status='posted').exists():
        _fail('有效分配期间不能改变产能政策')
    if dry_run:
        return _preview('capacity_confirm', pool)
    approval = audit(member, 'costing.capacity.confirmed', pool, {'proposal': proposal, 'review_note': note})
    CostPolicy.objects.create(organization=member.organization, employee=pool.employee, salary_pool=pool,
        predecessor_policy=pool.original_policy, period=pool.period, covered_through=pool.covered_through,
        component_snapshot=deepcopy(pool.approved_salary_snapshot['salary_components']), capacity_confirmed=True,
        denominator_minutes=proposal['capacity_minutes'], calendar_basis=proposal['calendar_basis'],
        calendar_digest=_digest(proposal), approved_by=member, approved_at=timezone.now(), approval_audit=approval)
    pool.capacity_proposal = {}; pool.capacity_proposed_by = None; pool.version += 1; pool.save()
    return _finish(member, 'capacity_confirm', token, digest, pool, pool)


def _allocation(pool, policy):
    if (not policy.capacity_confirmed or not policy.denominator_minutes or policy.salary_pool_id != pool.pk or
        policy.employee_id != pool.employee_id or policy.period != pool.period or policy.covered_through != pool.covered_through or
        policy.component_snapshot != pool.approved_salary_snapshot['salary_components'] or policy.status != 'approved' or
        policy.method != METHOD or policy.algorithm != ALGORITHM or
        policy.calendar_digest != _digest({'capacity_minutes': policy.denominator_minutes,
            'calendar_basis': policy.calendar_basis, 'salary_source_version': pool.approved_source_version})):
        _fail('容量政策不是该工资来源同覆盖的有效批准快照')
    first = _date(pool.period + '-01')
    rows = []
    for row in Timesheet.objects.filter(organization_id=pool.organization_id,
            status='approved', accounting_mode='monthly_pool').select_related('project', 'non_project_destination').order_by('id'):
        snapshot = row.approved_input_snapshot
        actual_in_scope = row.employee_id == pool.employee_id and first <= row.work_date <= pool.covered_through
        frozen_in_scope = (isinstance(snapshot, dict) and str(snapshot.get('employee_id')) == str(pool.employee_id)
            and isinstance(snapshot.get('work_date'), str)
            and first.isoformat() <= snapshot['work_date'] <= pool.covered_through.isoformat())
        if actual_in_scope or frozen_in_scope:
            rows.append(row)
    if Timesheet.objects.filter(organization_id=pool.organization_id, employee=pool.employee, work_date__gte=first, work_date__lte=pool.covered_through,
        status='submitted', accounting_mode='monthly_pool').exists():
        _fail('覆盖期还有待批投入，请先完成投入核对')
    grouped, inputs = {}, []
    for row in rows:
        snapshot = row.approved_input_snapshot
        if not snapshot:
            _fail('已批投入缺少不可变批准快照')
        if row.project_id:
            if row.project.organization_id != pool.organization_id:
                _fail('投入项目跨公司')
            key = ('project', str(row.project_id))
        else:
            destination = row.non_project_destination
            if not destination or destination.organization_id != pool.organization_id or not destination.active or destination.code == 'UNALLOCATED':
                _fail('非项目投入去向无效，未分配不能作为伪投入')
            key = ('non_project_pool', str(destination.pk))
        expected_target = 'project' if row.project_id else 'non_project_pool'
        if (str(snapshot.get('id')) != str(row.pk) or str(snapshot.get('employee_id')) != str(row.employee_id) or
            snapshot.get('work_date') != row.work_date.isoformat() or
            type(snapshot.get('minutes')) is not int or snapshot['minutes'] != row.minutes or
            snapshot.get('target_kind') != expected_target or str(snapshot.get('target_id')) != key[1] or
            str(snapshot.get('project_id')) != str(row.project_id) or
            str(snapshot.get('non_project_destination_id')) != str(row.non_project_destination_id) or
            str(snapshot.get('submitted_by')) != str(row.submitted_by_id) or
            str(snapshot.get('reviewed_by')) != str(row.reviewed_by_id) or
            type(snapshot.get('version')) is not int or snapshot['version'] != row.version):
            _fail('已批投入与冻结来源不一致，拒绝重新解释原分钟')
        grouped[key] = grouped.get(key, 0) + row.minutes
        inputs.append({'timesheet_ref': {'namespace': 'time', 'kind': 'timesheet', 'id': str(row.pk), 'version': snapshot['version']},
                       'target_kind': 'project' if row.project_id else 'nonproject', 'target_id': key[1], 'minutes': row.minutes})
    capacity, total = policy.denominator_minutes, pool.total_comprehensive_cost_cents
    used = sum(grouped.values())
    if used > capacity:
        _fail('真实已批分钟超过确认产能，请核对容量和加班依据')
    try:
        unallocated = NonProjectDestination.objects.get(organization_id=pool.organization_id, code='UNALLOCATED', active=True)
    except NonProjectDestination.DoesNotExist:
        _fail('公司未分配去向不存在')
    grouped[('non_project_pool', str(unallocated.pk))] = capacity - used
    raw = [(kind, target, weight, Decimal(total) * Decimal(weight) / Decimal(capacity))
           for (kind, target), weight in sorted(grouped.items())]
    amounts = { (kind, target): int(amount.to_integral_value(rounding=ROUND_FLOOR)) for kind, target, _, amount in raw }
    residual = int(sum((entry[3] for entry in raw), Decimal(0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) - sum(amounts.values())
    for kind, target, _, amount in sorted(raw, key=lambda x: (-(x[3] - Decimal(amounts[(x[0], x[1])])), x[0], x[1]))[:residual]:
        amounts[(kind, target)] += 1
    targets = [{'target_kind': kind, 'target_id': target, 'weight': weight, 'allocated_cents': amounts[(kind, target)]}
               for kind, target, weight, _ in raw]
    if sum(item['allocated_cents'] for item in targets) != total:
        _fail('分配整数结果未守恒')
    if len(targets) + 2 > 1000:
        _fail('分配目标超过单次事件允许范围')
    return inputs, targets


@transaction.atomic
def allocate_pool(member, pool_id, *, capacity_policy_id, expected_version, request_token, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id)
    token, digest, replay = _receipt(member, 'allocate', request_token,
        {'pool': str(pool.pk), 'policy': str(capacity_policy_id), 'expected': expected_version}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version); _active(pool)
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    if pool.allocation_runs.filter(status='posted').exists():
        _fail('已有有效分配，零金额也不能重复分配')
    try:
        policy = CostPolicy.objects.get(pk=capacity_policy_id, organization=member.organization)
    except (CostPolicy.DoesNotExist, ValueError, ValidationError):
        _fail('容量政策不存在或无权访问')
    inputs, targets = _allocation(pool, policy)
    if dry_run:
        return _preview('allocate', pool, targets=targets)
    run = AllocationRun.objects.create(organization=member.organization, salary_pool=pool, capacity_policy=policy,
        created_by=member, approved_inputs_snapshot=inputs, total_cents=pool.total_comprehensive_cost_cents,
        allocation_snapshot={'method': METHOD, 'capacity_minutes': policy.denominator_minutes, 'targets': targets,
            'policy_version': policy.policy_version, 'salary_source_ref': pool.source_ref,
            'salary_components': deepcopy(pool.approved_salary_snapshot['salary_components']),
            'covered_through': pool.covered_through.isoformat()})
    for item in targets:
        AllocationTarget.objects.create(organization=member.organization, allocation_run=run, **item)
    from core.labor_accounting import post_allocation
    accounting = post_allocation(member, pool, run, targets)
    run.accounting_event = accounting; run.save(update_fields=['accounting_event'])
    CostEvent.objects.create(organization=member.organization, salary_pool=pool, allocation_run=run, core_accounting_event=accounting)
    pool.version += 1; pool.save(update_fields=['version'])
    approval = audit(member, 'plugin.costing.allocated', run, {'salary_pool': str(pool.pk), 'targets': targets})
    _event(member, approval, run.source_ref, 'costing.allocated.v1', accounting,
           [run.source_ref] + [_ref(t, 'allocation_target') for t in run.targets.all()])
    return _finish(member, 'allocate', token, digest, pool, run)


@transaction.atomic
def reverse_allocation(member, run_id, *, request_token, reason, dry_run=True):
    member = _locked(member); reason = _text(reason)
    try:
        run = AllocationRun.objects.select_related('salary_pool', 'accounting_event').get(pk=run_id, organization=member.organization)
    except (AllocationRun.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('分配不存在或无权访问')
    pool = _pool(member, run.salary_pool_id)
    token, digest, replay = _receipt(member, 'unallocate', request_token, {'run': str(run.pk), 'reason': reason}, dry_run)
    if replay is not None:
        return replay
    _active(pool); _coverage(member, pool.employee, pool.period, pool.covered_through)
    if run.status != 'posted' or not run.accounting_event_id or run.reversal_event_id or AccountingEvent.objects.filter(reverses_event_id=run.accounting_event_id).exists():
        _fail('该分配已撤销或不可反冲')
    if dry_run:
        return _preview('unallocate', pool)
    from core.labor_accounting import reverse_labor_event
    reverse = reverse_labor_event(member, run.accounting_event, reason)
    run.status, run.reversal_event = 'reversed', reverse; run.save(update_fields=['status', 'reversal_event'])
    run.targets.update(status='reversed')
    CostEvent.objects.create(organization=member.organization, salary_pool=pool, allocation_run=run, core_accounting_event=reverse)
    pool.version += 1; pool.save(update_fields=['version'])
    approval = audit(member, 'plugin.costing.unallocated', run, {'reason': reason})
    ref = _ref(reverse, 'accounting_event', 'core')
    _event(member, approval, ref, 'core.posting_reversed.v1', reverse, [ref, run.source_ref])
    return _finish(member, 'unallocate', token, digest, pool, run)


@transaction.atomic
def reverse_salary_pool(member, pool_id, *, expected_version, request_token, reason, dry_run=True):
    member = _locked(member); pool = _pool(member, pool_id); reason = _text(reason)
    token, digest, replay = _receipt(member, 'reverse', request_token,
        {'pool': str(pool.pk), 'expected': expected_version, 'reason': reason}, dry_run)
    if replay is not None:
        return replay
    _version(pool, expected_version); _independent(member, pool); _active(pool)
    _coverage(member, pool.employee, pool.period, pool.covered_through)
    if pool.allocation_runs.filter(status='posted').exists():
        _fail('请先明确撤销有效分配，工资冲销不会自动级联')
    if dry_run:
        return _preview('reverse', pool)
    from core.labor_accounting import reverse_labor_event
    reverse = reverse_labor_event(member, pool.recognition_event, reason)
    pool.status, pool.reversed_event = 'superseded', reverse; pool.version += 1; pool.save()
    CostEvent.objects.create(organization=member.organization, salary_pool=pool, core_accounting_event=reverse)
    approval = audit(member, 'plugin.costing.salary.reversed', pool, {'reason': reason})
    ref = _ref(reverse, 'accounting_event', 'core')
    _event(member, approval, ref, 'core.posting_reversed.v1', reverse, [ref, pool.source_ref])
    return _finish(member, 'reverse', token, digest, pool, pool)


def resolve_salary_snapshot(member, pool_id, *, approved_version):
    pool = get_salary_pool(member, pool_id)
    if type(approved_version) is not int or approved_version != pool.approved_source_version:
        _fail('批准工资来源版本不存在')
    return deepcopy(pool.approved_salary_snapshot)


def company_labor_summary(member, *, period=None):
    rows = list_salary_pools(member, period=period)
    pools = list(rows)
    member = labor_reader(member)
    from core.models import LedgerPosting
    postings = LedgerPosting.objects.filter(organization=member.organization, book='cost')
    if period:
        postings = postings.filter(accounting_event__posting_period=_period(period))
    unallocated = NonProjectDestination.objects.filter(organization=member.organization, code='UNALLOCATED').first()
    total = lambda query: query.aggregate(value=Sum('amount_cents'))['value'] or 0
    return {'pools': pools, 'confirmed_cents': total(postings), 'company_cents': total(postings),
            'project_cents': total(postings.filter(scope_kind='project')),
            'nonproject_cents': total(postings.filter(scope_kind='nonproject').exclude(scope_id=str(unallocated.pk) if unallocated else '')),
            'unallocated_cents': total(postings.filter(scope_kind='nonproject', scope_id=str(unallocated.pk))) if unallocated else 0,
            'missing_capacity': [p for p in pools if p.status in {'approved', 'reconciled'} and not p.policies.filter(capacity_confirmed=True).exists()]}



def project_labor_summary(member, project_id, *, period=None):
    # Project-only projection; PM receives no salary-pool/employee/rate objects.
    member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    require_member(member)
    if member.role not in {'owner', 'finance', 'auditor', 'pm'} or not is_enabled(member.organization, 'costing'):
        raise PermissionDenied('无权查看项目人工')
    project = visible_projects(member).filter(pk=project_id).first()
    if not project:
        raise PermissionDenied('项目不存在或无权访问')
    from core.models import LedgerPosting
    rows = LedgerPosting.objects.filter(organization=member.organization, book='cost', scope_kind='project', scope_id=str(project.pk))
    if period:
        rows = rows.filter(accounting_event__posting_period=_period(period))
    return {'project_id': project.pk, 'current_labor_cents': rows.aggregate(total=Sum('amount_cents'))['total'] or 0,
            'posting_count': rows.count()}
