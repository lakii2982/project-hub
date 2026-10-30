"""PH-245 private internal transfer services: pair one out leg and one in leg of company cash.

A transfer links two posted cash facts of the same organization, writes one internal
`CashClassification(classification='transfer')` and its canonical classification event/posting
through the PH-239 named internal service for each leg, and appends one canonical
`cash.internal_transfer_linked.v1` event whose posting_event_refs is empty (the postings belong
to the two classification events). Reversal is append-only: two classification reversals plus a
reversed transfer status and one `InternalTransferReversal` fact carrying the canonical
`cash.internal_transfer_reversed.v1` source. No cost/revenue/receivable/open-item/clearance is
written.
"""
from datetime import date
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import DomainEvent
from core.services import audit

from . import classification_services as classification
from .funding_services import (_business_date, _digest, _dry, _evidence, _fail, _fresh,
                               _identifier, _locked, _text, _version)
from .models import (CashClassification, CashTransaction, InternalTransfer,
                     InternalTransferReceipt, InternalTransferReversal)
from .transaction_services import _get_transaction, _period

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor'}
LINKED_TOPIC = 'cash.internal_transfer_linked.v1'
REVERSED_TOPIC = 'cash.internal_transfer_reversed.v1'
LINKED_ACTION = 'cash.internal_transfer.linked'
REVERSED_ACTION = 'cash.internal_transfer.reversed'
MAX_CENTS = 10 ** 12


