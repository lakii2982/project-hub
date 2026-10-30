"""PH-280 read-only BankCharge approval/cost/net-fee pairing projections.

No write path, migration, canonical topic or business fact is created here. The
service composes the existing costing, cash and settlement named reads into a
pairing projection plus an issue list for reconciliation.
"""
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import visible_projects
from core.models import AccountingEvent, LedgerPosting, Membership, SourceConsumption
from core.services import require_member
from pmo_plugins.cash.refund_services import refund_coverage_for_cash
from pmo_plugins.cash.models import CashTransaction
from pmo_plugins.cash.transaction_services import cash_transaction_snapshot

from . import bank_charge_services
from .models import BankCharge

READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
ISSUE_CODES = {
    'approved_without_cost', 'cost_event_duplicate', 'cost_posting_duplicate',
    'cost_posting_amount_mismatch', 'cost_claim_amount_mismatch', 'cost_claim_duplicate',
    'fee_exceeds_cash_gross', 'cash_not_posted_in', 'cross_org_reference',
    'cross_project_reference', 'unapproved_cost_posted', 'coverage_unavailable',
}


def _fail(message):
    raise ValidationError(message)


def _reader(member):
    """history reads stay available when the costing module is disabled; pm is scoped."""
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权查看手续费成本配对')
    return member


def _date(value):
    if value in (None, ''):
        return None
    if type(value) is date:
        return value
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('配对日期须为有效的年月日')
    if parsed > timezone.localdate():
        raise ValidationError('配对日期不能在未来')
    return parsed


def _visible_charges(member):
    rows = BankCharge.objects.select_related(
        'created_by', 'submitted_by', 'approved_by').filter(organization=member.organization)
    if member.role == 'pm':
        projects = {str(pk) for pk in visible_projects(member).values_list('pk', flat=True)}
        rows = rows.filter(scope_kind='project', scope_id__in=projects)
    return rows


def _ref(kind, row, version=None):
    return {'namespace': 'costing' if kind.startswith('bank_charge') else 'core',
            'kind': kind, 'id': str(row.pk), 'version': version or getattr(row, 'version', 1)}


def _charge_ref(charge):
    return {'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk),
            'version': charge.version}


def _cash_snapshot(member, charge):
    try:
        return cash_transaction_snapshot(member, charge.cash_transaction_id)
    except (PermissionDenied, ValidationError):
        return None


def _coverage(member, charge):
    try:
        return refund_coverage_for_cash(member, charge.cash_transaction_ref)
    except (PermissionDenied, ValidationError):
        return None


def _cost_rows(charge):
    claims = list(SourceConsumption.objects.filter(
        organization_id=charge.organization_id, source_id=charge.pk,
        coverage_key=f'bank_charge:{charge.pk}').order_by('created_at', 'id'))
    events = list(AccountingEvent.objects.filter(
        organization_id=charge.organization_id, claim__in=claims).order_by('created_at', 'id'))
    postings = list(LedgerPosting.objects.filter(
        organization_id=charge.organization_id,
        accounting_event__in=events).order_by('created_at', 'id'))
    return claims, events, postings


