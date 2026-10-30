"""PH-244 bank-fee draft facts for posted incoming cash; no approval, cost or ledger.

The slice records one draft BankCharge per original bank transaction, writes one generic
audit row plus exactly one canonical `costing.bank_charge_saved.v1` event per save, and
offers a read-only net/fee reconciliation. It never writes cash facts, cost, ledger,
accounting events, settlements or open items, and it never produces a terminal status.
"""
from datetime import date
import hashlib
import json
import re
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.access import require_member
from core.models import (AccountingEvent, AccountingPeriod, DomainEvent, LedgerPosting,
                         Membership, Project, SourceConsumption)
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company
from pmo_plugins.cash.transaction_services import cash_transaction_snapshot, posted_cash_for_fee
from pmo_plugins.contracts.models import Counterparty

from .models import BANK_CHARGE_MAX_CENTS, BankCharge, BankChargeReceipt

MODULE = 'costing'
TOPIC = 'costing.bank_charge_saved.v1'
SAVED_ACTION = 'costing.bank_charge.saved'
PLUGIN_SAVED_ACTION = 'plugin.costing.bank_charge.saved'
RECEIPT_ACTION = 'bank_charge.draft.save'
SUBMIT_RECEIPT_ACTION = 'bank_charge.submit'
DECIDE_RECEIPT_ACTION = 'bank_charge.decide'
SUBMIT_ACTION = 'costing.bank_charge.submitted'
APPROVED_ACTION = 'costing.bank_charge.approved'
REJECTED_ACTION = 'costing.bank_charge.rejected'
COST_POSTED_ACTION = 'costing.bank_charge.cost_posted'
COST_EVENT_TYPE = 'costing.bank_charge.cost'
COST_ALGORITHM = 'bank_fee_cost_v1'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor'}
PERIOD_RE = re.compile(r'^[0-9]{4}-(0[1-9]|1[0-2])$')
EVIDENCE_KINDS = {'attachment', 'acceptance', 'customer_confirmation', 'bank_proof', 'invoice',
                  'approval'}
STATUS_LABELS = {'draft': '未批准草稿', 'pending': '待批准', 'approved': '已批准',
                 'rejected': '已驳回', 'superseded': '已取代'}
UNSUPPORTED_STATUS_MESSAGE = ('本片只登记手续费草稿，不提供提交/批准/驳回通道；'
                              '批准状态属后续手续费审批与成本过账薄片')


# ---------- guards ----------
def _fail(message):
    raise ValidationError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _fresh(member, write):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('只有公司负责人或项目财务可以登记手续费草稿' if write
                               else '无权查看银行手续费草稿')
    if not is_enabled(member.organization, MODULE):
        raise PermissionDenied('月度成本模块已停用，不能办理银行手续费草稿')
    return member


def _reader(member):
    """Read path for owner/finance/auditor; disabled modules keep history readable."""
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权查看银行手续费草稿')
    return member


def _locked(member):
    member = _fresh(member, True)
    lock_company(member)
    # Fresh re-read after the serialization point so revocation/disable cannot slip through.
    return _fresh(member, True)


