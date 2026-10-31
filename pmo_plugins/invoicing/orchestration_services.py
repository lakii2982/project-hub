"""PH-370 cross-owner orchestration execution on top of PH-320 reference links.

Invoicing owns the invoice/reversal and records the execution receipt; each
owner action is performed by that owner's named service. No global transaction
is claimed; failures leave the invoice fact unchanged and are retried through
the owner's own idempotency receipt plus the recorded reference chain.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, Membership
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company
from pmo_plugins.costing import budget_services
from pmo_plugins.management_revenue import services as revenue_services
from pmo_plugins.cash import refund_services as cash_services
from pmo_plugins.settlement import clearance_services as settlement_services
from .models import Invoice, InvoiceReversal
from . import services as invoice_services

ORCH_ACTIONS = {
    'revenue_reversal': 'revenue_reversal',
    'cash_refund': 'cash_refund',
    'cash_bank_return': 'cash_bank_return',
    'settlement_clearance': 'settlement_adjustment',
    'settlement_clearance_reversal': 'settlement_adjustment',
}
ACTION_ALIASES = {'settlement_adjustment': 'settlement_clearance'}
EXECUTED_ACTION = 'plugin.invoicing.orchestration_executed'
PENDING_ACTION = 'plugin.invoicing.orchestration_link_pending'


def _fail(message):
    raise ValidationError(message)


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _token(value):
    if type(value) is uuid.UUID:
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        _fail('request_token 无效')


def _fresh(member, *, write=True):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    if not row.active or not row.user.is_active:
        raise PermissionDenied('此账户尚未加入有效公司工作区')
    if write:
        if row.role not in {'owner', 'finance'}:
            raise PermissionDenied('仅 owner/finance 可执行跨 owner 编排')
        if not is_enabled(row.organization, 'invoicing'):
            raise PermissionDenied('发票模块已停用，不能执行跨 owner 编排')
    return row


def _receipt(member, token, digest):
    event = AuditEvent.objects.filter(
        organization=member.organization, action=EXECUTED_ACTION,
        payload__request_token=str(token)).order_by('-id').first()
    if event is None:
        return None
    payload = event.payload or {}
    if payload.get('request_digest') != digest:
        _fail('同一请求标识已用于不同编排输入')
    return payload.get('result')


def _owner_ref(action, result):
    if action == 'revenue_reversal':
        return result.source_ref
    if action == 'cash_refund':
        return {'namespace': 'cash', 'kind': 'cash_refund', 'id': str(result.pk),
                'version': result.version}
    if action == 'cash_bank_return':
        return {'namespace': 'cash', 'kind': 'cash_bank_return', 'id': str(result.pk),
                'version': result.version}
    return result.clearance_ref


def _run_owner(member, action, payload, token, dry_run):
    if action == 'revenue_reversal':
        return revenue_services.reverse_recognition(
            member, payload['recognition_id'], reason=payload['reason'],
            request_token=token, dry_run=dry_run)
    if action == 'cash_refund':
        return cash_services.record_refund(
            member, original_cash_transaction_id=payload['original_cash_transaction_id'],
            refund_date=payload['refund_date'], gross_cents=payload['gross_cents'],
            reason=payload['reason'], request_token=token, dry_run=dry_run)
    if action == 'cash_bank_return':
        return cash_services.record_bank_return(
            member, original_cash_transaction_id=payload['original_cash_transaction_id'],
            return_date=payload['return_date'], gross_cents=payload['gross_cents'],
            request_token=token, dry_run=dry_run)
    if action == 'settlement_clearance':
        return settlement_services.create_clearance(
            member, open_item_id=payload['open_item_id'],
            cash_transaction_ref=payload['cash_transaction_ref'],
            cleared_cents=payload['cleared_cents'],
            occurred_on=payload.get('occurred_on'), request_token=token, dry_run=dry_run)
    if action == 'settlement_clearance_reversal':
        return settlement_services.reverse_clearance(
            member, payload['clearance_id'], reason=payload['reason'],
            expected_version=payload.get('expected_version'), request_token=token,
            dry_run=dry_run)
    _fail('编排动作无效')


def _validate_payload(action, payload):
    if not isinstance(payload, dict):
        _fail('编排参数须为JSON对象')
    required = {
        'revenue_reversal': {'recognition_id', 'reason'},
        'cash_refund': {'original_cash_transaction_id', 'refund_date', 'gross_cents', 'reason'},
        'cash_bank_return': {'original_cash_transaction_id', 'return_date', 'gross_cents'},
        'settlement_clearance': {'open_item_id', 'cash_transaction_ref', 'cleared_cents'},
        'settlement_clearance_reversal': {'clearance_id', 'reason'},
    }[action]
    if not required <= set(payload):
        _fail('编排参数缺少必需字段')
    if 'gross_cents' in payload and type(payload['gross_cents']) is not int:
        _fail('金额须为整数分')
    if 'cleared_cents' in payload and type(payload['cleared_cents']) is not int:
        _fail('金额须为整数分')
    return payload


@transaction.atomic
def execute_invoice_orchestration(member, *, invoice_id, reversal_id, action, payload,
                                  request_token, expected_version, dry_run=False):
    """Execute one owner action then append its PH-320 reference link.

    Owner services are idempotent by a token derived from the orchestration token,
    so a retry after a link-recording failure reuses the owner fact instead of
    duplicating it. This function never writes revenue/cash/settlement itself and
    never claims a global single transaction.
    """
    member = _fresh(member)
    lock_company(member)
    action = ACTION_ALIASES.get(action, action)
    if action not in ORCH_ACTIONS:
        _fail('编排动作无效')
    token = _token(request_token)
    payload = _validate_payload(action, payload)
    digest_payload = {'invoice_id': str(invoice_id), 'reversal_id': str(reversal_id),
                      'action': action, 'payload': payload,
                      'expected_version': expected_version}
    digest = _digest(digest_payload)
    existing = _receipt(member, token, digest)
    if existing is not None:
        return existing
    try:
        invoice = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if type(expected_version) is not int or expected_version != invoice.version:
        _fail('发票版本已变化，请刷新后重试')
    try:
        reversal = InvoiceReversal.objects.get(
            organization=member.organization, invoice=invoice, pk=reversal_id)
    except (InvoiceReversal.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票红冲不存在或不属于该发票')
    link_kind = ORCH_ACTIONS[action]
    owner_token = uuid.uuid5(uuid.NAMESPACE_URL, f'pmo:ph370:{token}:{action}')
    if dry_run:
        preview = _run_owner(member, action, payload, owner_token, True)
        return {'dry_run': True, 'invoice_ref': invoice.invoice_ref,
                'reversal_ref': reversal.reversal_ref, 'action': action,
                'link_kind': link_kind, 'owner_preview': str(
                    getattr(preview, 'pk', '') or getattr(preview, 'id', ''))}
    owner_result = _run_owner(member, action, payload, owner_token, False)
    owner_ref = _owner_ref(action, owner_result)
    evidence = payload.get('evidence') or [{'kind': 'orchestration_execution',
                                            'action': action,
                                            'occurred_on': timezone.localdate().isoformat()}]
    try:
        link = invoice_services.link_invoice_orchestration(
            member, invoice_id=invoice.pk, link_kind=link_kind, owner_ref=owner_ref,
            evidence=evidence, request_token=uuid.uuid4(),
            expected_version=invoice.version, reversal_id=reversal.pk, dry_run=False)
    except ValidationError as exc:
        if any('已存在' in str(message) for message in getattr(exc, 'messages', [str(exc)])):
            link = {'owner_ref': owner_ref, 'link_kind': link_kind,
                    'status': 'linked'}
        else:
            audit(member, PENDING_ACTION, invoice, {
                'invoice_ref': invoice.invoice_ref, 'reversal_ref': reversal.reversal_ref,
                'action': action, 'owner_ref': owner_ref,
                'reason': str(exc), 'request_token': str(token)})
            raise ValidationError('owner 具名服务已执行，引用链登记失败；请用同一请求标识重试')
    result = {
        'status': 'executed', 'invoice_ref': invoice.invoice_ref,
        'reversal_ref': reversal.reversal_ref, 'action': action,
        'link_kind': link_kind, 'owner_ref': owner_ref,
        'link_status': link.get('status', 'linked'),
        'single_transaction_claim': False,
    }
    audit(member, EXECUTED_ACTION, invoice, {
        'request_token': str(token), 'request_digest': digest,
        'invoice_ref': invoice.invoice_ref, 'reversal_ref': reversal.reversal_ref,
        'action': action, 'link_kind': link_kind, 'owner_ref': owner_ref,
        'result': result})
    return result


def orchestration_snapshot(member, invoice_id):
    member = _fresh(member, write=False)
    try:
        invoice = Invoice.objects.select_related('counterparty').get(
            organization=member.organization, pk=invoice_id)
    except (Invoice.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('发票不存在或无权访问')
    if member.role in {'pm', 'member'}:
        raise PermissionDenied('项目经理只能查看其管理项目摘要；成员无权查看编排')
    events = AuditEvent.objects.filter(
        organization=member.organization,
        action__in=[EXECUTED_ACTION, PENDING_ACTION]).order_by('-id')[:200]
    executions = []
    for event in events:
        payload = event.payload or {}
        ref = payload.get('invoice_ref') or {}
        if str(ref.get('id')) != str(invoice.pk):
            continue
        executions.append({'action': payload.get('action'),
                           'status': 'executed' if event.action == EXECUTED_ACTION else 'link_pending',
                           'owner_ref': payload.get('owner_ref'),
                           'link_kind': payload.get('link_kind'),
                           'recorded_at': event.created_at.isoformat()})
    return {'invoice_ref': invoice.invoice_ref, 'executions': executions,
            'single_transaction_claim': False}
