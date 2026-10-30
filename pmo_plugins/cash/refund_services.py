"""Private refund and bank-return services: real reverse cash facts, no ledger writes (PH-240).

A refund reverses an incoming posted cash fact into a new posted outgoing cash fact within its
derived refundable coverage; a bank return reverses any posted cash fact in the opposite
direction. Both create the new posted cash through the PH-230 owner posting service (so the
standard cash.transaction_recorded.v1 event and posting receipt still hold), then append the
Refund/BankReturn fact, the generic audit, exactly one canonical refunded/returned event and
the idempotency receipt. No accounting event, ledger posting, revenue, cost, receivable,
payable, open item or clearance is written; PH-239 owns the later cash book bridge.
"""
from decimal import Decimal
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import DomainEvent
from core.services import audit

from . import transaction_services as tx
from .funding_services import (_amount, _business_date, _dry, _evidence, _fail, _fresh,
                               _identifier, _locked, _receipt, _receipt_row, _record, _text)
from .models import BankReturn, CashTransaction, Refund

REFUND_ACTION = 'cash.refund.recorded'
REFUND_TOPIC = 'cash.refunded.v1'
RETURN_ACTION = 'cash.bank_return.recorded'
RETURN_TOPIC = 'cash.returned.v1'
MAX_CENTS = 10 ** 12


# ---------- guards ----------
def _original(member, cash_transaction_id, *, lock=False):
    return tx._get_transaction(member, cash_transaction_id, lock=lock)


def _effective_counterparty(row):
    """transaction.counterparty_id or the latest unreversed identify match; else None."""
    if row.counterparty_id:
        return str(row.counterparty_id)
    reversed_ids = set(row.matches.filter(action='reverse').values_list(
        'original_match_id', flat=True))
    for match in row.matches.filter(action='identify').order_by('-created_at', '-id'):
        if match.pk in reversed_ids:
            continue
        payee = match.identified_payee or {}
        if payee.get('kind') == 'counterparty' and payee.get('id'):
            return str(payee['id'])
        return None
    return None


def _coverage(member, original):
    from pmo_plugins.settlement.clearance_services import clearance_coverage_for_cash
    settlement = clearance_coverage_for_cash(member, original.transaction_ref)
    refunded = Refund.objects.filter(
        organization=member.organization, original_cash_transaction=original).aggregate(
        total=Sum('gross_cents'))['total'] or 0
    returned = BankReturn.objects.filter(
        organization=member.organization, original_cash_transaction=original).aggregate(
        total=Sum('gross_cents'))['total'] or 0
    return {
        'posted_gross_cents': original.gross_cents,
        'cleared_cents': settlement['cleared_cents'],
        'offset_cents': settlement['offset_cents'],
        'refunded_cents': refunded,
        'returned_cents': returned,
        'refundable_cents': (original.gross_cents - settlement['total_cents']
                             - refunded - returned),
    }


def _posting_token(seed, label):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'ph240:{label}:{seed}')


def _create_reverse_cash(member, *, original, day, gross, direction, prefix, label):
    """Create the new posted reverse cash fact through the PH-230 posting owner service."""
    effective = _effective_counterparty(original)
    if effective is not None:
        payee = {'kind': 'counterparty', 'id': effective}
        counterparty_id = uuid.UUID(effective)
    else:
        payee = {'kind': 'unidentified', 'bank_label': label}
        counterparty_id = None
    evidence = _evidence([{'reference': f'{label}依据：原现金流水 {original.pk}'}])
    try:
        with transaction.atomic():
            row = CashTransaction.objects.create(
                organization=member.organization, funding_account=original.funding_account,
                transaction_date=day, direction=direction, gross_cents=gross,
                external_source_key=f'{prefix}-{uuid.uuid4().hex}', payee=payee,
                counterparty_id=counterparty_id, evidence=evidence, status='draft',
                version=1, created_by=member)
    except IntegrityError:
        _fail(f'{label}现金来源编号冲突，请重试')
    tx.record_cash_transaction(
        member, row.pk, expected_version=1, occurred_on=day,
        posting_period=day.strftime('%Y-%m'), request_token=_posting_token(row.pk, label),
        dry_run=False)
    row.refresh_from_db()
    return row


