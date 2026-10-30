"""PH-287 read-only approved-terms snapshot and settlement reconciliation.

No write path, migration, canonical topic or business fact is created here. The
service composes the existing contracts and settlement read facts into a
snapshot plus an issue list so approved templates/terms, effective changes,
termination guards and downstream settlement references can be reconciled.
"""
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Sum
from django.utils import timezone

from core.access import visible_projects
from . import approval_services
from .models import (Contract, ContractChange, ContractLine, ContractVersion, PaymentTerm,
                     WarrantyTerm)
from .party_services import READ_ROLES, _fresh_member

MAX_LINES = 200
ISSUE_CODES = {
    'terms_amount_mismatch', 'settlement_partition_mismatch', 'settlement_terms_mismatch',
    'terms_unapproved_referenced', 'stale_version_referenced', 'line_repeated_consumption',
    'cross_project_reference', 'cross_org_reference', 'terminated_still_settled',
    'old_terms_still_active',
}


def _reader(member):
    """History reads stay available after the contracts module is disabled."""
    fresh = _fresh_member(member)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied('无权查看合同条款快照')
    return fresh


def _date(value):
    if value in (None, ''):
        return timezone.localdate()
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('快照日期须为有效的年月日')
    if parsed != date.fromisoformat(str(value)):
        raise ValidationError('快照日期须为有效的年月日')
    if parsed > timezone.localdate():
        raise ValidationError('快照日期不能在未来')
    return parsed


def _contract_for_read(member, contract_id):
    try:
        contract = Contract.objects.select_related('project', 'party').get(
            pk=contract_id, organization=member.organization)
    except (Contract.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('合同不存在或无权访问')
    if member.role == 'pm':
        from core.access import require_project
        require_project(member, contract.project)
    return contract


def _visible_contracts(member):
    rows = Contract.objects.select_related('project', 'party').filter(organization=member.organization)
    if member.role == 'pm':
        rows = rows.filter(project__in=visible_projects(member))
    return rows


def _version_ref(version):
    return {'namespace': 'contracts', 'kind': 'contract_version', 'id': str(version.pk),
            'version': version.version, 'version_no': version.version_no}


def _line_ref(line):
    # The approved read interfaces accept exactly namespace/kind/id/version.
    return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk),
            'version': line.version}


def _change_ref(change):
    return {'namespace': 'contracts', 'kind': 'contract_change', 'id': str(change.pk),
            'version': change.version}


def _contract_ref(contract):
    return {'namespace': 'contracts', 'kind': 'contract', 'id': str(contract.pk),
            'version': contract.version}


def _snapshot_version(contract, as_of):
    versions = contract.versions.filter(status__in=['approved', 'superseded'])
    if as_of is not None:
        versions = versions.filter(effective_on__lte=as_of)
    version = versions.order_by('-version_no', '-id').first()
    if version is None:
        if as_of is not None:
            raise ValidationError('该日期没有生效的合同条款快照')
        version = contract.versions.filter(status='approved').order_by('-version_no', '-id').first()
    return version


def _direct_terms(line):
    payments = list(PaymentTerm.objects.filter(organization=line.organization, contract_line=line,
                                               status='active').order_by('created_at', 'id'))
    warranties = list(WarrantyTerm.objects.filter(organization=line.organization,
                                                  contract_line=line,
                                                  status='active').order_by('created_at', 'id'))
    terms = []
    for term in payments:
        if term.condition_type == 'warranty':
            continue
        terms.append({'kind': 'due', 'term_ref': {'namespace': 'contracts', 'kind': 'payment_term',
                                                  'id': str(term.pk), 'version': term.version},
                      'condition_type': term.condition_type, 'gross_cents': term.gross_cents,
                      'due_date': term.due_date.isoformat() if term.due_date else None,
                      'release_condition': term.release_condition})
    for term in warranties:
        terms.append({'kind': 'retention',
                      'term_ref': {'namespace': 'contracts', 'kind': 'warranty_term',
                                   'id': str(term.pk), 'version': term.version},
                      'condition_type': 'warranty', 'gross_cents': term.gross_cents,
                      'due_date': term.release_on.isoformat() if term.release_on else None,
                      'release_condition': term.release_condition})
    return terms


