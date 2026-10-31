"""PH-330 project budget versions and PH-306 budget_residual_v1 ETC read model."""
from datetime import date
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import AccountingPeriod, LedgerPosting, Membership, Project
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company, require_member
from pmo_plugins.costing.models import (ProjectBudgetLine, ProjectBudgetReceipt,
                                        ProjectBudgetVersion)

MODULE = 'costing'
WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
SUBJECTS = {'labor', 'expense', 'procurement', 'other'}
METHOD = 'budget_residual_v1'
MAX_CENTS = 10 ** 12


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(row)
    if row.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看预算/ETC')
    if write and not is_enabled(row.organization, MODULE):
        raise PermissionDenied('成本模块已停用，不能办理预算')
    return row


def _period(member, value, *, write=True):
    if not isinstance(value, str) or len(value) != 7 or value[4] != '-':
        _fail('预算期间须为YYYY-MM')
    try:
        start = date.fromisoformat(value + '-01')
    except ValueError:
        _fail('预算期间须为YYYY-MM')
    if start.strftime('%Y-%m') != value or start > timezone.localdate():
        _fail('预算期间须为有效且不晚于本月的YYYY-MM')
    if write and member.organization.locked_through and start <= member.organization.locked_through:
        _fail('预算期间已锁账')
    row = AccountingPeriod.objects.filter(organization=member.organization, period=value).first()
    if write and row and (row.status != 'active' or row.closed_through is not None):
        _fail('预算期间已关闭')
    return start


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _ref(value, label, namespace, kind):
    if (not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}
            or value.get('namespace') != namespace or value.get('kind') != kind
            or type(value.get('version')) is not int):
        _fail(f'{label}引用无效')
    return value


