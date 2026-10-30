"""PH-288 read-only bank-account cash balance reconciliation.

Derives per-account posted cash in/out, refund/return totals, internal-transfer legs,
classification/ledger cash-book net and opening/closing balances from existing cash/ledger
facts. Strictly read-only: no write, no receipt, no audit, no table/migration and no Offset
fact (PH-241 is still in_review; offset_implemented stays False).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import LedgerPosting
from core.models import AccountingPeriod, Membership
from core.access import require_member

from .models import (BankReturn, CashClassification, CashTransaction, FundingAccount,
                     InternalTransfer, Refund)

READ_ROLES = {'owner', 'finance', 'auditor'}
MAX_PAGE = 200


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以查看银行账户余额对账')
    return fresh


def _day(as_of):
    if as_of is None:
        return timezone.localdate()
    if isinstance(as_of, date):
        return as_of
    try:
        return date.fromisoformat(str(as_of))
    except (TypeError, ValueError):
        raise ValidationError('as_of 须为YYYY-MM-DD日期')


def _month(period):
    try:
        year, month = str(period).split('-')
        start = date(int(year), int(month), 1)
    except (TypeError, ValueError):
        raise ValidationError('period 须为YYYY-MM月份')
    if month == '12':
        nxt = date(start.year + 1, 1, 1)
    else:
        nxt = date(start.year, start.month + 1, 1)
    return start, nxt - timedelta(days=1), nxt


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _issue(issues, code, account, **details):
    entry = {'issue_code': code, 'account_ref': account.account_ref,
             'account_id': str(account.pk), 'account_name': account.name,
             'expected': details.pop('expected', None),
             'actual': details.pop('actual', None)}
    entry.update(details)
    issues.append(entry)


def _derived_cash_ids(organization_id):
    refund_ids = set(Refund.objects.filter(
        organization_id=organization_id).values_list('refund_cash_transaction_id', flat=True))
    return_ids = set(BankReturn.objects.filter(
        organization_id=organization_id).values_list('return_cash_transaction_id', flat=True))
    return refund_ids | return_ids


def _ledger_window(organization_id, account, *, start, day):
    rows = LedgerPosting.objects.filter(
        organization_id=organization_id, scope_kind='funding_account',
        scope_id=str(account.pk), accounting_event__occurred_on__lte=day)
    if start is not None:
        rows = rows.filter(accounting_event__occurred_on__gte=start)
    cash_in = rows.filter(book='cash_in').aggregate(total=Sum('amount_cents'))['total'] or 0
    cash_out = rows.filter(book='cash_out').aggregate(total=Sum('amount_cents'))['total'] or 0
    return cash_in, cash_out


def _in_window(day_value, start, day):
    return day_value <= day and (start is None or day_value >= start)


def _account_bundle(account, *, start, end, day, period, organization_id):
    transactions = list(CashTransaction.objects.filter(
        organization_id=organization_id, funding_account=account, status='posted',
        transaction_date__lte=end).select_related('funding_account'))
    opening_rows = [row for row in transactions
                    if start is not None and row.transaction_date < start]
    window_rows = [row for row in transactions
                   if start is None or start <= row.transaction_date <= end]
    opening = sum(row.gross_cents if row.direction == 'in' else -row.gross_cents
                  for row in opening_rows)
    posted_in = sum(row.gross_cents for row in window_rows if row.direction == 'in')
    posted_out = sum(row.gross_cents for row in window_rows if row.direction == 'out')
    posted_net = posted_in - posted_out
    refund_rows = Refund.objects.filter(
        organization_id=organization_id,
        original_cash_transaction__funding_account=account,
        refund_date__lte=day)
    if start is not None:
        refund_rows = refund_rows.filter(refund_date__gte=start)
    refunds = list(refund_rows)
    return_rows = BankReturn.objects.filter(
        organization_id=organization_id,
        original_cash_transaction__funding_account=account,
        return_date__lte=day)
    if start is not None:
        return_rows = return_rows.filter(return_date__gte=start)
    returns = list(return_rows)
    transfer_rows = InternalTransfer.objects.filter(
        organization_id=organization_id, transfer_date__lte=day).filter(
        Q(out_cash_transaction__funding_account=account)
        | Q(in_cash_transaction__funding_account=account))
    if start is not None:
        transfer_rows = transfer_rows.filter(transfer_date__gte=start)
    transfers = list(transfer_rows.select_related(
        'out_cash_transaction', 'in_cash_transaction').order_by('created_at', 'id'))
    transfer_in = sum(row.gross_cents for row in transfers
                      if row.in_cash_transaction.funding_account_id == account.pk)
    transfer_out = sum(row.gross_cents for row in transfers
                       if row.out_cash_transaction.funding_account_id == account.pk)
    transfer_net = transfer_in - transfer_out
    ledger_in, ledger_out = _ledger_window(
        organization_id, account, start=start, day=day)
    ledger_net = ledger_in - ledger_out
    derived_ids = _derived_cash_ids(organization_id)
    bridge_rows = [row for row in window_rows if row.pk not in derived_ids]
    bridge_in = sum(row.gross_cents for row in bridge_rows if row.direction == 'in')
    bridge_out = sum(row.gross_cents for row in bridge_rows if row.direction == 'out')
    classified_ids = set(CashClassification.objects.filter(
        organization_id=organization_id, status='active',
        cash_transaction_id__in=[row.pk for row in bridge_rows]).values_list(
        'cash_transaction_id', flat=True))
    closing = opening + posted_net
    return {'transactions': transactions, 'window_rows': window_rows,
            'bridge_rows': bridge_rows, 'derived_ids': derived_ids,
            'classified_ids': classified_ids, 'opening': opening,
            'posted_in': posted_in, 'posted_out': posted_out, 'posted_net': posted_net,
            'refunded': sum(row.gross_cents for row in refunds),
            'returned': sum(row.gross_cents for row in returns),
            'refunds': refunds, 'returns': returns, 'transfers': transfers,
            'transfer_in': transfer_in, 'transfer_out': transfer_out,
            'transfer_net': transfer_net, 'ledger_in': ledger_in,
            'ledger_out': ledger_out, 'ledger_net': ledger_net,
            'bridge_in': bridge_in, 'bridge_out': bridge_out,
            'closing': closing}


def _account_issues(account, bundle, *, period, organization_id):
    issues = []
    if not bundle['transactions']:
        _issue(issues, 'account_no_posted_facts', account)
    for row in bundle['bridge_rows']:
        if row.pk not in bundle['classified_ids']:
            _issue(issues, 'unclassified_posted_cash', account,
                   cash_transaction_ref=row.transaction_ref,
                   expected={'classification': 'active'}, actual=None)
    expected_net = bundle['bridge_in'] - bundle['bridge_out']
    if bundle['ledger_net'] != expected_net:
        _issue(issues, 'ledger_net_mismatch', account,
               expected={'ledger_net_cents': expected_net},
               actual={'ledger_net_cents': bundle['ledger_net'],
                       'ledger_in_cents': bundle['ledger_in'],
                       'ledger_out_cents': bundle['ledger_out']})
    for row in bundle['refunds']:
        if row.organization_id != organization_id:
            _issue(issues, 'refund_cross_org', account,
                   refund_ref=row.refund_ref)
        if row.refund_cash_transaction.funding_account_id != account.pk:
            _issue(issues, 'refund_cross_account', account,
                   refund_ref=row.refund_ref,
                   expected={'account_id': str(account.pk)},
                   actual={'account_id': str(row.refund_cash_transaction.funding_account_id)})
    for row in bundle['returns']:
        if row.organization_id != organization_id:
            _issue(issues, 'return_cross_org', account,
                   bank_return_ref=row.bank_return_ref)
        if row.return_cash_transaction.funding_account_id != account.pk:
            _issue(issues, 'return_cross_account', account,
                   bank_return_ref=row.bank_return_ref,
                   expected={'account_id': str(account.pk)},
                   actual={'account_id': str(row.return_cash_transaction.funding_account_id)})
    for row in bundle['transfers']:
        if row.organization_id != organization_id:
            _issue(issues, 'transfer_cross_org', account,
                   internal_transfer_ref=row.internal_transfer_ref)
        out_account = row.out_cash_transaction.funding_account_id
        in_account = row.in_cash_transaction.funding_account_id
        if out_account == in_account:
            _issue(issues, 'transfer_same_account', account,
                   internal_transfer_ref=row.internal_transfer_ref,
                   actual={'account_id': str(out_account)})
        if (row.gross_cents != row.out_cash_transaction.gross_cents
                or row.gross_cents != row.in_cash_transaction.gross_cents):
            _issue(issues, 'transfer_amount_mismatch', account,
                   internal_transfer_ref=row.internal_transfer_ref,
                   expected={'out_cents': row.out_cash_transaction.gross_cents,
                             'in_cents': row.in_cash_transaction.gross_cents},
                   actual={'transfer_cents': row.gross_cents})
    if bundle['closing'] < 0:
        _issue(issues, 'negative_balance', account,
               expected={'min_cents': 0}, actual={'closing_cents': bundle['closing']})
    if period is not None:
        accounting = AccountingPeriod.objects.filter(
            organization_id=organization_id, period=period).first()
        if (accounting is not None and accounting.status == 'posted'
                and accounting.closed_through is not None):
            future = [row for row in bundle['transactions']
                      if row.transaction_date > accounting.closed_through]
            if future:
                _issue(issues, 'future_fact_after_closed_period', account,
                       period=period, expected={'closed_through': accounting.closed_through.isoformat()},
                       actual={'future_count': len(future),
                               'max_date': max(row.transaction_date for row in future).isoformat()})
    return issues


def _account_projection(account, bundle):
    return {
        'account_ref': account.account_ref,
        'account_name': account.name,
        'masked_identifier': account.masked_identifier,
        'status': account.status,
        'opening_balance_cents': bundle['opening'],
        'posted_in_cents': bundle['posted_in'],
        'posted_out_cents': bundle['posted_out'],
        'posted_net_cents': bundle['posted_net'],
        'refunded_cents': bundle['refunded'],
        'returned_cents': bundle['returned'],
        'internal_transfer_in_cents': bundle['transfer_in'],
        'internal_transfer_out_cents': bundle['transfer_out'],
        'internal_transfer_offset_cents': bundle['transfer_net'],
        'internal_transfer_count': len(bundle['transfers']),
        'ledger_cash_in_cents': bundle['ledger_in'],
        'ledger_cash_out_cents': bundle['ledger_out'],
        'ledger_net_cents': bundle['ledger_net'],
        'closing_balance_cents': bundle['closing'],
        'offset_implemented': False,
        'offset_cents': 0,
        'balance_display': _yuan(bundle['closing']),
        'estimate_label': '现金账派生余额（posted 现金事实），非银行对账单余额/可用余额',
    }


def _accounts(member, funding_account_id):
    rows = FundingAccount.objects.filter(
        organization_id=member.organization_id).order_by('name')
    if funding_account_id is not None:
        try:
            account = rows.get(pk=funding_account_id)
        except (FundingAccount.DoesNotExist, ValueError):
            raise PermissionDenied('银行账户不存在或无权访问')
        rows = FundingAccount.objects.filter(pk=account.pk)
    return list(rows[:MAX_PAGE])


def _bounds(as_of, period):
    day = _day(as_of)
    if period is None:
        return None, day, day
    start, end, _nxt = _month(period)
    return start, end, min(end, day)


def bank_account_balance_snapshot(member, *, funding_account_id=None, as_of=None, period=None):
    """Read-only per-account balance/coverage snapshot; no persisted balance table."""
    member = _fresh(member)
    start, end, day = _bounds(as_of, period)
    rows, issues = [], []
    for account in _accounts(member, funding_account_id):
        bundle = _account_bundle(
            account, start=start, end=day, day=day, period=period,
            organization_id=member.organization_id)
        issues.extend(_account_issues(
            account, bundle, period=period, organization_id=member.organization_id))
        rows.append(_account_projection(account, bundle))
    transfer_query = InternalTransfer.objects.filter(
        organization_id=member.organization_id, transfer_date__lte=day)
    if start is not None:
        transfer_query = transfer_query.filter(transfer_date__gte=start)
    return {
        'as_of': day.isoformat(), 'period': period,
        'scope': 'company',
        'offset_implemented': False,
        'summary': {
            'account_count': len(rows),
            'posted_in_cents': sum(row['posted_in_cents'] for row in rows),
            'posted_out_cents': sum(row['posted_out_cents'] for row in rows),
            'posted_net_cents': sum(row['posted_net_cents'] for row in rows),
            'refunded_cents': sum(row['refunded_cents'] for row in rows),
            'returned_cents': sum(row['returned_cents'] for row in rows),
            'internal_transfer_offset_cents': sum(
                row['internal_transfer_offset_cents'] for row in rows),
            'internal_transfer_count': transfer_query.count(),
            'ledger_net_cents': sum(row['ledger_net_cents'] for row in rows),
            'closing_balance_cents': sum(row['closing_balance_cents'] for row in rows),
            'issue_count': len(issues),
        },
        'accounts': rows,
        'issues': issues,
        'estimate_label': '只读银行账户现金账派生汇总；非银行对账单余额/可用余额',
    }


def bank_account_issues(member, *, as_of=None, period=None):
    """Read-only issue list derived from the same account-balance rules."""
    snapshot = bank_account_balance_snapshot(member, as_of=as_of, period=period)
    return {'as_of': snapshot['as_of'], 'period': snapshot['period'],
            'scope': snapshot['scope'],
            'offset_implemented': False,
            'issue_count': len(snapshot['issues']),
            'issues': snapshot['issues'],
            'summary': snapshot['summary'],
            'estimate_label': snapshot['estimate_label']}
