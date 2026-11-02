"""Read-only owner adapters for reminder scanning (PH-355).

Adapters may query owner models or named read services. They never persist reminders,
write audit/outbox, send notifications or execute owner business actions.
"""
from datetime import timedelta
import uuid

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.utils import timezone

from .models import AgentToken, CashPlan, LedgerEntry, PluginState, Timesheet
from .plugin_runtime import is_enabled
from .reminder_services import build_reminder


def _empty():
    return {'reminders': [], 'missing': [], 'issues': []}


def _org_ref(member):
    return {'namespace': 'core', 'kind': 'organization',
            'id': str(member.organization_id), 'version': 1}


def _project_ref(project):
    if project is None:
        return None
    return {'namespace': 'core', 'kind': 'project', 'id': str(project.pk), 'version': 1}


def _scopes(roles=(), project=None):
    rows = [{'kind': 'role', 'id': role} for role in roles]
    if project is not None:
        rows.append({'kind': 'project', 'id': str(project.pk)})
    return rows


def _emit(member, *, as_of, issue_code, source_ref, source_owner, project=None,
          roles=(), title, summary, due_on=None, deeplink, refs=(), missing=()):
    return [build_reminder(
        member, issue_code=issue_code, source_ref=source_ref, source_owner=source_owner,
        project_ref=_project_ref(project), recipient_scope=scope, title=title,
        summary=summary, due_on=due_on, as_of=as_of, deeplink=deeplink,
        refs=refs, missing=missing)
        for scope in _scopes(roles, project)]


def _module(member, owner):
    if is_enabled(member.organization, owner):
        return None
    state = PluginState.objects.filter(
        organization_id=member.organization_id, plugin_id=owner).first()
    ref = ({'namespace': 'core', 'kind': 'plugin_state', 'id': str(state.pk), 'version': 1}
           if state else _org_ref(member))
    return {'reminders': [], 'missing': ['module_disabled_history'],
            'issues': [{'issue_code': 'read_model_missing', 'refs': [ref],
                        'expected': 'enabled owner read model',
                        'actual': 'module_disabled_history'}]}


def _read_failure(member, owner, error):
    return {'reminders': [], 'missing': ['read_model_missing'],
            'issues': [{'issue_code': 'read_model_missing', 'refs': [_org_ref(member)],
                        'expected': owner + ' read model',
                        'actual': type(error).__name__}]}


def _period_match(value, period):
    return period is None or (value is not None and value.strftime('%Y-%m') == period)


def _date(value):
    if value is None or isinstance(value, str) is False:
        return value
    from datetime import date
    return date.fromisoformat(value)


def _known_ref(value):
    return (isinstance(value, dict)
            and set(value) == {'namespace', 'kind', 'id', 'version'})


def _owned_ref(member, ref, projects):
    """Resolve a ref before reuse; unknown or foreign objects are never exposed."""
    if not _known_ref(ref):
        return None
    normalized = {'namespace': ref['namespace'], 'kind': ref['kind'],
                  'id': str(ref['id']), 'version': ref['version']}
    if ref['namespace'] == 'core' and ref['kind'] == 'organization':
        return normalized if str(member.organization_id) == normalized['id'] else None
    if ref['namespace'] == 'core' and ref['kind'] == 'project':
        return normalized if normalized['id'] in {str(row.pk) for row in projects} else None
    try:
        model_name = ''.join(part.capitalize() for part in ref['kind'].split('_'))
        model = apps.get_model(ref['namespace'], model_name)
    except (LookupError, ValueError):
        return None
    fields = {field.attname for field in model._meta.fields}
    selected = [name for name in ('organization_id', 'project_id') if name in fields]
    if not selected:
        return None
    try:
        row = model.objects.filter(pk=ref['id']).values(*selected).first()
    except (TypeError, ValueError, ValidationError):
        return None
    if not row:
        return None
    if row.get('organization_id') is not None:
        return normalized if str(row['organization_id']) == str(member.organization_id) else None
    if row.get('project_id') is not None:
        return normalized if str(row['project_id']) in {str(row.pk) for row in projects} else None
    return None


