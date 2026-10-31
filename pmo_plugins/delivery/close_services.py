"""PH-322 period close / project close write-side (append-only runs).

Closing/reopening updates only the existing AccountingPeriod/Project current row and appends
PeriodCloseRun/ProjectClose rows plus a DeliveryReceipt and plugin. audit. No ledger, revenue,
cost, cash or open-item fact is written; no auto-correction and no external notification.
"""
from datetime import date
from decimal import Decimal
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_member
from core.models import AccountingPeriod, AuditEvent, Membership, Project
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import DeliveryReceipt, PeriodCloseRun, ProjectClose

MODULE = 'delivery'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
PERIOD_RE = __import__('re').compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看关期/结项')
    if write and not is_enabled(fresh.organization, MODULE):
        raise PermissionDenied('交付模块已停用，不能关期/结项')
    return fresh


def _period(value):
    text = str(value or '')
    if not PERIOD_RE.match(text):
        _fail('期间须为YYYY-MM')
    year, month = text.split('-')
    if int(month) == 12:
        end = date(int(year) + 1, 1, 1)
    else:
        end = date(int(year), int(month) + 1, 1)
    return text, end - __import__('datetime').timedelta(days=1)


def _canonical_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _issue(code, *, severity='blocker', refs=None, expected=None, actual=None,
           reminder_id=None):
    return {'issue_code': code, 'severity': severity, 'refs': list(refs or []),
            'expected': expected, 'actual': actual,
            'reminder_ref': {'namespace': 'delivery', 'kind': 'close_reminder',
                             'id': reminder_id or str(uuid.uuid4()),
                             'severity': severity, 'issue_code': code}}


def _reminder(org_id, scope_kind, scope_id, code, digest, severity):
    key = f'close-reminder:{org_id}:{scope_kind}:{scope_id}:{code}:{digest}'
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _period_state(member, period):
    row = AccountingPeriod.objects.filter(organization=member.organization,
                                          period=period).first()
    runs = list(PeriodCloseRun.objects.filter(
        organization=member.organization, period=period).order_by('sequence'))
    latest = runs[-1] if runs else None
    if latest is not None and latest.state == 'closed':
        state = 'closed'
    elif latest is not None and latest.state == 'reopened':
        state = 'open'
    elif latest is not None and latest.state == 'closing':
        state = 'closing'
    elif row is not None and row.status == 'posted':
        state = 'closed'
    else:
        state = 'open'
    return row, runs, latest, state


