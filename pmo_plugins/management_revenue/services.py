"""Deterministic revenue from delivery-owned independently confirmed acceptance."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from core.access import require_member, require_project, visible_projects
from core.models import Membership, Project, AccountingPeriod, DomainEvent
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company, check_period
from .models import RevenueRecognition, RevenueAdjustment, RevenueReceipt

ALGORITHM = 'fixed_milestone_cumulative_half_up_v1'


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False, check_module=True):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in ({'owner', 'finance'} if write else {'owner', 'finance', 'pm', 'auditor'}):
        raise PermissionDenied('无权读取或办理管理收入')
    if check_module and not is_enabled(member.organization, 'management_revenue'):
        raise PermissionDenied('管理收入模块已停用')
    return member


def _locked(member):
    member = _fresh(member, write=True)
    lock_company(member)
    return _fresh(member, write=True)


def _identifier(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail('记录或请求标识无效')


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        _fail('收入须为0至100亿元的整数分')
    return value


def _business_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            result = date.fromisoformat(value)
            if result.isoformat() == value:
                return result
        except ValueError:
            pass
    _fail('业务日期无效')


def _period(member, occurred_on):
    check_period(member.organization, occurred_on)
    period = occurred_on.strftime('%Y-%m')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=period).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('原收入期间已关闭')
    return period


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token)
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = RevenueReceipt.objects.filter(organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        if dry_run:
            return token, digest, {'dry_run': True, 'existing': True, 'result': deepcopy(row.result)}
        model = RevenueRecognition if action == 'recognize' else RevenueAdjustment
        result = model.objects.get(organization=member.organization, pk=row.result['id'])
        require_project(member, result.project, finance=True)
        return token, digest, result
    return token, digest, None


def _finish(member, action, token, digest, result):
    RevenueReceipt.objects.create(organization=member.organization, request_token=token,
        action=action, input_digest=digest, result=result.source_ref, created_by=member)
    return result


def _event(member, result, core_event, topic):
    approval = audit(member, 'plugin.management_revenue.' + ('recognized' if isinstance(result, RevenueRecognition) else 'reversed'),
        result, {'amount_cents': result.recognized_net_cents if isinstance(result, RevenueRecognition) else result.adjustment_cents,
                 'accounting_event_id': str(core_event.pk)})
    event_id = uuid.uuid4()
    core_ref = {'namespace': 'core', 'kind': 'accounting_event', 'id': str(core_event.pk), 'version': 1}
    data = {'event_id': str(event_id), 'topic': topic, 'schema_version': 1,
        'organization_id': str(member.organization_id),
        'source': result.source_ref if isinstance(result, RevenueRecognition) else core_ref,
        'occurred_at': timezone.now().isoformat(),
        'payload': {'changed_refs': [result.source_ref], 'posting_event_refs': [core_ref]}}
    DomainEvent.objects.create(event_id=event_id, organization=member.organization,
        source=approval, topic=topic, data=data)


def _recognition(member, recognition_id):
    try:
        row = RevenueRecognition.objects.select_related('project', 'acceptance', 'contract_line', 'accounting_event').get(
            organization=member.organization, pk=_identifier(recognition_id))
    except RevenueRecognition.DoesNotExist:
        raise PermissionDenied('收入记录不存在或无权访问')
    require_project(member, row.project, finance=True)
    return row


@transaction.atomic
def recognize_acceptance(member, acceptance_id, *, expected_version, request_token, dry_run=True):
    member = _locked(member)
    _dry(dry_run)
    acceptance_id = _identifier(acceptance_id)
    if type(expected_version) is not int or expected_version < 1:
        _fail('来源版本无效')
    # Historical read allows a safe receipt replay after a subsequent reversal.
    from pmo_plugins.delivery.services import confirmed_acceptance_snapshot
    snapshot = confirmed_acceptance_snapshot(member, acceptance_id, for_recognition=False)
    token, digest, existing = _receipt(member, 'recognize', request_token,
        {'acceptance_id': str(acceptance_id), 'expected_version': expected_version}, dry_run)
    if existing is not None:
        return existing
    snapshot = confirmed_acceptance_snapshot(member, acceptance_id, for_recognition=True)
    if snapshot['version'] != expected_version:
        _fail('验收版本已变更，请刷新后重试')
    acceptance = snapshot['acceptance']
    project = snapshot['project']
    require_project(member, project, finance=True)
    if RevenueRecognition.objects.filter(organization=member.organization, acceptance_id=acceptance_id).exists():
        _fail('该原始验收已产生收入，不能再次确认')
    amount = _amount(snapshot['accepted_net_cents'])
    occurred_on = _business_date(snapshot['confirmed_on'])
    period = _period(member, occurred_on)
    line = snapshot['contract_line']
    cap = _amount(line.net_cents)
    income = RevenueRecognition.objects.filter(organization=member.organization, contract_line=line, status='posted').aggregate(total=Sum('recognized_net_cents'))['total'] or 0
    adjustments = RevenueAdjustment.objects.filter(organization=member.organization, recognition__contract_line=line, status='posted').aggregate(total=Sum('adjustment_cents'))['total'] or 0
    if income + adjustments + amount > cap:
        _fail('累计管理收入超过批准合同明细净额')
    if dry_run:
        return {'dry_run': True, 'action': 'recognize', 'acceptance_id': str(acceptance_id),
                'recognized_net_cents': amount, 'occurred_on': occurred_on.isoformat(), 'posting_period': period}
    row = RevenueRecognition.objects.create(organization=member.organization, project=project,
        acceptance=acceptance, contract_line=line, created_by=member,
        contract_line_ref=deepcopy(snapshot['contract_line_ref']), performance_ref=deepcopy(snapshot['source']),
        policy_version=deepcopy(snapshot['policy_version']), pricing_snapshot=deepcopy(snapshot['pricing_configuration']),
        accepted_snapshot={'acceptance_id': str(acceptance_id), 'version': expected_version,
            'accepted_net_cents': amount, 'confirmed_on': occurred_on.isoformat(),
            'approved_digest': snapshot['approved_digest'], 'evidence': deepcopy(snapshot['evidence']),
            'milestone_coverages': [{'milestone_id': str(c.milestone_id), 'from_basis_points': c.from_basis_points,
                'to_basis_points': c.to_basis_points, 'accepted_net_cents': c.accepted_net_cents}
                for c in acceptance.coverages.order_by('milestone_id', 'from_basis_points')]},
        recognized_net_cents=amount, occurred_on=occurred_on, posting_period=period, algorithm_version=ALGORITHM)
    from core.revenue_accounting import post_revenue_recognition
    row.accounting_event = post_revenue_recognition(member, row)
    row.status = 'posted'
    row.save(update_fields=['status', 'accounting_event'])
    _event(member, row, row.accounting_event, 'management_revenue.recognized.v1')
    return _finish(member, 'recognize', token, digest, row)


@transaction.atomic
def reverse_recognition(member, recognition_id, *, reason, request_token, dry_run=True):
    member = _locked(member)
    _dry(dry_run)
    row = _recognition(member, recognition_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        _fail('请填写500字以内的冲销原因')
    reason = reason.strip()
    token, digest, existing = _receipt(member, 'reverse', request_token,
        {'recognition_id': str(row.pk), 'reason': reason}, dry_run)
    if existing is not None:
        return existing
    if row.status != 'posted' or not row.accounting_event_id or row.is_reversed:
        _fail('收入尚未确认或已完整冲销')
    from pmo_plugins.delivery.services import confirmed_acceptance_snapshot
    snapshot = confirmed_acceptance_snapshot(member, row.acceptance_id, for_recognition=False)
    if snapshot['source'] != row.performance_ref or snapshot['accepted_net_cents'] != row.recognized_net_cents:
        _fail('原收入与验收来源不一致')
    _amount(row.recognized_net_cents)
    period = _period(member, row.occurred_on)
    if period != row.posting_period:
        _fail('原收入日期与期间不一致')
    if dry_run:
        return {'dry_run': True, 'action': 'reverse', 'recognition_id': str(row.pk),
                'adjustment_cents': -row.recognized_net_cents, 'occurred_on': row.occurred_on.isoformat(), 'posting_period': period}
    adjustment = RevenueAdjustment.objects.create(organization=member.organization, project=row.project,
        recognition=row, created_by=member, adjustment_cents=-row.recognized_net_cents,
        reason=reason, occurred_on=row.occurred_on, posting_period=period)
    from core.revenue_accounting import reverse_revenue_recognition
    adjustment.accounting_event = reverse_revenue_recognition(member, adjustment)
    adjustment.status = 'posted'
    adjustment.save(update_fields=['status', 'accounting_event'])
    _event(member, adjustment, adjustment.accounting_event, 'core.posting_reversed.v1')
    return _finish(member, 'reverse', token, digest, adjustment)


def acceptance_income_state(member, acceptance_id):
    # Dependency checks must still see existing facts when the module is disabled.
    member = _fresh(member, check_module=False)
    from pmo_plugins.delivery.models import Acceptance
    try:
        source = Acceptance.objects.select_related('project').get(organization=member.organization, pk=_identifier(acceptance_id))
    except Acceptance.DoesNotExist:
        raise PermissionDenied('验收记录不存在或无权访问')
    require_project(member, source.project, finance=True)
    row = RevenueRecognition.objects.filter(organization=member.organization, acceptance=source, status='posted').first()
    return {'has_recognition': row is not None, 'is_reversed': bool(row and row.is_reversed),
        'recognition_id': str(row.pk) if row else None, 'recognized_net_cents': row.recognized_net_cents if row else 0}


def _rows(member, project_id=None, as_of=None):
    member = _fresh(member, check_module=False)
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    if project_id is not None:
        try:
            project = Project.objects.get(organization=member.organization, pk=project_id)
        except (Project.DoesNotExist, ValueError):
            raise PermissionDenied('项目不存在或无权访问')
        require_project(member, project, finance=True)
        projects = projects.filter(pk=project.pk)
    rows = RevenueRecognition.objects.filter(organization=member.organization, project__in=projects, status='posted').select_related('project', 'accounting_event')
    adjustments = RevenueAdjustment.objects.filter(organization=member.organization, project__in=projects, status='posted').select_related('project', 'recognition', 'accounting_event')
    if as_of is not None:
        as_of = _business_date(as_of)
        rows, adjustments = rows.filter(occurred_on__lte=as_of), adjustments.filter(occurred_on__lte=as_of)
    return rows, adjustments


def project_revenue_summary(member, project_id=None, *, as_of=None):
    rows, adjustments = _rows(member, project_id, as_of)
    gross = rows.aggregate(total=Sum('recognized_net_cents'))['total'] or 0
    reversed_cents = adjustments.aggregate(total=Sum('adjustment_cents'))['total'] or 0
    from pmo_plugins.delivery.models import Acceptance
    member = _fresh(member, check_module=False)
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    if project_id is not None:
        projects = projects.filter(pk=project_id)
    sources = Acceptance.objects.filter(organization=member.organization, project__in=projects)
    if as_of is not None:
        sources = sources.filter(confirmed_on__lte=_business_date(as_of))
    confirmed = sources.filter(status='confirmed')
    posted = RevenueRecognition.objects.filter(organization=member.organization, status='posted')
    reversed_ids = RevenueAdjustment.objects.filter(organization=member.organization, status='posted').values('recognition__acceptance_id')
    return {'project_id': str(project_id) if project_id is not None else None,
        'recognized_net_cents': gross, 'adjustment_cents': reversed_cents,
        'management_revenue_cents': gross + reversed_cents,
        'recognition_count': rows.count(), 'adjustment_count': adjustments.count(),
        'pending_acceptance_count': sources.filter(status__in=['draft', 'submitted']).count(),
        'unrecognized_acceptance_count': confirmed.exclude(pk__in=posted.values('acceptance_id')).count(),
        'reversed_pending_acceptance_count': confirmed.filter(pk__in=reversed_ids).count()}


def revenue_export_rows(member, project_id=None, *, as_of=None):
    rows, adjustments = _rows(member, project_id, as_of)
    rows = rows.select_related('acceptance__contract_version')
    adjustments = adjustments.select_related('recognition__acceptance__contract_version')
    def trace(original, event):
        return {'contract_version_id': str(original.acceptance.contract_version_id),
            'contract_line_id': str(original.contract_line_id),
            'accounting_event_id': str(event.pk),
            'reverses_event_id': str(event.reverses_event_id) if event.reverses_event_id else None,
            'recognition_id': str(original.pk), 'acceptance_id': str(original.acceptance_id)}
    result = [{'id': str(row.pk), 'kind': 'revenue_recognition', 'project_id': str(row.project_id),
        'amount_cents': row.recognized_net_cents, 'occurred_on': row.occurred_on.isoformat(),
        'posting_period': row.posting_period, 'source': row.source_ref, 'is_reversed': row.is_reversed,
        **trace(row, row.accounting_event)} for row in rows]
    result += [{'id': str(row.pk), 'kind': 'revenue_adjustment', 'project_id': str(row.project_id),
        'amount_cents': row.adjustment_cents, 'occurred_on': row.occurred_on.isoformat(),
        'posting_period': row.posting_period, 'source': row.source_ref,
        **trace(row.recognition, row.accounting_event)} for row in adjustments]
    return sorted(result, key=lambda row: (row['occurred_on'], row['id']))