def _project_for_ref(member, ref, projects):
    if not _known_ref(ref):
        return None
    namespace, kind, identifier = ref['namespace'], ref['kind'], str(ref['id'])
    project = None
    if namespace == 'core' and kind == 'project':
        project = next((row for row in projects if str(row.pk) == identifier), None)
    elif namespace == 'contracts' and kind == 'contract_line':
        from pmo_plugins.contracts.models import ContractLine
        row = ContractLine.objects.filter(
            organization_id=member.organization_id, pk=identifier).select_related(
            'contract_version__contract__project').first()
        project = row.contract_version.contract.project if row else None
    elif namespace == 'settlement' and kind == 'open_item':
        from pmo_plugins.settlement.models import OpenItem
        row = OpenItem.objects.filter(
            organization_id=member.organization_id, pk=identifier).select_related('project').first()
        project = row.project if row else None
    elif namespace == 'settlement' and kind == 'settlement':
        from pmo_plugins.settlement.models import Settlement
        row = Settlement.objects.filter(
            organization_id=member.organization_id, pk=identifier).select_related('project').first()
        project = row.project if row else None
    if project is not None and str(project.pk) not in {str(row.pk) for row in projects}:
        return None
    return project


def scan_settlement(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'settlement')
    if disabled:
        return disabled
    from pmo_plugins.settlement import coverage_read_services as coverage
    try:
        snapshot = coverage.open_item_coverage_snapshot(member, as_of=as_of)
    except (PermissionDenied, ValidationError) as error:
        return _read_failure(member, 'settlement', error)
    result = _empty()
    project_by_id = {str(project.pk): project for project in projects}
    from pmo_plugins.settlement.models import Clearance, OpenItem
    from pmo_plugins.cash.models import BankReturn, Refund
    items = OpenItem.objects.filter(
        organization_id=member.organization_id, project__in=projects,
        status='active', occurred_on__lte=as_of, due_date__lt=as_of,
    ).select_related('project').order_by('due_date', 'id')
    for item in items:
        if not _period_match(item.due_date, period):
            continue
        clearances = Clearance.objects.filter(
            organization_id=member.organization_id, open_item=item,
            status='active', occurred_on__lte=as_of)
        cash_ids = set(clearances.values_list('cash_transaction_id', flat=True))
        cleared = clearances.aggregate(total=Sum('cleared_cents'))['total'] or 0
        refunded = Refund.objects.filter(
            organization_id=member.organization_id, original_cash_transaction_id__in=cash_ids,
            status='active', refund_date__lte=as_of).aggregate(
            total=Sum('gross_cents'))['total'] or 0
        returned = BankReturn.objects.filter(
            organization_id=member.organization_id, original_cash_transaction_id__in=cash_ids,
            status='active', return_date__lte=as_of).aggregate(
            total=Sum('gross_cents'))['total'] or 0
        remaining = item.gross_cents - cleared - refunded - returned
        if remaining <= 0:
            continue
        project = item.project
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='settlement_open_item_overdue',
            source_ref=item.item_ref, source_owner='settlement', project=project,
            roles=('owner', 'finance'), title='未清项逾期',
            summary=f'项目 {project.code} 存在逾期未清项，请人工核对来源。',
            due_on=item.due_date, deeplink='/modules/settlement/settlement-coverage-issues/',
            refs=[item.item_ref]))
    pending_codes = {'clearance_cash_missing', 'clearance_cash_cross_org',
                     'clearance_cash_not_posted', 'clearance_cash_direction',
                     'clearance_cash_ref_mismatch', 'clearance_cross_org'}
    pending = [issue for issue in snapshot['issues']
               if issue['issue_code'] in pending_codes and _known_ref(issue.get('clearance_ref'))]
    if pending:
        from pmo_plugins.settlement.models import Clearance
        rows = {str(row.pk): row for row in Clearance.objects.filter(
            organization_id=member.organization_id,
            pk__in=[issue['clearance_ref']['id'] for issue in pending]).select_related(
            'open_item__project')}
        for issue in pending:
            clearance = rows.get(issue['clearance_ref']['id'])
            if (not clearance or clearance.occurred_on > as_of
                    or not _period_match(clearance.occurred_on, period)):
                continue
            project = clearance.open_item.project
            if str(project.pk) not in project_by_id:
                continue
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='settlement_clearance_pending',
                source_ref=clearance.clearance_ref, source_owner='settlement', roles=('finance', 'auditor'),
                title='核销来源待核对', summary='有效核销的来源覆盖仍需人工核对。',
                deeplink='/modules/settlement/clearance-list/',
                refs=[clearance.clearance_ref, clearance.open_item.item_ref]))
    return result


