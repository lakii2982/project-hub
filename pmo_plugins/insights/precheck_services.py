"""只读期间预检聚合（PH-354，period.precheck.snapshot/v1 首片）。

严格复用 ``docs/v1.0-development/period-precheck-contract.md`` 的 envelope 与
``precheck_digest`` 定义；issue_code/金额口径完全来自既有 owner 只读服务
（cash/settlement/management_revenue/costing）与 PH-291 跨域总览，
本片只做聚合、权限、期间/锁期判定与 digest，不重算任何差额、不复制核对规则。

零写：不记 audit/receipt/session/outbox，不建表；上游缺失一律 null/missing，
不得以 0 冒充。无 canonical PeriodPrecheck 实体时 ``precheck=None`` +
``period_precheck_missing``，不伪造 canonical 字段。
"""
import hashlib
import json
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import require_member
from core.models import AccountingPeriod, Membership, Project

CONTRACT = 'period.precheck.snapshot/v1'
PURPOSE = 'period_close'
DOMAINS = ('cash', 'settlement', 'revenue', 'cost')
# 期间预检只读 envelope 的 blockers/warnings 上限（ADR 0014：≤500，超出截断）。
ISSUE_LIMIT = 500
# digest 参与字段（contract §8）：排除 precheck_digest 自身与 checks/summary 等派生展示。
_DIGEST_FIELDS = ('contract', 'scope', 'period', 'as_of_date', 'watermark',
                  'issues', 'differences', 'missing')

# 冻结的 severity 映射（contract §5）：只按 owner issue_code 归类，不改写 owner 口径。
_BLOCKER_CODES = frozenset({
    # 结算 remaining 不守恒
    'remaining_mismatch', 'remaining_negative', 'cleared_over_gross',
    'clearance_duplicate_active', 'cash_coverage_over_gross',
    # 收入超额确认 / 终止后确认
    'recognized_over_contract_line', 'settled_exceeds_recognized',
    'recognized_after_termination', 'duplicate_recognition', 'settlement_unreadable',
    # 成本重复入账 / 超额
    'cost_event_duplicate', 'cost_claim_duplicate', 'cost_posting_duplicate',
    'fee_exceeds_cash_gross', 'unapproved_cost_posted',
    # 合同终止期活动（ADR 0014 contracts blockers）
    'terminated_contract', 'terminated_still_active_terms',
    # 期间关闭/锁定
    'period_closed', 'period_locked',
})

_SOURCE_REF_KEYS = ('namespace', 'kind', 'id')


def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), default=str)


def _precheck_digest(payload):
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _severity(issue_code):
    if issue_code in _BLOCKER_CODES or issue_code.endswith('_cross_org'):
        return 'blocker'
    return 'warning'


def _normalize_refs(issue):
    """从 owner issue 提取 source-registry 引用（namespace/kind/id/version）。

    owner issue 结构不一：cash 用 transaction_ref/account_ref 平铺字段，
    revenue/costing 用 refs 列表（可能为 {'xxx_ref': {...}} 包装）。
    只透传，不构造、不补造 id。
    """
    refs = []

    def add(value):
        if not isinstance(value, dict):
            return
        if all(key in value for key in _SOURCE_REF_KEYS):
            ref = {'namespace': str(value['namespace']), 'kind': str(value['kind']),
                   'id': str(value['id'])}
            if 'version' in value:
                ref['version'] = value['version']
            refs.append(ref)
            return
        if len(value) == 1:
            add(next(iter(value.values())))

    for item in issue.get('refs') or ():
        add(item)
    for key, value in issue.items():
        if key in ('refs', 'expected', 'actual', 'message', 'summary'):
            continue
        add(value)
    unique, seen = [], set()
    for ref in sorted(refs, key=lambda row: (row['namespace'], row['kind'], row['id'])):
        marker = (ref['namespace'], ref['kind'], ref['id'], ref.get('version'))
        if marker not in seen:
            seen.add(marker)
            unique.append(ref)
    return unique


