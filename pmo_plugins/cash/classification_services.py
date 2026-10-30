"""Private cash classification services: posted bank facts become cash book entries (PH-239).

Each effective classification is append-only and writes exactly one canonical
cash.classification_recorded.v1 event plus (through the core-owned bridge) one
AccountingEvent/LedgerPosting pair. Reversal appends CashClassificationReversal plus a
mirrored accounting event/posting; the original rows are never edited or deleted, and the
permanent bank_effect SourceConsumption is re-used only after its events net to zero.
"""
from decimal import Decimal
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from core.models import AccountingEvent, DomainEvent, LedgerPosting
from core.services import audit

from .funding_services import (_business_date, _dry, _evidence, _fail, _fresh, _identifier,
                               _locked, _record, _receipt, _receipt_row, _text, _version)
from .models import (CLASSIFICATION_KINDS, CashClassification, CashClassificationReversal,
                     CashTransaction, FundingAccount)
from .transaction_services import _period, _posting_period

RECORDED_TOPIC = 'cash.classification_recorded.v1'
REVERSED_TOPIC = 'cash.classification_reversed.v1'
RECORDED_ACTION = 'cash.classification.recorded'
REVERSED_ACTION = 'cash.classification.reversed'
CLAIM_CONSUMER = 'cash'
CLAIM_COVERAGE_KEY = 'bank_effect'
BOOKS = {'in': 'cash_in', 'out': 'cash_out'}
DIRECTION_RULES = {'receipt': 'in', 'advance': 'in', 'payment': 'out', 'tax': 'out'}
MAX_CENTS = 10 ** 12


# ---------- guards ----------
def _classification_kind(value):
    if not isinstance(value, str) or not value:
        _fail('现金分类无效')
    if value == 'payroll':
        _fail('工资分类本片未支持；工资现金链接与成本账留后续薄片')
    if value not in CLASSIFICATION_KINDS:
        _fail('不支持的现金分类；本片仅支持收款、付款、预收/预付与税费')
    return value


def _supporting_evidence(value):
    return _evidence(value, required=True)


def _effective_counterparty(transaction):
    """transaction.counterparty_id or the latest unreversed identify match; else None."""
    if transaction.counterparty_id:
        return str(transaction.counterparty_id)
    reversed_ids = set(transaction.matches.filter(action='reverse').values_list(
        'original_match_id', flat=True))
    for match in transaction.matches.filter(action='identify').order_by('-created_at', '-id'):
        if match.pk in reversed_ids:
            continue
        payee = match.identified_payee or {}
        if payee.get('kind') == 'counterparty' and payee.get('id'):
            return str(payee['id'])
        return None
    return None


def _get_transaction(member, transaction_id, *, lock=False):
    rows = CashTransaction.objects.select_related('funding_account')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        return rows.get(organization=member.organization,
                        pk=_identifier(transaction_id, '银行流水不存在或无权访问'))
    except CashTransaction.DoesNotExist:
        raise PermissionDenied('银行流水不存在或无权访问')


def _get_classification(member, classification_id, *, lock=False):
    rows = CashClassification.objects.select_related('cash_transaction__funding_account')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        return rows.get(organization=member.organization,
                        pk=_identifier(classification_id, '现金分类不存在或无权访问'))
    except CashClassification.DoesNotExist:
        raise PermissionDenied('现金分类不存在或无权访问')


def _event_ref(event):
    return {'namespace': 'core', 'kind': 'accounting_event', 'id': str(event.pk),
            'version': event.version}


def _event_for(classification):
    return AccountingEvent.objects.filter(organization_id=classification.organization_id,
                                          source=classification.classification_ref,
                                          event_type='cash.classification').first()


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


# ---------- projections ----------
def _classification_projection(row):
    transaction = row.cash_transaction
    event = _event_for(row)
    reversal = row.reversals.order_by('created_at', 'id').first()
    return {
        'id': str(row.pk), 'classification_ref': row.classification_ref,
        'organization_id': str(row.organization_id),
        'classification': row.classification,
        'classification_display': row.get_classification_display(),
        'status': row.status, 'status_display': row.get_status_display(),
        'version': row.version, 'evidence': row.evidence,
        'cash_transaction_id': str(transaction.pk),
        'cash_transaction_ref': transaction.transaction_ref,
        'transaction_date': transaction.transaction_date.isoformat(),
        'direction': transaction.direction,
        'direction_display': {'in': '收入', 'out': '支出'}.get(transaction.direction,
                                                              transaction.direction),
        'gross_cents': transaction.gross_cents, 'gross_display': _yuan(transaction.gross_cents),
        'funding_account_id': str(transaction.funding_account_id),
        'funding_account_name': transaction.funding_account.name,
        'book': BOOKS[transaction.direction],
        'posting_event_ref': _event_ref(event) if event else None,
        'reversal_ref': reversal.reversal_ref if reversal else None,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
    }


