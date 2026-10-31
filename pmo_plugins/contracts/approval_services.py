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
from . import fx_services
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


def _normalize_lines(contract, lines, *, validate_live=True, member=None, effective_on=None):
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
        _keys(candidate, {'line_no', 'net_cents', 'tax_cents', 'gross_cents',
                          'pricing_configuration', 'payment_terms', 'warranty_terms',
                          'currency', 'original_net_cents', 'original_tax_cents',
                          'original_gross_cents', 'fx_rate_ref'},
              {'line_no', 'net_cents', 'tax_cents', 'gross_cents', 'pricing_configuration'})
        number = candidate['line_no']
        if type(number) is not int or number < 1 or number in line_numbers:
            _reject('明细行号须为不重复正整数')
        line_numbers.add(number)
        currency = candidate.get('currency') or 'CNY'
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isascii():
            _reject('合同行币种须为三位 ISO 4217 代码')
        currency = currency.upper()
        if currency == 'CNY':
            if any(candidate.get(field) is not None for field in
                   ('original_net_cents', 'original_tax_cents', 'original_gross_cents')) \
                    or candidate.get('fx_rate_ref') is not None:
                _reject('CNY 合同行不得携带原币金额或汇率引用')
            net, tax, gross = (_amount(candidate[k]) for k in ('net_cents', 'tax_cents', 'gross_cents'))
            original_net = original_tax = original_gross = None
            fx_ref = None
        else:
            if member is None or effective_on is None:
                _reject('非 CNY 合同行缺少汇率校验上下文')
            original_net = _amount(candidate.get('original_net_cents'))
            original_tax = _amount(candidate.get('original_tax_cents', 0))
            if original_net + original_tax > 10**12:
                _reject('原币含税金额超出允许范围')
            if candidate.get('original_gross_cents') is not None \
                    and candidate.get('original_gross_cents') != original_net + original_tax:
                _reject('原币净额加税额须等于原币含税额')
            prepared = fx_services.prepare_contract_line_fx(
                member, currency=currency, original_net_cents=original_net,
                original_tax_cents=original_tax, effective_on=effective_on,
                fx_rate_ref=candidate.get('fx_rate_ref'))
            net, tax, gross = prepared['net_cents'], prepared['tax_cents'], prepared['gross_cents']
            for field, expected in (('net_cents', net), ('tax_cents', tax), ('gross_cents', gross)):
                if candidate.get(field) is not None and candidate.get(field) != expected:
                    _reject('客户端记账币金额与汇率换算结果不一致')
            original_net = prepared['original_net_cents']
            original_tax = prepared['original_tax_cents']
            original_gross = prepared['original_gross_cents']
            fx_ref = prepared['fx_rate_ref']
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
        result.append({'line_no': number, 'net_cents': net, 'tax_cents': tax,
                       'gross_cents': gross, 'currency': currency,
                       'original_net_cents': original_net, 'original_tax_cents': original_tax,
                       'original_gross_cents': original_gross, 'fx_rate_ref': fx_ref,
                       'pricing_configuration': p, 'payment_terms': payments,
                       'warranty_terms': warranties})
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
    effective_on = _date(effective_on)
    if effective_on > timezone.localdate():
        _reject('首次生效日期不能在未来')
    normalized = _normalize_lines(contract, lines, validate_live=False,
                                  member=member, effective_on=effective_on)
    reference = _text(contract_reference, 240)
    payload = {'contract_id': str(contract.pk), 'version_id': str(version_id) if version_id else None,
               'lines': normalized, 'effective_on': effective_on, 'reference': reference, 'expected_version': expected_version}
    key, digest, existing = _receipt(member, 'save', request_token, payload)
    if existing:
        return existing
    _available(member, contract)
    normalized = _normalize_lines(contract, normalized, member=member, effective_on=effective_on)
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
    _normalize_lines(version.contract, version.draft_content['lines'],
                     member=member, effective_on=version.effective_on)
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
    lines = _normalize_lines(version.contract, version.draft_content['lines'],
                             member=member, effective_on=version.effective_on)
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
        line = ContractLine.objects.create(
            organization=member.organization, contract_version=version,
            **{key: candidate[key] for key in (
                'line_no', 'net_cents', 'tax_cents', 'gross_cents', 'currency',
                'original_net_cents', 'original_tax_cents', 'original_gross_cents',
                'fx_rate_ref')})
        selection_id = uuid.uuid4()
        config = {**deepcopy(candidate['pricing_configuration']), 'currency': candidate['currency'],
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


def approved_line_snapshot(member, line_ref, as_of):
    """One authoritative frozen price read for customer acceptance and income."""
    member = _reader(member)
    _keys(line_ref, {'namespace', 'kind', 'id', 'version'}, {'namespace', 'kind', 'id', 'version'})
    if line_ref['namespace'] != 'contracts' or line_ref['kind'] != 'contract_line' or type(line_ref['version']) is not int:
        _reject('必须引用真实合同明细及整数批准版本')
    when = _date(as_of)
    try:
        line = ContractLine.objects.select_related('contract_version__contract', 'policy_selection').get(
            pk=line_ref['id'], organization=member.organization)
    except (ContractLine.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('合同明细不存在或无权读取')
    version, contract = line.contract_version, line.contract
    _contract_for_read(member, contract.pk)
    # PH-265: a terminated contract stays readable for business dates before its
    # termination date; approved_terms_snapshot rejects settlement on/after it.
    terminated_before = (contract.status == 'terminated' and contract.terminated_on is not None
                         and when < contract.terminated_on)
    if ((contract.status != 'active' and not terminated_before)
            or version.status != 'approved' or line.status != 'approved'
            or line.version != 1 or line.version != line_ref['version'] or when < version.effective_on
            or when > timezone.localdate() or contract.versions.filter(status='approved').count() != 1):
        _reject('只有已生效合同的唯一批准版本可支持本次履约')
    approval = version.approval_audit
    if (not approval or approval.organization_id != member.organization_id
            or approval.action != 'plugin.contracts.version.approved'
            or approval.entity_id != str(version.pk)
            or not version.approved_by_member_id
            or approval.actor_id != version.approved_by_member.user_id
            or version.header_snapshot != _header(contract)
            or version.submitted_digest != _frozen_digest(version)
            or approval.payload.get('submitted_digest') != version.submitted_digest):
        _reject('合同批准依据与冻结摘要不一致')
    candidates = version.draft_content.get('lines', [])
    rows = list(version.lines.select_related('policy_selection'))
    if len(rows) != len(candidates) or {r.line_no for r in rows} != {c['line_no'] for c in candidates}:
        _reject('合同批准明细缺失或与冻结配置不一致')
    for actual in rows:
        candidate = next(c for c in candidates if c['line_no'] == actual.line_no)
        try:
            selection = actual.policy_selection
        except MethodPolicySelection.DoesNotExist:
            _reject('批准明细没有唯一规则来源')
        config = selection.pricing_configuration
        plain = {k: v for k, v in config.items() if k not in {'currency', 'confirmed_by_customer_required', 'policy_ref'}}
        if (actual.organization_id != member.organization_id or actual.status != 'approved'
                or any(getattr(actual, key) != candidate[key] for key in ('net_cents', 'tax_cents', 'gross_cents'))
                or selection.organization_id != member.organization_id or selection.version != 1 or selection.status != 'approved'
                or selection.selected_on != version.effective_on
                or selection.approval_audit_id != approval.pk
                or selection.approved_by_member_id != version.approved_by_member_id
                or config.get('currency') != 'CNY' or config.get('confirmed_by_customer_required') is not True
                or config.get('policy_ref') != {'policy_id': str(selection.pk), 'version': selection.version}
                or plain != candidate['pricing_configuration']):
            _reject('合同价款或规则已偏离原批准内容')
    selection = line.policy_selection
    return {'organization_id': member.organization_id, 'project_id': contract.project_id,
            'contract_id': str(contract.pk), 'contract_code': contract.contract_code,
            'counterparty_id': str(contract.party_id), 'contract_version_id': str(version.pk),
            'contract_version_no': version.version_no, 'contract_version': version.version,
            'line_id': str(line.pk), 'line_no': line.line_no,
            'line_ref': _ref(line, 'contract_line'), 'selection_id': str(selection.pk),
            'policy_version': deepcopy(selection.policy_version),
            'pricing_configuration': deepcopy(selection.pricing_configuration),
            'net_cents': line.net_cents, 'tax_cents': line.tax_cents, 'gross_cents': line.gross_cents,
            'effective_on': version.effective_on.isoformat(), 'digest': version.submitted_digest,
            'approval_audit_id': approval.pk, 'approval_audit_hash': approval.event_hash,
            # PH-275: downstream read-only pairing sees termination instead of a silent read.
            'contract_status': contract.status, 'terminated': contract.status == 'terminated',
            'terminated_on': contract.terminated_on.isoformat() if contract.terminated_on else None}


def approved_line_choices(member):
    """Candidates and prices share the exact same owner validation as writes."""
    member = _reader(member)
    rows = ContractLine.objects.filter(organization=member.organization, status='approved',
        contract_version__status='approved', contract_version__contract__status='active')
    from core.access import visible_projects
    rows = rows.filter(contract_version__contract__project__in=visible_projects(member))
    return [approved_line_snapshot(member, _ref(line, 'contract_line'), timezone.localdate())
            for line in rows.order_by('contract_version__contract__contract_code', 'line_no')]



def approved_terms_snapshot(member, line_ref, as_of):
    """Named read-only payment/warranty terms for one approved line; settlement never reads raw tables."""
    member = _reader(member)
    _keys(line_ref, {'namespace', 'kind', 'id', 'version'}, {'namespace', 'kind', 'id', 'version'})
    when = _date(as_of)
    candidate = ContractLine.objects.select_related('contract_version__contract').filter(
        pk=line_ref.get('id'), organization=member.organization).first()
    if candidate is not None:
        contract = candidate.contract_version.contract
        if (contract.status == 'terminated' and contract.terminated_on is not None
                and when >= contract.terminated_on):
            _reject('合同已终止，不能新建或继续结算')
    snapshot = approved_line_snapshot(member, line_ref, as_of)
    line = ContractLine.objects.select_related('contract_version__contract').get(pk=snapshot['line_id'])
    payments = list(PaymentTerm.objects.filter(organization=member.organization, contract_line=line,
                                                status='active').order_by('created_at', 'id'))
    warranties = list(WarrantyTerm.objects.filter(organization=member.organization, contract_line=line,
                                                  status='active').order_by('created_at', 'id'))
    terms, due_cents = [], 0
    for term in payments:
        if term.condition_type == 'warranty':
            continue
        terms.append({'kind': 'due', 'id': str(term.pk), 'condition_type': term.condition_type,
                      'gross_cents': term.gross_cents,
                      'due_date': term.due_date.isoformat() if term.due_date else None,
                      'release_condition': term.release_condition,
                      'term_ref': {'namespace': 'contracts', 'kind': 'payment_term',
                                   'id': str(term.pk), 'version': term.version}})
        due_cents += term.gross_cents
    retention_cents = 0
    for term in warranties:
        terms.append({'kind': 'retention', 'id': str(term.pk), 'condition_type': 'warranty',
                      'gross_cents': term.gross_cents,
                      'due_date': term.release_on.isoformat() if term.release_on else None,
                      'release_condition': term.release_condition,
                      'term_ref': {'namespace': 'contracts', 'kind': 'warranty_term',
                                   'id': str(term.pk), 'version': term.version}})
        retention_cents += term.gross_cents
    if due_cents + retention_cents != snapshot['gross_cents']:
        raise ValidationError('批准付款条款未覆盖全部含税额，暂不可结算')
    result = dict(snapshot)
    result.update({'line': line, 'terms': terms, 'due_cents': due_cents, 'retention_cents': retention_cents})
    return result



# ----- PH-275 read-only change impact trace (no writes, no recalculations) -----

IMPACT_READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}


def _impact_reader(member):
    """History reads stay available after the contracts module is disabled."""
    from .party_services import READ_ROLES, _fresh_member
    fresh = _fresh_member(member)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('无权查看合同变更影响')
    return fresh


def _version_ref(version):
    return {'namespace': 'contracts', 'kind': 'contract_version', 'id': str(version.pk),
            'version': version.version, 'version_no': version.version_no}


def _line_refs(version):
    if version is None:
        return []
    return [{'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk),
             'version': 1, 'line_no': line.line_no}
            for line in version.lines.order_by('line_no', 'id')]


def _change_version_refs(change):
    """Return (before approved version, after effective version) without reopening history."""
    contract = change.contract
    after = None
    if change.status == 'approved' and change.change_type != 'termination':
        for version in contract.versions.filter(status='approved').select_related('approval_audit'):
            payload = (version.approval_audit.payload if version.approval_audit_id else {}) or {}
            if payload.get('change_id') == str(change.pk):
                after = version
                break
    before = None
    if after is not None:
        before = contract.versions.filter(version_no=after.version_no - 1).first()
    if before is None:
        before = contract.versions.filter(status='approved').order_by('-version_no', '-id').first()
    return before, after


def _impact_amounts(member, line_refs):
    from django.db.models import Count, Sum
    from pmo_plugins.management_revenue.models import RevenueRecognition
    from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
    ids = []
    for row in line_refs:
        try:
            ids.append(uuid.UUID(str(row['id'])))
        except (KeyError, TypeError, ValueError):
            continue
    empty = {'count': 0, 'gross_cents': 0, 'net_cents': 0, 'cleared_cents': 0}
    if not ids:
        return {'settlements': dict(empty), 'open_items': dict(empty),
                'clearances': dict(empty), 'revenue_recognitions': dict(empty)}
    organization = member.organization
    settlements = Settlement.objects.filter(
        organization=organization, contract_line_id__in=ids, status='active').aggregate(
        count=Count('pk'), gross_cents=Sum('gross_cents'))
    open_items = OpenItem.objects.filter(
        organization=organization, settlement__contract_line_id__in=ids,
        status='active').aggregate(count=Count('pk'), gross_cents=Sum('gross_cents'))
    clearances = Clearance.objects.filter(
        organization=organization, open_item__settlement__contract_line_id__in=ids,
        status='active').aggregate(count=Count('pk'), cleared_cents=Sum('cleared_cents'))
    revenues = RevenueRecognition.objects.filter(
        organization=organization, contract_line_id__in=ids, status='posted').aggregate(
        count=Count('pk'), net_cents=Sum('recognized_net_cents'))
    def filled(row, key):
        return {'count': row.get('count') or 0, key: row.get(key) or 0}
    return {'settlements': filled(settlements, 'gross_cents'),
            'open_items': filled(open_items, 'gross_cents'),
            'clearances': filled(clearances, 'cleared_cents'),
            'revenue_recognitions': filled(revenues, 'net_cents')}


def _impact_payload(member, change):
    """One read-only projection; never writes a fact, audit, receipt or event."""
    contract = change.contract
    before, after = _change_version_refs(change)
    lines_before, lines_after = _line_refs(before), _line_refs(after)
    affected = {row['id']: row for row in lines_before + lines_after}.values()
    if not lines_after and change.change_type == 'termination':
        affected = lines_before
    amounts = _impact_amounts(member, list(affected))
    # The business effective date comes from the recorded fact, never from the request date:
    # a termination uses contract.terminated_on, an approved non-termination change uses the
    # new ContractVersion.effective_on; drafts/rejected/withdrawn have no effective date yet.
    effective_on = None
    if change.status == 'approved':
        if change.change_type == 'termination':
            effective_on = contract.terminated_on
        elif after is not None:
            effective_on = after.effective_on
    payload = {
        'change_ref': {'namespace': 'contracts', 'kind': 'contract_change',
                       'id': str(change.pk), 'version': change.version},
        'change_type': change.change_type,
        'change_type_display': change.get_change_type_display(),
        'status': change.status, 'status_display': change.get_status_display(),
        'requested_on': change.requested_on.isoformat(),
        'effective_on': effective_on.isoformat() if effective_on else None,
        'version': change.version,
        'decision_note': change.decision_note,
        'contract_ref': {'namespace': 'contracts', 'kind': 'contract',
                         'id': str(contract.pk), 'version': contract.version},
        'contract_code': contract.contract_code,
        'project_id': str(contract.project_id),
        'terminated': contract.status == 'terminated',
        'terminated_on': contract.terminated_on.isoformat() if contract.terminated_on else None,
        'before_version_ref': _version_ref(before) if before else None,
        'after_version_ref': _version_ref(after) if after else None,
        'lines_before': lines_before, 'lines_after': lines_after,
        'contract_line_refs': list(affected),
        'deltas': {'net_cents': change.net_delta_cents, 'tax_cents': change.tax_delta_cents,
                   'gross_cents': change.gross_delta_cents},
        **amounts,
    }
    if member.role == 'pm':
        # PMs receive the summary for their own managed projects only.
        payload['summary_only'] = True
        payload.pop('decision_note', None)
    else:
        payload['summary_only'] = False
    return payload


def contract_change_impact(member, change_id):
    """Read-only impact snapshot for one change; permission/tenant scoped, no side effects."""
    member = _impact_reader(member)
    from .models import ContractChange
    try:
        change = ContractChange.objects.select_related(
            'contract__project', 'contract__party', 'created_by_member__user',
            'decided_by_member__user').get(pk=change_id, organization=member.organization)
    except (ContractChange.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('变更记录不存在或无权访问')
    contract = _contract_for_read(member, change.contract_id)
    payload = _impact_payload(member, change)
    payload['contract'] = contract
    return payload


def contract_change_impact_list(member, *, contract_id=None):
    """Read-only list of impact summaries; PM sees only managed projects."""
    member = _impact_reader(member)
    from .models import ContractChange
    from core.access import visible_projects
    rows = ContractChange.objects.filter(organization=member.organization).select_related(
        'contract__project', 'contract__party', 'created_by_member__user',
        'decided_by_member__user')
    if contract_id:
        contract = _contract_for_read(member, contract_id)
        rows = rows.filter(contract=contract)
    elif member.role == 'pm':
        rows = rows.filter(contract__project__in=visible_projects(member))
    return [_impact_payload(member, change) for change in rows.order_by('-created_at', 'id')]