# ---------- receipts ----------
def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token, '请求标识无效')
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = InternalTransferReceipt.objects.filter(organization=member.organization,
                                                 request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, digest, row
    return token, digest, None


def _receipt_row(member, receipt):
    result = receipt.result or {}
    try:
        return InternalTransfer.objects.get(organization=member.organization,
                                            pk=result['id'])
    except (KeyError, ValueError, TypeError, InternalTransfer.DoesNotExist):
        raise PermissionDenied('原内部互转不存在或无权访问')


def _reversal_receipt_row(member, receipt):
    result = receipt.result or {}
    try:
        return InternalTransferReversal.objects.get(
            organization=member.organization, pk=result['id'])
    except (KeyError, ValueError, TypeError, InternalTransferReversal.DoesNotExist):
        raise PermissionDenied('原内部撤转不存在或无权访问')


def _record(member, action, token, digest, result_ref):
    InternalTransferReceipt.objects.create(
        organization=member.organization, request_token=token, action=action,
        input_digest=digest, result=result_ref, created_by=member)


# ---------- reads ----------
def _get_transfer(member, transfer_id, *, lock=False):
    rows = InternalTransfer.objects.select_related(
        'out_cash_transaction__funding_account', 'in_cash_transaction__funding_account')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        return rows.get(organization=member.organization,
                        pk=_identifier(transfer_id, '内部互转不存在或无权访问'))
    except InternalTransfer.DoesNotExist:
        raise PermissionDenied('内部互转不存在或无权访问')


def _classification_for(transfer, direction):
    return CashClassification.objects.filter(
        organization=transfer.organization,
        cash_transaction_id=(transfer.out_cash_transaction_id if direction == 'out'
                             else transfer.in_cash_transaction_id),
        status='active', classification='transfer').first()


def _transfer_projection(row):
    out_cash = row.out_cash_transaction
    in_cash = row.in_cash_transaction
    out_classification = _classification_for(row, 'out')
    in_classification = _classification_for(row, 'in')
    reversal = row.reversals.order_by('created_at', 'id').first()
    return {
        'id': str(row.pk), 'internal_transfer_ref': row.internal_transfer_ref,
        'organization_id': str(row.organization_id),
        'out_cash_transaction_id': str(row.out_cash_transaction_id),
        'in_cash_transaction_id': str(row.in_cash_transaction_id),
        'out_cash_ref': row.out_cash_ref, 'in_cash_ref': row.in_cash_ref,
        'out_account_id': str(out_cash.funding_account_id),
        'in_account_id': str(in_cash.funding_account_id),
        'out_account_name': out_cash.funding_account.name,
        'in_account_name': in_cash.funding_account.name,
        'out_transaction_date': row.out_transaction_date.isoformat(),
        'in_transaction_date': row.in_transaction_date.isoformat(),
        'transfer_date': row.transfer_date.isoformat(),
        'gross_cents': row.gross_cents, 'gross_display': _yuan(row.gross_cents),
        'status': row.status, 'status_display': row.get_status_display(),
        'version': row.version,
        'out_classification_ref': (out_classification.classification_ref
                                   if out_classification else None),
        'in_classification_ref': (in_classification.classification_ref
                                  if in_classification else None),
        'reversal_ref': reversal.reversal_ref if reversal else None,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
    }


def _yuan(cents):
    from decimal import Decimal
    return format(Decimal(cents) / 100, '.2f')


def internal_transfer_snapshot(member, transfer_id):
    member = _fresh(member, check_module=False)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权读取内部互转')
    return _transfer_projection(_get_transfer(member, transfer_id))


def internal_transfer_list(member, *, funding_account_id=None, status=None, as_of=None):
    member = _fresh(member, check_module=False)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权读取内部互转')
    rows = InternalTransfer.objects.filter(
        organization=member.organization).select_related(
        'out_cash_transaction__funding_account', 'in_cash_transaction__funding_account')
    if funding_account_id is not None:
        account_id = _identifier(funding_account_id, '银行账户不存在或无权访问')
        rows = rows.filter(out_cash_transaction__funding_account_id=account_id)
    if status is not None:
        if status not in {'active', 'reversed'}:
            _fail('内部互转状态无效')
        rows = rows.filter(status=status)
    if as_of is not None:
        rows = rows.filter(transfer_date__lte=_business_date(as_of))
    return [_transfer_projection(row) for row in rows.order_by('-created_at', '-id')]


def internal_transfer_integrity(member):
    """Read-only integrity summary: each active transfer is a valid, balanced pair."""
    member = _fresh(member, check_module=False)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权读取内部互转')
    findings = []
    rows = list(InternalTransfer.objects.filter(
        organization=member.organization).select_related(
        'out_cash_transaction', 'in_cash_transaction'))
    seen_out = set()
    seen_in = set()
    for row in rows:
        out_cash = row.out_cash_transaction
        in_cash = row.in_cash_transaction
        if (out_cash.organization_id != row.organization_id
                or in_cash.organization_id != row.organization_id):
            findings.append(f"internal_transfer:{row.pk}:cash_org")
        if out_cash.status != 'posted' or in_cash.status != 'posted':
            findings.append(f"internal_transfer:{row.pk}:cash_state")
        if out_cash.direction != 'out' or in_cash.direction != 'in':
            findings.append(f"internal_transfer:{row.pk}:cash_direction")
        if (out_cash.funding_account_id == in_cash.funding_account_id
                or out_cash.gross_cents != in_cash.gross_cents
                or out_cash.gross_cents != row.gross_cents
                or out_cash.transaction_date != in_cash.transaction_date
                or out_cash.transaction_date != row.transfer_date):
            findings.append(f"internal_transfer:{row.pk}:pair_mismatch")
        if row.status == 'active':
            if out_cash.pk in seen_out or in_cash.pk in seen_in:
                findings.append(f"internal_transfer:{row.pk}:duplicate_active")
            seen_out.add(out_cash.pk)
            seen_in.add(in_cash.pk)
            if _classification_for(row, 'out') is None or _classification_for(row, 'in') is None:
                findings.append(f"internal_transfer:{row.pk}:missing_classification")
        elif row.status == 'reversed':
            if _classification_for(row, 'out') is not None or _classification_for(row, 'in') is not None:
                findings.append(f"internal_transfer:{row.pk}:active_classification_after_reverse")
            if row.reversals.count() != 1:
                findings.append(f"internal_transfer:{row.pk}:reversal_count")
    # A transfer only contributes one out and one in leg of equal gross, so the cash book net
    # (cash_in - cash_out) is unchanged by the pair; this check guards the pair amounts.
    return {'ok': not findings, 'findings': findings,
            'active_count': len(seen_out),
            'transfer_count': len(rows)}


# ---------- writes ----------
@transaction.atomic
def link_internal_transfer(member, *, out_cash_transaction_id, in_cash_transaction_id,
                           evidence, occurred_on=None, posting_period=None, request_token,
                           dry_run=True):
    """Link two posted same-company cash facts as one internal transfer pair."""
    _dry(dry_run)
    member = _locked(member)
    if member.role not in WRITE_ROLES:
        raise PermissionDenied('仅公司负责人或财务可以办理内部互转')
    out_id = _identifier(out_cash_transaction_id, '转出现金流水不存在或无权访问')
    in_id = _identifier(in_cash_transaction_id, '转入现金流水不存在或无权访问')
    evidence_refs = _evidence(evidence)
    occurred = None if occurred_on is None else _business_date(occurred_on)
    period = None if posting_period is None else posting_period
    if period is not None:
        from .transaction_services import _posting_period
        period = _posting_period(period)
    payload = {'out_cash_transaction_id': str(out_id),
               'in_cash_transaction_id': str(in_id), 'evidence': evidence_refs,
               'occurred_on': occurred.isoformat() if occurred else None,
               'posting_period': period}
    token, digest, receipt = _receipt(member, 'link', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt)
    out_cash = _get_transaction(member, out_id, lock=True)
    in_cash = _get_transaction(member, in_id, lock=True)
    if out_cash.pk == in_cash.pk:
        _fail('转出与转入不能是同一条现金流水')
    if out_cash.organization_id != member.organization_id or in_cash.organization_id != member.organization_id:
        raise PermissionDenied('现金流水不属于本公司')
    if out_cash.status != 'posted' or in_cash.status != 'posted':
        _fail('仅已过账现金可以办理内部互转')
    if out_cash.direction != 'out' or in_cash.direction != 'in':
        _fail('内部互转要求一条转出（direction=out）与一条转入（direction=in）')
    if out_cash.funding_account_id == in_cash.funding_account_id:
        _fail('内部互转的两条现金必须属于不同银行账户')
    if out_cash.gross_cents != in_cash.gross_cents:
        _fail('内部互转两侧金额必须相等')
    if out_cash.transaction_date != in_cash.transaction_date:
        _fail('内部互转两侧日期必须相等')
    gross = out_cash.gross_cents
    if type(gross) is not int or not 0 < gross <= MAX_CENTS:
        _fail('内部互转金额须为大于0且不超过100亿元的整数分')
    day = out_cash.transaction_date
    if occurred is not None and occurred != day:
        _fail('内部互转日期必须与现金流水日期一致')
    if period is not None and period != day.strftime('%Y-%m'):
        _fail('内部互转过账期间必须与现金流水日期所属月份一致')
    _period(member, day)
    if InternalTransfer.objects.filter(
            organization=member.organization,
            out_cash_transaction_id__in=[out_cash.pk, in_cash.pk]).exists() or \
            InternalTransfer.objects.filter(
                organization=member.organization,
                in_cash_transaction_id__in=[out_cash.pk, in_cash.pk]).exists():
        _fail('该现金流水已参与内部互转')
    out_classification = classification.record_internal_classification(
        member, cash_transaction_id=out_cash.pk, evidence=evidence_refs,
        occurred_on=day, posting_period=day.strftime('%Y-%m'),
        request_token=uuid.uuid4(), dry_run=dry_run)
    in_classification = classification.record_internal_classification(
        member, cash_transaction_id=in_cash.pk, evidence=evidence_refs,
        occurred_on=day, posting_period=day.strftime('%Y-%m'),
        request_token=uuid.uuid4(), dry_run=dry_run)
    transfer = InternalTransfer(
        organization=member.organization, out_cash_transaction=out_cash,
        in_cash_transaction=in_cash, out_cash_ref=out_cash.transaction_ref,
        in_cash_ref=in_cash.transaction_ref, transfer_date=day, gross_cents=gross,
        out_transaction_date=out_cash.transaction_date,
        in_transaction_date=in_cash.transaction_date, status='active', version=1,
        created_by=member)
    if dry_run:
        transfer.out_cash_transaction = out_cash
        transfer.in_cash_transaction = in_cash
        return transfer
    try:
        with transaction.atomic():
            transfer.save()
    except IntegrityError:
        _fail('该现金流水已参与内部互转')
    ref = transfer.internal_transfer_ref
    audit(member, LINKED_ACTION, transfer,
          {'out_cash_ref': out_cash.transaction_ref, 'in_cash_ref': in_cash.transaction_ref,
           'out_classification_ref': out_classification.classification_ref,
           'in_classification_ref': in_classification.classification_ref,
           'gross_cents': gross})
    event_source = audit(member, 'plugin.cash.internal_transfer.linked', transfer,
                         {'internal_transfer_ref': ref})
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=event_source,
        topic=LINKED_TOPIC,
        data={'event_id': str(event_id), 'topic': LINKED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref, out_classification.classification_ref,
                                           in_classification.classification_ref],
                          'posting_event_refs': []}})
    _record(member, 'link', token, digest, ref)
    return transfer


