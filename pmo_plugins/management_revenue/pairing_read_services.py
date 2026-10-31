"""PH-284 read-only management-revenue / settlement / contract-terms pairing.

No write path, migration, canonical topic or business fact is created here. The
service composes the existing management-revenue, settlement and contracts
named reads into a project/period snapshot plus an issue list.
"""
import re
from collections import Counter
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.utils import timezone

from core.access import visible_projects
from core.models import Membership, Project
from core.services import require_member
from pmo_plugins.contracts import approval_services as contract_approvals
from pmo_plugins.contracts.models import Contract, ContractLine
from pmo_plugins.management_revenue import services as revenue_services
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition
from pmo_plugins.settlement import receivable_services
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement

READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
PERIOD_RE = re.compile(r'^[0-9]{4}-(0[1-9]|1[0-2])$')
MAX_ROWS = 200


def _fail(message):
    raise ValidationError(message)


def _reader(member):
    """History reads stay available when the module is disabled; pm is scoped."""
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权查看收入配对核对')
    return member


def _date(value):
    if value in (None, ''):
        return None
    if type(value) is date:
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError):
            _fail('基准日须为有效的年月日')
    if parsed > timezone.localdate():
        _fail('基准日不能在未来')
    return parsed


def _period(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not PERIOD_RE.match(value):
        _fail('期间须为YYYY-MM')
    return value


def _projects(member):
    rows = visible_projects(member)
    if member.role == 'pm':
        rows = rows.filter(manager=member)
    return rows


def _project(member, project_id):
    try:
        project = _projects(member).get(pk=project_id)
    except (Project.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('项目不存在或无权访问')
    return project


def _ref(kind, row, namespace):
    return {'namespace': namespace, 'kind': kind, 'id': str(row.pk), 'version': 1}


def _recognition_ref(row):
    return {'namespace': 'management_revenue', 'kind': 'revenue_recognition',
            'id': str(row.pk), 'version': 1}


def _settlement_ref(row):
    return {'namespace': 'settlement', 'kind': 'settlement', 'id': str(row.pk),
            'version': row.version}


def _line_ref(line):
    return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk),
            'version': line.version}


def _project_rows(member, projects, period, as_of):
    rows, adjustments = [], []
    for project in projects:
        try:
            exported = revenue_services.revenue_export_rows(
                member, project_id=project.pk, as_of=as_of.isoformat() if as_of else None)
        except (PermissionDenied, ValidationError, AttributeError):
            exported = _fallback_rows(member, project, as_of)
        for item in exported:
            if period and item.get('posting_period') != period:
                continue
            if item['kind'] == 'revenue_recognition':
                rows.append(item)
            else:
                adjustments.append(item)
    return rows, adjustments


def _fallback_rows(member, project, as_of):
    """Read-only line-level fallback when the owner export needs a missing accounting event."""
    rows = RevenueRecognition.objects.filter(
        organization=member.organization, project=project, status='posted')
    adjustments = RevenueAdjustment.objects.filter(
        organization=member.organization, project=project, status='posted')
    if as_of:
        rows = rows.filter(occurred_on__lte=as_of)
        adjustments = adjustments.filter(occurred_on__lte=as_of)
    result = [{'id': str(row.pk), 'kind': 'revenue_recognition',
               'amount_cents': row.recognized_net_cents, 'source': row.source_ref,
               'occurred_on': row.occurred_on.isoformat(),
               'posting_period': row.posting_period, 'project_id': str(row.project_id),
               'contract_line_id': str(row.contract_line_id),
               'acceptance_id': str(row.acceptance_id) if row.acceptance_id else None,
               'service_confirmation_month_id': (str(row.service_confirmation_month_id)
                                                  if row.service_confirmation_month_id else None),
               'is_reversed': row.is_reversed} for row in rows.order_by('occurred_on', 'id')]
    result += [{'id': str(row.pk), 'kind': 'revenue_adjustment',
                'amount_cents': row.adjustment_cents, 'source': row.source_ref,
                'occurred_on': row.occurred_on.isoformat(),
                'posting_period': row.posting_period, 'project_id': str(row.project_id),
                'contract_line_id': str(row.recognition.contract_line_id),
                'acceptance_id': None, 'service_confirmation_month_id': None,
                'is_reversed': False} for row in adjustments.order_by('occurred_on', 'id')]
    return result


