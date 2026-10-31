"""PH-325 read-only invoice / settlement / revenue / contract-terms pairing.

Every value is derived on request from owner facts (invoicing, settlement,
management_revenue, contracts). Nothing is written: no audit, receipt,
session, migration or second ledger. Missing upstream data is reported as
``None`` plus a ``missing`` code, never as zero.
"""
from datetime import date

from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone

from core.access import visible_projects
from core.models import Membership
from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement

from .models import Invoice, InvoiceAllocation, InvoiceLine, InvoiceReversal, TaxQualification

READ_ROLES = {'owner', 'finance', 'auditor'}
PAGE_LIMIT = 200
REF_LIMIT = 500


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied('无效公司成员上下文') from error
    if not fresh.active or not fresh.user.is_active:
        raise PermissionDenied('此账户尚未加入有效公司工作区')
    return fresh


def _reader(member):
    fresh = _fresh(member)
    if fresh.role == 'member' or fresh.role not in READ_ROLES | {'pm'}:
        raise PermissionDenied('无权查看发票对账')
    return fresh


def _as_of(value):
    if value in (None, ''):
        return timezone.localdate()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError('结算截止日须为 YYYY-MM-DD') from error


def _scoped_projects(member):
    if member.role == 'pm':
        return visible_projects(member)
    from core.models import Project
    return Project.objects.filter(organization=member.organization)


def _project_allowed(member, project_id):
    if project_id in (None, ''):
        return None
    projects = _scoped_projects(member)
    try:
        return projects.get(pk=project_id)
    except (projects.model.DoesNotExist, ValueError):
        raise PermissionDenied('项目不存在或无权访问')


def _source_ref(value):
    if not isinstance(value, dict):
        return None
    namespace, kind, identifier = value.get('namespace'), value.get('kind'), value.get('id')
    if not all(isinstance(item, str) and item for item in (namespace, kind, identifier)):
        return None
    return {'namespace': namespace, 'kind': kind, 'id': identifier,
            'version': value.get('version', 1)}


def _issue(code, severity='blocker', **details):
    row = {'code': code, 'severity': severity}
    row.update({key: value for key, value in details.items() if value is not None})
    return row


def _missing(code, ref=None, summary=None, **details):
    row = {'code': code}
    if ref is not None:
        row['ref'] = ref
    if summary is not None:
        row['summary'] = summary
    row.update({key: value for key, value in details.items() if value is not None})
    return row


def _resolve_source(member, ref, as_of):
    """Return (snapshot, project_id, issues, missing) for one allocation ref."""
    if ref is None:
        return None, None, [_issue('invalid_source_ref')], [_missing('source_ref_invalid')]
    namespace, kind, identifier = ref['namespace'], ref['kind'], ref['id']
    org = member.organization
    issues, missing = [], []
    if (namespace, kind) == ('contracts', 'contract_line'):
        try:
            line = ContractLine.objects.select_related('contract_version__contract').get(
                pk=identifier)
        except (ContractLine.DoesNotExist, ValueError):
            return None, None, [_issue('source_missing', namespace=namespace, kind=kind)], \
                [_missing('contract_line_missing', ref)]
        project_id = str(line.project_id)
        if line.contract_version.contract.organization_id != org.pk:
            issues.append(_issue('cross_org_source', source_ref=ref))
        snapshot = {'kind': 'contract_line', 'project_id': project_id,
                    'gross_cents': line.gross_cents, 'net_cents': line.net_cents,
                    'tax_cents': line.tax_cents,
                    'contract_version_id': str(line.contract_version_id)}
        return snapshot, project_id, issues, missing
    if (namespace, kind) == ('settlement', 'open_item'):
        try:
            item = OpenItem.objects.select_related('project').get(pk=identifier)
        except (OpenItem.DoesNotExist, ValueError):
            return None, None, [_issue('source_missing', namespace=namespace, kind=kind)], \
                [_missing('open_item_missing', ref)]
        if item.organization_id != org.pk:
            issues.append(_issue('cross_org_source', source_ref=ref))
        cleared = Clearance.objects.filter(
            organization=org, open_item=item, status='active',
            occurred_on__lte=as_of).aggregate(total=Sum('cleared_cents'))['total'] or 0
        snapshot = {'kind': 'open_item', 'project_id': str(item.project_id),
                    'gross_cents': item.gross_cents, 'tax_cents': item.tax_cents,
                    'cleared_cents': cleared, 'remaining_cents': item.gross_cents - cleared,
                    'status': item.status, 'due_date': item.due_date.isoformat()}
        return snapshot, str(item.project_id), issues, missing
    if (namespace, kind) == ('settlement', 'settlement'):
        try:
            settlement = Settlement.objects.select_related('project').get(pk=identifier)
        except (Settlement.DoesNotExist, ValueError):
            return None, None, [_issue('source_missing', namespace=namespace, kind=kind)], \
                [_missing('settlement_missing', ref)]
        items = OpenItem.objects.filter(settlement=settlement, status='active')
        cleared = Clearance.objects.filter(
            organization=org, open_item__settlement=settlement, status='active',
            occurred_on__lte=as_of).aggregate(total=Sum('cleared_cents'))['total'] or 0
        gross = items.aggregate(total=Sum('gross_cents'))['total'] or settlement.gross_cents
        snapshot = {'kind': 'settlement', 'project_id': str(settlement.project_id),
                    'gross_cents': gross, 'cleared_cents': cleared,
                    'remaining_cents': gross - cleared, 'status': settlement.status}
        return snapshot, str(settlement.project_id), issues, missing
    return None, None, [_issue('unsupported_source_kind', source_ref=ref)], \
        [_missing('unsupported_source', ref)]