def _envelope_issue(domain, owner, issue):
    refs = _normalize_refs(issue)
    code = issue.get('issue_code') or issue.get('code')
    return {'issue_code': code, 'severity': _severity(code), 'domain': domain,
            'source_refs': refs, 'owner': owner,
            'expected': issue.get('expected'), 'actual': issue.get('actual'),
            # 差额口径唯一来源是 owner 服务；本片不重算，恒为 null（不以 0 冒充）。
            'difference_cents': None,
            'message': issue.get('summary'), 'remediation_hint': None,
            'missing_code': None}


def _missing(missing_code, domain, *, reason, severity='warning', source_ref=None):
    return {'missing_code': missing_code, 'domain': domain, 'source_ref': source_ref,
            'reason': reason, 'severity': severity}


def _reader(member):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role in ('member',):
        raise PermissionDenied('仅公司负责人、财务、审阅或项目经理可以查看期间预检')
    return member


def _day(as_of):
    if as_of in (None, ''):
        return timezone.localdate()
    if type(as_of) is date:
        return as_of
    try:
        return date.fromisoformat(str(as_of))
    except (TypeError, ValueError):
        raise ValidationError('as_of 须为 YYYY-MM-DD 日期')


def _period_label(value):
    if value in (None, ''):
        return None
    text = str(value).strip()
    if (len(text) != 7 or text[4] != '-' or not text[:4].isdigit()
            or not text[5:].isdigit() or not 1 <= int(text[5:]) <= 12):
        raise ValidationError('period 须为有效的年月（YYYY-MM）')
    return text