def _settlements(member, projects, period, as_of):
    rows = Settlement.objects.select_related('project', 'contract_line').filter(
        organization=member.organization, project__in=projects, status='active')
    if as_of:
        rows = rows.filter(settled_on__lte=as_of)
    result, missing = [], []
    for row in rows.order_by('settled_on', 'id'):
        if period and row.settled_on.strftime('%Y-%m') != period:
            continue
        try:
            result.append(receivable_services.settlement_snapshot(member, row.pk))
        except (PermissionDenied, ValidationError) as error:
            missing.append({
                'settlement_ref': _settlement_ref(row),
                'reason': '；'.join(getattr(error, 'messages', [str(error)])),
            })
    return result, missing


def _unsettled_and_cash(settlements):
    settlement_ids = [item['id'] for item in settlements]
    items = OpenItem.objects.filter(settlement_id__in=settlement_ids, status='active')
    gross = items.aggregate(total=Sum('gross_cents'))['total'] or 0
    cleared = Clearance.objects.filter(
        open_item__in=items, status='active').aggregate(
        total=Sum('cleared_cents'))['total'] or 0
    return max(0, gross - cleared), cleared


def _terminated_contracts(member, projects):
    return list(Contract.objects.filter(
        organization=member.organization, project__in=projects,
        status='terminated').order_by('contract_code', 'id'))


