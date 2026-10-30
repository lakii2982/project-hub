"""Bank cash transaction drafts and party matching; no posting, classification or balance.

Cash facts never create accounting events, ledger postings, settlement/open items or
source consumptions. Only generic `cash.*` audit rows and their non-canonical outbox
events are produced.
"""
from datetime import date
from decimal import Decimal
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import AccountingPeriod, Employee
from core.services import audit

from .funding_services import (_amount, _business_date, _dry, _evidence, _fail, _fresh,
                               _get_account, _identifier, _locked, _optional_identifier,
                               _receipt, _receipt_row, _record, _text, _version)
from .models import CashPartyMatch, CashTransaction, FundingAccount

PAYEE_KINDS = {'unidentified', 'counterparty', 'own_account'}
MATCH_PAYEE_KINDS = {'counterparty', 'employee', 'own_account'}
MATCH_ACTIONS = {'identify', 'reverse'}
IDENTIFIED_LABELS = {'counterparty': '往来方', 'employee': '员工', 'own_account': '自有账户'}


def _party(member, party_id):
    from pmo_plugins.contracts.party_services import get_party
    try:
        return get_party(member, party_id)
    except (ValidationError, PermissionDenied):
        raise PermissionDenied('往来方不存在或无权访问')


def _party_for_day(member, party_id, day):
    """Named read verifies tenant/existence; cash adds the status/effective-date gate."""
    party = _party(member, party_id)
    if party.status != 'active':
        _fail('往来方已停用，不能作为收款方或匹配对象')
    if party.inactive_on is not None and party.inactive_on <= day:
        _fail('往来方在业务日已失效，不能作为收款方或匹配对象')
    if party.active_from is not None and day < party.active_from:
        _fail('往来方在业务日尚未生效，不能作为收款方或匹配对象')
    return party


def _period(member, day):
    if type(day) is not date:
        _fail('银行流水日期须为有效的年月日')
    if day > timezone.localdate():
        _fail('银行流水日期不能在未来')
    if member.organization.locked_through and day <= member.organization.locked_through:
        _fail('银行流水日期所在期间已锁账，不能补记')
    row = AccountingPeriod.objects.filter(organization=member.organization,
                                          period=day.strftime('%Y-%m')).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('银行流水日期所在会计期间已关闭')


def _external_key(value):
    return _text(value, 200, '外部流水编号须为1至200个字符')


def _payee_shape(value):
    """Structural payee normalization only; live party state is checked for new writes."""
    if not isinstance(value, dict) or 'kind' not in value:
        _fail('收款方信息无效')
    kind = value['kind']
    if kind not in PAYEE_KINDS:
        _fail('收款方类型无效；员工收款方请在往来方匹配中识别')
    if kind == 'unidentified':
        if set(value) - {'kind', 'bank_label'}:
            _fail('未识别收款方只能包含银行摘要标签')
        label = value.get('bank_label')
        if label is not None:
            if not isinstance(label, str) or len(label.strip()) > 200:
                _fail('银行摘要标签须不超过200字')
            label = label.strip() or None
        return {'kind': 'unidentified', 'bank_label': label}, None
    if set(value) != {'kind', 'id'}:
        _fail('收款方只接受 kind 与 id')
    if kind == 'counterparty':
        party_id = _identifier(value['id'], '往来方标识无效')
        return {'kind': 'counterparty', 'id': str(party_id)}, party_id
    account_id = _identifier(value['id'], '自有账户标识无效')
    return {'kind': 'own_account', 'id': str(account_id)}, None


def _payee_state(member, payee, day):
    """Live state gate for a new save only; replays must resolve before this point."""
    kind = payee['kind']
    if kind == 'counterparty':
        _party_for_day(member, _identifier(payee['id'], '往来方标识无效'), day)
    elif kind == 'own_account':
        if not _active_funding_account_exists(member, _identifier(payee['id'], '自有账户标识无效')):
            _fail('自有账户须为本公司已启用的银行账户')