def _charge_issues(member, charge, cash, claims, events, postings, coverage):
    issues = []
    if charge.status == 'approved' and (not claims or not events or not postings):
        issues.append({'issue_code': 'approved_without_cost',
                       'refs': [{'charge_ref': _charge_ref(charge)}],
                       'expected': 'exactly one cost claim/event/posting',
                       'actual': {'claims': len(claims), 'events': len(events),
                                  'postings': len(postings)}})
    if len(claims) > 1:
        issues.append({'issue_code': 'cost_claim_duplicate',
                       'refs': [{'charge_ref': _charge_ref(charge)}],
                       'expected': 1, 'actual': len(claims)})
    if len(events) > 1:
        issues.append({'issue_code': 'cost_event_duplicate',
                       'refs': [{'charge_ref': _charge_ref(charge)}],
                       'expected': 1, 'actual': len(events)})
    if len(postings) > 1:
        issues.append({'issue_code': 'cost_posting_duplicate',
                       'refs': [{'charge_ref': _charge_ref(charge)}],
                       'expected': 1, 'actual': len(postings)})
    for claim in claims:
        if claim.amount_cents != charge.gross_cents:
            issues.append({'issue_code': 'cost_claim_amount_mismatch',
                           'refs': [{'charge_ref': _charge_ref(charge),
                                     'claim_ref': _ref('source_consumption', claim)}],
                           'expected': charge.gross_cents, 'actual': claim.amount_cents})
    for posting in postings:
        if posting.amount_cents != charge.gross_cents:
            issues.append({'issue_code': 'cost_posting_amount_mismatch',
                           'refs': [{'charge_ref': _charge_ref(charge),
                                     'posting_ref': _ref('ledger_posting', posting)}],
                           'expected': charge.gross_cents, 'actual': posting.amount_cents})
    if cash is None:
        issues.append({'issue_code': 'cash_not_posted_in',
                       'refs': [{'charge_ref': _charge_ref(charge)}],
                       'expected': 'readable posted incoming cash', 'actual': 'unavailable'})
    else:
        if cash['status'] != 'posted' or cash['direction'] != 'in':
            issues.append({'issue_code': 'cash_not_posted_in',
                           'refs': [{'charge_ref': _charge_ref(charge),
                                     'cash_ref': charge.cash_transaction_ref}],
                           'expected': 'posted/in', 'actual': f"{cash['status']}/{cash['direction']}"})
        if charge.gross_cents > cash['gross_cents']:
            issues.append({'issue_code': 'fee_exceeds_cash_gross',
                           'refs': [{'charge_ref': _charge_ref(charge),
                                     'cash_ref': charge.cash_transaction_ref}],
                           'expected': f'<= {cash["gross_cents"]}',
                           'actual': charge.gross_cents})
        if cash['organization_id'] != str(charge.organization_id):
            issues.append({'issue_code': 'cross_org_reference',
                           'refs': [{'charge_ref': _charge_ref(charge)}],
                           'expected': str(charge.organization_id),
                           'actual': cash['organization_id']})
    cash_org = CashTransaction.objects.filter(pk=charge.cash_transaction_id).values_list(
        'organization_id', flat=True).first()
    if cash_org is not None and cash_org != charge.organization_id:
        issues.append({'issue_code': 'cross_org_reference',
                       'refs': [{'charge_ref': _charge_ref(charge),
                                 'cash_ref': charge.cash_transaction_ref}],
                       'expected': str(charge.organization_id), 'actual': str(cash_org)})
    coverage_refs = [{'charge_ref': _charge_ref(charge)}]
    if coverage is None:
        issues.append({'issue_code': 'coverage_unavailable', 'refs': coverage_refs,
                       'expected': 'refund/return coverage read', 'actual': 'unavailable'})
    if charge.status != 'approved':
        if claims or events or postings:
            issues.append({'issue_code': 'unapproved_cost_posted', 'refs': coverage_refs,
                           'expected': 'no cost before approval',
                           'actual': {'claims': len(claims), 'events': len(events),
                                      'postings': len(postings)}})
    for posting in postings:
        if posting.scope_id != charge.scope_id:
            issues.append({'issue_code': 'cross_project_reference',
                           'refs': [{'charge_ref': _charge_ref(charge),
                                     'posting_ref': _ref('ledger_posting', posting)}],
                           'expected': charge.scope_id, 'actual': posting.scope_id})
    return issues


def _pairing_status(charge, cash, claims, events, postings, issues, coverage):
    codes = {issue['issue_code'] for issue in issues}
    if charge.status != 'approved':
        return 'draft'
    if 'approved_without_cost' in codes:
        return 'approved_unposted'
    if codes & {'cost_event_duplicate', 'cost_posting_duplicate', 'cost_claim_duplicate',
                'cash_not_posted_in', 'unapproved_cost_posted'}:
        return '异常'
    amount_ok = (len(claims) == 1 and len(events) == 1 and len(postings) == 1
                 and claims[0].amount_cents == charge.gross_cents
                 and postings[0].amount_cents == charge.gross_cents)
    if not amount_ok or 'fee_exceeds_cash_gross' in codes:
        return 'partial'
    if coverage is not None and coverage['total_cents'] > 0:
        return 'paired'
    return 'posted'