def _issues_for(member, recognitions, settlements, terminated_contracts):
    issues = []
    recognized_by_line = Counter()
    for item in recognitions:
        recognized_by_line[item['contract_line_id']] += item['amount_cents']
    recognition_orgs = {str(pk): org for pk, org in RevenueRecognition.objects.filter(
        pk__in=[item['id'] for item in recognitions]).values_list('pk', 'organization_id')}
    for item in recognitions:
        refs = [{'recognition_ref': item['source']}]
        line_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                    'id': item['contract_line_id'], 'version': 1}
        refs.append({'contract_line_ref': line_ref})
        recognition_org = recognition_orgs.get(item['id'])
        line = ContractLine.objects.select_related(
            'contract_version__contract').filter(pk=item['contract_line_id']).first()
        if line is None:
            issues.append({'issue_code': 'recognition_source_invalid', 'refs': refs,
                           'expected': 'approved contract line', 'actual': 'missing'})
            continue
        contract = line.contract_version.contract
        if recognition_org is not None and line.organization_id != recognition_org:
            issues.append({'issue_code': 'cross_org_reference', 'refs': refs,
                           'expected': str(line.organization_id), 'actual': str(recognition_org)})
        if str(contract.project_id) != str(item['project_id']):
            issues.append({'issue_code': 'cross_project_reference', 'refs': refs,
                           'expected': str(contract.project_id), 'actual': str(item['project_id'])})
        if contract.status == 'terminated' and contract.terminated_on is not None \
                and item['occurred_on'] >= contract.terminated_on.isoformat():
            issues.append({'issue_code': 'recognized_after_termination', 'refs': refs,
                           'expected': f'before {contract.terminated_on.isoformat()}',
                           'actual': item['occurred_on']})
            continue
        try:
            snapshot = contract_approvals.approved_line_snapshot(
                member, line_ref, item['occurred_on'])
        except (PermissionDenied, ValidationError) as error:
            issues.append({'issue_code': 'recognition_source_invalid', 'refs': refs,
                           'expected': 'approved contract line', 'actual': '；'.join(
                               getattr(error, 'messages', [str(error)]))})
            continue
        if str(snapshot['project_id']) != str(item['project_id']):
            issues.append({'issue_code': 'cross_project_reference', 'refs': refs,
                           'expected': snapshot['project_id'], 'actual': item['project_id']})
        cap = snapshot['net_cents']
        if recognized_by_line[item['contract_line_id']] > cap:
            issues.append({'issue_code': 'recognized_over_contract_line', 'refs': refs,
                           'expected': cap, 'actual': recognized_by_line[item['contract_line_id']]})
    keys = Counter()
    for item in recognitions:
        key = (item.get('contract_line_id'), str(item.get('acceptance_id')),
               str(item.get('service_confirmation_month_id')))
        keys[key] += 1
    for (line_id, acceptance_id, service_month_id), count in keys.items():
        if count > 1 and (acceptance_id != 'None' or service_month_id != 'None'):
            issues.append({'issue_code': 'duplicate_recognition',
                           'refs': [{'contract_line_ref': {'namespace': 'contracts',
                                                           'kind': 'contract_line',
                                                           'id': line_id, 'version': 1}}],
                           'expected': 1, 'actual': count})
    recognized_lines = {item['contract_line_id'] for item in recognitions}
    settled_by_line = Counter()
    for item in settlements:
        line_id = item['contract_line_id']
        settled_by_line[line_id] += item['gross_cents']
        refs = [{'settlement_ref': item['settlement_ref']},
                {'contract_line_ref': item['contract_line_ref']}]
        settlement_line = ContractLine.objects.select_related(
            'contract_version__contract').filter(pk=line_id).first()
        if (settlement_line is not None
                and settlement_line.contract_version.contract.status == 'terminated'
                and settlement_line.contract_version.contract.terminated_on is not None
                and str(item['settled_on']) >= settlement_line.contract_version.contract.terminated_on.isoformat()):
            issues.append({'issue_code': 'settlement_after_termination', 'refs': refs,
                           'expected': 'before termination', 'actual': item['settled_on']})
        else:
            try:
                contract_approvals.approved_line_snapshot(
                    member, item['contract_line_ref'], item['settled_on'])
            except (PermissionDenied, ValidationError) as error:
                issues.append({'issue_code': 'settlement_source_invalid', 'refs': refs,
                               'expected': 'approved contract line', 'actual': '；'.join(
                                   getattr(error, 'messages', [str(error)]))})
        if line_id not in recognized_lines:
            issues.append({'issue_code': 'settlement_without_recognition', 'refs': refs,
                           'expected': 'matching recognition', 'actual': None})
        if settled_by_line[line_id] > recognized_by_line.get(line_id, 0):
            issues.append({'issue_code': 'settled_exceeds_recognized', 'refs': refs,
                           'expected': recognized_by_line.get(line_id, 0),
                           'actual': settled_by_line[line_id]})
    for contract in terminated_contracts:
        for version in contract.versions.filter(status='approved').prefetch_related('lines'):
            for line in version.lines.all():
                if str(line.pk) not in recognized_lines and str(line.pk) in settled_by_line:
                    issues.append({'issue_code': 'terms_without_recognition',
                                   'refs': [{'contract_line_ref': _line_ref(line)}],
                                   'expected': 'recognition', 'actual': None})
    return issues


