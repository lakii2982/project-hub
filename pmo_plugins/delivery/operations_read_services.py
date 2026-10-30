"""PH-291 cross-domain read-only operations reconciliation overview.

Aggregates already-delivered owner facts (contracts, settlement, management
revenue, time billing, onboarding preview, cash) into one read-only overview.
No write path, no audit, no session persistence, no migration and no second
insights owner: every value is derived on request through owner models/queries.
Missing or not-yet-implemented upstream data is reported as ``None`` plus a
``missing`` code, never as a zero.
"""
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.utils import timezone

from core.access import visible_projects
from core.models import Membership, Timesheet
from pmo_plugins.cash.models import (BankReturn, CashClassification, CashTransaction,
                                     Refund)
from pmo_plugins.contracts.models import Contract, ContractChange, ContractLine
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
from pmo_plugins.time.models import BillableConfirmation

READ_ROLES = {'owner', 'finance', 'auditor'}
REF_LIMIT = 50
PERIOD_ERROR = '期间须为有效的年月（YYYY-MM）'


class _Section(dict):
    """Plain dict carrying the overview section shape for stable assertions."""


def _fresh_member(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied('无效公司成员上下文') from error
    if not fresh.active or not fresh.user.is_active:
        raise PermissionDenied('此账户尚未加入有效公司工作区')
    return fresh


def _reader(member):
    """History reads stay available after a module is disabled."""
    fresh = _fresh_member(member)
    if fresh.role == 'member' or fresh.role not in READ_ROLES | {'pm'}:
        raise PermissionDenied('无权查看运营核对总览')
    return fresh


def _date(value):
    if value in (None, ''):
        return timezone.localdate()
    if isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise ValidationError('快照日期须为有效的年月日')
        if parsed.isoformat() != str(value):
            raise ValidationError('快照日期须为有效的年月日')
    if parsed > timezone.localdate():
        raise ValidationError('快照日期不能在未来')
    return parsed


def _period(value):
    if value in (None, ''):
        return None
    text = str(value)
    if len(text) != 7 or text[4] != '-' or not text[:4].isdigit() or not text[5:].isdigit():
        raise ValidationError(PERIOD_ERROR)
    month = int(text[5:])
    if not 1 <= month <= 12:
        raise ValidationError(PERIOD_ERROR)
    first = date(int(text[:4]), month, 1)
    return first, first.replace(day=28) + timedelta(days=4) - timedelta(days=4)


def _window(as_of, period):
    """Return inclusive (start, end) date bounds; ``start`` is None without a period."""
    if not period:
        return None, as_of
    start = period[0]
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, min(as_of, next_month - timedelta(days=1))


def _in_window(field, as_of, period):
    start, end = _window(as_of, period)
    query = Q(**{f'{field}__lte': as_of})
    if start is not None:
        query &= Q(**{f'{field}__gte': start, f'{field}__lte': end})
    return query


def _ref(namespace, kind, instance, version=1):
    return {'namespace': namespace, 'kind': kind, 'id': str(instance.pk),
            'version': getattr(instance, 'version', version)}


def _capped(rows):
    rows = list(rows)
    return rows[:REF_LIMIT], len(rows) > REF_LIMIT


def _metrics(counts, labels, totals=None, total_labels=None):
    rows = []
    for key, value in (counts or {}).items():
        rows.append({'key': key, 'label': labels.get(key, key), 'value': value,
                     'unit': 'count'})
    for key, value in (totals or {}).items():
        rows.append({'key': key, 'label': (total_labels or {}).get(key, key),
                     'value': value, 'unit': 'cents'})
    return rows


def _scoped_projects(member):
    if member.role == 'pm':
        return visible_projects(member)
    from core.models import Project
    return Project.objects.filter(organization=member.organization)


def _contracts_section(member, as_of, period):
    projects = _scoped_projects(member)
    changes = ContractChange.objects.filter(
        organization=member.organization, status__in=['draft', 'pending_customer'],
        contract__project__in=projects,
    ).filter(_in_window('requested_on', as_of, period)).select_related('contract')
    terminated = Contract.objects.filter(
        organization=member.organization, project__in=projects, status='terminated',
    ).filter(_in_window('terminated_on', as_of, period))
    active_lines = ContractLine.objects.filter(
        organization=member.organization, status='approved',
        contract_version__contract__project__in=projects,
        contract_version__contract__status='terminated',
    ).filter(_in_window('contract_version__contract__terminated_on', as_of, period))
    counts = {'pending_changes': changes.count(),
              'terminated_contracts': terminated.count(),
              'terminated_active_lines': active_lines.count()}
    totals = {'pending_gross_delta_cents':
              changes.aggregate(value=Sum('gross_delta_cents'))['value'] or 0}
    total_refs = changes.count() + terminated.count()
    refs = [_ref('contracts', 'contract_change', row) for row in changes[:REF_LIMIT]]
    refs += [_ref('contracts', 'contract', row) for row in terminated[:REF_LIMIT]]
    refs, truncated = refs[:REF_LIMIT], total_refs > REF_LIMIT
    issues = []
    if counts['pending_changes']:
        issues.append({'code': 'pending_change_approval',
                       'summary': f"{counts['pending_changes']} 个合同变更待审批/待客户确认",
                       'refs': [_ref('contracts', 'contract_change', row)
                                for row in changes[:3]]})
    if counts['terminated_contracts']:
        issues.append({'code': 'terminated_contract',
                       'summary': f"{counts['terminated_contracts']} 个合同已终止",
                       'refs': [_ref('contracts', 'contract', row) for row in terminated[:3]]})
    if counts['terminated_active_lines']:
        issues.append({'code': 'terminated_still_active_terms',
                       'summary': f"{counts['terminated_active_lines']} 条批准条款挂在已终止合同下",
                       'refs': [_ref('contracts', 'contract_line', row)
                                for row in active_lines[:3]]})
    return _Section(key='contracts', label='合同', counts=counts,
                    labels={'pending_changes': '待审批变更', 'terminated_contracts': '已终止合同',
                            'terminated_active_lines': '终止后仍生效条款'},
                    totals_cents=totals,
                    total_labels={'pending_gross_delta_cents': '待审变更含税差额'},
                    refs=refs, truncated=truncated, issues=issues,
                    issue_counts={item['code']: counts[{
                        'pending_change_approval': 'pending_changes',
                        'terminated_contract': 'terminated_contracts',
                        'terminated_still_active_terms': 'terminated_active_lines'}[item['code']]]
                        for item in issues},
                    missing=[])


def _settlement_section(member, as_of, period):
    projects = _scoped_projects(member)
    items = OpenItem.objects.filter(organization=member.organization,
                                    project__in=projects, status='active')
    items = items.filter(_in_window('occurred_on', as_of, period))
    clearances = Clearance.objects.filter(organization=member.organization,
                                          open_item__in=items)
    active = clearances.filter(status='active')
    cleared = {row['open_item_id']: row['value'] for row in active.values('open_item_id').annotate(value=Sum('cleared_cents'))}
    over = 0
    negative = 0
    remaining_total = 0
    for item in items:
        covered = cleared.get(item.pk, 0)
        if covered > item.gross_cents:
            over += 1
        if covered < 0:
            negative += 1
        remaining_total += max(0, item.gross_cents - covered)
    refunds = Refund.objects.filter(organization=member.organization,
                                    original_cash_transaction__funding_account__organization=member.organization)
    refunds = refunds.filter(_in_window('refund_date', as_of, period))
    returns = BankReturn.objects.filter(organization=member.organization,
                                        original_cash_transaction__funding_account__organization=member.organization)
    returns = returns.filter(_in_window('return_date', as_of, period))
    refund_total = refunds.aggregate(value=Sum('gross_cents'))['value'] or 0
    return_total = returns.aggregate(value=Sum('gross_cents'))['value'] or 0
    refunded_cash_ids = set(refunds.values_list('original_cash_transaction_id', flat=True))
    cleared_cash_ids = set(Clearance.objects.filter(organization=member.organization,
                                                    status='active').values_list('cash_transaction_id', flat=True))
    uncovered = len(refunded_cash_ids - cleared_cash_ids)
    counts = {'active_open_items': items.count(),
              'over_covered_items': over,
              'refunds': refunds.count(),
              'bank_returns': returns.count(),
              'refunds_without_clearance': uncovered}
    totals = {'remaining_cents': remaining_total,
              'refund_cents': refund_total,
              'bank_return_cents': return_total}
    refs = [_ref('settlement', 'open_item', row) for row in items[:REF_LIMIT]]
    refs, truncated = refs, items.count() > REF_LIMIT
    issues = []
    if over:
        issues.append({'code': 'over_cleared_open_item',
                       'summary': f'{over} 个未清项核销额超过应收额',
                       'refs': [_ref('settlement', 'open_item', row)
                                for row in items[:3] if cleared.get(row.pk, 0) > row.gross_cents]})
    if uncovered:
        issues.append({'code': 'refund_without_clearance',
                       'summary': f'{uncovered} 笔退款原现金未在核销记录中覆盖',
                       'refs': []})
    return _Section(key='settlement', label='结算与未清', counts=counts,
                    labels={'active_open_items': '活动未清项', 'over_covered_items': '超额核销未清项',
                            'refunds': '退款', 'bank_returns': '银行退回',
                            'refunds_without_clearance': '退款未覆盖'},
                    totals_cents=totals,
                    total_labels={'remaining_cents': '未清余额', 'refund_cents': '退款金额',
                                  'bank_return_cents': '银行退回金额'},
                    refs=refs, truncated=truncated, issues=issues,
                    issue_counts={item['code']: (over if item['code'] == 'over_cleared_open_item' else uncovered)
                                  for item in issues},
                    missing=[])


def _revenue_section(member, as_of, period):
    projects = _scoped_projects(member)
    recognized = RevenueRecognition.objects.filter(
        organization=member.organization, project__in=projects, status='posted')
    recognized = recognized.filter(_in_window('occurred_on', as_of, period))
    reversed_ids = RevenueAdjustment.objects.filter(
        organization=member.organization, status='posted').values_list('recognition_id', flat=True)
    terminated_ids = set()
    terminated_lines = ContractLine.objects.filter(
        organization=member.organization,
        contract_version__contract__status='terminated').values_list('pk', flat=True)
    for row in recognized:
        if row.contract_line_id in terminated_lines:
            terminated_ids.add(row.pk)
    counts = {'posted_recognitions': recognized.count(),
              'reversed_recognitions': len(set(recognized.values_list('pk', flat=True)) & set(reversed_ids)),
              'terminated_still_recognized': len(terminated_ids)}
    recognized_total = recognized.aggregate(value=Sum('recognized_net_cents'))['value'] or 0
    settled = Settlement.objects.filter(organization=member.organization,
                                        project__in=projects, status='active')
    settled = settled.filter(_in_window('settled_on', as_of, period))
    settled_total = settled.aggregate(value=Sum('net_cents'))['value'] or 0
    totals = {'recognized_net_cents': recognized_total, 'settled_net_cents': settled_total,
              'difference_cents': recognized_total - settled_total}
    refs = [_ref('management_revenue', 'revenue_recognition', row) for row in recognized[:REF_LIMIT]]
    refs, truncated = refs, recognized.count() > REF_LIMIT
    issues = []
    if counts['terminated_still_recognized']:
        issues.append({'code': 'terminated_still_recognized',
                       'summary': f"{counts['terminated_still_recognized']} 条收入确认来自已终止合同",
                       'refs': [_ref('management_revenue', 'revenue_recognition', row)
                                for row in recognized if row.pk in terminated_ids][:3]})
    return _Section(key='management_revenue', label='管理收入', counts=counts,
                    labels={'posted_recognitions': '已过账确认',
                            'reversed_recognitions': '已冲销确认',
                            'terminated_still_recognized': '终止后确认'},
                    totals_cents=totals,
                    total_labels={'recognized_net_cents': '确认净额', 'settled_net_cents': '生效结算净额',
                                  'difference_cents': '确认-结算差额'},
                    refs=refs, truncated=truncated, issues=issues,
                    issue_counts={item['code']: counts['terminated_still_recognized']
                                  for item in issues},
                    missing=[])


def _time_section(member, as_of, period):
    projects = _scoped_projects(member)
    confirmations = BillableConfirmation.objects.filter(organization=member.organization)
    if member.role == 'pm':
        line_ids = ContractLine.objects.filter(
            contract_version__contract__project__in=projects).values_list('pk', flat=True)
        confirmations = confirmations.filter(contract_line_id__in=list(line_ids))
    confirmations = confirmations.filter(_in_window('confirmed_on', as_of, period))
    draft = confirmations.filter(status='draft')
    confirmed = confirmations.filter(status='confirmed')
    sheets = Timesheet.objects.filter(organization=member.organization,
                                      project__in=projects, status='submitted')
    sheets = sheets.filter(_in_window('work_date', as_of, period))
    counts = {'pending_confirmations': draft.count(), 'confirmed_confirmations': confirmed.count(),
              'unapproved_timesheets': sheets.count()}
    minutes = {'confirmed_minutes': confirmed.aggregate(value=Sum('confirmed_minutes'))['value'] or 0}
    refs = [_ref('time', 'billable_confirmation', row) for row in draft[:REF_LIMIT]]
    refs += [_ref('time', 'billable_confirmation', row) for row in confirmed[:REF_LIMIT]]
    refs, truncated = refs[:REF_LIMIT], (draft.count() + confirmed.count()) > REF_LIMIT
    issues = []
    if counts['pending_confirmations']:
        issues.append({'code': 'billable_confirmation_pending',
                       'summary': f"{counts['pending_confirmations']} 条可计费确认待客户确认",
                       'refs': [_ref('time', 'billable_confirmation', row) for row in draft[:3]]})
    if counts['unapproved_timesheets']:
        issues.append({'code': 'timesheet_unapproved',
                       'summary': f"{counts['unapproved_timesheets']} 条工时待审批",
                       'refs': []})
    return _Section(key='time', label='工时与可计费', counts=counts,
                    labels={'pending_confirmations': '待确认可计费', 'confirmed_confirmations': '客户已确认',
                            'unapproved_timesheets': '待审批工时'},
                    totals_cents=None, minutes=minutes,
                    total_labels={},
                    refs=refs, truncated=truncated, issues=issues,
                    issue_counts={item['code']: counts[{'billable_confirmation_pending': 'pending_confirmations',
                                                        'timesheet_unapproved': 'unapproved_timesheets'}[item['code']]]
                                  for item in issues},
                    missing=[])


def _onboarding_section(member, as_of, period):
    # PH-274 preview is deliberately stateless; persistence arrives with PH-289.
    return _Section(key='onboarding', label='导入预览', counts=None, labels={},
                    totals_cents=None, total_labels={}, refs=[], truncated=False,
                    issues=[], issue_counts={},
                    missing=['import_preview_persistence_not_implemented'])


def _cash_section(member, as_of, period):
    if member.role == 'pm':
        return _Section(key='cash', label='现金', counts=None, labels={}, totals_cents=None,
                        total_labels={}, refs=[], truncated=False, issues=[], issue_counts={},
                        missing=['cash_not_scoped_to_project'])
    transactions = CashTransaction.objects.filter(organization=member.organization,
                                                  status='posted')
    transactions = transactions.filter(_in_window('transaction_date', as_of, period))
    posted_in = transactions.filter(direction='in').aggregate(value=Sum('gross_cents'))['value'] or 0
    posted_out = transactions.filter(direction='out').aggregate(value=Sum('gross_cents'))['value'] or 0
    classified = CashClassification.objects.filter(
        organization=member.organization, status='active',
        cash_transaction__in=transactions).values_list('cash_transaction_id', flat=True)
    missing_classification = transactions.exclude(pk__in=list(classified)).count()
    counts = {'posted_transactions': transactions.count(),
              'missing_classification': missing_classification}
    totals = {'posted_in_cents': posted_in, 'posted_out_cents': posted_out,
              'net_cents': posted_in - posted_out}
    refs = [_ref('cash', 'cash_transaction', row) for row in transactions[:REF_LIMIT]]
    refs, truncated = refs, transactions.count() > REF_LIMIT
    issues = []
    if missing_classification:
        issues.append({'code': 'cash_classification_missing',
                       'summary': f'{missing_classification} 笔已过账现金缺少有效分类',
                       'refs': []})
    return _Section(key='cash', label='现金', counts=counts,
                    labels={'posted_transactions': '已过账现金', 'missing_classification': '缺分类现金'},
                    totals_cents=totals,
                    total_labels={'posted_in_cents': '流入', 'posted_out_cents': '流出',
                                  'net_cents': '净额'},
                    refs=refs, truncated=truncated, issues=issues,
                    issue_counts={item['code']: missing_classification for item in issues},
                    missing=[])


def ops_reconciliation_overview(member, *, as_of=None, period=None):
    """Read-only cross-domain overview; no write, audit, receipt or persistence."""
    member = _reader(member)
    as_of_date = _date(as_of)
    period_bounds = _period(period)
    sections = [
        _contracts_section(member, as_of_date, period_bounds),
        _settlement_section(member, as_of_date, period_bounds),
        _revenue_section(member, as_of_date, period_bounds),
        _time_section(member, as_of_date, period_bounds),
        _onboarding_section(member, as_of_date, period_bounds),
        _cash_section(member, as_of_date, period_bounds),
    ]
    for section in sections:
        section['count_rows'] = [{'key': key, 'label': section['labels'].get(key, key),
                                  'value': value}
                                 for key, value in (section['counts'] or {}).items()]
        section['total_rows'] = [{'key': key,
                                  'label': section['total_labels'].get(key, key),
                                  'value': value}
                                 for key, value in (section['totals_cents'] or {}).items()]
        section['minute_rows'] = [{'key': key, 'label': '已确认分钟', 'value': value}
                                  for key, value in (section.get('minutes') or {}).items()]
    issue_count = sum(len(section['issues']) for section in sections)
    return {'as_of': as_of_date.isoformat(),
            'period': period or None,
            'role': member.role,
            'sections': sections,
            'issue_count': issue_count,
            'missing': [code for section in sections for code in section['missing']]}