def _text(value, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        _fail('请填写完整依据或原因，并遵守字段长度')
    return value.strip()


def _date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _fail('日期无效')


def _period(value):
    if not isinstance(value, str) or PERIOD_RE.fullmatch(value) is None:
        _fail('月份须为YYYY-MM')
    return value


def _token(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail('请求标识无效，请刷新表单')


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _money(value):
    if type(value) is not int or not 0 < value <= BANK_CHARGE_MAX_CENTS:
        _fail('手续费须为1至100亿元的整数分')
    return value


def _tax(value):
    if type(value) is not int or not 0 <= value <= BANK_CHARGE_MAX_CENTS:
        _fail('税额须为0至100亿元的整数分或留空')
    return value


def _version(value):
    if type(value) is not int or value < 1:
        _fail('版本号无效，请刷新后重试')
    return value


def _identifier(value, message='记录标识无效'):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail(message)


def _evidence(value):
    """Canonical EvidenceRef rows; a UI free-text basis becomes a deterministic attachment ref."""
    if value is None:
        items = []
    elif isinstance(value, str):
        items = [{'reference': value}]
    elif isinstance(value, list):
        items = value
    else:
        _fail('依据格式无效')
    refs = []
    for item in items:
        if not isinstance(item, dict):
            _fail('依据格式无效')
        if set(item) == {'reference'}:
            text = item['reference']
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2000:
                _fail('请填写1至2000字的真实依据')
            text = text.strip()
            refs.append({'evidence_id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                       'costing-fee-evidence:' + text)),
                         'sha256': hashlib.sha256(text.encode()).hexdigest(), 'kind': 'attachment'})
            continue
        if set(item) - {'evidence_id', 'sha256', 'kind'} or 'evidence_id' not in item or 'sha256' not in item:
            _fail('依据引用无效')
        digest = item['sha256']
        if not isinstance(digest, str) or len(digest) != 64:
            _fail('依据摘要无效')
        if item.get('kind') is not None and item['kind'] not in EVIDENCE_KINDS:
            _fail('依据类型无效')
        ref = {'evidence_id': str(item['evidence_id']), 'sha256': digest}
        if item.get('kind') is not None:
            ref['kind'] = item['kind']
        refs.append(ref)
    if not refs:
        _fail('请登记至少一条真实依据')
    if len(refs) > 1000:
        _fail('依据数量超过上限')
    return refs


def _cash_ref(value):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('现金流水来源无效')
    if value['namespace'] != 'cash' or value['kind'] != 'cash_transaction':
        _fail('现金流水来源无效')
    if type(value['version']) is not int or value['version'] < 1:
        _fail('现金流水来源版本无效')
    return {'namespace': 'cash', 'kind': 'cash_transaction',
            'id': str(_identifier(value['id'], '现金流水不存在或无权访问')),
            'version': value['version']}


def _project(member, value):
    try:
        project_id = int(value)
    except (TypeError, ValueError):
        _fail('项目标识无效')
    project = Project.objects.filter(organization=member.organization, pk=project_id).first()
    if project is None:
        _fail('项目不存在或无权访问')
    return project


def _counterparty(member, value):
    if value is None or value == '':
        return None
    party_id = _identifier(value, '往来方标识无效')
    if not Counterparty.objects.filter(organization=member.organization, pk=party_id).exists():
        _fail('往来方不存在或无权访问')
    return party_id


def _fee_period(member, cash_day, occurred_on, posting_period):
    cash_month = cash_day.strftime('%Y-%m')
    occurred = cash_day if occurred_on is None else _date(occurred_on)
    if occurred.strftime('%Y-%m') != cash_month:
        _fail('手续费发生日必须落在银行流水所属月份')
    period = cash_month if posting_period is None else _period(posting_period)
    if period != cash_month:
        _fail('手续费过账期间必须为银行流水所属月份')
    if occurred > timezone.localdate():
        _fail('手续费发生日不能在未来')
    if member.organization.locked_through and occurred <= member.organization.locked_through:
        _fail('手续费发生日所在期间已锁账，不能补记')
    existing = AccountingPeriod.objects.filter(organization=member.organization,
                                               period=period).first()
    if existing and (existing.status != 'active' or existing.closed_through is not None):
        _fail('手续费过账期间已关闭')
    return occurred, period


def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _token(request_token)
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = BankChargeReceipt.objects.filter(organization=member.organization,
                                           action=action, request_token=token).first()
    if row:
        if row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        try:
            charge = BankCharge.objects.get(organization=member.organization,
                                            pk=row.result_ref['id'])
        except (KeyError, TypeError, ValueError, BankCharge.DoesNotExist):
            raise PermissionDenied('原手续费草稿不存在或无权访问')
        return token, digest, charge
    return token, digest, None


def _record(member, action, token, digest, charge):
    BankChargeReceipt.objects.create(organization=member.organization, action=action,
                                     request_token=token, input_digest=digest,
                                     result_ref=charge.source_ref,
                                     result_snapshot={'source': charge.source_ref,
                                                      'status': charge.status,
                                                      'version': charge.version},
                                     created_by=member)


def _preview(action, charge=None, *, persisted, **values):
    return {'dry_run': True, 'action': action,
            'charge_id': str(charge.pk) if persisted else None,
            'cash_transaction_id': str(charge.cash_transaction_id) if charge is not None else None,
            'version': charge.version if persisted else 1, **values}


def _event(member, audit_row, charge):
    event_id = uuid.uuid4()
    ref = charge.source_ref
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=audit_row, topic=TOPIC,
        data={'event_id': str(event_id), 'topic': TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [ref], 'posting_event_refs': []}})