def _write_canonical_event(member, *, topic, source_ref, changed_refs, anchor):
    event_id = uuid.uuid4()
    event_source = audit(member, 'plugin.' + topic.replace('.v1', ''), anchor,
                         {'source_ref': source_ref})
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=event_source, topic=topic,
        data={'event_id': str(event_id), 'topic': topic, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': source_ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': changed_refs, 'posting_event_refs': []}})


# ---------- write services ----------
@transaction.atomic
def record_refund(member, *, original_cash_transaction_id, refund_date, gross_cents, reason,
                  request_token, dry_run=True):
    """Refund an incoming posted cash fact within its derived refundable coverage."""
    _dry(dry_run)
    member = _locked(member)
    original_id = _identifier(original_cash_transaction_id, '原现金流水不存在或无权访问')
    day = _business_date(refund_date)
    amount = _amount(gross_cents)
    reason_value = _text(reason, 500, '请填写1至500字的退款原因')
    payload = {'original_cash_transaction_id': str(original_id),
               'refund_date': day.isoformat(), 'gross_cents': amount, 'reason': reason_value}
    token, digest, receipt = _receipt(member, 'refund', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, Refund)
    original = _original(member, original_id, lock=True)
    if original.status != 'posted':
        _fail('仅已过账现金可以退款')
    if original.direction != 'in':
        _fail('仅收入类现金可以退款')
    tx._period(member, day)
    coverage = _coverage(member, original)
    if coverage['refundable_cents'] <= 0:
        _fail('该现金已无可退余额')
    if amount > coverage['refundable_cents']:
        _fail('退款金额超过可退余额')
    if dry_run:
        preview = Refund(organization=member.organization,
                         original_cash_transaction=original,
                         original_cash_ref=original.transaction_ref, refund_date=day,
                         gross_cents=amount, reason=reason_value, status='active', version=1,
                         created_by=member)
        preview.refund_cash_transaction = CashTransaction(
            organization=member.organization, funding_account=original.funding_account,
            transaction_date=day, direction='out', gross_cents=amount,
            status='posted', version=2, created_by=member)
        return preview
    new_cash = _create_reverse_cash(
        member, original=original, day=day, gross=amount, direction='out',
        prefix='REFUND', label='退款')
    refund = Refund.objects.create(
        organization=member.organization, original_cash_transaction=original,
        refund_cash_transaction=new_cash, original_cash_ref=original.transaction_ref,
        refund_date=day, gross_cents=amount, reason=reason_value, status='active', version=1,
        created_by=member)
    refund_ref = refund.refund_ref
    cash_ref = new_cash.transaction_ref
    audit(member, REFUND_ACTION, refund,
          {'refund_ref': refund_ref, 'original_cash_ref': refund.original_cash_ref,
           'refund_cash_ref': cash_ref, 'gross_cents': amount, 'reason': reason_value})
    _write_canonical_event(member, topic=REFUND_TOPIC, source_ref=refund_ref,
                           changed_refs=[refund_ref, cash_ref], anchor=refund)
    _record(member, 'refund', token, digest, refund_ref)
    return refund


@transaction.atomic
def record_bank_return(member, *, original_cash_transaction_id, return_date, gross_cents,
                       request_token, dry_run=True):
    """Register a bank return of any posted cash fact in the opposite direction."""
    _dry(dry_run)
    member = _locked(member)
    original_id = _identifier(original_cash_transaction_id, '原现金流水不存在或无权访问')
    day = _business_date(return_date)
    amount = _amount(gross_cents)
    payload = {'original_cash_transaction_id': str(original_id),
               'return_date': day.isoformat(), 'gross_cents': amount}
    token, digest, receipt = _receipt(member, 'bank_return', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, BankReturn)
    original = _original(member, original_id, lock=True)
    if original.status != 'posted':
        _fail('仅已过账现金可以登记银行退回')
    direction = 'in' if original.direction == 'out' else 'out'
    tx._period(member, day)
    coverage = _coverage(member, original)
    if coverage['refundable_cents'] <= 0:
        _fail('该现金已无可退回余额')
    if amount > coverage['refundable_cents']:
        _fail('银行退回金额超过可退回余额')
    if dry_run:
        preview = BankReturn(organization=member.organization,
                             original_cash_transaction=original,
                             original_cash_ref=original.transaction_ref, return_date=day,
                             gross_cents=amount, status='active', version=1,
                             created_by=member)
        preview.return_cash_transaction = CashTransaction(
            organization=member.organization, funding_account=original.funding_account,
            transaction_date=day, direction=direction, gross_cents=amount,
            status='posted', version=2, created_by=member)
        return preview
    new_cash = _create_reverse_cash(
        member, original=original, day=day, gross=amount, direction=direction,
        prefix='RETURN', label='银行退回')
    bank_return = BankReturn.objects.create(
        organization=member.organization, original_cash_transaction=original,
        return_cash_transaction=new_cash, original_cash_ref=original.transaction_ref,
        return_date=day, gross_cents=amount, status='active', version=1, created_by=member)
    return_ref = bank_return.bank_return_ref
    cash_ref = new_cash.transaction_ref
    audit(member, RETURN_ACTION, bank_return,
          {'bank_return_ref': return_ref, 'original_cash_ref': bank_return.original_cash_ref,
           'return_cash_ref': cash_ref, 'gross_cents': amount})
    _write_canonical_event(member, topic=RETURN_TOPIC, source_ref=return_ref,
                           changed_refs=[return_ref, cash_ref], anchor=bank_return)
    _record(member, 'bank_return', token, digest, return_ref)
    return bank_return


