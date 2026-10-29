"""First contract approval. No financial facts or public command API are created."""
from copy import deepcopy
from datetime import date
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.access import require_project
from core.models import DomainEvent, Milestone
from core.services import audit, lock_company
from .models import (Contract, ContractVersion, ContractLine, PaymentTerm, WarrantyTerm,
                     MethodPolicySelection, normalize_display)
from .party_services import _reader, _writer, _digest, _request_token, _version, _active_party
from .services import _contract_for_read, _project

MAX_AMOUNT = 10**12


def _reject(message):
    raise ValidationError(message)


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        _reject('配置包含未知字段或缺少必填项')
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value <= MAX_AMOUNT:
        _reject('所有金额须为0至100亿元的整数分')
    return value


def _text(value, maximum, required=True):
    if not isinstance(value, str):
        _reject('文本字段无效')
    value = normalize_display(value)
    if len(value) > maximum or (required and not value):
        _reject('请完整填写合同依据或条件，且不要超过字段长度')
    return value


def _date(value, optional=False):
    if optional and value in (None, ''):
        return None
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _reject('日期须为有效的年月日')


def _array(value, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= 1000:
        _reject('配置行数须在允许范围内')
    return value


def _header(contract):
    return {'project_id': str(contract.project_id), 'counterparty_id': str(contract.party_id),
            'contract_code': contract.contract_code,
            'signed_on': contract.signed_on.isoformat() if contract.signed_on else None}


def _normalize_lines(contract, lines, *, validate_live=True):
    result, line_numbers, milestones_seen, intervals = [], set(), set(), []
    # All callers hold the company lock. Approved coverage in this same project
    # remains authoritative across different contracts, not only this candidate.
    existing_coverage = MethodPolicySelection.objects.filter(
        organization=contract.organization, status='approved',
        contract_line__contract_version__status='approved',
        contract_line__contract_version__contract__project_id=contract.project_id).exclude(
        contract_line__contract_version__contract_id=contract.pk) if validate_live else ()
    for selection in existing_coverage:
        configuration = selection.pricing_configuration
        if configuration.get('template') == 'fixed_service_period':
            intervals.extend((_date(item['coverage_start']), _date(item['coverage_end']))
                             for item in configuration['service_schedule'])
    for candidate in _array(lines, 1):
        _keys(candidate, {'line_no', 'net_cents', 'tax_cents', 'gross_cents', 'pricing_configuration', 'payment_terms', 'warranty_terms'},
              {'line_no', 'net_cents', 'tax_cents', 'gross_cents', 'pricing_configuration'})
        number = candidate['line_no']
        if type(number) is not int or number < 1 or number in line_numbers:
            _reject('明细行号须为不重复正整数')
        line_numbers.add(number)
        net, tax, gross = (_amount(candidate[k]) for k in ('net_cents', 'tax_cents', 'gross_cents'))
        if net + tax != gross:
            _reject('净额加税额须等于含税额')
        p = deepcopy(candidate['pricing_configuration'])
        base = {'template', 'net_cap_cents'}
        template = p.get('template') if isinstance(p, dict) else None
        extra = {'fixed_milestone': {'milestones'},
                 'confirmed_time': {'time_unit', 'unit_minutes', 'unit_price_cents'},
                 'fixed_service_period': {'service_start', 'service_end', 'period_unit', 'service_schedule', 'partial_period_rule'}}
        if not isinstance(template, str) or template not in extra:
            _reject('请选择已支持的收费模板')
        _keys(p, base | extra[template], base | extra[template])
        cap = _amount(p['net_cap_cents'])
        if cap > net or (template != 'confirmed_time' and cap != net):
            _reject('累计净额上限不合法；固定价分配全价时上限须等于净额')
        if template == 'fixed_milestone':
            allocation = 0
            for item in _array(p['milestones'], 1):
                _keys(item, {'milestone_id', 'allocated_net_cents'}, {'milestone_id', 'allocated_net_cents'})
                raw = item['milestone_id']
                if type(raw) not in {int, str}:
                    _reject('节点无效')
                try:
                    milestone_id = int(raw)
                    if not 1 <= milestone_id <= 2**63-1:
                        _reject('节点标识无效')
                    if validate_live:
                        Milestone.objects.get(pk=milestone_id, project_id=contract.project_id)
                except (Milestone.DoesNotExist, ValueError, TypeError):
                    _reject('节点不存在或不属于本项目')
                if milestone_id in milestones_seen:
                    _reject('同一版本不能重复分配节点')
                milestones_seen.add(milestone_id)
                item['milestone_id'] = str(milestone_id)
                allocation += _amount(item['allocated_net_cents'])
            if allocation != net:
                _reject('节点价款分配合计须等于明细净额')
        elif template == 'confirmed_time':
            unit, minutes = p['time_unit'], p['unit_minutes']
            if not isinstance(unit, str) or unit not in {'hour', 'workday'} or type(minutes) is not int or not 1 <= minutes <= 1440 or (unit == 'hour' and minutes != 60):
                _reject('小时须为60分钟，人天须明确1至1440分钟')
            _amount(p['unit_price_cents'])
        else:
            start, end = _date(p['service_start']), _date(p['service_end'])
            if start > end or not isinstance(p['period_unit'], str) or p['period_unit'] not in {'calendar_month', 'calendar_day'} or not isinstance(p['partial_period_rule'], str) or p['partial_period_rule'] not in {'no_partial', 'calendar_days'}:
                _reject('服务期间与部分期间规则无效')
            p['service_start'], p['service_end'] = start.isoformat(), end.isoformat()
            allocation = 0
            for item in _array(p['service_schedule'], 1):
                _keys(item, {'coverage_start', 'coverage_end', 'allocated_net_cents'}, {'coverage_start', 'coverage_end', 'allocated_net_cents'})
                left, right = _date(item['coverage_start']), _date(item['coverage_end'])
                if not start <= left <= right <= end or any(left <= b and a <= right for a, b in intervals):
                    _reject('服务分配范围越界或重复覆盖')
                if p['partial_period_rule'] == 'no_partial' and p['period_unit'] == 'calendar_month':
                    import calendar
                    if left.day != 1 or right.day != calendar.monthrange(right.year, right.month)[1]:
                        _reject('整月规则不允许部分月份')
                intervals.append((left, right))
                item['coverage_start'], item['coverage_end'] = left.isoformat(), right.isoformat()
                allocation += _amount(item['allocated_net_cents'])
            if allocation != net:
                _reject('服务期分配合计须等于明细净额')
        payments, payment_keys, payment_sum = [], {}, 0
        for term in _array(candidate.get('payment_terms', [])):
            _keys(term, {'key', 'condition_type', 'gross_cents', 'due_date', 'release_condition'}, {'key', 'condition_type', 'gross_cents'})
            key = _text(term['key'], 80)
            kind = term['condition_type']
            if key in payment_keys or not isinstance(kind, str) or kind not in {'unconditional', 'conditional', 'warranty'}:
                _reject('付款条件类型或行标识无效')
            value = _amount(term['gross_cents'])
            normalized = {'key': key, 'condition_type': kind, 'gross_cents': value,
                          'due_date': (_date(term.get('due_date'), True) or None),
                          'release_condition': _text(term.get('release_condition', ''), 500, kind != 'unconditional')}
            if normalized['due_date']:
                normalized['due_date'] = normalized['due_date'].isoformat()
            payment_keys[key] = normalized
            payments.append(normalized)
            payment_sum += value
        if payment_sum > gross:
            _reject('付款条款合计超过明细含税额')
        warranties, used = [], {}
        for term in _array(candidate.get('warranty_terms', [])):
            _keys(term, {'payment_key', 'gross_cents', 'release_condition', 'release_on'}, {'payment_key', 'gross_cents', 'release_condition'})
            key = term['payment_key']
            if not isinstance(key, str) or key not in payment_keys or payment_keys[key]['condition_type'] != 'warranty':
                _reject('质保须引用本明细质保付款条款')
            value = _amount(term['gross_cents'])
            used[key] = used.get(key, 0) + value
            if used[key] > payment_keys[key]['gross_cents']:
                _reject('质保金额不是付款价款之外的额外收费')
            release = _date(term.get('release_on'), True)
            warranties.append({'payment_key': key, 'gross_cents': value,
                'release_condition': _text(term['release_condition'], 500), 'release_on': release.isoformat() if release else None})
        result.append({'line_no': number, 'net_cents': net, 'tax_cents': tax, 'gross_cents': gross,
                       'pricing_configuration': p, 'payment_terms': payments, 'warranty_terms': warranties})
    for field in ('net_cents', 'tax_cents', 'gross_cents'):
        _amount(sum(line[field] for line in result))
    count = 2 + sum(2 + len(line['payment_terms']) + len(line['warranty_terms']) for line in result)
    if count > 1000:
        _reject('本次批准变更引用超过1000项，请减少配置')
    return sorted(result, key=lambda item: item['line_no'])


def _locked(member):
    member = _writer(member)
    lock_company(member)
    return _writer(member)


def _get(member, version_id, lock=False):
    rows = ContractVersion.objects.select_related('contract__project', 'contract__party', 'approval_audit')
    if lock:
        rows = rows.select_for_update(of=('self',))
    try:
        version = rows.get(pk=version_id, organization=member.organization)
    except (ContractVersion.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('记录不存在或无权访问')
    _contract_for_read(member, version.contract_id)
    return version


def _receipt(member, action, token, payload):
    key, digest = action + ':' + str(_request_token(token)), _digest(payload)
    for row in ContractVersion.objects.filter(organization=member.organization).only('id', 'action_receipts'):
        if key in row.action_receipts:
            if row.action_receipts[key] != digest:
                _reject('请求标识已用于不同输入')
            return key, digest, _get(member, row.pk)
    return key, digest, None


def _record(version, key, digest):
    version.action_receipts[key] = digest
    version.save()


def _available(member, contract):
    if contract.status != 'draft' or contract.versions.filter(status='approved').exists():
        _reject('本片仅办理合同首次批准')
    _project(member, contract.project_id)
    party = _active_party(member, contract.party_id)
    if 'customer' not in party.roles:
        _reject('合同往来方必须具有客户角色')


def _frozen_digest(version):
    return _digest({'content': version.draft_content, 'header': version.header_snapshot,
                    'effective_on': version.effective_on, 'reference': version.contract_reference})


def list_versions(member, contract_id):
    member = _reader(member)
    contract = _contract_for_read(member, contract_id)
    return ContractVersion.objects.filter(contract=contract, organization=member.organization)


def get_version(member, version_id):
    return _get(_reader(member), version_id)


@transaction.atomic
def save_version(member, contract_id, *, lines, effective_on, contract_reference, request_token,
                 version_id=None, expected_version=None):
    member = _locked(member)
    contract = _contract_for_read(member, contract_id)
    # Hash deterministic submitted facts first. Live coverage/FK state must not
    # invalidate the receipt of an already completed save.
    normalized = _normalize_lines(contract, lines, validate_live=False)
    effective_on = _date(effective_on)
    if effective_on > timezone.localdate():
        _reject('首次生效日期不能在未来')
    reference = _text(contract_reference, 240)
    payload = {'contract_id': str(contract.pk), 'version_id': str(version_id) if version_id else None,
               'lines': normalized, 'effective_on': effective_on, 'reference': reference, 'expected_version': expected_version}
    key, digest, existing = _receipt(member, 'save', request_token, payload)
    if existing:
        return existing
    _available(member, contract)
    normalized = _normalize_lines(contract, normalized)
    if contract.versions.filter(status='submitted').exists():
        _reject('合同已提交，候选内容不可修改')
    if version_id:
        version = _get(member, version_id, True)
        if version.contract_id != contract.pk or version.status != 'draft' or version.version != _version(expected_version):
            _reject('草稿状态或版本冲突，请刷新')
        version.version += 1
    else:
        if expected_version is not None:
            _reject('新版本不接受已有版本号')
        number = (contract.versions.aggregate(value=Max('version_no'))['value'] or 0) + 1
        version = ContractVersion(organization=member.organization, contract=contract, version_no=number,
                                  created_by_member=member)
    version.draft_content = {'lines': normalized}
    version.effective_on, version.contract_reference = effective_on, reference
    version.header_snapshot = _header(contract)
    _record(version, key, digest)
    audit(member, 'contracts.version.saved', version, {'version': version.version})
    return version


@transaction.atomic
def submit_version(member, version_id, *, expected_version, request_token):
    member = _locked(member)
    version = _get(member, version_id, True)
    key, digest, existing = _receipt(member, 'submit', request_token, {'id': str(version.pk), 'expected_version': expected_version})
    if existing:
        return existing
    _available(member, version.contract)
    if version.status != 'draft' or version.version != _version(expected_version):
        _reject('草稿状态或版本冲突，请刷新')
    if version.contract.versions.filter(status='submitted').exclude(pk=version.pk).exists():
        _reject('已有等待审批的版本')
    if version.header_snapshot != _header(version.contract):
        _reject('合同头已变更，请重新保存配置')
    _normalize_lines(version.contract, version.draft_content['lines'])
    version.submitted_digest = _frozen_digest(version)
    version.status, version.submitted_by_member = 'submitted', member
    version.version += 1
    _record(version, key, digest)
    audit(member, 'contracts.version.submitted', version, {'digest': version.submitted_digest})
    return version


def _ref(row, kind):
    return {'namespace': 'contracts', 'kind': kind, 'id': str(row.pk), 'version': row.version}


@transaction.atomic
def review_version(member, version_id, *, expected_version, request_token, decision, review_note):
    member = _locked(member)
    version = _get(member, version_id, True)
    note = _text(review_note, 1000)
    if not isinstance(decision, str) or decision not in {'approve', 'reject'}:
        _reject('审批决定无效')
    payload = {'id': str(version.pk), 'expected_version': expected_version, 'decision': decision, 'review_note': note}
    key, digest, existing = _receipt(member, 'review', request_token, payload)
    if existing:
        return existing
    if version.status != 'submitted' or version.version != _version(expected_version):
        _reject('记录已处理或版本过期')
    if member.pk in {version.created_by_member_id, version.submitted_by_member_id}:
        _reject('制单人与提交人不能审批本合同')
    # Refusing an invalid candidate must remain possible. Fresh role/module,
    # tenant/project, state/version and independent reviewer checks still apply.
    if decision == 'reject':
        version.review_note = note
        version.version += 1
        version.status = 'rejected'
        _record(version, key, digest)
        audit(member, 'contracts.version.rejected', version, {'review_note': note})
        return version
    _available(member, version.contract)
    if version.header_snapshot != _header(version.contract) or version.submitted_digest != _frozen_digest(version):
        _reject('冻结内容不一致，拒绝审批')
    lines = _normalize_lines(version.contract, version.draft_content['lines'])
    if version.effective_on > timezone.localdate():
        _reject('首次生效日期不能在未来')
    version.review_note = note
    version.version += 1
    approval = audit(member, 'plugin.contracts.version.approved', version,
                     {'submitted_digest': version.submitted_digest, 'contract_reference': version.contract_reference, 'review_note': note})
    version.status, version.approved_by_member, version.approved_on = 'approved', member, timezone.now()
    version.approval_audit = approval
    version.evidence = [{'kind': 'approval', 'evidence_id': str(approval.pk), 'sha256': approval.event_hash}]
    _record(version, key, digest)
    contract = version.contract
    contract.status, contract.version = 'active', contract.version + 1
    contract.save(update_fields=['status', 'version'])
    changed = [_ref(contract, 'contract'), _ref(version, 'contract_version')]
    for candidate in lines:
        line = ContractLine.objects.create(organization=member.organization, contract_version=version,
                 **{key: candidate[key] for key in ('line_no', 'net_cents', 'tax_cents', 'gross_cents')})
        selection_id = uuid.uuid4()
        config = {**deepcopy(candidate['pricing_configuration']), 'currency': 'CNY',
                  'confirmed_by_customer_required': True, 'policy_ref': {'policy_id': str(selection_id), 'version': 1}}
        selection = MethodPolicySelection.objects.create(id=selection_id, organization=member.organization,
            contract_line=line, selected_on=version.effective_on, approved_by_member=member,
            approval_audit=approval, pricing_configuration=config)
        changed.extend([_ref(line, 'contract_line'), _ref(selection, 'method_policy_selection')])
        payments = {}
        for term in candidate['payment_terms']:
            values = {k: v for k, v in term.items() if k != 'key'}
            payment = PaymentTerm.objects.create(organization=member.organization, contract_line=line, **values)
            payments[term['key']] = payment
            changed.append(_ref(payment, 'payment_term'))
        for term in candidate['warranty_terms']:
            warranty = WarrantyTerm.objects.create(organization=member.organization, contract_line=line,
                payment_term=payments[term['payment_key']], **{k: v for k, v in term.items() if k != 'payment_key'})
            changed.append(_ref(warranty, 'warranty_term'))
    event_id = uuid.uuid4()
    DomainEvent.objects.create(event_id=event_id, organization=member.organization, source=approval,
        topic='contracts.version_approved.v1', data={'event_id': str(event_id), 'topic': 'contracts.version_approved.v1',
        'schema_version': 1, 'organization_id': str(member.organization_id), 'source': _ref(version, 'contract_version'),
        'occurred_at': version.approved_on.isoformat(), 'payload': {'changed_refs': changed, 'posting_event_refs': []}})
    return version


def resolve_policy(member, policy_ref, *, line_id, as_of):
    member = _reader(member)
    _keys(policy_ref, {'policy_id', 'version'}, {'policy_id', 'version'})
    when = _date(as_of)
    try:
        selection = MethodPolicySelection.objects.select_related('contract_line__contract_version__contract').get(
            pk=policy_ref['policy_id'], organization=member.organization, contract_line_id=line_id, status='approved')
    except (MethodPolicySelection.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('规则不存在或无权访问')
    _contract_for_read(member, selection.contract_line.contract_version.contract_id)
    if type(policy_ref['version']) is not int or policy_ref['version'] != selection.version or when < selection.selected_on:
        _reject('规则版本或生效日期不匹配')
    return deepcopy(selection.pricing_configuration)


def approved_contract_totals(member, project_id):
    member = _reader(member)
    project = _project(member, project_id)
    versions = ContractVersion.objects.filter(organization=member.organization, contract__project=project,
        status='approved', contract__status='active').select_related('contract__party').prefetch_related('lines')
    rows = list(versions)
    if not rows:
        return None
    result = {field: sum(getattr(line, field) for version in rows for line in version.lines.all())
              for field in ('net_cents', 'tax_cents', 'gross_cents')}
    result['contracts'] = [{'id': str(v.contract_id), 'contract_code': v.contract.contract_code,
                             'version_id': str(v.pk), 'version_no': v.version_no,
                             'customer': v.contract.party.display_name} for v in rows]
    return result


project_contract_totals = approved_contract_totals