@transaction.atomic
def reverse_internal_transfer(member, transfer_id, *, reason, evidence, occurred_on=None,
                              posting_period=None, request_token, expected_version,
                              dry_run=True):
    """Append-only reversal: reverse both classifications, then mark the transfer reversed."""
    _dry(dry_run)
    member = _locked(member)
    if member.role not in WRITE_ROLES:
        raise PermissionDenied('仅公司负责人或财务可以办理内部撤转')
    transfer_id = _identifier(transfer_id, '内部互转不存在或无权访问')
    version = _version(expected_version)
    reason_value = _text(reason, 1000, '请填写1至1000字的撤转原因')
    evidence_refs = _evidence(evidence)
    occurred = timezone.localdate() if occurred_on is None else _business_date(occurred_on)
    if occurred > timezone.localdate():
        _fail('撤转日期不能在未来')
    period = (occurred.strftime('%Y-%m') if posting_period is None
              else posting_period)
    from .transaction_services import _posting_period
    period = _posting_period(period)
    if period != occurred.strftime('%Y-%m'):
        _fail('撤转期间必须包含撤转日期')
    payload = {'transfer_id': str(transfer_id), 'reason': reason_value,
               'evidence': evidence_refs, 'occurred_on': occurred.isoformat(),
               'posting_period': period, 'expected_version': version}
    token, digest, receipt = _receipt(member, 'reverse', request_token, payload, dry_run)
    if receipt is not None:
        return _reversal_receipt_row(member, receipt)
    transfer = _get_transfer(member, transfer_id, lock=True)
    if transfer.version != version:
        _fail('内部互转版本已变化，请刷新后重试')
    if transfer.status != 'active':
        _fail('仅有效内部互转可以撤转；已撤转不可重复撤转')
    _period(member, occurred)
    out_classification = _classification_for(transfer, 'out')
    in_classification = _classification_for(transfer, 'in')
    if out_classification is None or in_classification is None:
        _fail('内部互转两侧分类不完整，不能撤转')
    if dry_run:
        return InternalTransferReversal(
            organization=member.organization, internal_transfer=transfer,
            internal_transfer_ref=transfer.internal_transfer_ref,
            out_classification_reversal_ref={}, in_classification_reversal_ref={},
            occurred_on=occurred, posting_period=period, status='posted', version=1,
            created_by=member)
    out_reversal = classification.reverse_classification(
        member, classification_id=out_classification.pk, reason=reason_value,
        evidence=evidence_refs, occurred_on=occurred, posting_period=period,
        request_token=uuid.uuid4(), expected_version=out_classification.version,
        dry_run=False)
    in_reversal = classification.reverse_classification(
        member, classification_id=in_classification.pk, reason=reason_value,
        evidence=evidence_refs, occurred_on=occurred, posting_period=period,
        request_token=uuid.uuid4(), expected_version=in_classification.version,
        dry_run=False)
    updated = InternalTransfer.objects.filter(
        pk=transfer.pk, organization=member.organization, version=version,
        status='active').update(status='reversed')
    if updated != 1:
        _fail('内部互转已被他人撤转，请刷新后重试')
    transfer.refresh_from_db()
    reversal = InternalTransferReversal(
        organization=member.organization, internal_transfer=transfer,
        internal_transfer_ref=transfer.internal_transfer_ref,
        out_classification_reversal_ref=out_reversal.reversal_ref,
        in_classification_reversal_ref=in_reversal.reversal_ref,
        occurred_on=occurred, posting_period=period, status='posted', version=1,
        created_by=member)
    try:
        with transaction.atomic():
            reversal.save()
    except IntegrityError:
        _fail('该内部互转已撤转，不能重复撤转')
    ref = reversal.reversal_ref
    audit(member, REVERSED_ACTION, reversal,
          {'internal_transfer_ref': transfer.internal_transfer_ref,
           'out_classification_reversal_ref': out_reversal.reversal_ref,
           'in_classification_reversal_ref': in_reversal.reversal_ref,
           'reason': reason_value})
    event_source = audit(member, 'plugin.cash.internal_transfer.reversed', reversal,
                         {'internal_transfer_reversal_ref': ref})
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=event_source,
        topic=REVERSED_TOPIC,
        data={'event_id': str(event_id), 'topic': REVERSED_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [transfer.internal_transfer_ref,
                                           out_reversal.reversal_ref,
                                           in_reversal.reversal_ref],
                          'posting_event_refs': [],
                          'original_internal_transfer_ref': transfer.internal_transfer_ref,
                          'classification_reversal_refs': [out_reversal.reversal_ref,
                                                           in_reversal.reversal_ref]}})
    _record(member, 'reverse', token, digest, ref)
    return reversal


