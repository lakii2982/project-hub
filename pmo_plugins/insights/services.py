"""Insights read-model services (PH-339, ADR 0013/0014).

Every metric is derived at request time from an existing owner read interface.
The only writes are insights-internal: ``ReconciliationCase`` (canonical
difference tracking) and the ``ReconciliationRefresh`` idempotency receipt.
No ledger, cash, revenue, cost, contract, settlement or period fact is written.
Missing upstream values stay ``None`` plus a ``missing[]`` entry; they are never
coerced to zero.
"""
import hashlib
import json
import re
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_member, require_write
from core.models import Membership, Project
from core.plugin_runtime import is_enabled
from core.services import lock_company

from . import precheck_services
from .models import (ACTIVE_STATUSES, TRANSITIONS, ReconciliationCase,
                     ReconciliationRefresh)

REFRESH_CONTRACT = 'insights.reconciliation.refresh/v1'
CASE_UPDATE_CONTRACT = 'insights.reconciliation.case.update/v1'
WORKBENCH_CONTRACT = 'insights.project_workbench_summary/v1'
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
WRITE_ROLES = {'owner', 'finance'}
CASE_LIMIT = 200
CASE_SCAN_LIMIT = 2000
METRIC_LIMIT = 200
NOTE_LIMIT = 1000


def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), default=str)


def _digest(payload):
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _fresh(member, *, write=False):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('成员无权查看洞察与核对数据')
    if write:
        require_write(fresh)
        if fresh.role not in WRITE_ROLES:
            raise PermissionDenied('只有公司负责人或财务可以刷新或更新核对差异单')
        if not is_enabled(fresh.organization, 'insights'):
            raise PermissionDenied('洞察与核对模块已停用，写入口不可用')
    return fresh


def _token(value):
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('request_token 须为有效 UUID，用于幂等重放')


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


def _month_day(period, as_of):
    day = _day(as_of)
    label = _period_label(period) or day.strftime('%Y-%m')
    if day.strftime('%Y-%m') != label:
        raise ValidationError('period 必须与 as_of 处于同一月份，跨期核对请分别刷新')
    return label, day


def _source_identity(ref):
    if not isinstance(ref, dict):
        return None
    namespace, kind, identity = ref.get('namespace'), ref.get('kind'), ref.get('id')
    if not all(isinstance(part, str) and part for part in (namespace, kind, identity)):
        return None
    return f'{namespace}/{kind}/{identity}'


def _coverage_key(period, issue):
    # Canonical identity: period + domain + owner + normalized source identities
    # (version excluded). Different source facts never collapse into one case.
    identities = sorted(filter(None, (
        _source_identity(ref) for ref in (issue.get('source_refs') or ()))))
    identity_part = ','.join(identities) if identities else 'none'
    return (f'{period}|{issue.get("domain") or "unknown"}|'
            f'{issue.get("owner") or "unknown"}|{identity_part}')


def _case_key(rule_code, coverage_key):
    return hashlib.sha256(f'{rule_code}|{coverage_key}'.encode()).hexdigest()


def _case_dict(case):
    """Canonical ReconciliationCase projection; exactly ADR 0014 fields."""
    return {
        'id': str(case.pk),
        'organization_id': str(case.organization_id),
        'version': case.version,
        'status': case.status,
        'source_refs': case.source_refs or [],
        'difference_cents': case.difference_cents,
        'owner_member_id': str(case.owner_member_id),
        'resolution_note': case.resolution_note or None,
        'case_key': case.case_key,
        'rule_code': case.rule_code,
        'coverage_key': case.coverage_key,
        'snapshot_watermark': case.snapshot_watermark or [],
    }


def _missing(missing_code, domain, *, reason, severity='warning', source_ref=None):
    return {'missing_code': missing_code, 'domain': domain, 'source_ref': source_ref,
            'reason': reason, 'severity': severity}


def _managed_projects(member):
    return Project.objects.filter(organization_id=member.organization_id,
                                  manager_id=member.pk)


def _pm_can_see(member, case):
    if member.role != 'pm':
        return True
    managed = {str(pk) for pk in _managed_projects(member).values_list('pk', flat=True)}
    for ref in case.source_refs or ():
        if isinstance(ref, dict) and ref.get('kind') == 'project' and str(ref.get('id')) in managed:
            return True
    return False