def _period_precheck(member, period, as_of=None):
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目的结项摘要')
    period, period_end = _period(period)
    row, runs, latest, state = _period_state(member, period)
    blockers, warnings, missing = [], [], []
    if row is None:
        missing.append('accounting_period')
        warnings.append(_issue('period_missing', severity='warning',
                               expected='存在会计期间行', actual='缺失'))
    else:
        if row.status == 'posted' and state != 'open':
            blockers.append(_issue('period_already_closed', refs=[{'kind': 'accounting_period',
                                                                   'id': str(row.pk)}]))
        if row.status == 'posted' and not runs:
            warnings.append(_issue('historical_close_without_run', severity='warning'))
        if row.closed_through is not None and state == 'open':
            blockers.append(_issue('period_already_closed',
                                   refs=[{'kind': 'accounting_period', 'id': str(row.pk)}]))
    if period_end > timezone.localdate():
        blockers.append(_issue('future_dated_fact', expected='期间已结束',
                               actual=period_end.isoformat()))
    from . import operations_read_services as overview
    data = overview.ops_reconciliation_overview(member, as_of=as_of, period=period)
    section_codes = {
        'contracts': 'contract_termination_pending', 'time': 'timesheet_pending',
        'cash': 'cash_unposted', 'settlement': 'settlement_coverage_issue',
        'revenue': 'revenue_pairing_issue', 'onboarding': 'import_preview_conflict',
    }
    for section in data.get('sections', []):
        issues = section.get('issues') or []
        if not issues:
            continue
        code = section_codes.get(section.get('key'), 'unapproved_documents')
        blockers.append(_issue(code, refs=[{'kind': 'ops_section', 'id': section.get('key')}],
                               expected='无未结事项', actual=len(issues)))
    for code in data.get('missing') or []:
        warnings.append(_issue(code, severity='warning'))
    from pmo_plugins.settlement import coverage_read_services as coverage
    try:
        coverage_data = coverage.open_item_coverage_issues(member, as_of=as_of)
        if coverage_data['issue_count']:
            blockers.append(_issue('settlement_coverage_issue',
                                   actual=coverage_data['issue_count']))
    except Exception:
        missing.append('settlement_coverage')
    from pmo_plugins.management_revenue import pairing_read_services as revenue
    try:
        issues = revenue.revenue_pairing_issues(member, as_of=as_of)
        if issues:
            blockers.append(_issue('revenue_pairing_issue', actual=len(issues)))
    except Exception:
        missing.append('revenue_pairing')
    from pmo_plugins.costing import bank_charge_pairing_services as fee
    try:
        issues = fee.bank_charge_pairing_issues(member, as_of=as_of)
        if issues:
            blockers.append(_issue('fee_pairing_issue', actual=len(issues)))
    except Exception:
        missing.append('fee_pairing')
    from pmo_plugins.cash import bank_balance_services as bank
    try:
        bank_data = bank.bank_account_issues(member, as_of=as_of, period=period)
        if bank_data['issue_count']:
            blockers.append(_issue('cash_unposted', actual=bank_data['issue_count']))
    except Exception:
        missing.append('cash_balance')
    if any(item['issue_code'] == 'forecast_gap' for item in warnings):
        pass
    digest = _canonical_digest({'scope': 'period', 'period': period,
                                'blockers': blockers, 'warnings': warnings,
                                'missing': missing})
    for item in blockers + warnings:
        item['reminder_ref']['id'] = _reminder(
            member.organization_id, 'period', period, item['issue_code'], digest,
            item['severity'])
    return {'scope': 'period', 'period': period, 'state': state,
            'period_version': row.version if row else None,
            'blockers': blockers, 'warnings': warnings, 'missing': missing,
            'precheck_digest': digest}