# ---------- read projections ----------
def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _refund_projection(row):
    return {
        'id': str(row.pk), 'refund_ref': row.refund_ref,
        'organization_id': str(row.organization_id),
        'original_cash_transaction_id': str(row.original_cash_transaction_id),
        'original_cash_ref': row.original_cash_ref,
        'refund_cash_transaction_id': str(row.refund_cash_transaction_id),
        'refund_cash_ref': row.refund_cash_transaction.transaction_ref,
        'refund_date': row.refund_date.isoformat(), 'gross_cents': row.gross_cents,
        'gross_display': _yuan(row.gross_cents), 'reason': row.reason,
        'status': row.status, 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
        'original_cash_url_id': str(row.original_cash_transaction_id),
    }


def _bank_return_projection(row):
    return {
        'id': str(row.pk), 'bank_return_ref': row.bank_return_ref,
        'organization_id': str(row.organization_id),
        'original_cash_transaction_id': str(row.original_cash_transaction_id),
        'original_cash_ref': row.original_cash_ref,
        'return_cash_transaction_id': str(row.return_cash_transaction_id),
        'return_cash_ref': row.return_cash_transaction.transaction_ref,
        'return_date': row.return_date.isoformat(), 'gross_cents': row.gross_cents,
        'gross_display': _yuan(row.gross_cents), 'status': row.status, 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
        'original_cash_url_id': str(row.original_cash_transaction_id),
    }


def _refund_queryset(member):
    return Refund.objects.filter(organization=member.organization).select_related(
        'refund_cash_transaction', 'original_cash_transaction')


def _bank_return_queryset(member):
    return BankReturn.objects.filter(organization=member.organization).select_related(
        'return_cash_transaction', 'original_cash_transaction')


def refund_snapshot(member, refund_id):
    member = _fresh(member, check_module=False)
    try:
        row = _refund_queryset(member).get(pk=_identifier(refund_id, '退款记录不存在或无权访问'))
    except Refund.DoesNotExist:
        raise PermissionDenied('退款记录不存在或无权访问')
    return _refund_projection(row)


def refund_list(member, *, original_cash_transaction_id=None, funding_account_id=None,
                as_of=None):
    member = _fresh(member, check_module=False)
    rows = _refund_queryset(member)
    if original_cash_transaction_id is not None:
        rows = rows.filter(original_cash_transaction_id=_identifier(
            original_cash_transaction_id, '原现金流水不存在或无权访问'))
    if funding_account_id is not None:
        rows = rows.filter(original_cash_transaction__funding_account_id=_identifier(
            funding_account_id, '银行账户不存在或无权访问'))
    if as_of is not None:
        rows = rows.filter(refund_date__lte=_business_date(as_of))
    return [_refund_projection(row) for row in rows.order_by('-refund_date', '-created_at', 'id')]


def bank_return_snapshot(member, bank_return_id):
    member = _fresh(member, check_module=False)
    try:
        row = _bank_return_queryset(member).get(
            pk=_identifier(bank_return_id, '银行退回记录不存在或无权访问'))
    except BankReturn.DoesNotExist:
        raise PermissionDenied('银行退回记录不存在或无权访问')
    return _bank_return_projection(row)


