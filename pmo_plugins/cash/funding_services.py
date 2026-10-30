"""Funding account drafts, owner activation and private read projections.

No cash balance, ledger posting, accounting event or settlement fact is written here.
"""
from datetime import date
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.access import require_member
from core.models import Membership
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import MAX_CENTS, CashReceipt, FundingAccount

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor'}
CASH_MODULE = 'cash'
EVIDENCE_KINDS = {'attachment', 'acceptance', 'customer_confirmation', 'bank_proof', 'invoice',
                  'approval'}


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False, check_module=True):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if write and member.role not in WRITE_ROLES:
        raise PermissionDenied('只有公司负责人或项目财务可以办理现金事实登记')
    if not write and member.role not in READ_ROLES:
        raise PermissionDenied('无权查看公司银行事实')
    if check_module and not is_enabled(member.organization, CASH_MODULE):
        raise PermissionDenied('现金事实模块已停用')
    return member


def _locked(member):
    member = _fresh(member, write=True)
    lock_company(member)
    # Fresh re-read after the serialization point so revocation/disable cannot slip through.
    return _fresh(member, write=True)


def _identifier(value, message='记录或请求标识无效'):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail(message)


def _optional_identifier(value, message='记录标识无效'):
    return None if value in (None, '') else _identifier(value, message)


def _version(value):
    if type(value) is not int or value < 1:
        _fail('记录版本无效')
    return value


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _amount(value):
    if type(value) is not int or not 0 < value <= MAX_CENTS:
        _fail('金额须为大于0且不超过100亿元的整数分')
    return value


def _text(value, maximum, message, required=True):
    if required and (not isinstance(value, str) or not value.strip()):
        _fail(message)
    if not isinstance(value, str):
        _fail(message)
    value = value.strip()
    if len(value) > maximum:
        _fail(message)
    return value