def _projection(member, charge, as_of=None):
    cash = _cash_snapshot(member, charge)
    coverage = _coverage(member, charge)
    claims, events, postings = _cost_rows(charge)
    issues = _charge_issues(member, charge, cash, claims, events, postings, coverage)
    approved_fee_cents = (charge.gross_cents if charge.status == 'approved' else 0)
    posted_net = cash['gross_cents'] if cash else None
    return {
        'charge_ref': _charge_ref(charge),
        'status': charge.status, 'status_display': charge.get_status_display(),
        'version': charge.version,
        'cash_transaction_ref': charge.cash_transaction_ref,
        'cash_status': cash['status'] if cash else None,
        'cash_direction': cash['direction'] if cash else None,
        'posted_net_cents': posted_net,
        'fee_gross_cents': charge.gross_cents, 'fee_tax_cents': charge.tax_cents,
        'tax_split_implemented': False,
        'submitted_by': str(charge.submitted_by_id) if charge.submitted_by_id else None,
        'submitted_at': charge.submitted_at.isoformat() if charge.submitted_at else None,
        'approved_by': str(charge.approved_by_id) if charge.approved_by_id else None,
        'approved_at': charge.approved_at.isoformat() if charge.approved_at else None,
        'cost': {
            'claim_count': len(claims),
            'claim_refs': [{'coverage_key': claim.coverage_key,
                            'claim_ref': _ref('source_consumption', claim),
                            'amount_cents': claim.amount_cents} for claim in claims],
            'event_count': len(events),
            'event_refs': [{'event_ref': _ref('accounting_event', event),
                            'claim_effect_cents': event.claim_effect_cents,
                            'event_type': event.event_type} for event in events],
            'posting_count': len(postings),
            'posting_refs': [{'posting_ref': _ref('ledger_posting', posting),
                              'amount_cents': posting.amount_cents,
                              'book': posting.book} for posting in postings],
            'cost_amount_cents': postings[0].amount_cents if len(postings) == 1 else None,
        },
        'net_fee': {
            'posted_net_cents': posted_net,
            'approved_fee_cents': approved_fee_cents,
            'implied_gross_cents': (posted_net + approved_fee_cents
                                    if posted_net is not None else None),
        },
        'refund_return': {
            'refunded_cents': coverage['refunded_cents'] if coverage else None,
            'returned_cents': coverage['returned_cents'] if coverage else None,
            'total_cents': coverage['total_cents'] if coverage else None,
            'offset_implemented': False,
        },
        'ph251': {'receipt_fee_settlement': 'not_implemented',
                  'existing_cost_ref_reuse': 'not_implemented',
                  'counterparty_borne': 'not_implemented',
                  'tax_deduction_split': 'not_implemented'},
        'issues': issues,
        'issue_codes': [issue['issue_code'] for issue in issues],
        'pairing_status': _pairing_status(charge, cash, claims, events, postings, issues,
                                          coverage),
        'as_of': as_of.isoformat() if as_of else None,
    }


def bank_charge_pairing(member, *, status=None, cash_transaction_id=None, as_of=None):
    """Read-only pairing list for visible charges; filters are read-only."""
    member = _reader(member)
    when = _date(as_of)
    rows = _visible_charges(member)
    if status is not None:
        if not isinstance(status, str) or status not in {
                'draft', 'pending', 'approved', 'rejected', 'superseded'}:
            _fail('手续费状态筛选无效')
        rows = rows.filter(status=status)
    if cash_transaction_id is not None:
        try:
            from uuid import UUID
            transaction_id = UUID(str(cash_transaction_id))
        except (TypeError, ValueError):
            _fail('现金流水标识无效')
        rows = rows.filter(cash_transaction_id=transaction_id)
    if when is not None:
        rows = rows.filter(occurred_on__lte=when)
    return [_projection(member, row, when) for row in rows.order_by('-created_at', 'id')]


def bank_charge_pairing_issues(member, *, as_of=None):
    """Read-only issue list over visible charges; no writes or audit."""
    member = _reader(member)
    issues = []
    for row in bank_charge_pairing(member, as_of=as_of):
        for issue in row['issues']:
            issues.append({'issue_code': issue['issue_code'],
                           'charge_ref': row['charge_ref'],
                           'refs': issue['refs'],
                           'expected': issue['expected'], 'actual': issue['actual']})
    return issues