def _project_precheck(member, project_id, as_of=None, allow_open_period=False):
    try:
        project = Project.objects.select_related('manager').get(
            organization=member.organization, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    if member.role == 'pm' and project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    blockers, warnings, missing = [], [], []
    from pmo_plugins.contracts.models import Contract, ContractChange
    contracts = list(Contract.objects.filter(organization=member.organization,
                                             project=project))
    active = [row for row in contracts if row.status == 'active']
    if not active:
        terminated = [row for row in contracts if row.status == 'terminated']
        if terminated:
            blockers.append(_issue('contract_terminated_pending_archive',
                                   refs=[{'kind': 'contract', 'id': str(row.pk)}
                                         for row in terminated]))
        else:
            blockers.append(_issue('contract_not_active', expected='项目合同 active',
                                   actual='缺失'))
    pending_changes = ContractChange.objects.filter(
        organization=member.organization, contract__project=project,
        status__in=['draft', 'submitted', 'reviewing']).count()
    if pending_changes:
        blockers.append(_issue('change_draft_pending', actual=pending_changes))
    from pmo_plugins.settlement.models import OpenItem
    open_items = OpenItem.objects.filter(organization=member.organization,
                                         project=project, status='active').count()
    if open_items:
        blockers.append(_issue('open_item_balance', actual=open_items))
    from pmo_plugins.settlement import coverage_read_services as coverage
    try:
        coverage_data = coverage.open_item_coverage_issues(member, as_of=as_of,
                                                           project_id=project.pk)
        if coverage_data['issue_count']:
            blockers.append(_issue('settlement_coverage_issue',
                                   actual=coverage_data['issue_count']))
    except Exception:
        missing.append('settlement_coverage')
    from pmo_plugins.management_revenue import pairing_read_services as revenue
    try:
        issues = revenue.revenue_pairing_issues(member, as_of=as_of)
        mine = [row for row in issues if str(row.get('project_id')) == str(project.pk)]
        if mine:
            blockers.append(_issue('revenue_pairing_issue', actual=len(mine)))
    except Exception:
        missing.append('revenue_pairing')
    from pmo_plugins.costing import bank_charge_pairing_services as fee
    try:
        issues = fee.bank_charge_pairing_issues(member, as_of=as_of)
        mine = [row for row in issues if str(row.get('project_id')) == str(project.pk)]
        if mine:
            blockers.append(_issue('fee_pairing_issue', actual=len(mine)))
    except Exception:
        missing.append('fee_pairing')
    from pmo_plugins.cash import bank_balance_services as bank
    try:
        bank_data = bank.bank_account_issues(member, as_of=as_of)
        mine = [row for row in bank_data['issues']
                if str(row.get('project_id')) == str(project.pk)]
        if mine:
            blockers.append(_issue('cash_reconciliation_issue', actual=len(mine)))
    except Exception:
        missing.append('cash_balance')
    period = timezone.localdate().strftime('%Y-%m')
    period_row = AccountingPeriod.objects.filter(organization=member.organization,
                                                 period=period).first()
    period_closed = bool(period_row and period_row.status == 'posted')
    if not period_closed:
        if allow_open_period:
            warnings.append(_issue('period_not_closed', severity='warning',
                                   expected='期间已关期', actual=period))
        else:
            blockers.append(_issue('period_not_closed', expected='期间已关期',
                                   actual=period))
    runs = list(ProjectClose.objects.filter(organization=member.organization,
                                            project=project).order_by('sequence'))
    if project.stage == 'closed' and not runs:
        warnings.append(_issue('historical_close_without_run', severity='warning'))
    warnings.append(_issue('archive_evidence_missing', severity='warning'))
    digest = _canonical_digest({'scope': 'project', 'project_id': str(project.pk),
                                'blockers': blockers, 'warnings': warnings,
                                'missing': missing})
    for item in blockers + warnings:
        item['reminder_ref']['id'] = _reminder(
            member.organization_id, 'project', str(project.pk), item['issue_code'],
            digest, item['severity'])
    return {'scope': 'project', 'project_id': str(project.pk),
            'project_version': project.version, 'stage': project.stage,
            'blockers': blockers, 'warnings': warnings, 'missing': missing,
            'precheck_digest': digest}


def period_close_precheck(member, period, *, as_of=None):
    return _period_precheck(_fresh(member), period, as_of=as_of)


def _reopen_period_precheck(member, period, as_of=None):
    if member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目的结项摘要')
    period, _ = _period(period)
    row, runs, latest, state = _period_state(member, period)
    if latest is None or latest.state != 'closed':
        _fail('仅已关期期间可以重开')
    missing = []
    if row is None:
        missing.append('accounting_period')
    digest = _canonical_digest({'scope': 'period_reopen', 'period': period,
                                'expected_version': row.version if row else None,
                                'latest_run': str(latest.pk)})
    return {'scope': 'period', 'mode': 'reopen', 'period': period,
            'state': 'closed', 'eligible': True,
            'period_version': row.version if row else None,
            'blockers': [], 'warnings': [], 'missing': missing,
            'precheck_digest': digest}


def reopen_period_precheck(member, period, *, as_of=None):
    return _reopen_period_precheck(_fresh(member), period, as_of=as_of)


def project_close_precheck(member, project_id, *, as_of=None, allow_open_period=False):
    return _project_precheck(_fresh(member), project_id, as_of=as_of,
                             allow_open_period=allow_open_period)


def reopen_project_precheck(member, project_id, *, as_of=None):
    member = _fresh(member)
    latest = ProjectClose.objects.filter(
        organization=member.organization, project_id=project_id).order_by('-sequence').first()
    if latest is None or latest.state != 'closed':
        _fail('仅已结项项目可以重开')
    return _project_precheck(member, project_id, as_of=as_of, allow_open_period=True)


def _receipt(member, action, token, digest, dry_run):
    if type(token) is not uuid.UUID:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            _fail('request_token 无效')
    existing = DeliveryReceipt.objects.filter(
        organization=member.organization, request_token=token).first()
    if existing is not None:
        if existing.digest != digest or existing.action != action:
            _fail('同一 request_token 的关期/结项输入已改变，请使用新的令牌')
        return existing, token
    if dry_run:
        return None, token
    return None, token


def _write_receipt(member, action, token, digest, result_id):
    return DeliveryReceipt.objects.create(organization=member.organization,
                                          request_token=token, action=action,
                                          digest=digest, result_id=result_id)


def _command_digest(payload):
    return _canonical_digest(payload)


@transaction.atomic
def close_period(member, period, *, request_token, expected_version, dry_run=True,
                 reason=''):
    member = _fresh(member, write=True)
    lock_company(member)
    period, period_end = _period(period)
    row, runs, latest, state = _period_state(member, period)
    if row is None:
        _fail('会计期间不存在')
    payload = {'scope': 'period', 'period': period, 'expected_version': expected_version,
               'reason': reason}
    digest = _command_digest(payload)
    receipt, token = _receipt(member, 'period.close', request_token, digest, dry_run)
    if receipt is not None:
        run = PeriodCloseRun.objects.get(pk=receipt.result_id)
        return {'replayed': True, 'run_ref': {'kind': 'period_close_run', 'id': str(run.pk)},
                'state': run.state}
    if row.version != expected_version:
        _fail('stale_version')
    if row.status == 'posted' or state == 'closed':
        _fail('period_already_closed')
    precheck = _period_precheck(member, period)
    if precheck['blockers']:
        _fail('预检存在 blocker，不能关期：' + '；'.join(
            item['issue_code'] for item in precheck['blockers']))
    if dry_run:
        return {'dry_run': True, 'scope': 'period', 'period': period,
                'state': 'open', 'blockers': precheck['blockers'],
                'warnings': precheck['warnings'], 'precheck_digest': precheck['precheck_digest'],
                'planned_state': 'closed'}
    sequence = (latest.sequence if latest else 0) + 1
    closing = PeriodCloseRun.objects.create(
        organization=member.organization, accounting_period=row, period=period,
        sequence=sequence, action='close', state='closing',
        from_period_version=row.version, closed_through=period_end,
        blockers=precheck['blockers'], warnings=precheck['warnings'],
        precheck_digest=precheck['precheck_digest'], reason=reason or '',
        actor=member, request_token=token)
    row.status = 'posted'
    row.closed_through = period_end
    row.version = row.version + 1
    row.save(update_fields=['status', 'closed_through', 'version'])
    closed = PeriodCloseRun.objects.create(
        organization=member.organization, accounting_period=row, period=period,
        sequence=sequence + 1, action='close', state='closed',
        from_period_version=row.version, closed_through=period_end,
        blockers=precheck['blockers'], warnings=precheck['warnings'],
        precheck_digest=precheck['precheck_digest'], reason=reason or '',
        actor=member, request_token=None)
    _write_receipt(member, 'period.close', token, digest, closed.pk)
    audit(member, 'plugin.delivery.period_closed', closed,
          {'run_ref': {'kind': 'period_close_run', 'id': str(closed.pk)},
           'period': period, 'precheck_digest': precheck['precheck_digest'],
           'blockers': precheck['blockers'], 'warnings': precheck['warnings']})
    return {'run_ref': {'kind': 'period_close_run', 'id': str(closed.pk)},
            'period': period, 'state': 'closed', 'period_version': row.version,
            'closing_run_ref': {'kind': 'period_close_run', 'id': str(closing.pk)},
            'precheck_digest': precheck['precheck_digest']}


@transaction.atomic
def reopen_period(member, period, *, request_token, expected_version, dry_run=True,
                  reason):
    member = _fresh(member, write=True)
    lock_company(member)
    period, _ = _period(period)
    row, runs, latest, state = _period_state(member, period)
    if row is None:
        _fail('会计期间不存在')
    text = str(reason or '').strip()
    if not text:
        _fail('重开必须填写原因')
    payload = {'scope': 'period', 'period': period, 'expected_version': expected_version,
               'reason': text}
    digest = _command_digest(payload)
    receipt, token = _receipt(member, 'period.reopen', request_token, digest, dry_run)
    if receipt is not None:
        run = PeriodCloseRun.objects.get(pk=receipt.result_id)
        return {'replayed': True, 'run_ref': {'kind': 'period_close_run', 'id': str(run.pk)},
                'state': run.state}
    if row.version != expected_version:
        _fail('stale_version')
    if latest is None or latest.state != 'closed':
        _fail('仅已关期期间可以重开')
    if dry_run:
        return {'dry_run': True, 'scope': 'period', 'period': period,
                'state': state, 'planned_state': 'open'}
    old_closed = row.closed_through
    row.status = 'active'
    row.closed_through = None
    row.version = row.version + 1
    row.save(update_fields=['status', 'closed_through', 'version'])
    reopened = PeriodCloseRun.objects.create(
        organization=member.organization, accounting_period=row, period=period,
        sequence=latest.sequence + 1, action='reopen', state='reopened',
        from_period_version=row.version, closed_through=old_closed,
        blockers=[], warnings=[], precheck_digest=digest, reason=text,
        actor=member, request_token=token)
    _write_receipt(member, 'period.reopen', token, digest, reopened.pk)
    audit(member, 'plugin.delivery.period_reopened', reopened,
          {'run_ref': {'kind': 'period_close_run', 'id': str(reopened.pk)},
           'period': period, 'reason': text})
    return {'run_ref': {'kind': 'period_close_run', 'id': str(reopened.pk)},
            'period': period, 'state': 'open', 'period_version': row.version}


@transaction.atomic
def close_project(member, project_id, *, request_token, expected_version, dry_run=True,
                  reason='', allow_open_period=False):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        project = Project.objects.get(organization=member.organization, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    payload = {'scope': 'project', 'project_id': str(project.pk),
               'expected_version': expected_version, 'reason': reason,
               'allow_open_period': allow_open_period}
    digest = _command_digest(payload)
    receipt, token = _receipt(member, 'project.close', request_token, digest, dry_run)
    if receipt is not None:
        run = ProjectClose.objects.get(pk=receipt.result_id)
        return {'replayed': True, 'run_ref': {'kind': 'project_close_run', 'id': str(run.pk)},
                'state': run.state}
    if project.version != expected_version:
        _fail('stale_version')
    if project.stage in ('closed', 'cancelled'):
        _fail('project_already_closed')
    precheck = _project_precheck(member, project_id, allow_open_period=allow_open_period)
    if precheck['blockers']:
        _fail('预检存在 blocker，不能结项：' + '；'.join(
            item['issue_code'] for item in precheck['blockers']))
    if dry_run:
        return {'dry_run': True, 'scope': 'project', 'project_id': str(project.pk),
                'state': 'open', 'blockers': precheck['blockers'],
                'warnings': precheck['warnings'], 'precheck_digest': precheck['precheck_digest'],
                'planned_state': 'closed'}
    latest = ProjectClose.objects.filter(organization=member.organization,
                                         project=project).order_by('-sequence').first()
    sequence = (latest.sequence if latest else 0) + 1
    from_stage = project.stage
    closing = ProjectClose.objects.create(
        organization=member.organization, project=project, sequence=sequence,
        action='close', state='closing', from_stage=from_stage,
        from_project_version=project.version, blockers=precheck['blockers'],
        warnings=precheck['warnings'], precheck_digest=precheck['precheck_digest'],
        reason=reason or '', actor=member, request_token=token)
    project.stage = 'closed'
    project.previous_stage = from_stage
    project.version = project.version + 1
    project.save(update_fields=['stage', 'previous_stage', 'version'])
    closed = ProjectClose.objects.create(
        organization=member.organization, project=project, sequence=sequence + 1,
        action='close', state='closed', from_stage=from_stage,
        from_project_version=project.version, blockers=precheck['blockers'],
        warnings=precheck['warnings'], precheck_digest=precheck['precheck_digest'],
        reason=reason or '', actor=member, request_token=None)
    _write_receipt(member, 'project.close', token, digest, closed.pk)
    audit(member, 'plugin.delivery.project_closed', closed,
          {'run_ref': {'kind': 'project_close_run', 'id': str(closed.pk)},
           'project_id': str(project.pk), 'from_stage': from_stage,
           'precheck_digest': precheck['precheck_digest']})
    return {'run_ref': {'kind': 'project_close_run', 'id': str(closed.pk)},
            'project_id': str(project.pk), 'state': 'closed',
            'project_version': project.version,
            'closing_run_ref': {'kind': 'project_close_run', 'id': str(closing.pk)},
            'precheck_digest': precheck['precheck_digest']}


@transaction.atomic
def reopen_project(member, project_id, *, request_token, expected_version, dry_run=True,
                   reason):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        project = Project.objects.get(organization=member.organization, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    latest = ProjectClose.objects.filter(organization=member.organization,
                                         project=project).order_by('-sequence').first()
    text = str(reason or '').strip()
    if not text:
        _fail('重开必须填写原因')
    payload = {'scope': 'project', 'project_id': str(project.pk),
               'expected_version': expected_version, 'reason': text}
    digest = _command_digest(payload)
    receipt, token = _receipt(member, 'project.reopen', request_token, digest, dry_run)
    if receipt is not None:
        run = ProjectClose.objects.get(pk=receipt.result_id)
        return {'replayed': True, 'run_ref': {'kind': 'project_close_run', 'id': str(run.pk)},
                'state': run.state}
    if project.version != expected_version:
        _fail('stale_version')
    if latest is None or latest.state != 'closed':
        _fail('仅已结项项目可以重开')
    if dry_run:
        return {'dry_run': True, 'scope': 'project', 'project_id': str(project.pk),
                'state': 'closed', 'planned_state': 'active',
                'restore_stage': latest.from_stage}
    restore = latest.from_stage
    if restore not in {choice[0] for choice in Project.Stage.choices} or restore == 'closed':
        _fail('关闭前阶段不合法，不能重开')
    project.stage = restore
    project.previous_stage = 'closed'
    project.version = project.version + 1
    project.save(update_fields=['stage', 'previous_stage', 'version'])
    reopened = ProjectClose.objects.create(
        organization=member.organization, project=project, sequence=latest.sequence + 1,
        action='reopen', state='reopened', from_stage='closed',
        from_project_version=project.version, blockers=[], warnings=[],
        precheck_digest=digest, reason=text, actor=member, request_token=token)
    _write_receipt(member, 'project.reopen', token, digest, reopened.pk)
    audit(member, 'plugin.delivery.project_reopened', reopened,
          {'run_ref': {'kind': 'project_close_run', 'id': str(reopened.pk)},
           'project_id': str(project.pk), 'reason': text, 'restore_stage': restore})
    return {'run_ref': {'kind': 'project_close_run', 'id': str(reopened.pk)},
            'project_id': str(project.pk), 'state': 'active',
            'project_version': project.version, 'restore_stage': restore}