def _tax_qualifications(member, invoice):
    rows = []
    for row in TaxQualification.objects.filter(organization=member.organization).order_by('created_at'):
        ref = row.source_ref or {}
        if ref.get('namespace') == 'invoicing' and str(ref.get('id')) == str(invoice.pk):
            rows.append({'id': str(row.pk), 'status': row.status,
                         'qualification_status': row.qualification_status,
                         'tax_cents': row.tax_cents,
                         'decided_on': row.decided_on.isoformat() if row.decided_on else None})
    return rows


def _revenue_for_project(member, project_id, as_of=None):
    """Posted recognition total for a project; None when no posted fact exists."""
    rows = list(RevenueRecognition.objects.filter(
        organization=member.organization, project_id=project_id, status='posted'))
    if as_of is not None:
        rows = [row for row in rows if row.occurred_on <= as_of]
    if not rows:
        return None
    totals = 0
    for row in rows:
        adjustments = RevenueAdjustment.objects.filter(recognition=row, status='posted')
        if as_of is not None:
            adjustments = adjustments.filter(occurred_on__lte=as_of)
        adjustment = adjustments.aggregate(total=Sum('adjustment_cents'))['total'] or 0
        totals += row.recognized_net_cents + adjustment
    return totals


def _invoice_detail(member, invoice, as_of):
    allocations = list(InvoiceAllocation.objects.filter(
        organization=member.organization, invoice_line__invoice=invoice,
        status='active').select_related('invoice_line').order_by('invoice_line__line_no', 'created_at'))
    refs = {}
    rows, issues, missing = [], [], []
    allocated = {'gross_cents': 0, 'net_cents': 0, 'tax_cents': 0}
    projects = set()
    for row in allocations:
        ref = _source_ref(row.business_source_ref)
        snapshot, project_id, source_issues, source_missing = _resolve_source(member, ref, as_of)
        issues.extend(source_issues)
        missing.extend(source_missing)
        if project_id:
            projects.add(project_id)
        key = (ref or {}).get('id')
        if key and key in refs:
            issues.append(_issue('duplicate_pairing', business_source_ref=ref,
                                 first_invoice=refs[key]))
        elif key:
            refs[key] = str(invoice.pk)
        if ref and InvoiceAllocation.objects.filter(
                organization=member.organization, business_source_ref=ref,
                status='active').exclude(invoice_line__invoice=invoice).exists():
            issues.append(_issue('duplicate_pairing', business_source_ref=ref,
                                 cross_invoice=True))
        allocated['gross_cents'] += row.gross_cents
        allocated['net_cents'] += row.net_cents
        allocated['tax_cents'] += row.tax_cents
        rows.append({
            'allocation_ref': row.allocation_ref,
            'business_source_ref': ref,
            'project_id': project_id,
            'gross_cents': row.gross_cents, 'net_cents': row.net_cents,
            'tax_cents': row.tax_cents,
            'source': snapshot,
        })
    unallocated = invoice.gross_cents - allocated['gross_cents']
    if unallocated > 0:
        issues.append(_issue('unallocated_amount', amount_cents=unallocated))
    elif unallocated < 0:
        issues.append(_issue('over_allocated', amount_cents=unallocated))
    reversals = list(invoice.reversals.filter(
        organization=member.organization, status='active'))
    reversed_total = sum(item.gross_cents for item in reversals)
    if reversed_total and (invoice.gross_cents + reversed_total) < 0:
        issues.append(_issue('reversal_exceeds_invoice', amount_cents=reversed_total))
    lines = [{'line_no': line.line_no, 'gross_cents': line.gross_cents,
              'net_cents': line.net_cents, 'tax_cents': line.tax_cents}
             for line in InvoiceLine.objects.filter(invoice=invoice).order_by('line_no')]
    tax_rows = _tax_qualifications(member, invoice)
    if invoice.tax_cents > 0 and not tax_rows:
        missing.append(_missing('tax_qualification_missing', invoice.invoice_ref))
    if any(row['status'] == 'pending' or row['qualification_status'] == 'unknown'
           for row in tax_rows):
        issues.append(_issue('tax_unconfirmed', invoice_ref=invoice.invoice_ref))
    net_by_project = {}
    for row in rows:
        if row['project_id']:
            net_by_project[row['project_id']] = (
                net_by_project.get(row['project_id'], 0) + row['net_cents'])
    recognized_by_project = {}
    for project_id, net_allocated in sorted(net_by_project.items()):
        recognized = _revenue_for_project(member, project_id, as_of)
        recognized_by_project[project_id] = recognized
        if recognized is None:
            missing.append(_missing('revenue_missing', project_id=project_id))
        elif recognized != 0 and recognized != net_allocated:
            issues.append(_issue('revenue_invoice_mismatch', project_id=project_id,
                                 recognized_net_cents=recognized,
                                 allocated_net_cents=net_allocated))
    known = [value for value in recognized_by_project.values() if value is not None]
    recognized = sum(known) if known else None
    detail = {
        'invoice_ref': invoice.invoice_ref,
        'invoice_number': invoice.invoice_number,
        'direction': invoice.direction,
        'status': invoice.status,
        'invoice_date': invoice.invoice_date.isoformat(),
        'counterparty_id': str(invoice.counterparty_id),
        'gross_cents': invoice.gross_cents,
        'net_cents': invoice.net_cents,
        'tax_cents': invoice.tax_cents,
        'currency': getattr(invoice, 'currency', None),
        'project_ids': sorted(projects),
        'lines': lines,
        'allocations': rows,
        'allocated': allocated,
        'unallocated_cents': unallocated,
        'reversed_cents': reversed_total,
        'tax_qualifications': tax_rows,
        'recognized_net_cents': recognized,
        'recognized_by_project': recognized_by_project,
        'issues': issues,
        'missing': missing,
    }
    return detail, (sorted(projects)[0] if projects else None), issues, missing