# ---------- projections ----------
def _projection(charge):
    return {'id': str(charge.pk), 'source_ref': charge.source_ref,
            'cash_transaction_id': str(charge.cash_transaction_id),
            'cash_transaction_ref': charge.cash_transaction_ref,
            'gross_cents': charge.gross_cents, 'tax_cents': charge.tax_cents,
            'existing_cost_ref': charge.existing_cost_ref,
            'counterparty_id': str(charge.counterparty_id) if charge.counterparty_id else None,
            'occurred_on': charge.occurred_on, 'posting_period': charge.posting_period,
            'scope': charge.scope, 'scope_kind': charge.scope_kind,
            'scope_id': charge.scope_id, 'fee_bearer': charge.fee_bearer,
            'fee_bearer_display': charge.get_fee_bearer_display(),
            'evidence': charge.evidence, 'status': charge.status,
            'status_display': charge.get_status_display(), 'version': charge.version,
            'created_at': charge.created_at,
            'created_by': str(charge.created_by_id) if charge.created_by_id else None}


def bank_charge_reader(member):
    return _reader(member)


def _get_charge(member, charge_id):
    try:
        return BankCharge.objects.select_related('created_by').get(
            organization=member.organization, pk=_identifier(charge_id, '手续费草稿标识无效'))
    except BankCharge.DoesNotExist:
        raise PermissionDenied('手续费草稿不存在或无权访问')


def get_bank_charge(member, charge_id):
    return _projection(_get_charge(_reader(member), charge_id))


def list_bank_charges(member, *, cash_transaction_id=None):
    member = _reader(member)
    rows = BankCharge.objects.select_related('created_by').filter(
        organization=member.organization).order_by('-created_at', 'id')
    if cash_transaction_id is not None:
        rows = rows.filter(cash_transaction_id=_identifier(cash_transaction_id,
                                                           '现金流水标识无效'))
    return [_projection(row) for row in rows]


def net_fee_reconciliation(member, cash_transaction_id):
    """Read-only net/fee reconciliation; unapproved fees never count into reconciliation."""
    member = _reader(member)
    transaction_id = _identifier(cash_transaction_id, '现金流水不存在或无权访问')
    snapshot = cash_transaction_snapshot(member, transaction_id)
    if snapshot['status'] != 'posted' or snapshot['direction'] != 'in':
        _fail('仅已过账收款流水可做净额/手续费对账')
    charge = BankCharge.objects.filter(organization=member.organization,
                                       cash_transaction_id=transaction_id).first()
    draft = charge.gross_cents if charge is not None else 0
    posted_net = snapshot['gross_cents']
    return {'cash_transaction_id': str(transaction_id),
            'cash_transaction_ref': snapshot['transaction_ref'],
            'posted_net_cents': posted_net,
            'approved_fee_cents': 0,
            'implied_gross_cents': posted_net,
            'draft_fee_cents': draft,
            'charge_status': charge.status if charge is not None else 'none',
            'charge_ref': charge.source_ref if charge is not None else None,
            'reconciled': False}