def _line_snapshot(member, line, as_of):
    """Prefer the owner named read; fall back to a read-only historical projection."""
    readable, reason = True, ''
    try:
        snapshot = approval_services.approved_terms_snapshot(member, _line_ref(line), as_of)
        terms = snapshot['terms']
    except (ValidationError, PermissionDenied) as error:
        readable = False
        reason = '；'.join(getattr(error, 'messages', [str(error)]))
        terms = _direct_terms(line)
    selection = line.policy_selection
    config = selection.pricing_configuration
    return {
        'line_ref': _line_ref(line), 'line_no': line.line_no,
        'template': config.get('template'),
        'template_label': {'fixed_milestone': '固定价节点', 'confirmed_time': '客户确认工时',
                           'fixed_service_period': '固定服务期'}.get(config.get('template'), ''),
        'net_cents': line.net_cents, 'tax_cents': line.tax_cents, 'gross_cents': line.gross_cents,
        'terms': terms,
        'terms_total_cents': sum(term['gross_cents'] for term in terms),
        'readable': readable, 'unreadable_reason': reason,
    }


def _change_summary(contract):
    rows = []
    for change in contract.changes.order_by('-created_at', 'id'):
        after = None
        if change.status == 'approved' and change.change_type != 'termination':
            for version in contract.versions.filter(status__in=['approved', 'superseded']).select_related('approval_audit'):
                payload = (version.approval_audit.payload if version.approval_audit_id else {}) or {}
                if payload.get('change_id') == str(change.pk):
                    after = version
                    break
        before = None
        if after is not None:
            before = contract.versions.filter(version_no=after.version_no - 1).first()
        if before is None:
            before = contract.versions.filter(status='approved').order_by('-version_no', '-id').first()
        effective_on = None
        if change.status == 'approved':
            if change.change_type == 'termination':
                effective_on = contract.terminated_on
            elif after is not None:
                effective_on = after.effective_on
        rows.append({'change_ref': _change_ref(change), 'change_type': change.change_type,
                     'status': change.status, 'requested_on': change.requested_on.isoformat(),
                     'effective_on': effective_on.isoformat() if effective_on else None,
                     'before_version_ref': _version_ref(before) if before else None,
                     'after_version_ref': _version_ref(after) if after else None})
    return rows


def approved_terms_snapshot(member, *, contract_id, as_of=None):
    """Read-only approved version/template/terms snapshot for one contract."""
    member = _reader(member)
    when = _date(as_of)
    contract = _contract_for_read(member, contract_id)
    version = _snapshot_version(contract, when)
    if version is None:
        raise ValidationError('合同尚无已批准版本，无法核对条款')
    lines = list(version.lines.select_related('policy_selection').order_by('line_no', 'id'))
    truncated = len(lines) > MAX_LINES
    lines = lines[:MAX_LINES]
    rendered = [_line_snapshot(member, line, when) for line in lines]
    templates = sorted({row['template'] for row in rendered if row['template']})
    changes = _change_summary(contract) if contract.changes.exists() else []
    return {
        'contract_ref': _contract_ref(contract), 'contract_code': contract.contract_code,
        'project_id': str(contract.project_id), 'status': contract.status,
        'terminated': contract.status == 'terminated',
        'terminated_on': contract.terminated_on.isoformat() if contract.terminated_on else None,
        'version_ref': _version_ref(version),
        'version_status': version.status,
        'effective_on': version.effective_on.isoformat(),
        'pricing_templates': templates,
        'lines': rendered, 'lines_truncated': truncated, 'line_limit': MAX_LINES,
        'changes': changes, 'as_of': when.isoformat(),
        'settlement_guard': {'readable': all(row['readable'] for row in rendered),
                             'terminated': contract.status == 'terminated'},
        'summary_only': member.role == 'pm',
    }