def _active_funding_account_exists(member, account_id):
    return FundingAccount.objects.filter(organization=member.organization, pk=account_id,
                                         status='active').exists()


def _identified_payee_shape(value):
    """Structural identification normalization; live state is checked for new matches only."""
    if not isinstance(value, dict) or set(value) != {'kind', 'id'}:
        _fail('识别收款方须为 kind 与 id')
    kind = value['kind']
    if kind not in MATCH_PAYEE_KINDS:
        _fail('识别收款方类型无效')
    if kind == 'counterparty':
        return {'kind': 'counterparty', 'id': str(_identifier(value['id'], '往来方标识无效'))}
    if kind == 'employee':
        raw = value['id']
        if type(raw) is bool:
            _fail('员工标识无效')
        try:
            return {'kind': 'employee', 'id': int(raw)}
        except (TypeError, ValueError):
            _fail('员工标识无效')
    return {'kind': 'own_account', 'id': str(_identifier(value['id'], '自有账户标识无效'))}


def _identified_payee_state(member, payee, day):
    kind = payee['kind']
    if kind == 'counterparty':
        _party_for_day(member, _identifier(payee['id'], '往来方标识无效'), day)
    elif kind == 'employee':
        if not Employee.objects.filter(organization=member.organization, pk=payee['id'],
                                       active=True).exists():
            _fail('员工不存在、已停用或不属于本公司')
    else:
        if not _active_funding_account_exists(member, _identifier(payee['id'], '自有账户标识无效')):
            _fail('自有账户须为本公司已启用的银行账户')


def _match_ref(value):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('原匹配引用无效')
    if value['namespace'] != 'cash' or value['kind'] != 'cash_party_match':
        _fail('原匹配引用无效')
    if type(value['version']) is not int or value['version'] < 1:
        _fail('原匹配引用无效')
    return {'namespace': 'cash', 'kind': 'cash_party_match',
            'id': str(_identifier(value['id'], '原匹配引用无效')), 'version': value['version']}


def _get_transaction(member, transaction_id, *, lock=False):
    rows = CashTransaction.objects.select_related('funding_account')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        return rows.get(organization=member.organization,
                        pk=_identifier(transaction_id, '银行流水不存在或无权访问'))
    except CashTransaction.DoesNotExist:
        raise PermissionDenied('银行流水不存在或无权访问')


def _get_match(member, match_id):
    try:
        return CashPartyMatch.objects.select_related('cash_transaction').get(
            organization=member.organization, pk=_identifier(match_id, '原匹配不存在或无权访问'))
    except CashPartyMatch.DoesNotExist:
        raise PermissionDenied('原匹配不存在或无权访问')


def _same_input(row, *, day, direction, gross, payee, counterparty_id, evidence):
    return (row.transaction_date == day and row.direction == direction
            and row.gross_cents == gross and row.payee == payee
            and row.counterparty_id == counterparty_id and row.evidence == evidence)


def _payee_display(payee):
    if not isinstance(payee, dict):
        return '—'
    kind = payee.get('kind')
    if kind == 'unidentified':
        label = payee.get('bank_label')
        return '未识别' + (f' · {label}' if label else '')
    return IDENTIFIED_LABELS.get(kind, str(kind)) + ' · ' + str(payee.get('id', ''))