# ---------- owner service ----------
@transaction.atomic
def save_bank_charge_draft(member, *, cash_transaction_ref, gross_cents, tax_cents=None,
                           counterparty_id=None, scope_kind='project', scope_id, evidence,
                           occurred_on=None, posting_period=None, request_token,
                           expected_version=None, dry_run=True, existing_cost_ref=None,
                           fee_bearer='company', status=None):
    """Create or revise the one draft fee per original bank transaction.

    Only draft is produced: explicit submit/approve/reject requests and any non-company
    bearer or existing-cost reuse are rejected with the deferred-slice message.
    """
    _dry(dry_run)
    if status is not None:
        _fail(UNSUPPORTED_STATUS_MESSAGE)
    if fee_bearer != 'company':
        _fail('本片仅支持公司承担手续费；交易对手承担或承担方未确认属后续薄片')
    if existing_cost_ref is not None:
        _fail('本片不支持既有成本复用；existing_cost_ref 必须为 null')
    if scope_kind != 'project':
        _fail('本片手续费范围仅支持项目（scope.kind=project）')
    member = _locked(member)
    ref = _cash_ref(cash_transaction_ref)
    gross = _money(gross_cents)
    tax = None if tax_cents is None else _tax(tax_cents)
    project = _project(member, scope_id)
    party = _counterparty(member, counterparty_id)
    evidence_refs = _evidence(evidence)
    cash = posted_cash_for_fee(member, ref, for_write=True)
    if gross > cash['gross_cents']:
        _fail('手续费不得超过该现金流水已过账金额')
    occurred, period = _fee_period(member, cash['transaction_date'],
                                   occurred_on if occurred_on not in (None, '') else None,
                                   posting_period if posting_period not in (None, '') else None)
    payload = {'cash': ref, 'gross': gross, 'tax': tax,
               'counterparty': str(party) if party else None,
               'scope': [scope_kind, str(project.pk)], 'occurred': occurred.isoformat(),
               'period': period, 'evidence': evidence_refs, 'expected': expected_version,
               'bearer': 'company', 'existing_cost': None}
    token, digest, replay = _receipt(member, RECEIPT_ACTION, request_token, payload, dry_run)
    if replay is not None:
        if dry_run:
            return _preview(RECEIPT_ACTION, replay, persisted=True,
                            gross_cents=replay.gross_cents, replayed=True)
        return replay
    charge = BankCharge.objects.select_for_update().filter(
        organization=member.organization,
        cash_transaction_id=uuid.UUID(ref['id'])).first()
    existing = charge is not None
    if existing:
        if expected_version is None:
            _fail('该现金流水已有手续费草稿，请从草稿改稿')
        _version(expected_version)
        if expected_version != charge.version:
            _fail('手续费草稿已变更，请刷新后重试')
    else:
        if expected_version is not None:
            _fail('手续费草稿尚不存在，不能指定版本；请新建草稿')
        charge = BankCharge(organization=member.organization,
                            cash_transaction_id=uuid.UUID(ref['id']),
                            cash_transaction_ref=ref, created_by=member, status='draft',
                            version=1)
    if dry_run:
        return _preview(RECEIPT_ACTION, charge, persisted=existing, gross_cents=gross)
    if existing:
        charge.version += 1
    charge.gross_cents = gross
    charge.tax_cents = tax
    charge.existing_cost_ref = None
    charge.counterparty_id = party
    charge.occurred_on = occurred
    charge.posting_period = period
    charge.scope_kind = scope_kind
    charge.scope_id = str(project.pk)
    charge.fee_bearer = 'company'
    charge.evidence = evidence_refs
    charge.status = 'draft'
    charge.save()
    saved_ref = charge.source_ref
    audit(member, SAVED_ACTION, charge, {'charge_ref': saved_ref, 'gross_cents': gross,
                                         'tax_cents': tax, 'cash_ref': ref,
                                         'scope': charge.scope})
    event_source = audit(member, PLUGIN_SAVED_ACTION, charge, {'charge_ref': saved_ref})
    _event(member, event_source, charge)
    _record(member, RECEIPT_ACTION, token, digest, charge)
    return charge


