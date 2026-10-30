"""PH-265 contract change draft, independent decision, effective version and termination.

The canonical contracts.contract_change.draft.save/v1 and
contracts.change.decide/v1 commands are carried by this named service and the
contracts browser workflow; no financial fact or ledger posting is written.
"""
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import DomainEvent
from core.services import audit, lock_company
from . import approval_services
from .models import (CHANGE_DECISIONS, CHANGE_TYPES, Contract, ContractChange, ContractChangeReceipt,
                     ContractLine, ContractVersion, MethodPolicySelection, PaymentTerm, WarrantyTerm)
from .party_services import _digest, _reader, _request_token, _version, _writer
from .services import _contract_for_read

MAX_AMOUNT = 10**12
CHANGE_TOPIC = 'contracts.change_effective.v1'
VERSION_TOPIC = 'contracts.version_approved.v1'
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}


def _reject(message):
    raise ValidationError(message)


def _ref(row, kind):
    return {'namespace': 'contracts', 'kind': kind, 'id': str(row.pk), 'version': row.version}


def _change_ref(change):
    return {'namespace': 'contracts', 'kind': 'contract_change', 'id': str(change.pk),
            'version': change.version}


def _locked(member):
    member = _writer(member)
    lock_company(member)
    return _writer(member)


def _reader_member(member):
    member = _reader(member)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权查看合同变更')
    return member


def _evidence(value):
    if value in (None, ''):
        return []
    if not isinstance(value, list) or len(value) > 20:
        _reject('变更证据须为至多20项的清单')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {'kind', 'reference', 'sha256', 'evidence_id'}:
            _reject('变更证据项无效')
        if not item or any(not isinstance(v, (str, int)) for v in item.values()):
            _reject('变更证据项无效')
        if len(str(item)) > 500:
            _reject('变更证据项过长')
        result.append(item)
    return result


def _request_date(value):
    if isinstance(value, date) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            _reject('变更生效日期须为有效的年月日')
        if parsed.isoformat() != value:
            _reject('变更生效日期须为有效的年月日')
    else:
        _reject('变更生效日期须为有效的年月日')
    if parsed > timezone.localdate():
        _reject('变更生效日期不能在未来')
    return parsed


def _change_type(value):
    if not isinstance(value, str) or value not in CHANGE_TYPES:
        _reject('变更类型无效')
    return value


def _approved_version(contract):
    versions = ContractVersion.objects.filter(contract=contract, status='approved')
    if versions.count() != 1:
        _reject('只有唯一已批准版本的生效合同可办理变更或终止')
    return versions.get()


def _totals(lines):
    return {field: sum(line[field] for line in lines)
            for field in ('net_cents', 'tax_cents', 'gross_cents')}


def _derive_deltas(contract, change_type, draft_content):
    previous = _approved_version(contract)
    if change_type == 'termination':
        if draft_content not in ({}, {'lines': []}):
            raise ValidationError('终止变更不得携带新版本内容')
        return {'net_delta_cents': 0, 'tax_delta_cents': 0, 'gross_delta_cents': 0}, []
    if not isinstance(draft_content, dict) or not isinstance(draft_content.get('lines'), list):
        _reject('变更草稿须包含完整的合同配置')
    normalized = approval_services._normalize_lines(contract, draft_content['lines'])
    new = _totals(normalized)
    old = {field: sum(getattr(line, field) for line in previous.lines.all())
           for field in ('net_cents', 'tax_cents', 'gross_cents')}
    return ({'net_delta_cents': new['net_cents'] - old['net_cents'],
             'tax_delta_cents': new['tax_cents'] - old['tax_cents'],
             'gross_delta_cents': new['gross_cents'] - old['gross_cents']}, normalized)