def _scope(member, scope, project_id):
    if scope not in ('company', 'project'):
        raise ValidationError('scope 仅支持 company 或 project')
    if project_id not in (None, ''):
        scope = 'project'
    project = None
    if scope == 'project':
        if project_id in (None, ''):
            if member.role == 'pm':
                raise PermissionDenied('项目经理查看预检必须指定其管理项目')
            raise ValidationError('scope=project 时必须提供 project_id')
        try:
            project = Project.objects.select_related('manager').get(
                organization_id=member.organization_id, pk=project_id)
        except (Project.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied('项目不存在或无权访问')
        if member.role == 'pm' and project.manager_id != member.pk:
            raise PermissionDenied('项目经理只能查看自己管理项目的期间预检')
    elif member.role == 'pm':
        raise PermissionDenied('项目经理只能查看其管理项目的期间预检摘要')
    return scope, project


def _check_sections(sections):
    if sections in (None, ''):
        return DOMAINS
    if isinstance(sections, str):
        sections = [item for item in sections.split(',') if item]
    requested = tuple(sections)
    unknown = set(requested) - set(DOMAINS)
    if unknown:
        raise ValidationError('sections 仅支持 cash/settlement/revenue/cost 子集')
    return requested


def _period_issues(member, period_label, day):
    """期间/锁期只读判定（core owner）；只读 AccountingPeriod/Organization.locked_through。"""
    blockers, missing = [], []
    row = AccountingPeriod.objects.filter(
        organization_id=member.organization_id, period=period_label).first()
    organization = member.organization
    watermark = {'namespace': 'core', 'kind': 'organization',
                 'id': str(member.organization_id), 'version': 1}
    if row is None:
        # 组织已使用期间跟踪但缺少该期间 → 阻塞（contract §5：缺少必需期间）。
        if AccountingPeriod.objects.filter(organization_id=member.organization_id).exists():
            missing.append(_missing('period_not_found', 'period',
                                    reason='组织已启用期间跟踪但缺少该会计期间',
                                    severity='blocker'))
    else:
        period_ref = {'namespace': 'core', 'kind': 'accounting_period',
                      'id': str(row.pk), 'version': row.version}
        if row.status == 'posted':
            blockers.append({'issue_code': 'period_closed', 'severity': 'blocker',
                             'domain': 'period', 'source_refs': [period_ref], 'owner': 'core',
                             'expected': '会计期间开放', 'actual': 'posted',
                             'difference_cents': None, 'message': None,
                             'remediation_hint': None, 'missing_code': None})
        if row.closed_through is not None and row.closed_through >= day:
            blockers.append({'issue_code': 'period_locked', 'severity': 'blocker',
                             'domain': 'period', 'source_refs': [period_ref], 'owner': 'core',
                             'expected': '关账日早于基准日', 'actual': row.closed_through.isoformat(),
                             'difference_cents': None, 'message': None,
                             'remediation_hint': None, 'missing_code': None})
    if organization.locked_through is not None and organization.locked_through >= day:
        blockers.append({'issue_code': 'period_locked', 'severity': 'blocker',
                         'domain': 'period', 'source_refs': [watermark], 'owner': 'core',
                         'expected': '锁账截止日早于基准日',
                         'actual': organization.locked_through.isoformat(),
                         'difference_cents': None, 'message': None,
                         'remediation_hint': None, 'missing_code': None})
    return blockers, missing, watermark


def _owner_issues(member, *, day, sections, scope, project):
    """调用既有 owner 只读服务聚合 issue；失败记 missing，绝不静默按 0 处理。"""
    issues, missing, checks = [], [], []

    def collect(domain, owner, call, missing_code, unavailable_reason):
        try:
            data = call()
        except ValidationError:
            raise
        except PermissionDenied:
            # pm 的项目摘要不含 cash 域（owner 服务拒绝 pm）；显式 missing，不按 0 处理。
            if member.role == 'pm':
                missing.append(_missing(missing_code, domain,
                                        reason=f'{unavailable_reason}（当前角色仅项目摘要）'))
                return
            raise
        except Exception:
            missing.append(_missing(missing_code, domain, reason=unavailable_reason))
            return
        # cash/settlement 返回 snapshot 字典；revenue/costing 直接返回 issue 列表。
        if isinstance(data, list):
            rows, summary, offset_flag = data, {}, None
        else:
            rows = data.get('issues') or []
            summary = data.get('summary') or {}
            offset_flag = data.get('offset_implemented')
        posted = summary.get('open_item_count', summary.get('posted_cash_count'))
        checks.append({'domain': domain, 'source_refs': [],
                       'expected_cents': None, 'actual_cents': None, 'difference_cents': None,
                       'counts': {'items': posted, 'issues': len(rows), 'missing': 0},
                       'issues': [], 'missing': []})
        for row in rows:
            issues.append(_envelope_issue(domain, owner, row))
        if offset_flag is False:
            missing.append(_missing('offset_not_implemented', 'settlement',
                                    reason='现金 offset（PH-241）未实现，offset 按 null 处理'))

    if 'cash' in sections:
        from pmo_plugins.cash import reconciliation_services as cash
        collect('cash', 'cash',
                lambda: cash.cash_bridge_issues(member, as_of=day),
                'cash_bridge_unavailable', '现金分类/桥接只读服务不可读')
    if 'settlement' in sections:
        from pmo_plugins.settlement import coverage_read_services as settlement
        collect('settlement', 'settlement',
                lambda: settlement.open_item_coverage_issues(
                    member, as_of=day,
                    project_id=project.pk if project is not None else None),
                'settlement_coverage_unavailable', '结算未清覆盖只读服务不可读')
    if 'revenue' in sections:
        from pmo_plugins.management_revenue import pairing_read_services as revenue
        collect('revenue', 'management_revenue',
                lambda: revenue.revenue_pairing_issues(member, as_of=day),
                'revenue_pairing_unavailable', '收入配对只读服务不可读')
    if 'cost' in sections:
        from pmo_plugins.costing import bank_charge_pairing_services as costing
        collect('cost', 'costing',
                lambda: costing.bank_charge_pairing_issues(member, as_of=day),
                'bank_charge_pairing_unavailable', '手续费成本配对只读服务不可读')
    return issues, missing, checks


def _contracts_issues(member, *, day, period_label):
    """合同域经 PH-291 跨域总览只读派生（ADR 0014 contracts blockers）。"""
    from pmo_plugins.delivery import operations_read_services as overview
    data = overview.ops_reconciliation_overview(member, as_of=day, period=period_label)
    issues, missing = [], []
    for section in data.get('sections') or []:
        if section.get('key') != 'contracts':
            continue
        for row in section.get('issues') or []:
            envelope = _envelope_issue('contracts', 'contracts', row)
            envelope['source_refs'] = [
                ref for ref in (row.get('refs') or []) if isinstance(ref, dict)]
            issues.append(envelope)
        for code in section.get('missing') or []:
            missing.append(_missing(code, 'contracts', reason='合同只读分区暂不可读'))
    return issues, missing


def period_precheck_snapshot(member, *, scope='company', project_id=None,
                             period=None, as_of=None, sections=None):
    """只读期间预检快照（period.precheck.snapshot/v1）；零写、无迁移、无审计。"""
    member = _reader(member)
    scope, project = _scope(member, scope, project_id)
    requested = _check_sections(sections)
    day = _day(as_of)
    period_label = _period_label(period) or day.strftime('%Y-%m')
    watermark = {'namespace': 'core', 'kind': 'organization',
                 'id': str(member.organization_id), 'version': 1}

    blockers, warnings, missing, checks = [], [], [], []
    period_blockers, period_missing, watermark = _period_issues(member, period_label, day)
    blockers.extend(period_blockers)
    missing.extend(period_missing)
    if day > timezone.localdate():
        warnings.append({'issue_code': 'future_as_of', 'severity': 'warning',
                         'domain': 'period', 'source_refs': [], 'owner': 'insights',
                         'expected': '基准日不晚于今天', 'actual': day.isoformat(),
                         'difference_cents': None, 'message': None,
                         'remediation_hint': None, 'missing_code': None})

    owner_issues, owner_missing, owner_checks = _owner_issues(
        member, day=min(day, timezone.localdate()), sections=requested,
        scope=scope, project=project)
    checks.extend(owner_checks)
    missing.extend(owner_missing)
    try:
        contract_issues, contract_missing = _contracts_issues(
            member, day=min(day, timezone.localdate()), period_label=period_label)
    except (PermissionDenied, ValidationError):
        raise
    except Exception:
        # delivery 总览不是本片 requires 依赖；不可读时显式 missing，不按 0 处理。
        missing.append(_missing('contracts_overview_unavailable', 'contracts',
                                reason='跨域总览只读服务不可读'))
    else:
        owner_issues.extend(contract_issues)
        missing.extend(contract_missing)

    # 上游无 canonical PeriodPrecheck 实体（历史数据缺口）→ precheck=None + missing，
    # 不伪造 canonical id/version/status（contract §2.1）。
    missing.append(_missing('period_precheck_missing', 'period',
                            reason='上游尚无 canonical PeriodPrecheck 实体，仅返回只读派生 envelope'))

    for issue in owner_issues:
        (blockers if issue['severity'] == 'blocker' else warnings).append(issue)

    # issue 去重（issue_code + 排序后的 source_refs）并稳定排序，保证 digest 可复现。
    def dedup(rows):
        unique, seen = [], set()
        for row in sorted(rows, key=lambda item: _canonical_json(item)):
            marker = (row['issue_code'], json.dumps(row['source_refs'], sort_keys=True))
            if marker not in seen:
                seen.add(marker)
                unique.append(row)
        return unique

    blockers, warnings = dedup(blockers), dedup(warnings)
    truncated = len(blockers) + len(warnings) > ISSUE_LIMIT
    if truncated:
        blockers = blockers[:ISSUE_LIMIT]
        warnings = warnings[:ISSUE_LIMIT - len(blockers)]
        missing.append(_missing('result_truncated', 'period',
                                reason='预检 issue 超出上限，已截断', severity='warning'))

    issues = blockers + warnings
    scope_obj = {'kind': scope, 'project_id': str(project.pk) if project is not None else None}
    differences = []
    digest_payload = {field: value for field, value in (
        ('contract', CONTRACT), ('scope', scope_obj), ('period', period_label),
        ('as_of_date', day.isoformat()), ('watermark', watermark),
        ('issues', issues), ('differences', differences), ('missing', missing))}
    return {'contract': CONTRACT,
            'scope': scope_obj,
            'period': period_label,
            'as_of_date': day.isoformat(),
            'watermark': watermark,
            'purpose': PURPOSE,
            # 无 canonical PeriodPrecheck 实体；不伪造。
            'precheck': None,
            'checks': checks,
            'differences': differences,
            'issues': issues,
            'missing': missing,
            'summary': {'issue_count': len(issues),
                        'blocking_count': len(blockers),
                        'warning_count': len(warnings),
                        'missing_count': len(missing),
                        'domains': sorted({issue['domain'] for issue in issues}
                                          | {entry['domain'] for entry in checks})},
            'precheck_digest': _precheck_digest(digest_payload),
            'estimate_label': '只读期间预检；derived values are not an editable balance'}
