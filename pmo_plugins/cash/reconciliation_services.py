"""PH-279 read-only cash reconciliation snapshot: classifications -> bridge events ->
postings/claim, refunds/returns, internal-transfer pairs and cash-book balances.

This slice is strictly read-only: it derives projections from existing owner facts and
services, writes no cash/ledger/audit fact, adds no table/migration and never invents an
offset fact (PH-241 is still in_review, so offset_implemented stays False).
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import AccountingEvent, LedgerPosting, SourceConsumption

from . import classification_services
from .models import (BankReturn, CashClassification, CashTransaction, FundingAccount,
                     InternalTransfer, Refund)

READ_ROLES = {'owner', 'finance', 'auditor'}
CLAIM_CONSUMER = 'cash'
CLAIM_COVERAGE_KEY = 'bank_effect'
BOOKS = {'in': 'cash_in', 'out': 'cash_out'}
MAX_PAGE = 500


def _fresh(member):
    from core.access import require_member
    from core.models import Membership
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以查看现金核对快照')
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


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _ref(value, kind):
    return {'namespace': 'core', 'kind': kind, 'id': str(value.pk),
            'version': getattr(value, 'version', 1)}


def _issue(issues, code, transaction, **details):
    item = {'issue_code': code, 'transaction_ref': transaction.transaction_ref,
            'transaction_id': str(transaction.pk),
            'account_ref': transaction.funding_account.account_ref,
            'expected': details.pop('expected', None),
            'actual': details.pop('actual', None)}
    item.update(details)
    issues.append(item)


def _classification_bundle(transaction):
    rows = list(CashClassification.objects.filter(
        organization_id=transaction.organization_id,
        cash_transaction_id=transaction.pk).order_by('created_at', 'id'))
    active = [row for row in rows if row.status == 'active']
    latest = rows[-1] if rows else None
    reversal = None
    if latest is not None:
        reversal = latest.reversals.order_by('created_at', 'id').first()
    classification = active[0] if len(active) == 1 else None
    events = []
    if classification is not None:
        events = list(AccountingEvent.objects.filter(
            organization_id=transaction.organization_id,
            source=classification.classification_ref,
            event_type='cash.classification').order_by('created_at', 'id'))
    reversal_event = None
    if reversal is not None:
        reversal_event = AccountingEvent.objects.filter(
            organization_id=transaction.organization_id,
            source=reversal.reversal_ref,
            event_type='cash.classification.reversed').first()
    return {'rows': rows, 'active': active, 'classification': classification,
            'reversal': reversal, 'events': events, 'reversal_event': reversal_event}


def _posting_rows(event):
    if event is None:
        return []
    return list(event.postings.order_by('line_index', 'created_at'))


def _claim_net(claim):
    if claim is None:
        return 0
    return claim.events.aggregate(total=Sum('claim_effect_cents'))['total'] or 0


def _transfer_rows(transaction):
    return list(InternalTransfer.objects.filter(
        Q(out_cash_transaction_id=transaction.pk)
        | Q(in_cash_transaction_id=transaction.pk)).select_related(
        'out_cash_transaction', 'in_cash_transaction').order_by('created_at', 'id'))


def _refund_rows(transaction):
    refunds = list(Refund.objects.filter(
        organization_id=transaction.organization_id,
        original_cash_transaction_id=transaction.pk).order_by('refund_date', 'created_at'))
    returns = list(BankReturn.objects.filter(
        organization_id=transaction.organization_id,
        original_cash_transaction_id=transaction.pk).order_by('return_date', 'created_at'))
    return refunds, returns


def _derived_cash(transaction):
    """Refund/return reverse cash facts are covered by the refund/return fact itself."""
    return (Refund.objects.filter(refund_cash_transaction_id=transaction.pk).exists()
            or BankReturn.objects.filter(return_cash_transaction_id=transaction.pk).exists())


def _transaction_issues(transaction, bundle, postings, refunds, returns, transfers):
    issues = []
    if _derived_cash(transaction):
        return issues
    classification = bundle['classification']
    events = bundle['events']
    if classification is None:
        if bundle['reversal'] is None:
            _issue(issues, 'classification_missing', transaction)
        elif bundle['reversal_event'] is None:
            _issue(issues, 'reversal_event_missing', transaction,
                   classification_ref=bundle['reversal'].classification_ref)
    else:
        # The single-active unique constraint makes >1 active rows unreachable through the
        # owner services; keep the branch as defence in depth for raw/legacy rows.
        if len(bundle['active']) > 1:
            _issue(issues, 'classification_duplicate_active', transaction,
                   actual=len(bundle['active']), expected=1)
        if not events:
            _issue(issues, 'event_missing', transaction,
                   classification_ref=classification.classification_ref)
        elif len(events) > 1:
            _issue(issues, 'event_duplicate', transaction, actual=len(events), expected=1)
        event = events[0] if events else None
        if event is not None:
            if event.organization_id != transaction.organization_id:
                _issue(issues, 'event_cross_org', transaction)
            if len(postings) != 1:
                _issue(issues, 'posting_count', transaction,
                       actual=len(postings), expected=1)
            for posting in postings:
                if posting.organization_id != transaction.organization_id:
                    _issue(issues, 'posting_cross_org', transaction,
                           posting_ref=_ref(posting, 'ledger_posting'))
                if (posting.amount_cents != transaction.gross_cents
                        or posting.book != BOOKS[transaction.direction]):
                    _issue(issues, 'posting_amount_mismatch', transaction,
                           expected={'amount_cents': transaction.gross_cents,
                                     'book': BOOKS[transaction.direction]},
                           actual={'amount_cents': posting.amount_cents, 'book': posting.book})
                if (posting.scope_kind != 'funding_account'
                        or posting.scope_id != str(transaction.funding_account_id)):
                    _issue(issues, 'posting_scope_mismatch', transaction,
                           expected={'scope_kind': 'funding_account',
                                     'scope_id': str(transaction.funding_account_id)},
                           actual={'scope_kind': posting.scope_kind,
                                   'scope_id': posting.scope_id})
            claim = event.claim
            if claim is None or claim.consumer != CLAIM_CONSUMER \
                    or claim.coverage_key != CLAIM_COVERAGE_KEY \
                    or claim.source_id != transaction.pk \
                    or claim.amount_cents != transaction.gross_cents:
                _issue(issues, 'claim_missing_or_mismatch', transaction,
                       expected={'consumer': CLAIM_CONSUMER,
                                 'coverage_key': CLAIM_COVERAGE_KEY,
                                 'amount_cents': transaction.gross_cents},
                       actual=None if claim is None else {
                           'consumer': claim.consumer, 'coverage_key': claim.coverage_key,
                           'amount_cents': claim.amount_cents})
            expected_net = 0 if classification.status == 'reversed' else transaction.gross_cents
            net = _claim_net(claim)
            if net != expected_net:
                _issue(issues, 'claim_net_mismatch', transaction,
                       expected=expected_net, actual=net)
    refund_total = sum(row.gross_cents for row in refunds) + sum(
        row.gross_cents for row in returns)
    if refund_total > transaction.gross_cents:
        _issue(issues, 'refund_over_gross', transaction,
               expected={'max_cents': transaction.gross_cents}, actual=refund_total)
    for transfer in transfers:
        if transfer.organization_id != transaction.organization_id:
            _issue(issues, 'transfer_cross_org', transaction,
                   transfer_ref=transfer.internal_transfer_ref)
        out_row = transfer.out_cash_transaction
        in_row = transfer.in_cash_transaction
        if (out_row.status != 'posted' or in_row.status != 'posted'
                or out_row.organization_id != in_row.organization_id):
            _issue(issues, 'transfer_leg_missing', transaction,
                   transfer_ref=transfer.internal_transfer_ref)
        if (transfer.gross_cents != out_row.gross_cents
                or out_row.gross_cents != in_row.gross_cents
                or transfer.out_transaction_date != out_row.transaction_date
                or transfer.in_transaction_date != in_row.transaction_date):
            _issue(issues, 'transfer_amount_or_date_mismatch', transaction,
                   transfer_ref=transfer.internal_transfer_ref,
                   expected={'gross_cents': out_row.gross_cents,
                             'out_date': out_row.transaction_date.isoformat(),
                             'in_date': in_row.transaction_date.isoformat()},
                   actual={'gross_cents': transfer.gross_cents,
                           'out_date': transfer.out_transaction_date.isoformat(),
                           'in_date': transfer.in_transaction_date.isoformat()})
    return issues


def _transaction_projection(transaction, bundle, postings, refunds, returns, transfers):
    classification = bundle['classification']
    event = bundle['events'][0] if bundle['events'] else None
    claim = event.claim if event is not None else None
    return {
        'transaction_ref': transaction.transaction_ref,
        'transaction_date': transaction.transaction_date.isoformat(),
        'direction': transaction.direction,
        'direction_display': {'in': '收入', 'out': '支出'}.get(
            transaction.direction, transaction.direction),
        'gross_cents': transaction.gross_cents, 'gross_display': _yuan(transaction.gross_cents),
        'account_ref': transaction.funding_account.account_ref,
        'account_name': transaction.funding_account.name,
        'classification': None if classification is None else {
            'ref': classification.classification_ref,
            'classification': classification.classification,
            'classification_display': classification.get_classification_display(),
            'status': classification.status, 'version': classification.version},
        'reversed_classification': None if bundle['reversal'] is None else {
            'ref': bundle['reversal'].classification_ref,
            'reversal_ref': bundle['reversal'].reversal_ref},
        'accounting_event': None if event is None else {
            'ref': _ref(event, 'accounting_event'),
            'event_type': event.event_type,
            'amount_cents': event.claim_effect_cents,
            'occurred_on': event.occurred_on.isoformat(),
            'posting_period': event.posting_period},
        'postings': [{'ref': _ref(posting, 'ledger_posting'),
                      'book': posting.book, 'amount_cents': posting.amount_cents,
                      'scope_kind': posting.scope_kind, 'scope_id': posting.scope_id}
                     for posting in postings],
        'claim': None if claim is None else {
            'coverage_key': claim.coverage_key, 'amount_cents': claim.amount_cents,
            'consumer': claim.consumer, 'net_effect_cents': _claim_net(claim)},
        'refunds': {'count': len(refunds),
                    'total_cents': sum(row.gross_cents for row in refunds),
                    'refs': [row.refund_ref for row in refunds]},
        'bank_returns': {'count': len(returns),
                         'total_cents': sum(row.gross_cents for row in returns),
                         'refs': [row.bank_return_ref for row in returns]},
        'internal_transfer': None if not transfers else {
            'ref': transfers[0].internal_transfer_ref,
            'status': transfers[0].status,
            'out_ref': transfers[0].out_cash_ref,
            'in_ref': transfers[0].in_cash_ref},
        'derived_cash': ('refund' if Refund.objects.filter(
            refund_cash_transaction_id=transaction.pk).exists() else
            ('bank_return' if BankReturn.objects.filter(
                return_cash_transaction_id=transaction.pk).exists() else None)),
        'offset': {'implemented': False, 'note': 'PH-241 Offset 未合入，本片仅占位'},
    }


def _transactions(member, as_of, funding_account_id=None):
    day = _day(as_of)
    rows = CashTransaction.objects.filter(
        organization_id=member.organization_id, status='posted',
        transaction_date__lte=day).select_related('funding_account')
    if funding_account_id is not None:
        try:
            account = FundingAccount.objects.get(
                organization_id=member.organization_id, pk=funding_account_id)
        except (FundingAccount.DoesNotExist, ValueError):
            raise PermissionDenied('银行账户不存在或无权访问')
        rows = rows.filter(funding_account=account)
    return day, list(rows.order_by('transaction_date', 'created_at', 'pk')[:MAX_PAGE])


def cash_bridge_snapshot(member, *, as_of=None, funding_account_id=None):
    """Read-only per-cash-fact bridge snapshot plus cash-book account balances."""
    member = _fresh(member)
    day, transactions = _transactions(member, as_of, funding_account_id)
    items, issues = [], []
    for transaction in transactions:
        bundle = _classification_bundle(transaction)
        postings = _posting_rows(bundle['events'][0] if bundle['events'] else None)
        refunds, returns = _refund_rows(transaction)
        transfers = _transfer_rows(transaction)
        issues.extend(_transaction_issues(
            transaction, bundle, postings, refunds, returns, transfers))
        items.append(_transaction_projection(
            transaction, bundle, postings, refunds, returns, transfers))
    accounts = []
    account_rows = FundingAccount.objects.filter(
        organization_id=member.organization_id).order_by('name')
    for account in account_rows:
        coverage = classification_services.cash_book_coverage(
            member, account.pk, as_of=day)
        accounts.append(coverage)
    classified = sum(1 for item in items if item['classification'] is not None)
    return {
        'as_of': day.isoformat(),
        'scope': 'company',
        'offset_implemented': False,
        'summary': {
            'posted_cash_count': len(items),
            'classified_count': classified,
            'unclassified_count': len(items) - classified,
            'refund_count': sum(item['refunds']['count'] for item in items),
            'refund_total_cents': sum(item['refunds']['total_cents'] for item in items),
            'bank_return_count': sum(item['bank_returns']['count'] for item in items),
            'bank_return_total_cents': sum(item['bank_returns']['total_cents'] for item in items),
            'internal_transfer_count': sum(
                1 for item in items if item['internal_transfer'] is not None),
            'issue_count': len(issues),
            'account_count': len(accounts),
        },
        'transactions': items,
        'accounts': accounts,
        'issues': issues,
        'estimate_label': '只读核对快照，非银行余额/可用余额',
    }


def cash_bridge_issues(member, *, as_of=None, funding_account_id=None):
    """Read-only issue list derived from the same bridge rules; no writes."""
    snapshot = cash_bridge_snapshot(
        member, as_of=as_of, funding_account_id=funding_account_id)
    return {'as_of': snapshot['as_of'], 'scope': snapshot['scope'],
            'offset_implemented': False,
            'issue_count': len(snapshot['issues']),
            'issues': snapshot['issues'],
            'summary': snapshot['summary'],
            'estimate_label': snapshot['estimate_label']}