def _visible_cases(member):
    rows = ReconciliationCase.objects.filter(organization_id=member.organization_id)
    if member.role == 'pm':
        # Refresh derives company-scope cases in this slice; a pm view never mixes
        # company cases into managed-project summaries.
        return rows.none()
    return rows


def reconciliation_case_list(member, *, period=None, status=None, owner_member_id=None,
                             project_id=None, as_of=None, limit=CASE_LIMIT):
    """Read-only canonical case list; pm sees only explicit managed-project refs."""
    member = _fresh(member)
    rows = _visible_cases(member)
    if status not in (None, ''):
        if status not in dict(ReconciliationCase._meta.get_field('status').choices):
            raise ValidationError('status 不在冻结的核对差异单状态枚举内')
        rows = rows.filter(status=status)
    if owner_member_id not in (None, ''):
        rows = rows.filter(owner_member_id=owner_member_id,
                           owner_member__organization_id=member.organization_id)
    project = None
    if project_id not in (None, ''):
        project = _project_for(member, project_id)
    if period not in (None, ''):
        label = _period_label(period)
        rows = rows.filter(coverage_key__startswith=f'{label}|')
    day = None
    if as_of not in (None, ''):
        day = _day(as_of)
        rows = rows.filter(created_at__date__lte=day)
    limit = max(1, min(int(limit), CASE_LIMIT))
    items = list(rows.order_by('-created_at', 'id')[:CASE_SCAN_LIMIT])
    if day is not None:
        # The case's own snapshot date is the watermark external_key; a case
        # derived from a later snapshot must not appear in an earlier as_of view.
        items = [case for case in items
                 if (_watermark_as_of(case.snapshot_watermark) or day) <= day]
    if project is not None:
        # Only an explicit project source ref may match; company-scope cases never
        # leak into a project filter.
        items = [case for case in items if any(
            isinstance(ref, dict) and ref.get('kind') == 'project'
            and str(ref.get('id')) == str(project.pk)
            for ref in (case.source_refs or ()))]
    truncated = len(items) > limit
    items = items[:limit]
    missing = []
    if member.role == 'pm':
        missing.append(_missing(
            'reconciliation_company_scope_hidden', 'insights',
            reason='项目经理仅查看显式引用其管理项目的差异单，不混入公司范围差异'))
    return {'contract': REFRESH_CONTRACT, 'scope': 'managed_projects' if member.role == 'pm' else 'company',
            'as_of': (day or timezone.localdate()).isoformat(), 'period': period,
            'cases': [_case_dict(case) for case in items], 'truncated': truncated,
            'missing': missing,
            'estimate_label': '只读核对差异单；由 owner 只读事实派生，本页不写业务事实'}