@transaction.atomic
def save_cash_transaction(member, *, funding_account_id, transaction_date, direction, gross_cents,
                          external_source_key, payee, evidence=None, request_token,
                          transaction_id=None, expected_version=None, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    account_id = _identifier(funding_account_id, '银行账户标识无效')
    day = _business_date(transaction_date)
    if not isinstance(direction, str) or direction not in {'in', 'out'}:
        _fail('银行流水方向须为收入或支出')
    gross = _amount(gross_cents)
    key = _external_key(external_source_key)
    payee_value, counterparty_id = _payee_shape(payee)
    evidence_refs = _evidence(evidence, required=True)
    transaction_id = _optional_identifier(transaction_id, '银行流水标识无效')
    payload = {'funding_account_id': str(account_id), 'transaction_date': day.isoformat(),
               'direction': direction, 'gross_cents': gross, 'external_source_key': key,
               'payee': payee_value, 'evidence': evidence_refs,
               'transaction_id': str(transaction_id) if transaction_id else None,
               'expected_version': expected_version}
    token, digest, receipt = _receipt(member, 'cash_transaction.save', request_token, payload,
                                      dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, CashTransaction)
    # Replay resolution must not depend on mutable party/account state: a later
    # counterparty/employee/account deactivation cannot invalidate an existing fact.
    duplicates = CashTransaction.objects.filter(organization=member.organization,
                                                funding_account_id=account_id,
                                                external_source_key=key)
    if transaction_id:
        duplicates = duplicates.exclude(pk=transaction_id)
    existing = duplicates.first()
    if existing is not None:
        if transaction_id is None and _same_input(
                existing, day=day, direction=direction, gross=gross, payee=payee_value,
                counterparty_id=counterparty_id, evidence=evidence_refs):
            return existing
        _fail('同一外部流水编号已用于不同内容，请核对后使用新的外部编号')
    _payee_state(member, payee_value, day)
    account = _get_account(member, account_id, lock=True)
    if account.status != 'active':
        _fail('只能在已启用的银行账户登记流水')
    _period(member, day)
    if transaction_id:
        row = _get_transaction(member, transaction_id, lock=True)
        if row.version != _version(expected_version):
            _fail('银行流水版本已变化，请刷新后重试')
        row.version += 1
    else:
        if expected_version is not None:
            _fail('新建银行流水不接受已有版本号')
        row = CashTransaction(organization=member.organization, created_by=member,
                              status='draft', version=1)
    row.funding_account, row.transaction_date, row.direction = account, day, direction
    row.gross_cents, row.external_source_key = gross, key
    row.payee, row.counterparty_id, row.evidence = payee_value, counterparty_id, evidence_refs
    if not dry_run:
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            _fail('同一外部流水编号已用于不同内容，请核对后使用新的外部编号')
        audit(member, 'cash.cash_transaction.saved', row,
              {'funding_account_id': str(account.pk), 'transaction_date': day.isoformat(),
               'direction': direction, 'gross_cents': gross, 'external_source_key': key,
               'payee_kind': payee_value['kind']})
        _record(member, 'cash_transaction.save', token, digest, row.transaction_ref)
    return row


@transaction.atomic
def match_party(member, transaction_id, *, action, identified_payee=None, match_ref=None,
                reason=None, evidence=None, request_token, expected_version, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    if not isinstance(action, str) or action not in MATCH_ACTIONS:
        _fail('往来方匹配动作无效')
    transaction_id = _identifier(transaction_id, '银行流水标识无效')
    expected = _version(expected_version)
    if action == 'identify':
        if match_ref is not None:
            _fail('识别匹配不接受原匹配引用')
        if reason not in (None, ''):
            _fail('识别匹配不记录原因')
        payee_value = _identified_payee_shape(identified_payee)
        reason_value, match_ref_value = None, None
    else:
        payee_value = None
        reason_value = _text(reason, 1000, '反向匹配必须填写1至1000字原因')
        match_ref_value = _match_ref(match_ref)
    evidence_refs = _evidence(evidence, required=True)
    payload = {'transaction_id': str(transaction_id), 'action': action,
               'identified_payee': payee_value, 'match_ref': match_ref_value,
               'reason': reason_value, 'evidence': evidence_refs,
               'expected_version': expected}
    token, digest, receipt = _receipt(member, 'cash_party_match.' + action, request_token,
                                      payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, CashPartyMatch)
    row = _get_transaction(member, transaction_id, lock=True)
    if row.status != 'draft':
        _fail('只能对草稿银行流水办理往来方匹配')
    if row.version != expected:
        _fail('银行流水版本已变化，请刷新后重试')
    if action == 'identify':
        _identified_payee_state(member, payee_value, row.transaction_date)
        if CashPartyMatch.objects.filter(cash_transaction=row, action='identify',
                                         reversals__isnull=True).exists():
            _fail('该银行流水已有未被反向的识别匹配，请先反向后再重新识别')
    original = None
    if action == 'reverse':
        original = _get_match(member, match_ref_value['id'])
        if original.cash_transaction_id != row.pk:
            _fail('原匹配不属于该银行流水')
        if original.action != 'identify' or original.version != match_ref_value['version']:
            _fail('只能反向该流水当前版本的识别匹配')
        if original.reversals.exists():
            _fail('该识别匹配已被反向，不能重复反向')
    match = CashPartyMatch(organization=member.organization, cash_transaction=row, action=action,
                           identified_payee=payee_value, original_match=original,
                           reason=reason_value, evidence=evidence_refs, version=1,
                           created_by=member)
    if not dry_run:
        try:
            with transaction.atomic():
                match.save()
        except IntegrityError:
            _fail('该识别匹配已被反向，不能重复反向')
        audit(member, 'cash.cash_party_match.' + ('identified' if action == 'identify'
                                                  else 'reversed'),
              match, {'transaction_id': str(row.pk), 'action': action,
                      'identified_payee': payee_value, 'original_match_id':
                      str(original.pk) if original else None})
        _record(member, 'cash_party_match.' + action, token, digest, match.match_ref)
    return match


def _match_projection(row):
    return {
        'id': str(row.pk), 'match_ref': row.match_ref, 'action': row.action,
        'action_display': row.get_action_display(),
        'identified_payee': row.identified_payee,
        'identified_payee_display': ('—' if not row.identified_payee else
                                     IDENTIFIED_LABELS.get(row.identified_payee.get('kind'), '')
                                     + ' · ' + str(row.identified_payee.get('id', ''))),
        'original_match_ref': row.original_match.match_ref if row.original_match_id else None,
        'reason': row.reason, 'evidence': row.evidence, 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
    }


def _transaction_projection(row):
    return {
        'id': str(row.pk), 'transaction_ref': row.transaction_ref,
        'organization_id': str(row.organization_id),
        'funding_account_id': str(row.funding_account_id),
        'funding_account_ref': row.funding_account.account_ref,
        'funding_account_name': row.funding_account.name,
        'transaction_date': row.transaction_date.isoformat(),
        'direction': row.direction,
        'direction_display': {'in': '收入', 'out': '支出'}.get(row.direction, row.direction),
        'gross_cents': row.gross_cents,
        'gross_display': format(Decimal(row.gross_cents) / 100, '.2f'),
        'external_source_key': row.external_source_key,
        'payee': row.payee, 'payee_display': _payee_display(row.payee),
        'counterparty_id': str(row.counterparty_id) if row.counterparty_id else None,
        'evidence': row.evidence, 'status': row.status,
        'status_display': row.get_status_display(), 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
        'matches': [_match_projection(match) for match in row.matches.order_by(
            'created_at', 'id')],
    }


def cash_transaction_snapshot(member, transaction_id):
    """Private read projection only: no canonical allocated_cents/available_cents."""
    member = _fresh(member, check_module=False)
    return _transaction_projection(_get_transaction(member, transaction_id))


def cash_transaction_list(member, *, as_of=None, status=None):
    member = _fresh(member, check_module=False)
    rows = CashTransaction.objects.filter(organization=member.organization).select_related(
        'funding_account')
    if as_of is not None:
        rows = rows.filter(transaction_date__lte=_business_date(as_of))
    if status is not None:
        if not isinstance(status, str) or status not in {'draft'}:
            _fail('银行流水状态筛选无效')
        rows = rows.filter(status=status)
    rows = rows.prefetch_related('matches').order_by('-transaction_date', '-created_at', 'id')
    return [_transaction_projection(row) for row in rows]