def _change_receipt(member, action, request_token, payload, dry_run):
    token = _request_token(request_token)
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = ContractChangeReceipt.objects.filter(organization=member.organization,
                                               request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _reject('同一请求标识已用于不同输入')
        try:
            change = ContractChange.objects.get(organization=member.organization,
                                                pk=row.result_ref['id'])
        except (KeyError, TypeError, ValueError, ContractChange.DoesNotExist):
            raise PermissionDenied('原变更记录不存在或无权访问')
        return token, digest, change
    return token, digest, None


def _record(member, action, token, digest, change):
    ContractChangeReceipt.objects.create(
        organization=member.organization, action=action, request_token=token,
        input_digest=digest, result_ref=_change_ref(change),
        result_snapshot={'source': _change_ref(change), 'status': change.status,
                         'contract_id': str(change.contract_id), 'version': change.version},
        created_by=member)


def _locked_contract(member, contract_id):
    try:
        contract = Contract.objects.select_for_update().select_related('project', 'party').get(
            organization=member.organization, pk=contract_id)
    except (Contract.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('合同不存在或无权访问')
    if member.role == 'pm':
        from core.access import require_project
        require_project(member, contract.project)
    return contract


@transaction.atomic
def save_contract_change_draft(member, *, contract_id, change_type, requested_on, draft_content,
                               evidence=None, request_token, change_id=None, expected_version=None,
                               provided_deltas=None, dry_run=True):
    """Canonical contracts.contract_change.draft.save/v1 equivalent: draft only."""
    member = _locked(member)
    contract = _locked_contract(member, contract_id)
    if contract.status != 'active':
        _reject('只有已生效合同可以办理变更或终止')
    kind = _change_type(change_type)
    when = _request_date(requested_on)
    evidence = _evidence(evidence)
    existing = None
    if change_id:
        try:
            existing = contract.changes.select_for_update().get(
                pk=change_id, organization=member.organization, status='draft')
        except (ContractChange.DoesNotExist, ValueError, ValidationError):
            raise PermissionDenied('变更草稿不存在或无权修改')
        if existing.version != _version(expected_version):
            _reject('变更草稿版本已变化，请刷新后重试')
    else:
        if expected_version is not None:
            _reject('新建变更不接受已有版本号')
    if existing is not None and (existing.change_type != kind or existing.requested_on != when):
        _reject('变更类型与生效日期创建后不可修改')
    deltas, normalized = _derive_deltas(contract, kind, draft_content)
    if provided_deltas is not None:
        if not isinstance(provided_deltas, dict) or set(provided_deltas) != set(deltas):
            _reject('客户端变更差额字段无效')
        for key, value in deltas.items():
            if provided_deltas.get(key) != value:
                _reject('客户端变更差额与派生结果不一致')
    payload = {'contract': str(contract.pk), 'change_type': kind, 'requested_on': str(when),
               'deltas': deltas, 'draft_content': draft_content, 'evidence': evidence,
               'change_id': str(existing.pk) if existing else None, 'expected_version': expected_version}
    token, digest, replay = _change_receipt(member, 'draft.save', request_token, payload, dry_run)
    if replay is not None:
        return replay
    if existing is None and contract.changes.filter(status='draft').exists():
        _reject('该合同已有待决策的变更草稿，请先办理或撤回')
    if dry_run:
        return {'contract_id': str(contract.pk), 'change_type': kind, 'requested_on': str(when),
                **deltas, 'status': 'draft'}
    if existing is not None:
        existing.version += 1
        change = existing
    else:
        change = ContractChange(organization=member.organization, contract=contract,
                                created_by_member=member, create_token=token, create_digest=digest)
    change.change_type = kind
    change.requested_on = when
    change.draft_content = {'lines': normalized} if kind != 'termination' else {}
    change.evidence = evidence
    for key, value in deltas.items():
        setattr(change, key, value)
    change.save()
    audit(member, 'contracts.change.draft_saved', change,
          {'contract_id': str(contract.pk), 'change_type': kind, 'deltas': deltas})
    _record(member, 'draft.save', token, digest, change)
    return change


def _create_version_from_change(member, contract, change, normalized, note):
    previous = _approved_version(contract)
    reference = previous.contract_reference
    number = (contract.versions.aggregate(value=Max('version_no'))['value'] or 0) + 1
    version = ContractVersion(
        organization=member.organization, contract=contract, version_no=number,
        created_by_member=change.created_by_member, submitted_by_member=change.created_by_member,
        effective_on=change.requested_on, contract_reference=reference,
        draft_content={'lines': normalized}, header_snapshot=approval_services._header(contract),
        status='approved', approved_by_member=member, approved_on=timezone.now())
    version.submitted_digest = approval_services._frozen_digest(version)
    approval = audit(member, 'plugin.contracts.version.approved', version, {
        'submitted_digest': version.submitted_digest, 'contract_reference': reference,
        'review_note': note, 'change_id': str(change.pk)})
    version.approval_audit = approval
    version.evidence = [{'kind': 'approval', 'evidence_id': str(approval.pk),
                         'sha256': approval.event_hash}]
    version.version = 2
    version.save()
    previous.status = 'superseded'
    previous.version += 1
    previous.save(update_fields=['status', 'version'])
    contract.version += 1
    contract.save(update_fields=['version'])
    return version


def _emit_version_event(member, version, change, changed_refs):
    event_id = uuid.uuid4()
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=version.approval_audit,
        topic=VERSION_TOPIC, data={'event_id': str(event_id), 'topic': VERSION_TOPIC,
        'schema_version': 1, 'organization_id': str(member.organization_id),
        'source': _ref(version, 'contract_version'), 'occurred_at': timezone.now().isoformat(),
        'payload': {'changed_refs': changed_refs, 'posting_event_refs': []}})


def _emit_change_event(member, change, changed_refs):
    event_id = uuid.uuid4()
    row = audit(member, 'plugin.contracts.change.effective', change, {
        'changed_refs': changed_refs, 'decision': 'approved'})
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=row, topic=CHANGE_TOPIC,
        data={'event_id': str(event_id), 'topic': CHANGE_TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': _change_ref(change),
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': changed_refs, 'posting_event_refs': []}})


def _apply_approved_change(member, change, note):
    contract = change.contract
    kind = change.change_type
    if kind == 'termination':
        if change.draft_content not in ({}, {'lines': []}):
            _reject('终止变更内容已损坏，拒绝生效')
        if any(getattr(change, f'{field}_delta_cents')
               for field in ('net', 'tax', 'gross')):
            _reject('终止变更不得携带金额差额')
        contract.status, contract.terminated_on = 'terminated', change.requested_on
        contract.version += 1
        contract.save(update_fields=['status', 'terminated_on', 'version'])
        _emit_change_event(member, change, [_change_ref(change), _ref(contract, 'contract')])
        return None
    normalized = approval_services._normalize_lines(contract, change.draft_content.get('lines'))
    new = _totals(normalized)
    previous = _approved_version(contract)
    old = {field: sum(getattr(line, field) for line in previous.lines.all())
           for field in ('net_cents', 'tax_cents', 'gross_cents')}
    if any(getattr(change, f'{field}_delta_cents') != new[field + '_cents'] - old[field + '_cents']
           for field in ('net', 'tax', 'gross')):
        _reject('变更草稿差额已损坏，拒绝生效')
    version = _create_version_from_change(member, contract, change, normalized, note)
    changed = [_ref(contract, 'contract'), _ref(version, 'contract_version')]
    for candidate in normalized:
        line = ContractLine.objects.create(
            organization=member.organization, contract_version=version,
            **{key: candidate[key] for key in ('line_no', 'net_cents', 'tax_cents', 'gross_cents')})
        selection_id = uuid.uuid4()
        config = {**candidate['pricing_configuration'], 'currency': 'CNY',
                  'confirmed_by_customer_required': True,
                  'policy_ref': {'policy_id': str(selection_id), 'version': 1}}
        selection = MethodPolicySelection.objects.create(
            id=selection_id, organization=member.organization, contract_line=line,
            selected_on=version.effective_on, approved_by_member=member,
            approval_audit=version.approval_audit, pricing_configuration=config)
        changed.extend([_ref(line, 'contract_line'), _ref(selection, 'method_policy_selection')])
        payments = {}
        for term in candidate['payment_terms']:
            values = {k: v for k, v in term.items() if k != 'key'}
            payment = PaymentTerm.objects.create(organization=member.organization,
                                                 contract_line=line, **values)
            payments[term['key']] = payment
            changed.append(_ref(payment, 'payment_term'))
        for term in candidate['warranty_terms']:
            warranty = WarrantyTerm.objects.create(
                organization=member.organization, contract_line=line,
                payment_term=payments[term['payment_key']],
                **{k: v for k, v in term.items() if k != 'payment_key'})
            changed.append(_ref(warranty, 'warranty_term'))
    _emit_version_event(member, version, change,
                        [_ref(version, 'contract_version'), _change_ref(change)])
    _emit_change_event(member, change, [_change_ref(change), _ref(version, 'contract_version')])
    return version


@transaction.atomic
def decide_contract_change(member, change_id, *, decision, note='', request_token,
                           expected_version=None, dry_run=True):
    """Canonical contracts.change.decide/v1 equivalent: independent approve/reject/withdraw."""
    member = _locked(member)
    if decision not in CHANGE_DECISIONS:
        _reject('变更决策无效；本片支持批准、驳回与撤回')
    if not isinstance(note, str):
        _reject('决策说明无效')
    note = note.strip()
    if len(note) > 500:
        _reject('决策说明不能超过500字')
    if decision == 'rejected' and not note:
        _reject('驳回合同变更必须填写原因')
    try:
        change = ContractChange.objects.select_for_update().select_related(
            'contract__project', 'contract__party').get(
            pk=change_id, organization=member.organization)
    except (ContractChange.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('变更记录不存在或无权访问')
    contract = _locked_contract(member, change.contract_id)
    change.contract = contract
    payload = {'change': str(change.pk), 'decision': decision, 'note': note,
               'expected_version': expected_version}
    token, digest, replay = _change_receipt(member, 'decide', request_token, payload, dry_run)
    if replay is not None:
        return replay
    if change.status != 'draft':
        _reject('变更已处理，请刷新后查看')
    if expected_version is not None and change.version != _version(expected_version):
        _reject('变更草稿版本已变化，请刷新后重试')
    if member.pk in {change.created_by_member_id, }:
        _reject('创建人不能审批自己的合同变更，请由另一位负责人处理')
    if dry_run:
        return {'change_id': str(change.pk), 'status': decision,
                'contract_id': str(contract.pk), 'change_type': change.change_type}
    if decision == 'approved' and contract.status != 'active':
        _reject('只有已生效合同可以批准变更或终止；终止后仅可驳回或撤回既有草稿')
    change.decided_by_member, change.decided_on = member, timezone.now()
    change.decision_note = note
    change.version += 1
    change.status = decision
    change.save()
    if decision == 'approved':
        # The canonical change_effective event is sourced from the suppressed
        # plugin audit written inside _apply_approved_change; a second generic
        # audit with the same action would emit a colliding non-canonical topic.
        _apply_approved_change(member, change, note)
    else:
        audit(member, 'contracts.change.rejected' if decision == 'rejected'
              else 'contracts.change.withdrawn', change, {'note': note})
    _record(member, 'decide', token, digest, change)
    return change


def list_contract_changes(member, contract_id):
    member = _reader_member(member)
    contract = _contract_for_read(member, contract_id)
    rows = ContractChange.objects.filter(organization=member.organization, contract=contract)
    if member.role == 'pm':
        from core.access import require_project
        require_project(member, contract.project)
    return rows.select_related('decided_by_member__user', 'created_by_member__user')


def get_contract_change(member, change_id):
    member = _reader_member(member)
    try:
        change = ContractChange.objects.select_related(
            'contract__project', 'contract__party', 'created_by_member__user',
            'decided_by_member__user').get(pk=change_id, organization=member.organization)
    except (ContractChange.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('变更记录不存在或无权访问')
    _contract_for_read(member, change.contract_id)
    return change