def _issue(code, contract, refs, expected, actual):
    assert code in ISSUE_CODES
    return {'issue_code': code, 'contract_ref': _contract_ref(contract), 'refs': refs,
            'expected': expected, 'actual': actual}


def _settlement_issues(member, contract, when):
    from pmo_plugins.settlement.models import OpenItem, Settlement
    issues = []
    approved = contract.versions.filter(status='approved').order_by('-version_no', '-id').first()
    if approved is None:
        return issues
    for line in approved.lines.select_related('policy_selection').order_by('line_no', 'id'):
        total = sum(term['gross_cents'] for term in _direct_terms(line))
        if total != line.gross_cents:
            issues.append(_issue('terms_amount_mismatch', contract,
                                 [{'line_ref': _line_ref(line)}], line.gross_cents, total))
    superseded = list(contract.versions.filter(status='superseded'))
    for old_version in superseded:
        for line in old_version.lines.all():
            terms = _direct_terms(line)
            if terms:
                issues.append(_issue('old_terms_still_active', contract,
                                     [{'version_ref': _version_ref(old_version),
                                       'line_ref': _line_ref(line)}], 'superseded', 'active'))
    settlements = Settlement.objects.select_related('contract_line', 'contract_version').filter(
        organization=member.organization, contract_line__contract_version__contract=contract,
        status='active', settled_on__lte=when).order_by('settled_on', 'id')
    per_line = {}
    for row in settlements:
        per_line[row.contract_line_id] = per_line.get(row.contract_line_id, 0) + 1
        refs = [{'settlement_ref': row.settlement_ref, 'line_ref': _line_ref(row.contract_line),
                 'version_ref': _version_ref(row.contract_version)}]
        if (row.contract_line.status != 'approved'
                or row.contract_version.status not in {'approved', 'superseded'}):
            issues.append(_issue('terms_unapproved_referenced', contract, refs,
                                 'approved', f'line={row.contract_line.status}/version={row.contract_version.status}'))
        elif row.contract_version.status == 'superseded':
            issues.append(_issue('stale_version_referenced', contract, refs,
                                 'current approved version', _version_ref(row.contract_version)))
        if row.project_id != contract.project_id:
            issues.append(_issue('cross_project_reference', contract, refs,
                                 str(contract.project_id), str(row.project_id)))
        if row.organization_id != row.contract_line.organization_id:
            issues.append(_issue('cross_org_reference', contract, refs,
                                 str(row.contract_line.organization_id), str(row.organization_id)))
        if contract.status == 'terminated' and contract.terminated_on and row.settled_on >= contract.terminated_on:
            issues.append(_issue('terminated_still_settled', contract, refs,
                                 f'no active settlement on/after {contract.terminated_on}',
                                 row.settled_on.isoformat()))
        items_total = OpenItem.objects.filter(settlement=row).aggregate(
            total=Sum('gross_cents'))['total'] or 0
        if items_total != row.gross_cents:
            issues.append(_issue('settlement_partition_mismatch', contract, refs,
                                 row.gross_cents, items_total))
        selected = (row.terms_snapshot or {}).get('selected') or []
        if selected:
            selected_total = sum(int(term.get('gross_cents', 0)) for term in selected)
            if selected_total != row.gross_cents:
                issues.append(_issue('settlement_terms_mismatch', contract, refs,
                                     row.gross_cents, selected_total))
    for line_id, count in per_line.items():
        if count > 1:
            line = ContractLine.objects.get(pk=line_id)
            issues.append(_issue('line_repeated_consumption', contract,
                                 [{'line_ref': _line_ref(line)}], 1, count))
    return issues


def terms_settlement_issues(member, *, as_of=None):
    """Read-only reconciliation anomalies across visible contracts; no side effects."""
    member = _reader(member)
    when = _date(as_of)
    issues = []
    for contract in _visible_contracts(member).order_by('contract_code', 'id'):
        issues.extend(_settlement_issues(member, contract, when))
    return issues
