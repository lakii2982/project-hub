"""Service-period customer confirmation owner. All public mutations default to simulation.

The delivery plugin is the only writer of the customer-confirmed service performance,
its frozen calendar-month allocation rows and the parent reversal record. Income remains
owned by management_revenue; this module only reads existing RevenueRecognition facts.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_project
from core.models import AccountingPeriod, DomainEvent, Membership, Project
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company
from pmo_plugins.contracts.approval_services import approved_line_choices
from pmo_plugins.contracts.models import ContractLine, ContractVersion, MethodPolicySelection
from pmo_plugins.contracts.party_services import _digest, _request_token, _version

from .models import (CustomerConfirmationEvidence, DeliveryReceipt, ServiceConfirmation,
                     ServiceConfirmationMonth, ServiceConfirmationReversal)
from .services import approved_line_snapshot, evidence_ref, register_customer_evidence

READ_ROLES = {'owner', 'finance', 'pm', 'auditor'}
WRITE_ROLES = {'owner', 'finance', 'pm'}
CANONICAL_TOPIC = 'delivery.service_confirmation_changed.v1'
CANONICAL_AUDIT = 'plugin.delivery.service_confirmation_confirmed'
NO_MONTH_PRICE = '该批准合同缺明确月价，需后续正式合同变更独立批准月度分配'


def _fail(message):
    raise ValidationError(message)


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _fresh(member, write=False, *, check_module=True):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('无效公司成员上下文')
    from core.access import require_member
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理服务期客户确认')
    if check_module and (not is_enabled(member.organization, 'delivery')
                         or not is_enabled(member.organization, 'contracts')):
        raise PermissionDenied('服务期确认或合同模块已停用')
    return member


def _locked(member):
    member = _fresh(member, True)
    lock_company(member)
    return _fresh(member, True)


def _as_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _fail('日期须为有效的年月日')


def _identifier(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail('记录标识无效')


def _optional_identifier(value):
    if value in (None, ''):
        return None
    return _identifier(value)


def _text(value, maximum, required=True):
    if not isinstance(value, str):
        _fail('文本字段无效')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        _fail('请完整填写依据或原因，且不要超过字段长度')
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value <= 10 ** 12:
        _fail('金额须为0至100亿元的整数分')
    return value


def _source_ref(row):
    return {'namespace': 'delivery', 'kind': 'service_confirmation',
            'id': str(row.pk), 'version': row.confirmed_version or row.version}


def _ref(value):
    if isinstance(value, dict):
        if set(value) != {'namespace', 'kind', 'id', 'version'}:
            _fail('服务确认来源无效')
        if (value['namespace'] != 'delivery' or value['kind'] != 'service_confirmation'
                or type(value['version']) is not int or value['version'] < 1):
            _fail('服务确认来源无效')
        return _identifier(value['id']), value['version']
    return _identifier(value), None


def _project(member, project):
    require_project(member, project, finance=True)


def _get(member, confirmation_id, *, lock=False):
    rows = ServiceConfirmation.objects.select_related(
        'project', 'organization', 'contract_line', 'contract_version', 'selection')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        row = rows.get(pk=_identifier(confirmation_id), organization=member.organization)
    except (ServiceConfirmation.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('服务期确认不存在或无权访问')
    _project(member, row.project)
    return row


def _receipt(member, action, token, payload):
    token = _request_token(token)
    digest = _digest({'action': action, 'actor': str(member.pk), 'payload': payload})
    row = DeliveryReceipt.objects.filter(organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.digest != digest:
            _fail('同一请求标识已用于不同输入')
        result = ServiceConfirmation.objects.select_related('project').get(pk=row.result_id)
        _project(member, result.project)
        return token, digest, result
    return token, digest, None


def _record(member, action, token, digest, row):
    DeliveryReceipt.objects.create(organization=member.organization, action=action,
                                   request_token=token, digest=digest, result_id=row.pk)


def evidence_choices(member):
    member = _fresh(member, True)
    return [value for value in approved_line_choices(member)
            if value['pricing_configuration'].get('template') == 'fixed_service_period']


def _service_snapshot(member, contract_line_ref, as_of):
    value = approved_line_snapshot(member, contract_line_ref, as_of)
    config = value['pricing_configuration']
    if config.get('template') != 'fixed_service_period':
        _fail('本片只支持已批准固定服务期合同')
    if _amount(value['net_cents']) != _amount(config.get('net_cap_cents')):
        _fail('固定服务期分配金额须等于合同明细净额')
    schedule = config.get('service_schedule')
    if not isinstance(schedule, list) or not schedule:
        _fail('批准合同缺少服务分配段')
    total = 0
    for item in schedule:
        if not isinstance(item, dict) or set(item) != {'coverage_start', 'coverage_end', 'allocated_net_cents'}:
            _fail('批准合同服务分配段无效')
        left, right = _as_date(item['coverage_start']), _as_date(item['coverage_end'])
        if left > right:
            _fail('批准合同服务分配段日期无效')
        total += _amount(item['allocated_net_cents'])
    if total != value['net_cents']:
        _fail('服务期分配金额和合同明细净额不一致')
    return value


def _segment(snapshot, coverage_start, coverage_end):
    schedule = snapshot['pricing_configuration']['service_schedule']
    for index, item in enumerate(schedule, start=1):
        left, right = _as_date(item['coverage_start']), _as_date(item['coverage_end'])
        if left <= coverage_start and coverage_end <= right:
            return index, {'coverage_start': left.isoformat(),
                           'coverage_end': right.isoformat(),
                           'allocated_net_cents': _amount(item['allocated_net_cents'])}
    _fail('服务覆盖须完整落在一个已批准服务分配段内，不能跨段或越界')


def _cum_through(segment, day):
    start = _as_date(segment['coverage_start'])
    end = _as_date(segment['coverage_end'])
    total = _amount(segment['allocated_net_cents'])
    days = (end - start).days + 1
    return int((Decimal(total) * Decimal((day - start).days + 1) / Decimal(days)).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))


def _cum_before(segment, day):
    start = _as_date(segment['coverage_start'])
    end = _as_date(segment['coverage_end'])
    total = _amount(segment['allocated_net_cents'])
    days = (end - start).days + 1
    return int((Decimal(total) * Decimal((day - start).days) / Decimal(days)).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))


def _derived_months(segment, coverage_start, coverage_end):
    rows, cursor = [], coverage_start
    while cursor <= coverage_end:
        month_end = date(cursor.year + (1 if cursor.month == 12 else 0),
                         1 if cursor.month == 12 else cursor.month + 1, 1) - timedelta(days=1)
        seg_end = min(coverage_end, month_end)
        allocated = _amount(_cum_through(segment, seg_end) - _cum_before(segment, cursor))
        rows.append({'service_month': cursor.strftime('%Y-%m'), 'coverage_start': cursor,
                     'coverage_end': seg_end, 'allocated_net_cents': allocated})
        cursor = seg_end + timedelta(days=1)
    if not rows:
        _fail('服务覆盖没有可分配的月份')
    return rows


def _derive(snapshot, coverage_start, coverage_end, confirmed_on):
    if coverage_start > coverage_end:
        _fail('服务覆盖起始日不能晚于结束日')
    effective = _as_date(snapshot['effective_on'])
    if coverage_start < effective:
        _fail('服务覆盖不能早于合同生效日')
    if coverage_end > confirmed_on:
        _fail('客户确认日期不能早于服务覆盖结束日')
    if confirmed_on > timezone.localdate():
        _fail('客户确认日期不能在未来')
    index, segment = _segment(snapshot, coverage_start, coverage_end)
    config = snapshot['pricing_configuration']
    if config.get('partial_period_rule') == 'no_partial':
        if (coverage_start != _as_date(segment['coverage_start'])
                or coverage_end != _as_date(segment['coverage_end'])):
            _fail('不允许部分期间规则只可完整确认原服务分配段')
    months = _derived_months(segment, coverage_start, coverage_end)
    total = _amount(sum(item['allocated_net_cents'] for item in months))
    return {'schedule_index': index, 'schedule': segment, 'months': months,
            'confirmed_net_cents': total, 'effective_on': effective}


def _is_cross_month(segment):
    left, right = _as_date(segment['coverage_start']), _as_date(segment['coverage_end'])
    return (left.year, left.month) != (right.year, right.month)


def _no_partial_cross_month(row):
    if row.pricing_configuration.get('partial_period_rule') == 'no_partial' and _is_cross_month(row.schedule_snapshot):
        _fail(NO_MONTH_PRICE)


def _month_payload(months):
    return [{'service_month': item['service_month'],
             'coverage_start': _as_date(item['coverage_start']).isoformat()
             if isinstance(item['coverage_start'], date) else item['coverage_start'],
             'coverage_end': _as_date(item['coverage_end']).isoformat()
             if isinstance(item['coverage_end'], date) else item['coverage_end'],
             'allocated_net_cents': _amount(item['allocated_net_cents'])} for item in months]


def _approved_input(row, months):
    return {'organization_id': str(row.organization_id), 'project_id': str(row.project_id),
            'contract_line_id': str(row.contract_line_id), 'contract_line_ref': row.contract_line_ref,
            'contract_version_id': str(row.contract_version_id), 'selection_id': str(row.selection_id),
            'policy_version': row.policy_version, 'pricing_configuration': row.pricing_configuration,
            'approved_digest': row.approved_digest, 'evidence': row.evidence,
            'confirmed_on': row.confirmed_on.isoformat(),
            'coverage_start': row.coverage_start.isoformat(), 'coverage_end': row.coverage_end.isoformat(),
            'schedule_index': row.schedule_index, 'schedule': row.schedule_snapshot,
            'confirmed_net_cents': row.confirmed_net_cents, 'confirmed_version': row.confirmed_version,
            'confirmed_at': row.confirmed_at.isoformat() if row.confirmed_at else None,
            'confirmed_by_member_id': str(row.confirmed_by_id),
            'created_by_member_id': str(row.created_by_id),
            'submitted_by_member_id': str(row.submitted_by_id),
            'months': _month_payload(months)}


def _evidence(member, refs, line, day):
    if not isinstance(refs, list) or not refs:
        _fail('请登记真实客户确认依据')
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'evidence_id', 'sha256', 'kind'}
                or ref['kind'] != 'customer_confirmation'):
            _fail('客户依据引用无效')
        row = CustomerConfirmationEvidence.objects.filter(
            pk=ref['evidence_id'], organization=member.organization,
            contract_line=line, confirmed_on=day).first()
        if not row or row.sha256 != ref['sha256']:
            _fail('客户依据不存在、日期不符或摘要不符')


def _replacement(member, ref, project, contract_line_id, coverage_start, coverage_end):
    old = None
    if ref is not None:
        if (not isinstance(ref, dict) or set(ref) != {'namespace', 'kind', 'id', 'version'}
                or ref['namespace'] != 'delivery' or ref['kind'] != 'service_confirmation'
                or type(ref['version']) is not int):
            _fail('替代来源无效')
        old = _get(member, ref['id'])
        if (old.status != 'reversed' or old.confirmed_version != ref['version']
                or old.project_id != project.pk or old.contract_line_id != contract_line_id):
            _fail('替代来源须为同项目同合同明细已反服务确认')
        state = service_confirmation_income_state(member, old.pk)
        if state['any_posted'] and not state['all_reversed']:
            _fail('替代来源仍有未冲管理收入')
    history = ServiceConfirmation.objects.filter(
        organization=member.organization, project=project, status='reversed',
        coverage_start__lte=coverage_end, coverage_end__gte=coverage_start)
    if old is not None:
        history = history.exclude(pk=old.pk)
    if history.exists():
        _fail('重用已反服务覆盖必须明确替代来源')
    return old


def _coverage_conflict(member, project, coverage_start, coverage_end, exclude=None):
    rows = ServiceConfirmation.objects.filter(
        organization=member.organization, project=project, status='confirmed',
        coverage_start__lte=coverage_end, coverage_end__gte=coverage_start)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    if rows.exists():
        _fail('服务覆盖与已有生效确认重叠')


def _check_periods(member, coverage_start, months):
    organization = member.organization
    if organization.locked_through and coverage_start <= organization.locked_through:
        _fail('服务覆盖与原锁账期间相交，不能补记')
    for item in months:
        period = item['service_month']
        row = AccountingPeriod.objects.filter(organization=organization, period=period).first()
        if row and (row.status != 'active' or row.closed_through is not None):
            _fail(f'{period}会计期间已关闭或非活动，不能确认服务收入归属')


def _refreeze(member, row):
    snapshot = _service_snapshot(member, row.contract_line_ref, row.confirmed_on)
    if (snapshot['version'].pk != row.contract_version_id or snapshot['selection'].pk != row.selection_id
            or any(snapshot[key] != getattr(row, field) for key, field in
                   [('digest', 'approved_digest'), ('line_ref', 'contract_line_ref'),
                    ('policy_version', 'policy_version'), ('pricing_configuration', 'pricing_configuration')])):
        _fail('服务确认原合同批准来源或冻结价格已失效')
    schedule = snapshot['pricing_configuration']['service_schedule']
    if not 1 <= row.schedule_index <= len(schedule):
        _fail('原服务分配段已失效')
    item = schedule[row.schedule_index - 1]
    expected = {'coverage_start': _as_date(item['coverage_start']).isoformat(),
                'coverage_end': _as_date(item['coverage_end']).isoformat(),
                'allocated_net_cents': _amount(item['allocated_net_cents'])}
    if expected != row.schedule_snapshot:
        _fail('原服务分配段快照发生变化')
    left, right = _as_date(row.schedule_snapshot['coverage_start']), _as_date(row.schedule_snapshot['coverage_end'])
    if not left <= row.coverage_start <= row.coverage_end <= right:
        _fail('服务覆盖超出原分配段')
    if row.pricing_configuration.get('partial_period_rule') == 'no_partial':
        if row.coverage_start != left or row.coverage_end != right:
            _fail('不允许部分期间规则只可完整确认原服务分配段')
    months = _derived_months(row.schedule_snapshot, row.coverage_start, row.coverage_end)
    if sum(item['allocated_net_cents'] for item in months) != row.confirmed_net_cents:
        _fail('服务月分配金额与确认净额不一致')
    return snapshot, months


def _stored_months(row):
    rows = list(row.months.order_by('service_month'))
    if not rows:
        _fail('已确认服务确认缺少真实月分配行')
    segment = row.schedule_snapshot
    expected = _derived_months(segment, row.coverage_start, row.coverage_end)
    if len(rows) != len(expected):
        _fail('服务月分配行数量不一致')
    for stored, clean in zip(rows, expected):
        if (stored.service_month != clean['service_month']
                or stored.coverage_start != clean['coverage_start']
                or stored.coverage_end != clean['coverage_end']
                or stored.allocated_net_cents != clean['allocated_net_cents']):
            _fail('服务月分配行与冻结覆盖不一致')
    if sum(item.allocated_net_cents for item in rows) != row.confirmed_net_cents:
        _fail('服务月分配金额合计不一致')
    return [{'service_month': item.service_month, 'coverage_start': item.coverage_start,
             'coverage_end': item.coverage_end, 'allocated_net_cents': item.allocated_net_cents}
            for item in rows]


def _verify_frozen(row, months):
    if not row.confirmed_version or not row.confirmation_audit_id:
        _fail('服务确认缺少永久批准版本或真实确认审计')
    if not row.confirmed_by_id or row.confirmed_by_id in {row.created_by_id, row.submitted_by_id}:
        _fail('服务确认缺少独立确认')
    expected = _approved_input(row, months)
    audit_row = row.confirmation_audit
    if (row.approved_input_snapshot != expected
            or _digest(expected) != _digest(row.approved_input_snapshot)
            or not isinstance(audit_row.payload, dict)
            or audit_row.action != CANONICAL_AUDIT
            or audit_row.organization_id != row.organization_id
            or audit_row.actor_id != row.confirmed_by.user_id
            or audit_row.entity_id != str(row.pk)
            or audit_row.payload.get('approved_digest') != _digest(row.approved_input_snapshot)):
        _fail('服务确认冻结输入与真实独立确认审计不一致')


@transaction.atomic
def save_service_confirmation(member, *, contract_line_ref, coverage_start, coverage_end, confirmed_on,
                              evidence, request_token, confirmation_id=None, expected_version=None,
                              replaces_confirmation_ref=None, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    confirmation_id = _optional_identifier(confirmation_id)
    confirmed_on = _as_date(confirmed_on)
    coverage_start, coverage_end = _as_date(coverage_start), _as_date(coverage_end)
    snapshot = _service_snapshot(member, contract_line_ref, confirmed_on)
    project, line = snapshot['project'], snapshot['line']
    _project(member, project)
    derived = _derive(snapshot, coverage_start, coverage_end, confirmed_on)
    _evidence(member, evidence, line, confirmed_on)
    old = _replacement(member, replaces_confirmation_ref, project, line.pk, coverage_start, coverage_end)
    _coverage_conflict(member, project, coverage_start, coverage_end, exclude=confirmation_id)
    # Saving a draft already refuses covered months that are locked or closed, like submit/approve.
    _check_periods(member, coverage_start, derived['months'])
    payload = {'contract_line_ref': contract_line_ref,
               'coverage_start': coverage_start.isoformat(), 'coverage_end': coverage_end.isoformat(),
               'confirmed_on': confirmed_on.isoformat(), 'evidence': evidence,
               'confirmation_id': str(confirmation_id) if confirmation_id else None,
               'expected_version': expected_version,
               'replaces_confirmation_ref': replaces_confirmation_ref}
    token, digest, existing = _receipt(member, 'service-save', request_token, payload)
    if existing is not None:
        return existing
    if confirmation_id:
        row = _get(member, confirmation_id, lock=True)
        if row.status != 'draft' or row.version != _version(expected_version):
            _fail('仅可编辑当前版本草稿')
        if member.role == 'pm' and row.created_by_id != member.pk:
            raise PermissionDenied('仅可编辑本人草稿')
    else:
        if expected_version is not None:
            _fail('新建服务确认不接受版本号')
        row = ServiceConfirmation(organization=member.organization, created_by=member)
    for field, value in {
        'project': project, 'contract_line': line, 'contract_version': snapshot['version'],
        'selection': snapshot['selection'], 'contract_line_ref': snapshot['line_ref'],
        'policy_version': snapshot['policy_version'], 'pricing_configuration': snapshot['pricing_configuration'],
        'approved_digest': snapshot['digest'], 'evidence': evidence,
        'confirmed_on': confirmed_on, 'coverage_start': coverage_start, 'coverage_end': coverage_end,
        'schedule_index': derived['schedule_index'], 'schedule_snapshot': derived['schedule'],
        'confirmed_net_cents': derived['confirmed_net_cents'], 'replaces_confirmation': old,
    }.items():
        setattr(row, field, value)
    if confirmation_id:
        row.version += 1
    if not dry_run:
        row.save()
        _record(member, 'service-save', token, digest, row)
        audit(member, 'delivery.service_confirmation.saved', row, payload)
    return row


@transaction.atomic
def save_service_confirmation_with_evidence(member, *, contract_line_ref, contact_id, coverage_start,
                                            coverage_end, confirmed_on, channel, reference, statement,
                                            request_token, confirmation_id=None, expected_version=None,
                                            replaces_confirmation_ref=None, dry_run=True):
    _dry(dry_run)
    token = _request_token(request_token)
    confirmed_on = _as_date(confirmed_on)
    evidence = register_customer_evidence(
        member, contract_line_ref=contract_line_ref, contact_id=contact_id, confirmed_on=confirmed_on,
        channel=channel, reference=reference, statement=statement,
        request_token=uuid.uuid5(token, 'service-evidence'), dry_run=False)
    row = save_service_confirmation(
        member, contract_line_ref=contract_line_ref, coverage_start=coverage_start,
        coverage_end=coverage_end, confirmed_on=confirmed_on, evidence=[evidence_ref(evidence)],
        request_token=token, confirmation_id=confirmation_id, expected_version=expected_version,
        replaces_confirmation_ref=replaces_confirmation_ref, dry_run=dry_run)
    if dry_run:
        transaction.set_rollback(True)
    return row


@transaction.atomic
def submit_service_confirmation(member, confirmation_id, *, expected_version, request_token, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, confirmation_id, lock=True)
    payload = {'id': str(row.pk), 'expected_version': expected_version}
    token, digest, existing = _receipt(member, 'service-submit', request_token, payload)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status != 'draft':
        _fail('仅可提交草稿')
    _refreeze(member, row)
    _no_partial_cross_month(row)
    _evidence(member, row.evidence, row.contract_line, row.confirmed_on)
    _replacement(member, _source_ref(row.replaces_confirmation) if row.replaces_confirmation_id else None,
                 row.project, row.contract_line_id, row.coverage_start, row.coverage_end)
    _coverage_conflict(member, row.project, row.coverage_start, row.coverage_end, exclude=row.pk)
    _check_periods(member, row.coverage_start,
                   _derived_months(row.schedule_snapshot, row.coverage_start, row.coverage_end))
    row.status, row.submitted_by = 'submitted', member
    row.version += 1
    if not dry_run:
        row.save()
        _record(member, 'service-submit', token, digest, row)
        audit(member, 'delivery.service_confirmation.submitted', row, payload)
    return row


@transaction.atomic
def review_service_confirmation(member, confirmation_id, *, expected_version, request_token, decision,
                                note, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, confirmation_id, lock=True)
    if not isinstance(decision, str) or decision not in {'approve', 'return', 'reject'}:
        _fail('独立核对结论无效')
    note = _text(note, 2000)
    payload = {'id': str(row.pk), 'expected_version': expected_version,
               'decision': decision, 'note': note}
    token, digest, existing = _receipt(member, 'service-review', request_token, payload)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status != 'submitted':
        _fail('服务确认状态或决定无效')
    if member.role not in {'owner', 'finance'} or member.pk in {row.created_by_id, row.submitted_by_id}:
        raise PermissionDenied('需要另一位公司负责人或财务独立办理')
    if decision != 'approve':
        row.status, row.review_note = 'draft', note
        row.version += 1
        action = 'delivery.service_confirmation.rejected' if decision == 'reject' else 'delivery.service_confirmation.returned'
        if not dry_run:
            row.save()
            _record(member, 'service-review', token, digest, row)
            audit(member, action, row, payload)
        return row
    _refreeze(member, row)
    _no_partial_cross_month(row)
    _evidence(member, row.evidence, row.contract_line, row.confirmed_on)
    _replacement(member, _source_ref(row.replaces_confirmation) if row.replaces_confirmation_id else None,
                 row.project, row.contract_line_id, row.coverage_start, row.coverage_end)
    _coverage_conflict(member, row.project, row.coverage_start, row.coverage_end, exclude=row.pk)
    months = _derived_months(row.schedule_snapshot, row.coverage_start, row.coverage_end)
    _check_periods(member, row.coverage_start, months)
    row.status, row.confirmed_by = 'confirmed', member
    row.confirmed_at = timezone.now()
    row.version += 1
    row.confirmed_version = row.version
    approved_input = _approved_input(row, months)
    if not dry_run:
        record = audit(member, CANONICAL_AUDIT, row,
                       {'approved_input': approved_input, 'approved_digest': _digest(approved_input)})
        row.confirmation_audit = record
        row.approved_input_snapshot = approved_input
        # The delivery trigger only accepts month rows while the parent is still submitted.
        ServiceConfirmationMonth.objects.bulk_create([
            ServiceConfirmationMonth(service_confirmation=row,
                                     service_month=item['service_month'],
                                     coverage_start=item['coverage_start'],
                                     coverage_end=item['coverage_end'],
                                     allocated_net_cents=item['allocated_net_cents'])
            for item in months])
        row.save()
        _record(member, 'service-review', token, digest, row)
        event_id = uuid.uuid4()
        ref = _source_ref(row)
        DomainEvent.objects.create(
            event_id=event_id, organization=member.organization, source=record, topic=CANONICAL_TOPIC,
            data={'event_id': str(event_id), 'topic': CANONICAL_TOPIC, 'schema_version': 1,
                  'organization_id': str(member.organization_id), 'source': ref,
                  'occurred_at': timezone.now().isoformat(),
                  'payload': {'changed_refs': [ref], 'posting_event_refs': []}})
    return row


@transaction.atomic
def withdraw_service_confirmation(member, confirmation_id, *, expected_version, request_token, reason,
                                  dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, confirmation_id, lock=True)
    reason = _text(reason, 2000)
    payload = {'id': str(row.pk), 'expected_version': expected_version, 'reason': reason}
    token, digest, existing = _receipt(member, 'service-withdraw', request_token, payload)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status not in {'draft', 'submitted'}:
        _fail('仅可撤回未确认的服务确认')
    if member.role not in {'owner', 'finance'} and row.created_by_id != member.pk:
        raise PermissionDenied('仅可撤回本人服务确认')
    row.status, row.review_note = 'withdrawn', reason
    row.version += 1
    if not dry_run:
        row.save()
        _record(member, 'service-withdraw', token, digest, row)
        audit(member, 'delivery.service_confirmation.withdrawn', row, payload)
    return row


@transaction.atomic
def reverse_service_confirmation(member, confirmation_id, *, expected_version, request_token, reason,
                                 dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, confirmation_id, lock=True)
    reason = _text(reason, 2000)
    payload = {'id': str(row.pk), 'expected_version': expected_version, 'reason': reason}
    token, digest, existing = _receipt(member, 'service-reverse', request_token, payload)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status != 'confirmed':
        _fail('仅可反冲已确认服务确认')
    if member.role not in {'owner', 'finance'} or member.pk in {row.created_by_id, row.submitted_by_id}:
        raise PermissionDenied('需要另一位公司负责人或财务独立反冲')
    confirmed_service_snapshot(member, _source_ref(row), for_recognition=False)
    state = service_confirmation_income_state(member, row.pk)
    if state['any_posted'] and not state['all_reversed']:
        _fail('须先逐月完整反冲全部已产生管理收入')
    # The delivery trigger only permits confirmed -> reversed with version unchanged.
    row.status = 'reversed'
    if not dry_run:
        row.save()
        ServiceConfirmationReversal.objects.create(confirmation=row, reason=reason, actor=member)
        _record(member, 'service-reverse', token, digest, row)
        audit(member, 'delivery.service_confirmation.reversed', row, {'reason': reason})
    return row


def confirmed_service_snapshot(member, ref, *, for_recognition=True):
    if type(for_recognition) is not bool:
        _fail('for_recognition须为明确布尔值')
    confirmation_id, ref_version = _ref(ref)
    if for_recognition:
        member = _fresh(member)
        if not is_enabled(member.organization, 'management_revenue'):
            raise PermissionDenied('管理收入模块已停用')
    else:
        member = _fresh(member, check_module=False)
    row = _get(member, confirmation_id)
    if row.status not in {'confirmed', 'reversed'} or not row.confirmed_version:
        _fail('服务确认尚未独立确认或来源版本无效')
    if ref_version is not None and ref_version != row.confirmed_version:
        _fail('服务确认来源版本无效')
    if for_recognition and row.status != 'confirmed':
        _fail('已反服务确认不能确认收入')
    if for_recognition:
        _refreeze(member, row)
        months = _stored_months(row)
    else:
        months = _stored_months(row)
    _verify_frozen(row, months)
    return {'confirmation': row, 'project': row.project, 'contract_line': row.contract_line,
            'contract_line_ref': deepcopy(row.contract_line_ref), 'version': row.confirmed_version,
            'confirmed_net_cents': row.confirmed_net_cents, 'confirmed_on': row.confirmed_on,
            'coverage_start': row.coverage_start, 'coverage_end': row.coverage_end,
            'schedule_index': row.schedule_index, 'schedule': deepcopy(row.schedule_snapshot),
            'pricing_configuration': deepcopy(row.pricing_configuration),
            'policy_version': deepcopy(row.policy_version), 'approved_digest': row.approved_digest,
            'evidence': deepcopy(row.evidence), 'source_ref': _source_ref(row),
            'months': [{'id': item['id'], 'service_month': item['service_month'],
                        'coverage_start': item['coverage_start'], 'coverage_end': item['coverage_end'],
                        'allocated_net_cents': item['allocated_net_cents']}
                       for item in _month_models(row)]}


def _month_models(row):
    return [{'id': item.pk, 'service_month': item.service_month, 'coverage_start': item.coverage_start,
             'coverage_end': item.coverage_end, 'allocated_net_cents': item.allocated_net_cents}
            for item in row.months.order_by('service_month')]


def service_confirmation_income_state(member, ref):
    member = _fresh(member, check_module=False)
    confirmation_id, _unused = _ref(ref)
    row = _get(member, confirmation_id)
    from pmo_plugins.management_revenue.models import RevenueRecognition
    posted = list(RevenueRecognition.objects.filter(
        organization=member.organization, service_confirmation=row, status='posted'))
    by_month = {item.service_confirmation_month_id: item for item in posted
                if item.service_confirmation_month_id}
    months = [{'service_month': item.service_month, 'allocated_net_cents': item.allocated_net_cents,
               'recognition_id': str(by_month[item.pk].pk) if item.pk in by_month else None,
               'is_reversed': bool(item.pk in by_month and by_month[item.pk].is_reversed)}
              for item in row.months.order_by('service_month')]
    return {'months': months,
            'all_reversed': all(item.is_reversed for item in posted) if posted else True,
            'any_posted': bool(posted)}


def get_service_confirmation(member, confirmation_id):
    return _get(_fresh(member), confirmation_id)


def list_service_confirmations(member):
    member = _fresh(member)
    rows = ServiceConfirmation.objects.filter(organization=member.organization).select_related(
        'project', 'contract_line__contract_version__contract').order_by('-created_at')
    if member.role == 'pm':
        rows = rows.filter(project__manager=member)
    return rows


def service_confirmation_detail(member, confirmation_id):
    row = get_service_confirmation(member, confirmation_id)
    income = service_confirmation_income_state(member, row.pk)
    stored = {item['service_month']: item for item in income['months']}
    months = []
    source = _month_models(row) if row.status in {'confirmed', 'reversed'} else []
    if not source:
        try:
            segment = row.schedule_snapshot
            source = _derived_months(segment, row.coverage_start, row.coverage_end)
        except (ValidationError, KeyError, TypeError):
            source = []
    for item in source:
        month = item.get('service_month')
        status = stored.get(month)
        months.append({'service_month': month, 'coverage_start': item['coverage_start'],
                       'coverage_end': item['coverage_end'],
                       'allocated_net_cents': item['allocated_net_cents'],
                       'persisted': bool(stored), 'has_recognition': bool(status and status['recognition_id']),
                       'is_reversed': bool(status and status['is_reversed']),
                       'recognition_id': status['recognition_id'] if status else None})
    evidence_ids = []
    for item in row.evidence or []:
        if isinstance(item, dict) and item.get('evidence_id'):
            evidence_ids.append(item['evidence_id'])
    return {'confirmation': row, 'months': months, 'evidence': CustomerConfirmationEvidence.objects.filter(
        pk__in=evidence_ids), 'income': income, 'reversal': getattr(row, 'reversal', None)}