def internal_transfer_for_cash(member, cash_transaction_id):
    """Read-only transfer projection if the cash fact participates in a transfer, else None."""
    member = _fresh(member, check_module=False)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权读取内部互转')
    cash_id = _identifier(cash_transaction_id, '现金流水不存在或无权访问')
    from django.db.models import Q
    row = InternalTransfer.objects.filter(
        Q(out_cash_transaction_id=cash_id) | Q(in_cash_transaction_id=cash_id),
        organization=member.organization).select_related(
        'out_cash_transaction__funding_account',
        'in_cash_transaction__funding_account').first()
    return _transfer_projection(row) if row is not None else None


def internal_transfer_candidates(member, direction):
    """Read-only posted cash facts not yet participating in any transfer (lifetime unique)."""
    member = _fresh(member, check_module=False)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权读取可互转的现金事实')
    if direction not in {'in', 'out'}:
        _fail('内部互转方向无效')
    used = set(InternalTransfer.objects.filter(
        organization=member.organization).values_list(
        'out_cash_transaction_id', 'in_cash_transaction_id'))
    used_ids = {str(value) for pair in used for value in pair}
    rows = CashTransaction.objects.filter(
        organization=member.organization, status='posted',
        direction=direction).select_related('funding_account').order_by(
        '-transaction_date', '-created_at', '-id')
    result = []
    for row in rows:
        if str(row.pk) in used_ids:
            continue
        result.append({'id': str(row.pk), 'transaction_ref': row.transaction_ref,
                       'transaction_date': row.transaction_date.isoformat(),
                       'direction': row.direction, 'gross_cents': row.gross_cents,
                       'gross_display': _yuan(row.gross_cents),
                       'funding_account_id': str(row.funding_account_id),
                       'funding_account_name': row.funding_account.name})
    return result