def _project_snapshot(member, project, period, as_of):
    totals = contract_approvals.approved_contract_totals(member, project.pk) or {
        'net_cents': 0, 'tax_cents': 0, 'gross_cents': 0}
    rows, adjustments = _project_rows(member, [project], period, as_of)
    recognized = sum(row['amount_cents'] for row in rows) + sum(
        row['amount_cents'] for row in adjustments)
    settlements, settlement_missing = _settlements(member, [project], period, as_of)
    if settlement_missing:
        # A denied/unreadable settlement must be shown as missing, never fabricated as zero.
        settled = unsettled = cash_coverage = None
    else:
        unsettled, cash_coverage = _unsettled_and_cash(settlements)
        settled = sum(item['gross_cents'] for item in settlements)
    terminated = _terminated_contracts(member, [project])
    issues = _issues_for(member, rows, settlements, terminated)
    issues.extend({
        'issue_code': 'settlement_unreadable',
        'refs': [{'settlement_ref': item['settlement_ref']}],
        'expected': 'readable active settlement', 'actual': item['reason'],
    } for item in settlement_missing)
    missing = list(settlement_missing)
    if settlement_missing:
        missing.append({'missing_fields': ['settled_cents', 'unsettled_cents',
                                          'cash_coverage_cents', 'difference_cents']})
    return {
        'project_id': str(project.pk), 'project_code': project.code, 'project_name': project.name,
        'period': period, 'as_of': as_of.isoformat() if as_of else None,
        'contract_value_cents': totals['net_cents'],
        'recognized_cents': recognized, 'settled_cents': settled,
        'unsettled_cents': unsettled, 'cash_coverage_cents': cash_coverage,
        'difference_cents': (recognized - settled) if settled is not None else None,
        'contract_variance_cents': totals['net_cents'] - recognized,
        'settlement_missing': settlement_missing, 'missing': missing,
        'terminated': bool(terminated),
        'terminated_contract_refs': [
            {'namespace': 'contracts', 'kind': 'contract', 'id': str(c.pk), 'version': c.version}
            for c in terminated],
        'recognition_refs': [row['source'] for row in rows][:MAX_ROWS],
        'settlement_refs': [item['settlement_ref'] for item in settlements][:MAX_ROWS],
        'issues': issues,
        'issues_count': len(issues),
    }


def revenue_pairing_snapshot(member, *, project_id=None, period=None, as_of=None):
    """Read-only project/period revenue-vs-settlement snapshot; no writes or audit."""
    member = _reader(member)
    when = _date(as_of)
    period = _period(period)
    if project_id is not None:
        project = _project(member, project_id)
        return _project_snapshot(member, project, period, when)
    projects = list(_projects(member).order_by('code', 'id')[:MAX_ROWS])
    rows = [_project_snapshot(member, project, period, when) for project in projects]
    def total(key):
        values = [row[key] for row in rows]
        return None if any(value is None for value in values) else sum(values)
    aggregate = {
        'contract_value_cents': total('contract_value_cents'),
        'recognized_cents': total('recognized_cents'),
        'settled_cents': total('settled_cents'),
        'unsettled_cents': total('unsettled_cents'),
        'cash_coverage_cents': total('cash_coverage_cents'),
    }
    aggregate.update({
        'difference_cents': (aggregate['recognized_cents'] - aggregate['settled_cents']
                             if aggregate['settled_cents'] is not None
                             and aggregate['recognized_cents'] is not None else None),
        'contract_variance_cents': (
            aggregate['contract_value_cents'] - aggregate['recognized_cents']
            if aggregate['contract_value_cents'] is not None
            and aggregate['recognized_cents'] is not None else None),
        'settlement_missing': [item for row in rows for item in row['settlement_missing']],
        'missing': [item for row in rows for item in row['missing']],
    })
    return {'project_id': None, 'period': period, 'as_of': when.isoformat() if when else None,
            **aggregate, 'projects': rows, 'project_limit': MAX_ROWS,
            'truncated': len(_projects(member)) > MAX_ROWS,
            'issues': [issue for row in rows for issue in row['issues']]}


def revenue_pairing_issues(member, *, as_of=None):
    """Read-only issue list over visible projects; no writes or audit."""
    member = _reader(member)
    when = _date(as_of)
    issues = []
    for project in _projects(member).order_by('code', 'id')[:MAX_ROWS]:
        snapshot = _project_snapshot(member, project, None, when)
        for issue in snapshot['issues']:
            issues.append({'issue_code': issue['issue_code'],
                           'project_id': str(project.pk),
                           'refs': issue['refs'],
                           'expected': issue['expected'], 'actual': issue['actual']})
    return issues