def _business_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _fail('业务日期须为有效的年月日')


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _evidence(value, *, required=True):
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
            refs.append({'evidence_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'cash-evidence:' + text)),
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
    if required and not refs:
        _fail('请登记至少一条真实依据')
    if len(refs) > 1000:
        _fail('依据数量超过上限')
    return refs


def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token, '请求标识无效')
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = CashReceipt.objects.filter(organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, digest, row
    return token, digest, None


def _receipt_row(member, receipt, model):
    result = receipt.result or {}
    try:
        return model.objects.get(organization=member.organization, pk=result['id'])
    except (KeyError, ValueError, TypeError, model.DoesNotExist):
        raise PermissionDenied('原记录不存在或无权访问')


def _record(member, action, token, digest, result_ref):
    CashReceipt.objects.create(organization=member.organization, request_token=token,
                               action=action, input_digest=digest, result=result_ref,
                               created_by=member)


def _get_account(member, account_id, *, lock=False):
    rows = FundingAccount.objects.select_related('organization')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        return rows.get(organization=member.organization,
                        pk=_identifier(account_id, '银行账户不存在或无权访问'))
    except FundingAccount.DoesNotExist:
        raise PermissionDenied('银行账户不存在或无权访问')


def _account_projection(row, *, transaction_count=True):
    return {
        'id': str(row.pk), 'account_ref': row.account_ref,
        'organization_id': str(row.organization_id), 'name': row.name,
        'masked_identifier': row.masked_identifier, 'account_owner': row.account_owner,
        'opening_cents': row.opening_cents, 'opening_on': row.opening_on.isoformat(),
        'status': row.status, 'status_display': row.get_status_display(),
        'is_canonical': row.status == 'active', 'version': row.version,
        'created_by_member_id': str(row.created_by_id),
        'created_at': row.created_at.isoformat(),
        'transaction_count': row.transactions.count() if transaction_count else None,
    }


@transaction.atomic
def save_funding_account(member, *, name, masked_identifier=None, request_token,
                         account_id=None, expected_version=None, dry_run=True):
    """Draft create/update; opening_on is fixed on creation and opening_cents stays 0."""
    _dry(dry_run)
    member = _locked(member)
    name = _text(name, 120, '请填写1至120字的银行账户名称')
    masked = '' if masked_identifier in (None, '') else _text(
        masked_identifier, 64, '银行账号标识须不超过64字')
    account_id = _optional_identifier(account_id, '银行账户标识无效')
    payload = {'name': name, 'masked_identifier': masked,
               'account_id': str(account_id) if account_id else None,
               'expected_version': expected_version}
    token, digest, receipt = _receipt(member, 'funding_account.save', request_token, payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, FundingAccount)
    if account_id:
        row = _get_account(member, account_id, lock=True)
        if row.status != 'draft' or row.version != _version(expected_version):
            _fail('仅可修改当前版本的银行账户草稿')
        if row.opening_cents != 0 or row.opening_on is None:
            _fail('银行账户期初余额或期初日期异常，不能继续编辑')
        if FundingAccount.objects.filter(organization=member.organization,
                                         name=name).exclude(pk=row.pk).exists():
            _fail('同名银行账户已存在，请使用不同名称')
        if dry_run:
            row.name, row.masked_identifier = name, masked
            row.version += 1
            return row
        # Conditional update so only one worker can advance this draft's version/receipt.
        try:
            updated = FundingAccount.objects.filter(
                pk=row.pk, organization=member.organization, version=row.version,
                status='draft').update(name=name, masked_identifier=masked,
                                       version=row.version + 1)
        except IntegrityError:
            _fail('同名银行账户已存在，请使用不同名称')
        if updated != 1:
            _fail('银行账户版本已变化，请刷新后重试')
        row.refresh_from_db()
        audit(member, 'cash.funding_account.saved', row,
              {'name': row.name, 'masked_identifier': row.masked_identifier,
               'status': row.status})
        _record(member, 'funding_account.save', token, digest, row.account_ref)
        return row
    if expected_version is not None:
        _fail('新建银行账户不接受已有版本号')
    row = FundingAccount(organization=member.organization, created_by=member,
                         account_owner='organization', opening_cents=0,
                         opening_on=timezone.localdate(), status='draft', version=1)
    row.name, row.masked_identifier = name, masked
    if FundingAccount.objects.filter(organization=member.organization, name=name).exists():
        _fail('同名银行账户已存在，请使用不同名称')
    if not dry_run:
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            _fail('同名银行账户已存在，请使用不同名称')
        audit(member, 'cash.funding_account.saved', row,
              {'name': row.name, 'masked_identifier': row.masked_identifier,
               'status': row.status})
        _record(member, 'funding_account.save', token, digest, row.account_ref)
    return row


@transaction.atomic
def activate_funding_account(member, account_id, *, expected_version, request_token, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    if member.role != 'owner':
        raise PermissionDenied('只有公司负责人可以启用银行账户')
    account_id = _identifier(account_id, '银行账户标识无效')
    version = _version(expected_version)
    payload = {'account_id': str(account_id), 'expected_version': version}
    token, digest, receipt = _receipt(member, 'funding_account.activate', request_token,
                                      payload, dry_run)
    if receipt is not None:
        return _receipt_row(member, receipt, FundingAccount)
    row = _get_account(member, account_id, lock=True)
    if row.version != version:
        _fail('银行账户版本已变化，请刷新后重试')
    if row.status != 'draft':
        _fail('仅可启用当前版本的银行账户草稿')
    if row.opening_cents != 0 or row.opening_on is None:
        _fail('期初余额必须为0且期初日期必须有效，期初导入留待后续片')
    if dry_run:
        row.status, row.version = 'active', row.version + 1
        return row
    # Conditional update: only one worker may flip draft->active and write the receipt.
    updated = FundingAccount.objects.filter(pk=row.pk, organization=member.organization,
                                            version=version, status='draft').update(
        status='active', version=version + 1)
    if updated != 1:
        _fail('银行账户版本已变化，请刷新后重试')
    row.refresh_from_db()
    audit(member, 'cash.funding_account.activated', row,
          {'name': row.name, 'opening_on': row.opening_on.isoformat()})
    _record(member, 'funding_account.activate', token, digest, row.account_ref)
    return row


def funding_account_snapshot(member, account_id):
    """Historical read stays available after the module toggle; write routes still refuse."""
    member = _fresh(member, check_module=False)
    return _account_projection(_get_account(member, account_id))


def funding_account_list(member):
    member = _fresh(member, check_module=False)
    rows = FundingAccount.objects.filter(organization=member.organization).select_related(
        'organization').order_by('name', 'id')
    return [_account_projection(row) for row in rows]