def _project(member, project_id):
    try:
        return Project.objects.get(organization=member.organization, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')


def _line_key(subject, contract_ref, cost_ref):
    raw = {'subject': subject, 'contract_line_ref': contract_ref, 'cost_owner_ref': cost_ref}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _validate_lines(member, project, effective_from, lines):
    if not isinstance(lines, list) or not lines or len(lines) > 500:
        _fail('预算行须为非空列表且不超过500项')
    result, keys = [], set()
    for item in lines:
        if not isinstance(item, dict) or set(item) - {'subject', 'amount_cents',
                                                      'contract_line_ref', 'cost_owner_ref'}:
            _fail('预算行字段无效')
        subject = item.get('subject')
        if subject not in SUBJECTS:
            _fail('预算科目无效')
        amount = item.get('amount_cents')
        if type(amount) is not int or not 0 <= amount <= MAX_CENTS:
            _fail('预算金额须为非负整数分且不超过100亿元')
        contract_ref = item.get('contract_line_ref')
        if contract_ref is not None:
            contract_ref = _ref(contract_ref, '合同行', 'contracts', 'contract_line')
            from pmo_plugins.contracts.approval_services import approved_line_snapshot
            snapshot = approved_line_snapshot(member, contract_ref, effective_from)
            if str(snapshot['project_id']) != str(project.pk):
                _fail('预算合同行不属于当前项目')
        cost_ref = item.get('cost_owner_ref')
        if cost_ref is not None:
            _ref(cost_ref, '成本归属', 'costing', 'cost_owner')
        key = _line_key(subject, contract_ref, cost_ref)
        if key in keys:
            _fail('预算行重复（同科目+同引用）')
        keys.add(key)
        result.append({'line_key': key, 'subject': subject, 'amount_cents': amount,
                       'contract_line_ref': contract_ref, 'cost_owner_ref': cost_ref})
    if sum(row['amount_cents'] for row in result) > MAX_CENTS:
        _fail('预算合计超出允许范围')
    return result


def _receipt(member, action, token, digest, dry_run):
    if type(token) is not uuid.UUID:
        try:
            token = uuid.UUID(str(token))
        except (TypeError, ValueError):
            _fail('request_token 无效')
    row = ProjectBudgetReceipt.objects.filter(
        organization=member.organization, request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        return token, row.result
    if dry_run:
        return token, None
    return token, None


@transaction.atomic
def create_project_budget(member, *, project_id, period, effective_from, lines,
                          request_token, based_on_version_id=None, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    project = _project(member, project_id)
    start = _period(member, period)
    try:
        when = date.fromisoformat(str(effective_from))
    except (TypeError, ValueError):
        _fail('预算生效日须为YYYY-MM-DD')
    if when > timezone.localdate() or when.strftime('%Y-%m') != period:
        _fail('预算生效日须在本期间内且不得未来')
    rows = _validate_lines(member, project, when, lines)
    based_on = None
    if based_on_version_id is not None:
        try:
            based_on = ProjectBudgetVersion.objects.get(
                organization=member.organization, project=project, period=period,
                status='approved', pk=based_on_version_id)
        except (ProjectBudgetVersion.DoesNotExist, ValueError, TypeError):
            _fail('依据预算版本不存在或未批准')
    payload = {'project_id': str(project.pk), 'period': period,
               'effective_from': when.isoformat(),
               'lines': rows, 'based_on_version': str(based_on.pk) if based_on else None}
    digest = _digest(payload)
    token, existing = _receipt(member, 'create', request_token, digest, dry_run)
    if existing is not None:
        return existing
    previous = ProjectBudgetVersion.objects.filter(
        organization=member.organization, project=project, period=period).order_by(
        '-version').first()
    version_no = (previous.version if previous else 0) + 1
    if dry_run:
        return {'dry_run': True, 'project_id': str(project.pk), 'period': period,
                'version': version_no, 'effective_from': when.isoformat(),
                'total_cents': sum(row['amount_cents'] for row in rows)}
    version = ProjectBudgetVersion.objects.create(
        organization=member.organization, project=project, period=period,
        version=version_no, status='draft', effective_from=when, created_by=member,
        submitted_by=member, based_on_version=based_on, input_digest=digest)
    for row in rows:
        ProjectBudgetLine.objects.create(organization=member.organization, version=version,
                                         **row)
    result = {'version_ref': {'namespace': 'costing', 'kind': 'project_budget_version',
                              'id': str(version.pk), 'version': version.version},
              'project_id': str(project.pk), 'period': period, 'version': version_no,
              'status': 'draft', 'total_cents': sum(row['amount_cents'] for row in rows)}
    ProjectBudgetReceipt.objects.create(
        organization=member.organization, request_token=token, action='create',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.budget_created', version, result)
    return result


@transaction.atomic
def approve_project_budget(member, version_id, *, request_token, expected_version, dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        version = ProjectBudgetVersion.objects.select_for_update().get(
            organization=member.organization, pk=version_id)
    except (ProjectBudgetVersion.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('预算版本不存在或无权访问')
    payload = {'version_id': str(version.pk), 'expected_version': expected_version}
    digest = _digest(payload)
    token, existing = _receipt(member, 'approve', request_token, digest, dry_run)
    if existing is not None:
        return existing
    if version.status != 'draft' or expected_version != version.version:
        _fail('仅草稿预算可按当前版本号批准')
    if member.pk in {version.created_by_id, version.submitted_by_id}:
        _fail('预算制单人/提交人不能批准自己的预算')
    if not version.effective_from:
        _fail('预算缺少生效日')
    _period(member, version.period)
    if dry_run:
        return {'dry_run': True, 'version_id': str(version.pk), 'status': 'approved'}
    ProjectBudgetVersion.objects.filter(
        organization=member.organization, project=version.project,
        period=version.period, status='approved').exclude(pk=version.pk).update(
        status='superseded')
    version.status = 'approved'
    version.approved_by = member
    version.approved_at = timezone.now()
    version.save()
    result = {'version_ref': {'namespace': 'costing', 'kind': 'project_budget_version',
                              'id': str(version.pk), 'version': version.version},
              'project_id': str(version.project_id), 'period': version.period,
              'status': 'approved', 'approved_by': str(member.pk)}
    ProjectBudgetReceipt.objects.create(
        organization=member.organization, request_token=token, action='approve',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.budget_approved', version, result)
    return result


@transaction.atomic
def reject_project_budget(member, version_id, *, request_token, expected_version, reason,
                          dry_run=True):
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        version = ProjectBudgetVersion.objects.select_for_update().get(
            organization=member.organization, pk=version_id)
    except (ProjectBudgetVersion.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('预算版本不存在或无权访问')
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        _fail('驳回原因须为1至500字')
    payload = {'version_id': str(version.pk), 'expected_version': expected_version,
               'reason': reason.strip()}
    digest = _digest(payload)
    token, existing = _receipt(member, 'reject', request_token, digest, dry_run)
    if existing is not None:
        return existing
    if version.status != 'draft' or expected_version != version.version:
        _fail('仅草稿预算可驳回')
    if dry_run:
        return {'dry_run': True, 'version_id': str(version.pk), 'status': 'rejected'}
    version.status = 'rejected'
    version.reject_reason = reason.strip()
    version.save()
    result = {'version_ref': {'namespace': 'costing', 'kind': 'project_budget_version',
                              'id': str(version.pk), 'version': version.version},
              'status': 'rejected', 'reject_reason': reason.strip()}
    ProjectBudgetReceipt.objects.create(
        organization=member.organization, request_token=token, action='reject',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.budget_rejected', version, result)
    return result


def supersede_project_budget(member, version_id, *, request_token, expected_version,
                             replacement_version_id, dry_run=True):
    """Mark an older approved budget superseded only when a newer approved replacement exists."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        old = ProjectBudgetVersion.objects.select_for_update().get(
            organization=member.organization, pk=version_id)
        new = ProjectBudgetVersion.objects.get(
            organization=member.organization, pk=replacement_version_id,
            project=old.project, period=old.period, status='approved')
    except (ProjectBudgetVersion.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('预算版本不存在或无权访问')
    payload = {'version_id': str(old.pk), 'replacement_version_id': str(new.pk)}
    digest = _digest(payload)
    token, existing = _receipt(member, 'supersede', request_token, digest, dry_run)
    if existing is not None:
        return existing
    if old.status != 'approved' or expected_version != old.version or new.pk == old.pk:
        _fail('仅可被更新且更晚版本的已批准预算取代')
    if dry_run:
        return {'dry_run': True, 'version_id': str(old.pk), 'status': 'superseded'}
    old.status = 'superseded'
    old.save()
    result = {'version_ref': {'namespace': 'costing', 'kind': 'project_budget_version',
                              'id': str(old.pk), 'version': old.version},
              'replacement_version_id': str(new.pk), 'status': 'superseded'}
    ProjectBudgetReceipt.objects.create(
        organization=member.organization, request_token=token, action='supersede',
        input_digest=digest, result=result, created_by=member)
    audit(member, 'plugin.costing.budget_superseded', old, result)
    return result


def budget_etc_snapshot(member, *, project_id, period=None, as_of=None):
    member = _fresh(member)
    project = _project(member, project_id)
    if member.role == 'pm' and project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看其管理项目摘要')
    when = timezone.localdate() if as_of is None else date.fromisoformat(str(as_of))
    if when > timezone.localdate():
        _fail('基准日不能未来')
    budget_query = ProjectBudgetVersion.objects.filter(
        organization=member.organization, project=project, status='approved',
        effective_from__lte=when)
    if period:
        _period(member, period, write=False)
        budget_query = budget_query.filter(period=period)
    budget = budget_query.order_by('-effective_from', '-version').first()
    lines = list(budget.lines.all()) if budget else []
    budget_total = sum(line.amount_cents for line in lines) if budget else None
    cost_rows = LedgerPosting.objects.filter(
        organization=member.organization, book='cost', origin='current', status='posted',
        scope_kind='project', scope_id=str(project.pk))
    if period:
        cost_rows = cost_rows.filter(accounting_event__posting_period=period)
    if as_of is not None:
        cost_rows = cost_rows.filter(accounting_event__occurred_on__lte=when)
    has_cost = cost_rows.exists()
    incurred = (cost_rows.aggregate(total=Sum('amount_cents'))['total'] or 0) if has_cost else None
    missing = []
    if budget is None:
        missing.append('approved_budget')
    if not has_cost:
        missing.append('incurred_cost')
    etc = (max(0, budget_total - incurred)
           if budget is not None and incurred is not None else None)
    eac = (incurred + etc) if etc is not None else None
    variance = (budget_total - eac) if eac is not None else None
    over = max(0, eac - budget_total) if eac is not None else None
    remaining = (budget_total - incurred) if budget_total is not None and incurred is not None else None
    issues = []
    if budget is None:
        issues.append({'issue_code': 'budget_missing',
                       'refs': [{'project_id': str(project.pk)}],
                       'expected': 'approved budget', 'actual': None})
    if not has_cost:
        issues.append({'issue_code': 'incurred_cost_missing',
                       'refs': [{'project_id': str(project.pk)}],
                       'expected': 'posted project cost facts', 'actual': None})
    if budget is not None and not lines:
        issues.append({'issue_code': 'budget_lines_exceed_total',
                       'refs': [{'version_ref': {'namespace': 'costing',
                                                 'kind': 'project_budget_version',
                                                 'id': str(budget.pk),
                                                 'version': budget.version}}],
                       'expected': 'budget lines', 'actual': 0})
    if over:
        issues.append({'issue_code': 'over_budget',
                       'refs': [{'project_id': str(project.pk)}],
                       'expected': budget_total, 'actual': eac})
    if budget is not None:
        issues.append({'issue_code': 'subject_attribution_missing',
                       'refs': [{'project_id': str(project.pk)}],
                       'expected': 'subject-attributed actuals',
                       'actual': 'not implemented in first slice'})
    return {
        'project_id': str(project.pk), 'period': period,
        'as_of': when.isoformat(), 'algorithm_version': METHOD,
        'budget_cents': budget_total, 'incurred_cost_cents': incurred,
        'etc_cents': etc, 'eac_cents': eac, 'budget_variance_cents': variance,
        'over_budget_cents': over, 'remaining_budget_cents': remaining,
        'budget_lines': [{'subject': line.subject, 'amount_cents': line.amount_cents,
                          'contract_line_ref': line.contract_line_ref} for line in lines],
        'budget_missing': budget is None, 'missing': missing, 'issues': issues,
        'subject_actuals': None,
    }


def budget_etc_issues(member, *, as_of=None):
    member = _fresh(member)
    projects = Project.objects.filter(organization=member.organization)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    issues = []
    for project in projects.order_by('code', 'id')[:200]:
        snapshot = budget_etc_snapshot(member, project_id=project.pk, as_of=as_of)
        for issue in snapshot['issues']:
            issues.append({'issue_code': issue['issue_code'],
                           'project_id': str(project.pk),
                           'refs': issue['refs'], 'expected': issue['expected'],
                           'actual': issue['actual']})
    return issues


def integrity_errors():
    errors = []
    for version in ProjectBudgetVersion.objects.all():
        total = sum(line.amount_cents for line in version.lines.all())
        if total > MAX_CENTS:
            errors.append(f'project_budget:{version.pk}:line_total')
        if version.status == 'approved':
            if (version.approved_by_id is None or version.approved_at is None
                    or version.effective_from is None):
                errors.append(f'project_budget:{version.pk}:approved_fields')
            if ProjectBudgetVersion.objects.filter(
                    organization_id=version.organization_id, project_id=version.project_id,
                    period=version.period, status='approved').exclude(pk=version.pk).exists():
                errors.append(f'project_budget:{version.pk}:duplicate_active')
        if version.status == 'rejected' and not version.reject_reason.strip():
            errors.append(f'project_budget:{version.pk}:rejected_reason')
    return errors
