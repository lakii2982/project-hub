"""Customer receivable settlement owner: approved term rows become immutable open items.

No cash, tax ledger, management revenue, cost or AccountingEvent is created here.
Cross-owner facts are read only through contracts.approved_terms_snapshot and
delivery.confirmed_line_performance.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.access import require_member, require_project, visible_projects
from core.models import Membership, Project, AccountingPeriod, DomainEvent, SourceConsumption
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import MAX_CENTS, OpenItem, Settlement, SettlementReceipt

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'pm', 'auditor'}
TERM_KINDS = {'payment_term': 'due', 'warranty_term': 'retention'}


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False, check_module=True):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权读取或办理客户结算')
    if check_module and not is_enabled(member.organization, 'settlement'):
        raise PermissionDenied('客户结算模块已停用')
    if write and (not is_enabled(member.organization, 'contracts')
                  or not is_enabled(member.organization, 'delivery')):
        raise PermissionDenied('合同或履约模块已停用，不能办理客户结算')
    return member


def _locked(member):
    member = _fresh(member, write=True)
    lock_company(member)
    return _fresh(member, write=True)


def _identifier(value, message='记录或请求标识无效'):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail(message)


def _optional_identifier(value):
    return None if value in (None, '') else _identifier(value)


def _version(value):
    if type(value) is not int or value < 1:
        _fail('记录版本无效')
    return value


def _dry(value):
    if type(value) is not bool:
        _fail('dry_run须为明确布尔值')
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value <= MAX_CENTS:
        _fail('结算金额须为0至100亿元的整数分')
    return value


def _business_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            result = date.fromisoformat(value)
            if result.isoformat() == value:
                return result
        except ValueError:
            pass
    _fail('结算业务日期无效')


def _text(value, maximum, required=True):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        if required:
            _fail('请填写500字以内的完整原因')
    return value.strip()[:maximum]


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()


def _period(member, settled_on):
    if type(settled_on) is not date or settled_on > timezone.localdate():
        _fail('结算日期须为有效日期且不得未来')
    if member.organization.locked_through and settled_on <= member.organization.locked_through:
        _fail('结算日所在原期间已锁，不能换开放月绕过')
    period = settled_on.strftime('%Y-%m')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=period).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('结算日所在会计期间已关闭')
    return period


def _line_ref(value):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('必须引用真实合同明细及批准版本')
    if value['namespace'] != 'contracts' or value['kind'] != 'contract_line' or type(value['version']) is not int:
        _fail('必须引用真实合同明细及批准版本')
    return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(value['id']),
            'version': value['version']}


def _term_refs(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 1000:
        _fail('请选择1至1000个完整条款行')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'namespace', 'kind', 'id', 'version'}:
            _fail('条款引用无效')
        kind = item['kind']
        if item['namespace'] != 'contracts' or kind not in TERM_KINDS or type(item['version']) is not int:
            _fail('条款引用无效')
        try:
            term_id = uuid.UUID(str(item['id']))
        except (TypeError, ValueError, AttributeError):
            _fail('条款引用无效')
        ref = {'namespace': 'contracts', 'kind': kind, 'id': str(term_id), 'version': item['version']}
        key = (kind, str(term_id))
        if key in seen:
            _fail('同一条款不能重复选择')
        seen.add(key)
        result.append(ref)
    return result


def _cum_net(line, gross_cents):
    if line.gross_cents <= 0:
        _fail('批准合同明细含税额无效')
    return int((Decimal(line.net_cents) * Decimal(gross_cents) / Decimal(line.gross_cents)
                ).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _active_totals(member, line):
    rows = Settlement.objects.filter(organization=member.organization, contract_line=line, status='active')
    return (rows.aggregate(total=Sum('gross_cents'))['total'] or 0,
            rows.aggregate(total=Sum('net_cents'))['total'] or 0)


def _consumed(member, line, term_ref):
    coverage_key = 'term:' + term_ref['kind'] + ':' + term_ref['id']
    return SourceConsumption.objects.filter(organization=member.organization, consumer='settlement',
        source_id=line.pk, coverage_key=coverage_key).exists()


def _derive(member, contract_line_ref, term_refs, settled_on):
    from pmo_plugins.contracts.approval_services import approved_terms_snapshot
    from pmo_plugins.delivery.services import confirmed_line_performance
    terms = approved_terms_snapshot(member, contract_line_ref, settled_on)
    performance = confirmed_line_performance(member, terms['line_ref'], settled_on)
    line = terms['line']
    if performance['contract_line'].pk != line.pk or performance['project'].pk != terms['project_id']:
        _fail('履约来源与批准合同明细不一致')
    if line.gross_cents <= 0 or line.net_cents > line.gross_cents:
        _fail('批准合同明细金额无效')
    available_terms = {(term['term_ref']['kind'], term['term_ref']['id']): term
                       for term in terms['terms']}
    requested = []
    for ref in term_refs:
        term = available_terms.get((ref['kind'], ref['id']))
        if term is None or term['term_ref']['version'] != ref['version']:
            _fail('所选条款不属于该批准合同明细或版本已失效')
        if term['due_date'] is None:
            _fail('所选条款缺少到期日或质保释放日，不能结算')
        if _consumed(member, line, term['term_ref']):
            _fail('所选条款已被其他结算使用')
        requested.append(term)
    order = {(term['term_ref']['kind'], term['term_ref']['id']): index
             for index, term in enumerate(terms['terms'])}
    requested.sort(key=lambda term: order[(term['term_ref']['kind'], term['term_ref']['id'])])
    selected = []
    for term in requested:
        kind = term['term_ref']['kind']
        partition_kind = TERM_KINDS[kind]
        selected.append({
            'kind': kind, 'partition_kind': partition_kind, 'id': term['id'],
            'term_ref': deepcopy(term['term_ref']), 'condition_type': term['condition_type'],
            'gross_cents': _amount(term['gross_cents']),
            'due_date': _business_date(term['due_date']),
            'release_condition': term.get('release_condition') or '',
            'coverage_key': 'term:' + kind + ':' + term['id'],
            'partition_key': partition_kind + ':' + kind + ':' + term['id'],
        })
    new_gross = sum(term['gross_cents'] for term in selected)
    if new_gross <= 0 or new_gross > MAX_CENTS:
        _fail('本次结算含税额无效')
    existing_gross, existing_net = _active_totals(member, line)
    cumulative_gross = existing_gross + new_gross
    if cumulative_gross > line.gross_cents:
        _fail('本次结算超过批准合同明细含税额')
    net_cents = _cum_net(line, cumulative_gross) - _cum_net(line, existing_gross)
    tax_cents = new_gross - net_cents
    if net_cents < 0 or tax_cents < 0:
        _fail('本次结算金额无效')
    confirmed_net = performance['confirmed_net_cents']
    sources = performance['sources']
    if not sources or confirmed_net <= 0:
        _fail('该合同明细尚无真实已确认履约，不能结算')
    if net_cents > confirmed_net - existing_net:
        _fail('本次结算净额超过已确认未结算履约额度')
    running = existing_gross
    items = []
    for term in selected:
        after = running + term['gross_cents']
        item_net = _cum_net(line, after) - _cum_net(line, running)
        item_tax = term['gross_cents'] - item_net
        if item_net < 0 or item_tax < 0:
            _fail('条款分摊金额无效')
        items.append({**term, 'net_cents': item_net, 'tax_cents': item_tax})
        running = after
    if (sum(item['gross_cents'] for item in items) != new_gross
            or sum(item['tax_cents'] for item in items) != tax_cents):
        _fail('条款分摊金额不守恒')
    due_date = min(term['due_date'] for term in selected)
    latest = max(sources, key=lambda source: (source['confirmed_on'],
        str(source['ref']['id']), str(source.get('month_id', ''))))
    terms_snapshot = {
        'line_id': str(terms['line_id']), 'line_ref': deepcopy(terms['line_ref']),
        'line_net_cents': line.net_cents, 'line_tax_cents': line.tax_cents,
        'line_gross_cents': line.gross_cents, 'all_terms': deepcopy(terms['terms']),
        'due_cents': terms['due_cents'], 'retention_cents': terms['retention_cents'],
        'selected': [{key: deepcopy(term[key]) for key in
                      ('kind', 'partition_kind', 'id', 'term_ref', 'condition_type',
                       'gross_cents', 'due_date', 'release_condition', 'coverage_key', 'partition_key')}
                     for term in items],
    }
    evidence = [{'evidence_id': str(terms['approval_audit_id']),
                 'sha256': terms['approval_audit_hash'], 'kind': 'approval'}]
    project = Project.objects.get(organization=member.organization, pk=terms['project_id'])
    return {
        'line': line, 'project': project, 'line_ref': deepcopy(terms['line_ref']),
        'contract_version_id': terms['contract_version_id'], 'selection_id': terms['selection_id'],
        'counterparty_id': terms['counterparty_id'],
        'policy_version': deepcopy(terms['policy_version']),
        'pricing_configuration': deepcopy(terms['pricing_configuration']),
        'terms_snapshot': terms_snapshot, 'source_performance': deepcopy(sources),
        'source_ref': deepcopy(latest['ref']), 'evidence': evidence,
        'settled_on': settled_on, 'due_date': due_date,
        'gross_cents': new_gross, 'net_cents': net_cents, 'tax_cents': tax_cents,
        'items': items,
        'scope': {'kind': 'project', 'id': str(terms['project_id'])},
        'payee_ref': {'kind': 'counterparty', 'id': str(terms['counterparty_id'])},
    }


def _echo(derived, *, gross_cents, net_cents, tax_cents, due_date, direction, payee_ref, scope):
    if gross_cents is not None and gross_cents != derived['gross_cents']:
        _fail('结算含税额由批准条款派生，不得手填或部分取条款金额')
    if net_cents is not None and net_cents != derived['net_cents']:
        _fail('结算净额由批准条款派生，不得手填')
    if tax_cents is not None and tax_cents != derived['tax_cents']:
        _fail('结算税额由批准条款派生，不得手填')
    if due_date is not None and _business_date(due_date) != derived['due_date']:
        _fail('到期日由所选条款派生，不得手填')
    if direction is not None:
        _fail('结算方向由批准来源派生，不得覆盖')
    if payee_ref is not None or scope is not None:
        _fail('收款对象与范围由批准来源派生，不得覆盖')


def _receipt(member, action, request_token, payload, dry_run):
    _dry(dry_run)
    token = _identifier(request_token, '请求标识无效')
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = SettlementReceipt.objects.filter(organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        if dry_run:
            return token, digest, {'dry_run': True, 'existing': True, 'result': deepcopy(row.result)}
        settlement = Settlement.objects.get(organization=member.organization, pk=row.result['id'])
        require_project(member, settlement.project, finance=True)
        return token, digest, settlement
    return token, digest, None


def _finish(member, action, token, digest, settlement):
    SettlementReceipt.objects.create(organization=member.organization, request_token=token,
        action=action, input_digest=digest, result=settlement.settlement_ref, created_by=member)
    return settlement


def _get(member, settlement_id, *, lock=False):
    try:
        rows = Settlement.objects.select_related('project', 'items')
        if lock:
            rows = rows.select_for_update(of=('self',))
        row = rows.get(organization=member.organization, pk=_identifier(settlement_id, '结算记录不存在或无权访问'))
    except Settlement.DoesNotExist:
        raise PermissionDenied('结算记录不存在或无权访问')
    require_project(member, row.project, finance=True)
    return row


def _approved_input(derived, member, approved_version):
    return {
        'settlement_ref': None,  # replaced by the real ref after the row exists
        'version': approved_version,
        'contract_line_ref': derived['line_ref'],
        'contract_version_id': str(derived['contract_version_id']),
        'selection_id': str(derived['selection_id']),
        'counterparty_id': str(derived['counterparty_id']),
        'policy_version': deepcopy(derived['policy_version']),
        'pricing_configuration': deepcopy(derived['pricing_configuration']),
        'terms_snapshot': deepcopy(derived['terms_snapshot']),
        'source_ref': deepcopy(derived['source_ref']),
        'source_performance': deepcopy(derived['source_performance']),
        'evidence': deepcopy(derived['evidence']),
        'settled_on': derived['settled_on'].isoformat(), 'due_date': derived['due_date'].isoformat(),
        'gross_cents': derived['gross_cents'], 'net_cents': derived['net_cents'],
        'tax_cents': derived['tax_cents'], 'scope': deepcopy(derived['scope']),
        'payee_ref': deepcopy(derived['payee_ref']),
        'approved_by_member_id': str(member.pk),
        'approved_at': timezone.now().isoformat(),
    }


@transaction.atomic
def save_settlement(member, *, contract_line_ref, term_refs, request_token, settled_on=None,
                    settlement_id=None, expected_version=None, dry_run=True,
                    gross_cents=None, net_cents=None, tax_cents=None, due_date=None,
                    direction=None, payee_ref=None, scope=None):
    _dry(dry_run)
    member = _locked(member)
    contract_line_ref = _line_ref(contract_line_ref)
    term_refs = _term_refs(term_refs)
    settled_on = timezone.localdate() if settled_on is None else _business_date(settled_on)
    _period(member, settled_on)
    settlement_id = _optional_identifier(settlement_id)
    if settlement_id is None and expected_version is not None:
        _fail('新建结算不接受版本号')
    derived = _derive(member, contract_line_ref, term_refs, settled_on)
    _echo(derived, gross_cents=gross_cents, net_cents=net_cents, tax_cents=tax_cents,
          due_date=due_date, direction=direction, payee_ref=payee_ref, scope=scope)
    payload = {'contract_line_ref': contract_line_ref, 'term_refs': term_refs,
               'settled_on': settled_on.isoformat(), 'settlement_id': str(settlement_id) if settlement_id else None,
               'expected_version': expected_version, 'gross_cents': gross_cents, 'net_cents': net_cents,
               'tax_cents': tax_cents, 'due_date': due_date, 'direction': direction,
               'payee_ref': payee_ref, 'scope': scope}
    token, digest, existing = _receipt(member, 'save', request_token, payload, dry_run)
    if existing is not None:
        return existing
    if settlement_id:
        row = _get(member, settlement_id, lock=True)
        if row.status != 'draft' or row.version != _version(expected_version):
            _fail('仅可编辑当前版本结算草稿')
        if row.contract_line_id != derived['line'].pk:
            _fail('草稿只能修改原合同明细的条款选择')
        row.version += 1
    else:
        row = Settlement(organization=member.organization, created_by=member, version=1)
    for field, value in {
        'project': derived['project'], 'contract_line': derived['line'],
        'contract_version_id': derived['contract_version_id'],
        'selection_id': derived['selection_id'], 'counterparty_id': derived['counterparty_id'],
        'contract_line_ref': derived['line_ref'], 'source_ref': derived['source_ref'],
        'source_performance': derived['source_performance'], 'policy_version': derived['policy_version'],
        'pricing_configuration': derived['pricing_configuration'],
        'terms_snapshot': derived['terms_snapshot'], 'evidence': derived['evidence'],
        'settled_on': settled_on, 'due_date': derived['due_date'],
        'gross_cents': derived['gross_cents'], 'net_cents': derived['net_cents'],
        'tax_cents': derived['tax_cents'], 'status': 'draft',
    }.items():
        setattr(row, field, value)
    if not dry_run:
        row.save()
        audit(member, 'settlement.settlement.saved', row,
              {'terms': derived['terms_snapshot']['selected'], 'gross_cents': row.gross_cents})
        _finish(member, 'save', token, digest, row)
    return row


@transaction.atomic
def submit_settlement(member, settlement_id, *, expected_version, request_token, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, settlement_id, lock=True)
    payload = {'id': str(row.pk), 'expected_version': expected_version}
    token, digest, existing = _receipt(member, 'submit', request_token, payload, dry_run)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status != 'draft':
        _fail('仅可提交草稿结算')
    _period(member, row.settled_on)
    row.status = 'submitted'
    row.submitted_by = member
    row.version += 1
    if not dry_run:
        row.save()
        audit(member, 'settlement.settlement.submitted', row, {'version': row.version})
        _finish(member, 'submit', token, digest, row)
    return row


def _selected_refs(row):
    try:
        selected = row.terms_snapshot.get('selected', [])
        refs = [{'namespace': 'contracts', 'kind': term['kind'], 'id': term['id'],
                 'version': term['term_ref']['version']} for term in selected]
    except (AttributeError, KeyError, TypeError):
        _fail('结算条款冻结快照无效，请重新保存')
    return _term_refs(refs)


@transaction.atomic
def review_settlement(member, settlement_id, *, expected_version, request_token, decision, note,
                      dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, settlement_id, lock=True)
    note = _text(note, 2000)
    if not isinstance(decision, str) or decision not in {'approve', 'return', 'reject'}:
        _fail('审核决定无效')
    payload = {'id': str(row.pk), 'expected_version': expected_version,
               'decision': decision, 'note': note}
    token, digest, existing = _receipt(member, 'review', request_token, payload, dry_run)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status != 'submitted':
        _fail('仅可审核待审核结算')
    if member.pk in {row.created_by_id, row.submitted_by_id}:
        raise PermissionDenied('须由另一位负责人或财务独立审核')
    _period(member, row.settled_on)
    if decision != 'approve':
        row.status = 'draft'
        row.review_note = note
        row.version += 1
        if not dry_run:
            row.save()
            audit(member, 'settlement.settlement.' + ('returned' if decision == 'return' else 'rejected'),
                  row, {'review_note': note})
            _finish(member, 'review', token, digest, row)
        return row
    # Re-read approved terms, confirmed performance and existing consumption under the
    # company lock, then recompute this draft from the current cumulative state.
    derived = _derive(member, row.contract_line_ref, _selected_refs(row), row.settled_on)
    if (derived['gross_cents'] != row.gross_cents or derived['net_cents'] != row.net_cents
            or derived['tax_cents'] != row.tax_cents or derived['due_date'] != row.due_date
            or derived['source_ref'] != row.source_ref
            or derived['source_performance'] != row.source_performance
            or derived['terms_snapshot'] != row.terms_snapshot
            or derived['evidence'] != row.evidence):
        _fail('批准条款或已确认履约累计已变化，请重新保存结算草稿后再审核')
    row.status = 'active'
    row.review_note = note
    row.version += 1
    row.approved_by = member
    row.approved_at = timezone.now()
    row.approved_version = row.version
    approved_input = _approved_input(derived, member, row.version)
    approved_input['settlement_ref'] = row.settlement_ref
    row.approved_input_snapshot = approved_input
    if dry_run:
        return row
    record = audit(member, 'settlement.settlement.activated', row,
                   {'approved_input': approved_input,
                    'approved_digest': _digest(approved_input)})
    row.approval_audit = record
    row.save()
    items = []
    for item in derived['items']:
        items.append(OpenItem(organization=member.organization, project=derived['project'],
            settlement=row, direction='receivable', partition_kind=item['partition_kind'],
            partition_key=item['partition_key'], term_ref=item['term_ref'],
            counterparty_id=derived['counterparty_id'], payee_ref=derived['payee_ref'],
            scope=derived['scope'], source=derived['source_ref'],
            gross_cents=item['gross_cents'], tax_cents=item['tax_cents'],
            due_date=item['due_date'], occurred_on=row.settled_on,
            release_condition=item['release_condition'], status='active', version=1))
    OpenItem.objects.bulk_create(items)
    for item in items:
        SourceConsumption.objects.create(organization=member.organization, consumer='settlement',
            source=row.contract_line_ref, source_id=row.contract_line_id,
            coverage_key=item.partition_key and 'term:' + item.term_ref['kind'] + ':' + item.term_ref['id'],
            amount_cents=item.gross_cents, event_ref=row.settlement_ref)
    for item in items:
        event_id = uuid.uuid4()
        ref = item.item_ref
        DomainEvent.objects.create(event_id=event_id, organization=member.organization, source=record,
            topic='settlement.open_item_created.v1',
            data={'event_id': str(event_id), 'topic': 'settlement.open_item_created.v1',
                  'schema_version': 1, 'organization_id': str(member.organization_id),
                  'source': ref, 'occurred_at': timezone.now().isoformat(),
                  'payload': {'changed_refs': [ref], 'posting_event_refs': []}})
    return _finish(member, 'review', token, digest, row)


@transaction.atomic
def withdraw_settlement(member, settlement_id, *, expected_version, request_token, reason, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    row = _get(member, settlement_id, lock=True)
    reason = _text(reason, 2000)
    payload = {'id': str(row.pk), 'expected_version': expected_version, 'reason': reason}
    token, digest, existing = _receipt(member, 'withdraw', request_token, payload, dry_run)
    if existing is not None:
        return existing
    if row.version != _version(expected_version):
        _fail('记录版本过期，请刷新后重试')
    if row.status not in {'draft', 'submitted'}:
        _fail('仅可撤回草稿或待审核结算')
    row.status = 'withdrawn'
    row.review_note = reason
    row.version += 1
    if not dry_run:
        row.save()
        audit(member, 'settlement.settlement.withdrawn', row, {'reason': reason})
        _finish(member, 'withdraw', token, digest, row)
    return row


def settlement_snapshot(member, settlement_id):
    member = _fresh(member, check_module=False)
    row = _get(member, settlement_id)
    items = list(row.items.order_by('created_at', 'id'))
    return {
        'settlement': row, 'id': str(row.pk), 'settlement_ref': row.settlement_ref,
        'version': row.version, 'status': row.status,
        'project_id': str(row.project_id), 'contract_line_ref': deepcopy(row.contract_line_ref),
        'contract_line_id': str(row.contract_line_id), 'counterparty_id': str(row.counterparty_id),
        'source_ref': deepcopy(row.source_ref), 'source_performance': deepcopy(row.source_performance),
        'policy_version': deepcopy(row.policy_version),
        'pricing_configuration': deepcopy(row.pricing_configuration),
        'terms_snapshot': deepcopy(row.terms_snapshot), 'evidence': deepcopy(row.evidence),
        'settled_on': row.settled_on, 'due_date': row.due_date,
        'gross_cents': row.gross_cents, 'net_cents': row.net_cents, 'tax_cents': row.tax_cents,
        'direction': 'receivable', 'scope': row.scope, 'payee': row.payee_ref,
        'created_by_member_id': str(row.created_by_id),
        'submitted_by_member_id': str(row.submitted_by_id) if row.submitted_by_id else None,
        'approved_by_member_id': str(row.approved_by_id) if row.approved_by_id else None,
        'approved_at': row.approved_at.isoformat() if row.approved_at else None,
        'approved_version': row.approved_version,
        'approval_audit_id': str(row.approval_audit_id) if row.approval_audit_id else None,
        'approved_input_snapshot': deepcopy(row.approved_input_snapshot),
        'items': [open_item_projection(item) for item in items],
        'is_canonical': row.status in {'draft', 'active'},
    }


def open_item_projection(item):
    return {
        'id': str(item.pk), 'item_ref': item.item_ref, 'organization_id': str(item.organization_id),
        'project_id': str(item.project_id), 'settlement_ref': item.settlement.settlement_ref,
        'direction': item.direction, 'partition_kind': item.partition_kind,
        'partition_key': item.partition_key, 'term_ref': deepcopy(item.term_ref),
        'counterparty_id': str(item.counterparty_id), 'payee': deepcopy(item.payee_ref),
        'scope': deepcopy(item.scope), 'source': deepcopy(item.source),
        'gross_cents': item.gross_cents, 'tax_cents': item.tax_cents,
        'remaining_cents': item.gross_cents, 'due_date': item.due_date,
        'occurred_on': item.occurred_on, 'release_condition': item.release_condition,
        'status': item.status, 'version': item.version,
        'created_at': item.created_at.isoformat(),
    }


def open_items_for_settlement(member, settlement_id):
    member = _fresh(member, check_module=False)
    row = _get(member, settlement_id)
    return [open_item_projection(item) for item in row.items.order_by('created_at', 'id')]