def scan_core(member, *, as_of, period, projects):
    result = _empty()
    if member.role != 'member' and is_enabled(member.organization, 'cash'):
        plans = CashPlan.objects.filter(
            project__in=projects, approved=True, active=True, due_date__lt=as_of,
        ).select_related('project').order_by('due_date', 'id')
        for plan in plans:
            if not _period_match(plan.due_date, period):
                continue
            expected_book = 'cash_in' if plan.direction == 'in' else 'cash_out'
            settled = LedgerEntry.objects.filter(
                document__plan=plan, book=expected_book, occurred_on__lte=as_of,
            ).aggregate(total=Sum('amount_cents'))['total'] or 0
            if plan.amount_cents - settled <= 0:
                continue
            ref = {'namespace': 'core', 'kind': 'cash_plan',
                   'id': str(plan.pk), 'version': 1}
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='cash_plan_overdue', source_ref=ref,
                source_owner='core', project=plan.project, roles=('owner', 'finance'),
                title='现金计划已逾期', summary=f'项目 {plan.project.code} 的批准计划仍需人工跟进。',
                due_on=plan.due_date, deeplink='/finance/', refs=[ref]))
    elif member.role != 'member':
        result['missing'].append('module_disabled_history')
        result['issues'].append({'issue_code': 'read_model_missing', 'refs': [_org_ref(member)],
                                 'expected': 'cash read model', 'actual': 'module_disabled_history'})

    tokens = AgentToken.objects.filter(member=member, revoked_at__isnull=True)
    for token in tokens.order_by('expires_at', 'id'):
        due_on = timezone.localtime(token.expires_at).date()
        if due_on > as_of + timedelta(days=30) or not _period_match(due_on, period):
            continue
        ref = {'namespace': 'core', 'kind': 'agent_token', 'id': str(token.pk), 'version': 1}
        result['reminders'].append(build_reminder(
            member, issue_code='agent_token_expiring', source_ref=ref, source_owner='core',
            project_ref=None, recipient_scope={'kind': 'member', 'id': str(member.pk)},
            title='个人令牌即将到期', summary='本人 Agent 令牌已到期或将在30日内到期，请按需更换。',
            due_on=due_on, as_of=as_of, deeplink='/connections/', refs=[ref]))
    return result


def scan_contracts(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'contracts')
    if disabled:
        return disabled
    from pmo_plugins.contracts.models import Contract, ContractChange
    result = _empty()
    changes = ContractChange.objects.filter(
        organization_id=member.organization_id, contract__project__in=projects,
        status__in=['draft', 'pending_customer'], requested_on__lte=as_of,
    ).select_related('contract__project').order_by('requested_on', 'id')
    for change in changes:
        if not _period_match(change.requested_on, period):
            continue
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='contract_change_pending',
            source_ref=change.source_ref, source_owner='contracts', project=change.contract.project,
            roles=('owner', 'finance'), title='合同变更待处理',
            summary=f'项目 {change.contract.project.code} 存在草稿或待客户确认的变更。',
            deeplink='/modules/contracts/contract-changes/', refs=[change.source_ref]))
    rows = Contract.objects.filter(
        organization_id=member.organization_id, project__in=projects,
        status='terminated', terminated_on__lte=as_of,
    ).select_related('project').order_by('terminated_on', 'id')
    for contract in rows:
        if not _period_match(contract.terminated_on, period):
            continue
        ref = {'namespace': 'contracts', 'kind': 'contract',
               'id': str(contract.pk), 'version': contract.version}
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='contract_terminated', source_ref=ref,
            source_owner='contracts', project=contract.project,
            roles=('owner', 'finance', 'auditor'), title='合同已终止',
            summary=f'项目 {contract.project.code} 的合同终止事实需要核对后续办理。',
            due_on=contract.terminated_on, deeplink='/modules/contracts/contracts/', refs=[ref]))
    return result