def _summary(row):
    return {'invoice_ref': row['invoice_ref'], 'invoice_number': row['invoice_number'],
            'status': row['status'], 'invoice_date': row['invoice_date'],
            'gross_cents': row['gross_cents'], 'net_cents': row['net_cents'],
            'tax_cents': row['tax_cents'], 'allocated': row['allocated'],
            'unallocated_cents': row['unallocated_cents'],
            'project_ids': row['project_ids'], 'issue_count': len(row['issues'])}


def invoice_pairing(member, *, invoice_id=None, project_id=None, as_of=None):
    """Read-only invoice pairing snapshot; zero writes or receipts."""
    member = _reader(member)
    day = _as_of(as_of)
    org = member.organization
    project = _project_allowed(member, project_id)
    invoices = Invoice.objects.filter(organization=org)
    if invoice_id not in (None, ''):
        try:
            invoice = invoices.get(pk=invoice_id)
        except (Invoice.DoesNotExist, ValueError):
            raise PermissionDenied('发票不存在或无权访问')
        detail, invoice_project, issues, missing = _invoice_detail(member, invoice, day)
        if project is not None and str(project.pk) not in detail['project_ids']:
            raise PermissionDenied('发票不属于所选项目')
        if member.role == 'pm':
            visible = {str(pk) for pk in _scoped_projects(member).values_list('pk', flat=True)}
            if not (set(detail['project_ids']) & visible):
                raise PermissionDenied('无权查看该发票的对账')
        return {'as_of': day.isoformat(), 'scope': {'kind': 'invoice', 'invoice_id': str(invoice.pk)},
                'invoice': detail, 'issues': issues, 'missing': missing, 'truncated': False}
    rows, org_issues, org_missing, project_ids = [], [], [], set()
    for invoice in invoices.order_by('invoice_date', 'invoice_number')[:REF_LIMIT]:
        detail, invoice_project, issues, missing = _invoice_detail(member, invoice, day)
        if member.role == 'pm':
            visible = {str(pk) for pk in _scoped_projects(member).values_list('pk', flat=True)}
            if not (set(detail['project_ids']) & visible):
                continue
        if project is not None and str(project.pk) not in detail['project_ids']:
            continue
        rows.append(_summary(detail))
        org_issues.extend(issues)
        org_missing.extend(missing)
        project_ids.update(detail['project_ids'])
    totals = {'invoice_gross_cents': sum(row['gross_cents'] for row in rows),
              'invoice_net_cents': sum(row['net_cents'] for row in rows),
              'invoice_tax_cents': sum(row['tax_cents'] for row in rows),
              'allocated_gross_cents': sum(row['allocated']['gross_cents'] for row in rows),
              'unallocated_gross_cents': sum(row['unallocated_cents'] for row in rows)}
    result = {'as_of': day.isoformat(),
              'scope': {'kind': 'project' if project is not None else 'organization',
                        'project_id': str(project.pk) if project is not None else None},
              'invoices': rows, 'totals': totals, 'project_ids': sorted(project_ids),
              'issues': org_issues, 'missing': org_missing, 'truncated': False}
    return result
