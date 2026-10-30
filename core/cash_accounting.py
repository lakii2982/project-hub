"""Core-owned append-only bank ledger bridge for cash classifications (PH-239).

Only the cash classification owner service calls these functions after it has validated the
posted bank fact, role, period and effective-classification rules. Every effective
classification writes exactly one AccountingEvent (event_type=cash.classification) with one
cash_in/cash_out LedgerPosting scoped to the funding account; a reversal appends a mirrored
event/posting and never edits or deletes the original rows. The single bank_effect
SourceConsumption is permanent and re-used only after its events net to zero.
"""
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum

from .access import require_member
from .models import AccountingEvent, LedgerPosting, Membership, SourceConsumption

ALGORITHM = 'cash_classification_bank_effect_v1'
EVENT_TYPE = 'cash.classification'
REVERSED_EVENT_TYPE = 'cash.classification.reversed'
CLAIM_CONSUMER = 'cash'
CLAIM_COVERAGE_KEY = 'bank_effect'
BOOKS = {'in': 'cash_in', 'out': 'cash_out'}
SCOPE_KIND = 'funding_account'
MAX_CENTS = 10 ** 12


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in {'owner', 'finance'}:
        raise PermissionDenied('只有公司负责人或项目财务可以办理现金账桥接')
    return fresh


def _amount(value):
    if type(value) is not int or not 0 < value <= MAX_CENTS:
        raise ValidationError('现金账金额须为大于0且不超过100亿元的整数分')
    return value


def _claim(member, transaction, event_id, amount):
    """Return the permanent bank_effect claim, creating it once per bank fact."""
    claim, created = SourceConsumption.objects.get_or_create(
        organization=member.organization, consumer=CLAIM_CONSUMER,
        source_id=transaction.pk, coverage_key=CLAIM_COVERAGE_KEY,
        defaults={'source': transaction.transaction_ref, 'amount_cents': amount,
                  'event_ref': {'namespace': 'core', 'kind': 'accounting_event',
                                'id': str(event_id), 'version': 1}})
    if created:
        return claim
    if claim.source.get('id') != str(transaction.pk):
        raise ValidationError('银行流水现金账消费来源不一致')
    if (claim.events.aggregate(total=Sum('claim_effect_cents'))['total'] or 0) != 0:
        raise ValidationError('该银行流水已有有效现金账，不能重复入账')
    return claim


def _event_ref(event):
    return {'namespace': 'core', 'kind': 'accounting_event', 'id': str(event.pk),
            'version': event.version}


def _posting_ref(posting):
    return {'namespace': 'core', 'kind': 'ledger_posting', 'id': str(posting.pk),
            'version': posting.version}


def post_cash_classification(member, classification):
    """Append the cash book event + posting for one effective classification."""
    member = _fresh(member)
    transaction = classification.cash_transaction
    if classification.organization_id != member.organization_id:
        raise PermissionDenied('现金分类不属于本公司')
    if transaction.organization_id != member.organization_id:
        raise PermissionDenied('银行流水不属于本公司')
    if transaction.status != 'posted':
        raise ValidationError('仅已过账银行流水可以桥接现金账')
    if classification.status != 'active':
        raise ValidationError('仅有效现金分类可以桥接现金账')
    amount = _amount(transaction.gross_cents)
    event_id = uuid.uuid4()
    claim = _claim(member, transaction, event_id, amount)
    event = AccountingEvent.objects.create(
        id=event_id, organization=member.organization,
        source=classification.classification_ref, event_type=EVENT_TYPE,
        occurred_on=transaction.transaction_date,
        posting_period=transaction.transaction_date.strftime('%Y-%m'),
        algorithm_version=ALGORITHM, claim=claim, claim_effect_cents=amount)
    posting = LedgerPosting.objects.create(
        organization=member.organization, accounting_event=event, version=1, status='posted',
        book=BOOKS[transaction.direction], amount_cents=amount, scope_kind=SCOPE_KIND,
        scope_id=str(transaction.funding_account_id), line_index=0, origin='current')
    return event, posting


def reverse_cash_classification(member, reversal):
    """Append the mirrored event + negative posting; original event/posting stay untouched."""
    member = _fresh(member)
    classification = reversal.classification
    transaction = reversal.cash_transaction
    if (reversal.organization_id != member.organization_id
            or classification.organization_id != member.organization_id
            or transaction.organization_id != member.organization_id):
        raise PermissionDenied('撤分类记录不属于本公司')
    if classification.pk != reversal.classification_id or transaction.pk != classification.cash_transaction_id:
        raise ValidationError('撤分类记录引用的原分类或银行流水不一致')
    original = AccountingEvent.objects.filter(
        organization=member.organization, source=classification.classification_ref,
        event_type=EVENT_TYPE).first()
    if original is None:
        raise ValidationError('原分类现金账事件不存在，不能撤分类')
    if original.reverses_event_id or AccountingEvent.objects.filter(reverses_event=original).exists():
        raise ValidationError('原分类现金账事件已冲销，不能重复撤分类')
    postings = list(original.postings.order_by('line_index'))
    if len(postings) != 1:
        raise ValidationError('原分类现金账事件必须恰有一条过账行')
    original_posting = postings[0]
    if (original.claim_effect_cents <= 0
            or original.claim_effect_cents != original_posting.amount_cents):
        raise ValidationError('原分类现金账金额不一致，不能撤分类')
    event = AccountingEvent.objects.create(
        organization=member.organization, source=reversal.reversal_ref,
        event_type=REVERSED_EVENT_TYPE, occurred_on=reversal.occurred_on,
        posting_period=reversal.posting_period, algorithm_version=original.algorithm_version,
        reverses_event=original, claim=original.claim,
        claim_effect_cents=-original.claim_effect_cents)
    posting = LedgerPosting.objects.create(
        organization=member.organization, accounting_event=event, version=1, status='posted',
        book=original_posting.book, amount_cents=-original_posting.amount_cents,
        scope_kind=original_posting.scope_kind, scope_id=original_posting.scope_id,
        line_index=0, origin='current', reverses_posting=original_posting)
    return event, posting


def classification_events(organization_id, classification_ref):
    return AccountingEvent.objects.filter(organization_id=organization_id,
                                          source=classification_ref, event_type=EVENT_TYPE)


def cash_book_totals(organization_id, funding_account_id, *, through):
    rows = LedgerPosting.objects.filter(
        organization_id=organization_id, scope_kind=SCOPE_KIND,
        scope_id=str(funding_account_id), accounting_event__occurred_on__lte=through)
    cash_in = rows.filter(book='cash_in').aggregate(total=Sum('amount_cents'))['total'] or 0
    cash_out = rows.filter(book='cash_out').aggregate(total=Sum('amount_cents'))['total'] or 0
    return cash_in, cash_out