def scan_time(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'time')
    if disabled:
        return disabled
    from pmo_plugins.time.models import BillableConfirmation
    result = _empty()
    rows = BillableConfirmation.objects.filter(
        organization_id=member.organization_id, status='draft', confirmed_on__lte=as_of,
    ).order_by('confirmed_on', 'id')
    project_ids = {str(project.pk) for project in projects}
    for row in rows:
        if not _period_match(row.confirmed_on, period):
            continue
        try:
            timesheet = Timesheet.objects.get(
                pk=int(row.timesheet_ref['id']), status='approved', project__in=projects)
        except (KeyError, TypeError, ValueError, Timesheet.DoesNotExist):
            continue
        if str(timesheet.project_id) not in project_ids:
            continue
        project = next(project for project in projects if project.pk == timesheet.project_id)
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='billable_confirmation_pending',
            source_ref=row.source_ref, source_owner='time', project=project, roles=('finance',),
            title='可计费确认待完成', summary=f'项目 {project.code} 的已批准工时仍有确认草稿。',
            deeplink='/modules/time/billable-confirmation-list/', refs=[row.source_ref]))
    return result


def scan_invoicing(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'invoicing')
    if disabled:
        return disabled
    from pmo_plugins.invoicing.models import Invoice, InvoiceAllocation, TaxQualification
    result = _empty()
    rows = TaxQualification.objects.filter(
        organization_id=member.organization_id, status='pending').order_by('created_at', 'id')
    for row in rows:
        invoice_ref = row.source_ref
        invoice = Invoice.objects.filter(
            organization_id=member.organization_id, pk=invoice_ref.get('id')).first() \
            if isinstance(invoice_ref, dict) else None
        if invoice is None or not _period_match(invoice.invoice_date, period):
            continue
        source_ref = {'namespace': 'invoicing', 'kind': 'tax_qualification',
                      'id': str(row.pk), 'version': row.version}
        allocations = InvoiceAllocation.objects.filter(
            organization_id=member.organization_id, invoice_line__invoice=invoice,
            status='active')
        fact_projects = []
        for allocation in allocations:
            project = _project_for_ref(member, allocation.business_source_ref, projects)
            if project and str(project.pk) not in {str(row.pk) for row in fact_projects}:
                fact_projects.append(project)
        refs = [source_ref] + ([invoice_ref] if _known_ref(invoice_ref) else [])
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='tax_qualification_pending', source_ref=source_ref,
            source_owner='invoicing', roles=('finance',), title='税额资格待确认',
            summary='同公司发票的税额资格仍为待确认，请人工核对。',
            deeplink='/modules/invoicing/invoice-tax/', refs=refs,
            missing=[] if fact_projects else ['project_ref_missing']))
        for project in fact_projects:
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='tax_qualification_pending',
                source_ref=source_ref, source_owner='invoicing', project=project,
                title='税额资格待确认', summary=f'项目 {project.code} 的发票税额资格待确认。',
                deeplink='/modules/invoicing/invoice-tax/', refs=refs))
    return result


def scan_costing(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'costing')
    if disabled:
        return disabled
    from pmo_plugins.costing import budget_services
    result = _empty()
    for project in projects:
        try:
            snapshot = budget_services.budget_etc_snapshot(
                member, project_id=project.pk, period=period, as_of=as_of)
        except (PermissionDenied, ValidationError) as error:
            failure = _read_failure(member, 'costing', error)
            result['missing'].extend(failure['missing'])
            result['issues'].extend(failure['issues'])
            continue
        result['missing'].extend(snapshot['missing'])
        project_ref = _project_ref(project)
        if snapshot['budget_missing']:
            source_ref = {'namespace': 'costing', 'kind': 'project_budget_version',
                          'id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                              f'pmo:costing:missing-budget:{member.organization_id}:{project.pk}')),
                          'version': 1}
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='budget_missing', source_ref=source_ref,
                source_owner='costing', project=project, roles=('finance',),
                title='项目缺少批准预算', summary=f'项目 {project.code} 尚无批准预算；缺失不按0处理。',
                deeplink='/modules/costing/budget-etc/', refs=[project_ref],
                missing=['approved_budget']))
        if snapshot['over_budget_cents'] is not None and snapshot['over_budget_cents'] > 0:
            source_ref = {'namespace': 'costing', 'kind': 'budget_etc_snapshot',
                          'id': str(uuid.uuid5(uuid.NAMESPACE_URL,
                              f'pmo:costing:budget-etc:{member.organization_id}:{project.pk}')),
                          'version': 1}
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='budget_overrun', source_ref=source_ref,
                source_owner='costing', project=project, roles=('finance',),
                title='项目预计超出预算', summary=f'项目 {project.code} 的只读预计成本超过批准预算。',
                deeplink='/modules/costing/budget-etc/', refs=[project_ref]))
    return result