# ---------- writes ----------
@transaction.atomic
def record_classification(member, *, cash_transaction_id, classification, evidence,
                          occurred_on=None, posting_period=None, request_token, dry_run=True):
    """Record the single effective classification of a posted bank fact and bridge cash book."""
    _dry(dry_run)
    member = _locked(member)
    transaction_id = _identifier(cash_transaction_id, '银行流水不存在或无权访问')
    kind = _classification_kind(classification)
    occurred = None if occurred_on is None else _business_date(occurred_on)
    period = None if posting_period is None else _posting_period(posting_period)
    evidence_refs = _supporting_evidence(evidence)
    payload = {'cash_transaction_id': str(transaction_id), 'classification': kind,
               'evidence': evidence_refs,
               'occurred_on': occurred.isoformat() if occurred else None,
               'posting_period': period}
    token, digest, receipt = _receipt(member, 'cash_classification.record', request_token,
                                      payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, CashClassification)
    row = _get_transaction(member, transaction_id, lock=True)
    if row.organization_id != member.organization_id:
        raise PermissionDenied('银行流水不属于本公司')
    if row.status != 'posted':
        _fail('仅已过账银行流水可以分类')
    if DIRECTION_RULES[kind] != row.direction:
        _fail('现金分类与银行流水方向不符')
    if occurred is not None and occurred != row.transaction_date:
        _fail('分类日期必须与银行流水日期一致')
    if period is not None and period != row.transaction_date.strftime('%Y-%m'):
        _fail('过账期间必须与银行流水日期所属月份一致')
    _period(member, row.transaction_date)
    if kind == 'advance' and not _effective_counterparty(row):
        _fail('预收/预付分类要求存在有效往来方')
    if CashClassification.objects.filter(organization=member.organization,
                                         cash_transaction=row, status='active').exists():
        _fail('该银行流水已有有效分类，请先撤分类')
    row_id = row.pk
    classification_row = CashClassification(
        organization=member.organization, cash_transaction=row, classification=kind,
        status='active', version=1, evidence=evidence_refs, created_by=member)
    if dry_run:
        classification_row.cash_transaction = row
        return classification_row
    try:
        with transaction.atomic():
            classification_row.save()
    except IntegrityError:
        _fail('该银行流水已有有效分类，请先撤分类')
    from core import cash_accounting
    event, _posting = cash_accounting.post_cash_classification(member, classification_row)
    event_ref = _event_ref(event)
    audit(member, RECORDED_ACTION, classification_row,
          {'classification_ref': classification_row.classification_ref,
           'cash_transaction_ref': row.transaction_ref, 'classification': kind,
           'posting_event_ref': event_ref})
    event_source = audit(member, 'plugin.cash.classification.recorded', classification_row,
                         {'classification_ref': classification_row.classification_ref,
                          'posting_event_ref': event_ref})
    canonical_id = uuid.uuid4()
    ref = classification_row.classification_ref
    DomainEvent.objects.create(
        event_id=canonical_id, organization=member.organization, source=event_source,
        topic=RECORDED_TOPIC,
        data={'event_id': str(canonical_id), 'topic': RECORDED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref],
                          'posting_event_refs': [event_ref]}})
    _record(member, 'cash_classification.record', token, digest, ref)
    classification_row.cash_transaction = row
    return classification_row


@transaction.atomic
def reverse_classification(member, *, classification_id, reason, evidence, occurred_on=None,
                           posting_period=None, request_token, expected_version, dry_run=True):
    """Append the single full reversal of an effective classification; originals stay intact."""
    _dry(dry_run)
    member = _locked(member)
    classification_id = _identifier(classification_id, '现金分类不存在或无权访问')
    version = _version(expected_version)
    reason_value = _text(reason, 1000, '请填写1至1000字的撤分类原因')
    evidence_refs = _supporting_evidence(evidence)
    occurred = None if occurred_on is None else _business_date(occurred_on)
    period = None if posting_period is None else _posting_period(posting_period)
    reversal_day = occurred or timezone.localdate()
    reversal_period = period or reversal_day.strftime('%Y-%m')
    if reversal_period != reversal_day.strftime('%Y-%m'):
        _fail('撤分类期间必须包含撤分类日期')
    payload = {'classification_id': str(classification_id), 'reason': reason_value,
               'evidence': evidence_refs, 'expected_version': version,
               'occurred_on': reversal_day.isoformat(), 'posting_period': reversal_period}
    token, digest, receipt = _receipt(member, 'cash_classification.reverse', request_token,
                                      payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, CashClassificationReversal)
    _period(member, reversal_day)
    row = _get_classification(member, classification_id, lock=True)
    if row.organization_id != member.organization_id:
        raise PermissionDenied('现金分类不属于本公司')
    if row.version != version:
        _fail('现金分类版本已变化，请刷新后重试')
    if row.status != 'active':
        _fail('仅有效现金分类可以撤分类；已撤分类不可重复撤分类')
    if dry_run:
        return CashClassificationReversal(
            organization=member.organization, classification=row,
            cash_transaction=row.cash_transaction, status='posted', reason=reason_value,
            evidence=evidence_refs, occurred_on=reversal_day,
            posting_period=reversal_period, created_by=member)
    updated = CashClassification.objects.filter(
        pk=row.pk, organization=member.organization, version=version, status='active').update(
        status='reversed')
    if updated != 1:
        _fail('现金分类已被他人撤分类，请刷新后重试')
    row.refresh_from_db()
    reversal = CashClassificationReversal(
        organization=member.organization, classification=row,
        cash_transaction=row.cash_transaction, status='posted', reason=reason_value,
        evidence=evidence_refs, occurred_on=reversal_day, posting_period=reversal_period,
        created_by=member)
    try:
        with transaction.atomic():
            reversal.save()
    except IntegrityError:
        _fail('该现金分类已撤分类，不能重复撤分类')
    from core import cash_accounting
    event, _posting = cash_accounting.reverse_cash_classification(member, reversal)
    event_ref = _event_ref(event)
    audit(member, REVERSED_ACTION, reversal,
          {'classification_ref': row.classification_ref, 'reversal_ref': reversal.reversal_ref,
           'reason': reason_value, 'posting_event_ref': event_ref})
    event_source = audit(member, 'plugin.cash.classification.reversed', reversal,
                         {'reversal_ref': reversal.reversal_ref,
                          'posting_event_ref': event_ref})
    canonical_id = uuid.uuid4()
    ref = reversal.reversal_ref
    DomainEvent.objects.create(
        event_id=canonical_id, organization=member.organization, source=event_source,
        topic=REVERSED_TOPIC,
        data={'event_id': str(canonical_id), 'topic': REVERSED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref], 'posting_event_refs': [event_ref],
                          'original_classification_ref': row.classification_ref,
                          'reversed_payroll_link_refs': []}})
    _record(member, 'cash_classification.reverse', token, digest, ref)
    return reversal