def reconciliation_case_snapshot(member, case_id):
    member = _fresh(member)
    try:
        case = ReconciliationCase.objects.get(organization_id=member.organization_id,
                                              pk=case_id)
    except (ReconciliationCase.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('核对差异单不存在或无权访问')
    if not _pm_can_see(member, case):
        raise PermissionDenied('项目经理只能查看其管理项目的差异单')
    return _case_dict(case)


def _watermark(snapshot):
    """Canonical SourceRef pointing at the period-precheck snapshot itself.

    The business date travels in the optional ``external_key`` so the case needs
    no non-canonical snapshot_as_of column to reject an older refresh.
    """
    digest = snapshot.get('precheck_digest')
    if not isinstance(digest, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', digest):
        return []
    ref = {'namespace': 'core', 'kind': 'period_precheck', 'id': digest, 'version': 1}
    as_of = snapshot.get('as_of_date')
    if as_of:
        ref['external_key'] = str(as_of)[:200]
    return [ref]


def _watermark_as_of(refs):
    for ref in refs or ():
        if isinstance(ref, dict) and ref.get('external_key'):
            return _iso_day(ref['external_key'])
    return None


def _candidate_cases(snapshot, period_label):
    candidates, skipped, by_key = [], [], {}
    watermark = _watermark(snapshot)
    for issue in snapshot.get('issues') or ():
        code = issue.get('issue_code')
        # future_as_of is a request-scope warning, not a correctness difference.
        if not code or code == 'future_as_of':
            continue
        refs = issue.get('source_refs') if isinstance(issue.get('source_refs'), list) else []
        if not refs:
            # Canonical source_refs requires at least one identity; an issue whose
            # upstream refs could not be normalized is surfaced as missing instead
            # of creating a case that merges unrelated sources under one key.
            skipped.append({'issue_code': code, 'domain': issue.get('domain'),
                            'reason': '来源身份无法规范化，未生成差异单'})
            continue
        coverage = _coverage_key(period_label, issue)
        key = _case_key(code, coverage)
        if key in by_key:
            row = by_key[key]
            existing = {_canonical_json(ref) for ref in row['source_refs']}
            for ref in refs:
                if _canonical_json(ref) not in existing:
                    row['source_refs'].append(ref)
                    existing.add(_canonical_json(ref))
            row['source_refs'].sort(key=lambda ref: (
                str(ref.get('namespace')), str(ref.get('kind')), str(ref.get('id'))))
            if row['difference_cents'] is None and issue.get('difference_cents') is not None:
                row['difference_cents'] = issue.get('difference_cents')
            continue
        difference = _difference_cents(issue)
        row = {
            'case_key': key, 'rule_code': code, 'coverage_key': coverage,
            'source_refs': sorted(refs, key=lambda ref: (
                str(ref.get('namespace')), str(ref.get('kind')), str(ref.get('id')))),
            'difference_cents': difference,
            'snapshot_watermark': watermark,
        }
        by_key[key] = row
        candidates.append(row)
    return candidates, skipped


def _iso_day(value):
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


_MONEY_KEYS = ('cents', 'amount_cents', 'min_cents', 'max_cents', 'gross_cents',
               'net_cents', 'tax_cents', 'value_cents', 'total_cents', 'difference_cents')


def _money(value):
    if type(value) is int:
        return value
    if isinstance(value, dict):
        for key in _MONEY_KEYS:
            if type(value.get(key)) is int:
                return value[key]
    return None


def _difference_cents(issue):
    # Canonical SignedCnyCents is non-null. Prefer the owner-provided difference,
    # then an expected/actual cents pair (owner issues wrap money in dicts such as
    # {'max_cents': 10000}); an issue that carries no monetary difference at all
    # (for example an unclassified cash row) has a true difference of zero.
    value = issue.get('difference_cents')
    if type(value) is int:
        return value
    expected = _money(issue.get('expected_cents', issue.get('expected')))
    actual = _money(issue.get('actual_cents', issue.get('actual')))
    if expected is not None and actual is not None:
        return actual - expected
    return 0


def _same_content(case, candidate):
    return (case.rule_code == candidate['rule_code']
            and case.coverage_key == candidate['coverage_key']
            and (case.source_refs or []) == candidate['source_refs']
            and case.difference_cents == candidate['difference_cents']
            and (case.snapshot_watermark or []) == candidate['snapshot_watermark'])


def refresh_reconciliation_cases(member, *, period, as_of, request_token, dry_run=True):
    """Derive cases from the PH-354 precheck and append replacement rows.

    Company lock + org+token receipt make concurrent refreshes deterministic;
    only one active case may exist per case_key. Replaced draft/pending rows are
    kept as ``superseded``; approved rows are never overwritten by a refresh.
    """
    member = _fresh(member, write=True)
    token = _token(request_token)
    label, day = _month_day(period, as_of)
    payload = {'contract': REFRESH_CONTRACT, 'organization_id': str(member.organization_id),
               'period': label, 'as_of': day.isoformat()}
    digest = _digest(payload)
    today = timezone.localdate()
    warnings = []
    if day > today:
        warnings.append({'issue_code': 'future_as_of', 'severity': 'warning',
                         'reason': '基准日晚于今天；事实按今天截断后派生'})

    with transaction.atomic():
        lock_company(member)
        receipt = ReconciliationRefresh.objects.filter(
            organization_id=member.organization_id, request_token=token).first()
        if receipt is not None:
            if receipt.input_digest != digest:
                raise ValidationError('同一 request_token 的刷新输入不一致')
            return receipt.result

        snapshot = precheck_services.period_precheck_snapshot(
            member, scope='company', period=label, as_of=day.isoformat())
        candidates, skipped_issues = _candidate_cases(snapshot, label)
        keys = [row['case_key'] for row in candidates]
        existing = {case.case_key: case for case in ReconciliationCase.objects.filter(
            organization_id=member.organization_id, case_key__in=keys,
            status__in=ACTIVE_STATUSES)}
        result_cases, created, superseded, unchanged, approved_kept = [], [], [], [], []
        for candidate in candidates:
            case = existing.get(candidate['case_key'])
            if case is not None and case.status == 'approved':
                approved_kept.append(_case_dict(case))
                result_cases.append(_case_dict(case))
                continue
            if case is not None and _same_content(case, candidate):
                unchanged.append(_case_dict(case))
                result_cases.append(_case_dict(case))
                continue
            case_as_of = _watermark_as_of(case.snapshot_watermark) if case is not None else None
            candidate_as_of = _watermark_as_of(candidate['snapshot_watermark'])
            if (case_as_of is not None and candidate_as_of is not None
                    and candidate_as_of < case_as_of):
                # Canonical: an older as_of refresh cannot overwrite a newer snapshot.
                unchanged.append(_case_dict(case))
                result_cases.append(_case_dict(case))
                continue
            if case is not None and not dry_run:
                case.status = 'superseded'
                case.updated_by = member
                case.save(update_fields=['status', 'version', 'updated_at', 'updated_by'])
            if not dry_run:
                created_case = ReconciliationCase.objects.create(
                    organization_id=member.organization_id,
                    owner_member=member,
                    created_by=member,
                    updated_by=member,
                    **candidate)
                view = _case_dict(created_case)
                created.append(view)
                result_cases.append(view)
            else:
                preview = {key: value for key, value in candidate.items()}
                preview.update({'id': None, 'organization_id': str(member.organization_id),
                                'version': 1, 'status': 'draft',
                                'owner_member_id': str(member.pk),
                                'resolution_note': None})
                created.append(preview)
                result_cases.append(preview)
            if case is not None:
                superseded.append(_case_dict(case))

        active_keys = set(keys)
        stale = list(ReconciliationCase.objects.filter(
            organization_id=member.organization_id, status__in=ACTIVE_STATUSES,
            coverage_key__startswith=f'{label}|',
        ).exclude(case_key__in=active_keys).values_list('case_key', flat=True)[:CASE_LIMIT])
        result = {
            'contract': REFRESH_CONTRACT,
            'scope': 'company',
            'period': label,
            'as_of': day.isoformat(),
            'dry_run': bool(dry_run),
            'request_token': str(token),
            'input_digest': digest,
            'watermark': snapshot.get('watermark'),
            'cases': sorted(result_cases, key=lambda row: (row['rule_code'], row['case_key'])),
            'created': sorted(created, key=lambda row: (row['rule_code'], row['case_key'])),
            'superseded': sorted(superseded, key=lambda row: (row['rule_code'], row['case_key'])),
            'unchanged': sorted(unchanged, key=lambda row: (row['rule_code'], row['case_key'])),
            'approved_kept': sorted(approved_kept, key=lambda row: (row['rule_code'], row['case_key'])),
            'stale_case_keys': stale,
            'missing': (snapshot.get('missing') or []) + skipped_issues,
            'warnings': warnings,
            'truncated': bool(snapshot.get('truncated')),
            'estimate_label': '服务端派生的核对差异单；不自动更正、不写账本或业务事实',
        }
        if not dry_run:
            receipt_id = uuid.uuid4()
            result['receipt_id'] = str(receipt_id)
            ReconciliationRefresh.objects.create(
                id=receipt_id, organization_id=member.organization_id,
                request_token=token, input_digest=digest,
                watermark=snapshot.get('watermark') or {},
                result=result, created_by=member)
        return result


def update_reconciliation_case(member, *, case_id, decision, note, expected_version,
                               request_token, dry_run=True):
    """Advance one case through the frozen state machine; never edits business facts."""
    member = _fresh(member, write=True)
    token = _token(request_token)
    if decision not in ('pending', 'approved', 'rejected', 'closed'):
        raise ValidationError('decision 仅支持 pending/approved/rejected/closed')
    if type(expected_version) is not int or expected_version < 1:
        raise ValidationError('expected_version 须为正整数')
    text = '' if note is None else str(note).strip()
    if len(text) > NOTE_LIMIT:
        raise ValidationError('resolution_note 过长')
    if decision in ('approved', 'rejected') and not text:
        raise ValidationError('确认通过或驳回必须填写 resolution_note')
    try:
        case_uuid = uuid.UUID(str(case_id))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError('case_id 须为有效 UUID')
    payload = {'contract': CASE_UPDATE_CONTRACT, 'organization_id': str(member.organization_id),
               'case_id': str(case_uuid), 'decision': decision, 'note': text,
               'expected_version': expected_version}
    digest = _digest(payload)

    with transaction.atomic():
        lock_company(member)
        receipt = ReconciliationRefresh.objects.filter(
            organization_id=member.organization_id, request_token=token).first()
        if receipt is not None:
            if receipt.input_digest != digest:
                raise ValidationError('同一 request_token 的输入不一致')
            return receipt.result
        case = ReconciliationCase.objects.select_for_update().filter(
            organization_id=member.organization_id, pk=case_uuid).first()
        if case is None:
            raise PermissionDenied('核对差异单不存在或无权访问')
        if case.version != expected_version:
            raise ValidationError('核对差异单版本冲突，请刷新后重试')
        if decision not in TRANSITIONS.get(case.status, set()):
            raise ValidationError(f'核对差异单状态不允许从 {case.status} 变为 {decision}')
        preview = _case_dict(case)
        preview.update({'status': decision, 'version': case.version + 1,
                        'resolution_note': text or case.resolution_note})
        result = {'contract': CASE_UPDATE_CONTRACT, 'dry_run': bool(dry_run),
                  'request_token': str(token), 'input_digest': digest,
                  'case': preview}
        if dry_run:
            return result
        case.status = decision
        case.resolution_note = text or case.resolution_note
        case.updated_by = member
        case.save(update_fields=['status', 'resolution_note', 'version', 'updated_at',
                                 'updated_by'])
        result['case'] = _case_dict(case)
        receipt_id = uuid.uuid4()
        result['receipt_id'] = str(receipt_id)
        ReconciliationRefresh.objects.create(
            id=receipt_id, organization_id=member.organization_id,
            request_token=token, input_digest=digest,
            watermark=case.snapshot_watermark or {},
            result=result, created_by=member)
        return result


def _project_for(member, project_id):
    if project_id in (None, ''):
        raise ValidationError('project_id 必填')
    try:
        project = Project.objects.select_related('manager').get(
            organization_id=member.organization_id, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    if member.role == 'pm' and project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看其管理项目的摘要')
    return project


def _metric(metric_id, name, owner, value, *, as_of, scope, source_ref=None):
    return {'metric_id': metric_id, 'name': name, 'formula_version': 'workbench.v1',
            'as_of_date': as_of, 'scope': scope, 'value_cents': value,
            'owner': owner, 'source_ref': source_ref}


def project_workbench_summary(member, project_id, *, as_of=None):
    """Single-project read-only projection; missing upstreams are null + missing.

    Cash facts carry no project attribution in this slice, so a project-scoped
    cash net is always explicit missing rather than company cash mixed in.
    """
    member = _fresh(member)
    project = _project_for(member, project_id)
    day = _day(as_of)
    facts_day = min(day, timezone.localdate())
    warnings = []
    if day > timezone.localdate():
        warnings.append({'issue_code': 'future_as_of', 'severity': 'warning',
                         'reason': '基准日晚于今天；工作台事实按今天截断'})
    project_ref = {'namespace': 'core', 'kind': 'project', 'id': str(project.pk),
                   'version': project.version if hasattr(project, 'version') else 1}
    missing, metrics = [], []
    values = {}

    def record(key, name, owner, value, *, missing_code, reason, severity='warning',
               source_ref=None):
        values[key] = value
        metrics.append(_metric(key, name, owner, value, as_of=facts_day.isoformat(),
                               scope='project', source_ref=source_ref or project_ref))
        if value is None:
            missing.append(_missing(missing_code, owner, reason=reason,
                                    severity=severity, source_ref=source_ref or project_ref))

    from pmo_plugins.contracts import approval_services as contracts
    try:
        totals = contracts.approved_contract_totals(member, project.pk)
        contract_value = None if totals is None else totals.get('net_cents')
        record('contract_value', '合同额（净额）', 'contracts', contract_value,
               missing_code='contract_value_missing',
               reason='项目没有已生效合同的唯一批准版本')
    except (PermissionDenied, ValidationError):
        record('contract_value', '合同额（净额）', 'contracts', None,
               missing_code='contracts_unavailable', reason='合同只读服务不可读')
    except Exception:
        record('contract_value', '合同额（净额）', 'contracts', None,
               missing_code='contracts_unavailable', reason='合同只读服务异常，按缺失处理')

    from pmo_plugins.management_revenue import services as revenue
    try:
        summary = revenue.project_revenue_summary(member, project.pk, as_of=facts_day)
        record('recognized_revenue', '已确认管理收入', 'management_revenue',
               summary['management_revenue_cents'],
               missing_code='revenue_unavailable', reason='收入只读服务不可读')
    except (PermissionDenied, ValidationError):
        record('recognized_revenue', '已确认管理收入', 'management_revenue', None,
               missing_code='revenue_unavailable', reason='收入只读服务不可读')
    except Exception:
        record('recognized_revenue', '已确认管理收入', 'management_revenue', None,
               missing_code='revenue_unavailable', reason='收入只读服务异常，按缺失处理')

    from pmo_plugins.costing import budget_services
    try:
        budget = budget_services.budget_etc_snapshot(
            member, project_id=project.pk, as_of=facts_day)
        budget_missing = set(budget.get('missing') or ())
        record('cost', '已发生成本', 'costing', budget.get('incurred_cost_cents'),
               missing_code='incurred_cost_missing',
               reason='项目尚无已过账成本事实' if 'incurred_cost' in budget_missing
               else '成本只读服务返回缺失')
        record('budget_etc', '预算 ETC', 'costing', budget.get('etc_cents'),
               missing_code='approved_budget_missing',
               reason='项目没有已批准的预算版本或成本基础缺失'
               if 'approved_budget' in budget_missing else '预算 ETC 上游缺失')
    except (PermissionDenied, ValidationError):
        for key, name in (('cost', '已发生成本'), ('budget_etc', '预算 ETC')):
            record(key, name, 'costing', None, missing_code=f'{key}_unavailable',
                   reason='成本/预算只读服务不可读')
    except Exception:
        for key, name in (('cost', '已发生成本'), ('budget_etc', '预算 ETC')):
            record(key, name, 'costing', None, missing_code=f'{key}_unavailable',
                   reason='成本/预算只读服务异常，按缺失处理')

    # Cash facts have no project attribution: never inject company cash into a project.
    record('cash_net', '项目净现金', 'cash', None,
           missing_code='cash_project_scope_unavailable',
           reason='现金事实无项目归属；项目工作台不以公司现金冒充')

    from pmo_plugins.settlement import coverage_read_services as coverage
    try:
        open_items = coverage.open_item_coverage_snapshot(
            member, as_of=facts_day, project_id=project.pk)
        record('open_item_remaining', '未清剩余', 'settlement',
               open_items['summary']['remaining_cents'],
               missing_code='settlement_unavailable', reason='结算未清只读服务不可读')
    except (PermissionDenied, ValidationError):
        record('open_item_remaining', '未清剩余', 'settlement', None,
               missing_code='settlement_unavailable', reason='结算未清只读服务不可读')
    except Exception:
        record('open_item_remaining', '未清剩余', 'settlement', None,
               missing_code='settlement_unavailable', reason='结算未清只读服务异常，按缺失处理')

    # Deduplicate missing by (missing_code, domain, source_ref) without hiding any.
    unique, seen = [], set()
    for entry in missing:
        marker = (entry['missing_code'], entry['domain'],
                  _canonical_json(entry.get('source_ref')))
        if marker not in seen:
            seen.add(marker)
            unique.append(entry)
    metrics = metrics[:METRIC_LIMIT]
    return {
        'contract': WORKBENCH_CONTRACT,
        'project_id': str(project.pk),
        'as_of': day.isoformat(),
        'facts_as_of': facts_day.isoformat(),
        'scope': 'project',
        'role': member.role,
        'contract_value_cents': values.get('contract_value'),
        'recognized_revenue_cents': values.get('recognized_revenue'),
        'cost_cents': values.get('cost'),
        'cash_net_cents': values.get('cash_net'),
        'open_item_remaining_cents': values.get('open_item_remaining'),
        'budget_etc_cents': values.get('budget_etc'),
        'metric_snapshots': metrics,
        'missing': unique,
        'truncated': False,
        'warnings': warnings,
        'estimate_label': '只读项目工作台；字段经各 owner 只读接口派生，缺失一律 null+missing',
    }


def period_precheck(member, *, scope='company', project_id=None, period=None,
                    as_of=None, sections=None):
    """Explicit PH-339 reuse: the frozen PH-354 precheck, no second formula."""
    return precheck_services.period_precheck_snapshot(
        member, scope=scope, project_id=project_id, period=period, as_of=as_of,
        sections=sections)