def scan_delivery(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'delivery') or _module(member, 'insights')
    if disabled:
        return disabled
    if period is not None and period != as_of.strftime('%Y-%m'):
        return _empty()
    from pmo_plugins.insights import precheck_services
    result = _empty()
    targets = [(None, 'company')] if member.role != 'pm' else [
        (project, 'project') for project in projects]
    for project, scope in targets:
        try:
            snapshot = precheck_services.period_precheck_snapshot(
                member, scope=scope, project_id=project.pk if project else None,
                period=as_of.strftime('%Y-%m'), as_of=as_of)
        except (PermissionDenied, ValidationError) as error:
            failure = _read_failure(member, 'delivery', error)
            result['missing'].extend(failure['missing'])
            result['issues'].extend(failure['issues'])
            continue
        result['missing'].extend(row['missing_code'] for row in snapshot['missing'])
        for issue in snapshot['issues']:
            if issue['severity'] != 'blocker':
                continue
            refs = [owned for owned in (
                _owned_ref(member, ref, projects) for ref in issue['source_refs'])
                if owned is not None]
            source_ref = refs[0] if refs else _org_ref(member)
            fact_project = project or next(
                (candidate for candidate in (
                    _project_for_ref(member, ref, projects) for ref in refs)
                 if candidate is not None), None)
            result['reminders'].extend(_emit(
                member, as_of=as_of, issue_code='close_precheck_blocker', source_ref=source_ref,
                source_owner='delivery', project=fact_project,
                roles=('owner', 'finance', 'auditor'), title='期间预检存在阻塞',
                summary='只读期间预检存在需人工处理的阻塞；未执行关期或结项。',
                deeplink='/modules/insights/period-precheck/', refs=refs))
    return result


def scan_onboarding(member, *, as_of, period, projects):
    if member.role == 'member':
        return _empty()
    disabled = _module(member, 'onboarding')
    if disabled:
        return disabled
    from pmo_plugins.onboarding.models import ImportConflict
    result = _empty()
    rows = ImportConflict.objects.filter(
        organization_id=member.organization_id, resolution__isnull=True,
        preview__status='ready', created_at__date__lte=as_of,
    ).select_related('preview').order_by('created_at', 'id')
    for row in rows:
        if not _period_match(timezone.localtime(row.created_at).date(), period):
            continue
        ref = {'namespace': 'onboarding', 'kind': 'import_conflict',
               'id': str(row.pk), 'version': 1}
        owned_refs = [owned for owned in (
            _owned_ref(member, row.source_ref, projects),) if owned is not None]
        project = next((candidate for candidate in (
            _project_for_ref(member, owned, projects) for owned in owned_refs)
            if candidate is not None), None)
        refs = [ref] + owned_refs
        result['reminders'].extend(_emit(
            member, as_of=as_of, issue_code='import_conflict_pending', source_ref=ref,
            source_owner='onboarding', project=project, roles=('finance',), title='导入冲突待消解',
            summary=f'预览 {row.preview_id} 存在未消解冲突，不会自动提交。',
            deeplink='/modules/onboarding/import-submit/', refs=refs))
    return result


def scan(member, *, as_of, period, projects):
    result = _empty()
    for adapter in (scan_settlement, scan_core, scan_contracts, scan_time,
                    scan_invoicing, scan_costing, scan_delivery, scan_onboarding):
        current = adapter(member, as_of=as_of, period=period, projects=projects)
        result['reminders'].extend(current['reminders'])
        result['missing'].extend(current['missing'])
        result['issues'].extend(current['issues'])
    return result