# ---------- reads ----------
def classification_snapshot(member, classification_id):
    member = _fresh(member, check_module=False)
    return _classification_projection(_get_classification(member, classification_id))


def classification_list(member, *, funding_account_id=None, cash_transaction_id=None,
                        posting_period=None):
    member = _fresh(member, check_module=False)
    rows = CashClassification.objects.filter(
        organization=member.organization).select_related(
        'cash_transaction__funding_account', 'created_by')
    if funding_account_id is not None:
        rows = rows.filter(cash_transaction__funding_account_id=_identifier(
            funding_account_id, '银行账户不存在或无权访问'))
    if cash_transaction_id is not None:
        rows = rows.filter(cash_transaction_id=_identifier(
            cash_transaction_id, '银行流水不存在或无权访问'))
    if posting_period is not None:
        rows = rows.filter(cash_transaction__transaction_date__startswith=posting_period + '-01')
    return [_classification_projection(row) for row in rows.order_by(
        '-created_at', '-id')]


def cash_book_coverage(member, funding_account_id, *, as_of=None):
    """Read-only cash book coverage derived from ledger postings; never a bank/available balance."""
    member = _fresh(member, check_module=False)
    account_id = _identifier(funding_account_id, '银行账户不存在或无权访问')
    try:
        account = FundingAccount.objects.get(organization=member.organization, pk=account_id)
    except FundingAccount.DoesNotExist:
        raise PermissionDenied('银行账户不存在或无权访问')
    day = timezone.localdate() if as_of is None else _business_date(as_of)
    postings = LedgerPosting.objects.filter(
        organization_id=member.organization_id, scope_kind='funding_account',
        scope_id=str(account.pk), accounting_event__occurred_on__lte=day)
    cash_in = postings.filter(book='cash_in').aggregate(
        total=Sum('amount_cents'))['total'] or 0
    cash_out = postings.filter(book='cash_out').aggregate(
        total=Sum('amount_cents'))['total'] or 0
    transaction_ids = CashTransaction.objects.filter(
        organization=member.organization, funding_account=account,
        transaction_date__lte=day).values_list('pk', flat=True)
    active = CashClassification.objects.filter(
        organization=member.organization, cash_transaction_id__in=transaction_ids,
        status='active')
    classified_count = active.count()
    posted_count = CashTransaction.objects.filter(
        organization=member.organization, funding_account=account, status='posted',
        transaction_date__lte=day).count()
    classification_ids = [str(pk) for pk in CashClassification.objects.filter(
        organization=member.organization,
        cash_transaction_id__in=transaction_ids).values_list('pk', flat=True)]
    last_event = AccountingEvent.objects.filter(
        organization=member.organization, source__id__in=classification_ids,
        event_type__in=['cash.classification', 'cash.classification.reversed']).aggregate(
        last=Max('created_at'))['last']
    return {
        'funding_account_id': str(account.pk), 'account_ref': account.account_ref,
        'account_name': account.name, 'as_of': day.isoformat(),
        'from_ledger_cash_in_cents': cash_in, 'from_ledger_cash_out_cents': cash_out,
        'book_net_cents': cash_in - cash_out,
        'classified_count': classified_count,
        'unclassified_posted_count': max(0, posted_count - classified_count),
        'last_event_at': last_event.isoformat() if last_event else None,
        'estimate_label': '现金账净额（含冲销），非银行余额/可用余额',
    }