# ---------- PH-250 submit / independent decision / one-time cost ----------
def _check_period_open(member, charge):
    occurred = charge.occurred_on
    if occurred > timezone.localdate():
        _fail('手续费发生日不能在未来，不能提交或审批')
    if member.organization.locked_through and occurred <= member.organization.locked_through:
        _fail('手续费发生日所在期间已锁账，不能提交或审批')
    existing = AccountingPeriod.objects.filter(organization=member.organization,
                                               period=charge.posting_period).first()
    if existing and (existing.status != 'active' or existing.closed_through is not None):
        _fail('手续费过账期间已关闭，不能提交或审批')


def _charge_ref_shape(source_ref):
    if not isinstance(source_ref, dict) or set(source_ref) != {
            'namespace', 'kind', 'id', 'version'}:
        _fail('手续费审批来源无效')
    if source_ref['namespace'] != 'costing' or source_ref['kind'] != 'bank_charge':
        _fail('手续费审批来源无效')
    if type(source_ref['version']) is not int or source_ref['version'] < 1:
        _fail('手续费审批来源版本无效')
    return {'namespace': 'costing', 'kind': 'bank_charge',
            'id': str(_identifier(source_ref['id'], '手续费草稿不存在或无权访问')),
            'version': source_ref['version']}


def _locked_charge(member, charge_id):
    # The approval-chain FKs are nullable, so select_related() would build an
    # outer join and PostgreSQL rejects FOR UPDATE on the nullable side. Lock only
    # the BankCharge row itself and fetch the related members afterwards.
    try:
        return BankCharge.objects.select_for_update(of=('self',)).select_related(
            'created_by', 'submitted_by', 'approved_by').get(
            organization=member.organization,
            pk=_identifier(charge_id, '手续费草稿不存在或无权访问'))
    except BankCharge.DoesNotExist:
        raise PermissionDenied('手续费草稿不存在或无权访问')


def _post_fee_cost(member, charge):
    """Exactly one company cost fact set per approved BankCharge; caller holds the row lock."""
    coverage_key = f'bank_charge:{charge.pk}'
    event_id = uuid.uuid4()
    claim = SourceConsumption(
        organization=member.organization, source=charge.source_ref, source_id=charge.pk,
        consumer='cost', coverage_key=coverage_key, amount_cents=charge.gross_cents,
        event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id),
                   'version': 1})
    try:
        claim.save()
    except IntegrityError:
        _fail('该手续费成本事实已存在；批准后不可重复过账')
    event = AccountingEvent(
        id=event_id, organization=member.organization, source=charge.source_ref,
        event_type=COST_EVENT_TYPE, occurred_on=charge.occurred_on,
        posting_period=charge.posting_period, algorithm_version=COST_ALGORITHM,
        claim=claim, claim_effect_cents=charge.gross_cents)
    event.save()
    posting = LedgerPosting.objects.create(
        organization=member.organization, accounting_event=event, book='cost',
        amount_cents=charge.gross_cents, scope_kind='project', scope_id=charge.scope_id,
        origin='current', line_index=1)
    audit(member, COST_POSTED_ACTION, charge, {
        'charge_ref': charge.source_ref, 'accounting_event_id': str(event.pk),
        'ledger_posting_id': str(posting.pk), 'amount_cents': charge.gross_cents})
    return claim, event, posting


@transaction.atomic
def submit_bank_charge(member, charge_id, *, request_token, dry_run=True, expected_version=None):
    """draft -> pending; the submitter is recorded and facts become immutable."""
    _dry(dry_run)
    member = _locked(member)
    charge = _locked_charge(member, charge_id)
    _check_period_open(member, charge)
    if expected_version is not None and (type(expected_version) is not int
                                         or expected_version != charge.version):
        _fail('手续费草稿版本已变化，请刷新后重试')
    payload = {'charge': str(charge.pk), 'version': charge.version, 'decision': 'submit'}
    token, digest, replay = _receipt(member, SUBMIT_RECEIPT_ACTION, request_token, payload, dry_run)
    if replay is not None:
        return replay
    if charge.status != 'draft':
        _fail('仅未提交草稿可以提交审批')
    if dry_run:
        return _preview('bank_charge.submit', charge, persisted=True)
    charge.status = 'pending'
    charge.submitted_by, charge.submitted_at = member, timezone.now()
    charge.save(update_fields=['status', 'submitted_by', 'submitted_at'])
    audit(member, SUBMIT_ACTION, charge, {'charge_ref': charge.source_ref})
    _record(member, SUBMIT_RECEIPT_ACTION, token, digest, charge)
    return charge