def bank_return_list(member, *, original_cash_transaction_id=None, funding_account_id=None,
                     as_of=None):
    member = _fresh(member, check_module=False)
    rows = _bank_return_queryset(member)
    if original_cash_transaction_id is not None:
        rows = rows.filter(original_cash_transaction_id=_identifier(
            original_cash_transaction_id, '原现金流水不存在或无权访问'))
    if funding_account_id is not None:
        rows = rows.filter(original_cash_transaction__funding_account_id=_identifier(
            funding_account_id, '银行账户不存在或无权访问'))
    if as_of is not None:
        rows = rows.filter(return_date__lte=_business_date(as_of))
    return [_bank_return_projection(row) for row in rows.order_by(
        '-return_date', '-created_at', 'id')]


def refund_coverage_for_cash(member, cash_transaction_ref):
    """Cross-owner read-only refund/return coverage of one posted bank fact.

    Settlement clearance consumes this named read so a cash fact can never be simultaneously
    refunded and cleared beyond its posted gross (offset coverage is not implemented yet).
    """
    member = _fresh(member)
    if (not isinstance(cash_transaction_ref, dict)
            or set(cash_transaction_ref) != {'namespace', 'kind', 'id', 'version'}):
        _fail('现金流水来源无效')
    if (cash_transaction_ref['namespace'] != 'cash'
            or cash_transaction_ref['kind'] != 'cash_transaction'
            or type(cash_transaction_ref['version']) is not int):
        _fail('现金流水来源无效')
    original = _original(member, _identifier(cash_transaction_ref['id'],
                                             '现金流水不存在或无权访问'))
    if original.version != cash_transaction_ref['version']:
        _fail('现金流水版本已变化，请刷新后重试')
    if original.status != 'posted':
        _fail('仅已过账现金有退款/退回覆盖')
    refunded = Refund.objects.filter(
        organization=member.organization, original_cash_transaction=original).aggregate(
        total=Sum('gross_cents'))['total'] or 0
    returned = BankReturn.objects.filter(
        organization=member.organization, original_cash_transaction=original).aggregate(
        total=Sum('gross_cents'))['total'] or 0
    return {'cash_transaction_ref': original.transaction_ref,
            'refunded_cents': refunded, 'returned_cents': returned,
            'total_cents': refunded + returned, 'offset_cents': 0,
            'offset_implemented': False}


def provenance_for_cash(member, cash_transaction_id):
    """Return the append-only refund/bank-return fact that produced this cash fact, if any."""
    member = _fresh(member, check_module=False)
    cash_id = _identifier(cash_transaction_id, '现金流水不存在或无权访问')
    refund = _refund_queryset(member).filter(refund_cash_transaction_id=cash_id).first()
    bank_return = _bank_return_queryset(member).filter(
        return_cash_transaction_id=cash_id).first()
    return {'refund': _refund_projection(refund) if refund else None,
            'bank_return': _bank_return_projection(bank_return) if bank_return else None}


def cash_refund_coverage(member, cash_transaction_id):
    """Server-derived refund/return coverage; never a caller-supplied balance or proof."""
    member = _fresh(member, check_module=False)
    original = _original(member, cash_transaction_id)
    if original.status != 'posted':
        _fail('仅已过账现金有退款/退回覆盖')
    coverage = _coverage(member, original)
    return {
        'cash_transaction_id': str(original.pk),
        'cash_transaction_ref': original.transaction_ref,
        'funding_account_id': str(original.funding_account_id),
        'transaction_date': original.transaction_date.isoformat(),
        'direction': original.direction,
        'posted_gross_cents': coverage['posted_gross_cents'],
        'cleared_cents': coverage['cleared_cents'],
        'offset_cents': coverage['offset_cents'],
        'refunded_cents': coverage['refunded_cents'],
        'returned_cents': coverage['returned_cents'],
        'refundable_cents': coverage['refundable_cents'],
        'posted_gross_display': _yuan(coverage['posted_gross_cents']),
        'cleared_display': _yuan(coverage['cleared_cents']),
        'refunded_display': _yuan(coverage['refunded_cents']),
        'returned_display': _yuan(coverage['returned_cents']),
        'refundable_display': _yuan(coverage['refundable_cents']),
        'coverage_label': '可退额 = 已过账金额 − 有效核销 − 已退款 − 已银行退回；服务端派生，非余额/证明',
    }