@transaction.atomic
def decide_bank_charge(member, charge_id, *, decision, note='', request_token, dry_run=True,
                       expected_version=None):
    """Independent approve/reject of a pending charge; approval posts the one-time cost."""
    _dry(dry_run)
    member = _locked(member)
    if decision not in {'approve', 'reject', 'return'}:
        _fail('手续费审批结论无效；本片支持提交、批准与驳回')
    note = '' if note is None else _text(note, 1000) if str(note).strip() else ''
    if decision in {'reject', 'return'} and not note:
        _fail('驳回手续费必须填写原因')
    charge = _locked_charge(member, charge_id)
    _check_period_open(member, charge)
    if expected_version is not None and (type(expected_version) is not int
                                         or expected_version != charge.version):
        _fail('手续费草稿版本已变化，请刷新后重试')
    payload = {'charge': str(charge.pk), 'version': charge.version, 'decision': decision,
               'note': note}
    token, digest, replay = _receipt(member, DECIDE_RECEIPT_ACTION, request_token, payload, dry_run)
    if replay is not None:
        return replay
    if charge.status != 'pending':
        _fail('仅待批准手续费可以审批；草稿需先提交')
    if member.pk in {charge.created_by_id, charge.submitted_by_id}:
        _fail('创建人或提交人不能审批自己的手续费记录，请由另一位负责人处理')
    if dry_run:
        return _preview('bank_charge.decide', charge, persisted=True, decision=decision)
    approved = decision == 'approve'
    charge.status = 'approved' if approved else 'rejected'
    charge.approved_by, charge.approved_at = member, timezone.now()
    charge.reject_reason = '' if approved else note
    charge.save(update_fields=['status', 'approved_by', 'approved_at', 'reject_reason'])
    if approved:
        _post_fee_cost(member, charge)
        audit(member, APPROVED_ACTION, charge, {'charge_ref': charge.source_ref,
                                                'approved_by': str(member.pk)})
    else:
        audit(member, REJECTED_ACTION, charge, {'charge_ref': charge.source_ref,
                                                'approved_by': str(member.pk), 'reason': note})
    _record(member, DECIDE_RECEIPT_ACTION, token, digest, charge)
    return charge


def bank_charge_cost_snapshot(member, charge_id):
    """Read-only approval/cost projection for one charge."""
    member = _reader(member)
    charge = _get_charge(member, charge_id)
    claim = SourceConsumption.objects.filter(
        organization_id=charge.organization_id,
        coverage_key=f'bank_charge:{charge.pk}').first()
    event = AccountingEvent.objects.filter(claim=claim).first() if claim is not None else None
    posting = LedgerPosting.objects.filter(accounting_event=event).first() if event is not None else None
    return {'charge': _projection(charge),
            'submitted_by': str(charge.submitted_by_id) if charge.submitted_by_id else None,
            'submitted_at': charge.submitted_at,
            'approved_by': str(charge.approved_by_id) if charge.approved_by_id else None,
            'approved_at': charge.approved_at,
            'reject_reason': charge.reject_reason,
            'cost_claim_ref': claim.event_ref if claim is not None else None,
            'cost_event_ref': ({'namespace': 'core', 'kind': 'accounting_event',
                                'id': str(event.pk), 'version': 1} if event is not None else None),
            'cost_posting_ref': ({'namespace': 'core', 'kind': 'ledger_posting',
                                  'id': str(posting.pk), 'version': posting.version}
                                 if posting is not None else None),
            'cost_amount_cents': posting.amount_cents if posting is not None else None}
