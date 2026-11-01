"""PH-338 read-only invoice import template/preview/batch-result projections.

The sole preview/receipt/submission path remains ``pmo_plugins.onboarding``. This
module only projects the frozen sales-invoice template, persisted previews and
append-only receipts for the invoicing browser page, and exposes the owner source
quota read used by the shared onboarding precheck. It writes no business fact and
adds no second CSV parser, receipt or commit entry.
"""
import uuid
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError

from core.access import require_member
from core.imports import HEADERS, INVOICING_ALLOCATION_KINDS
from core.models import Membership
from core.plugin_runtime import is_enabled
from pmo_plugins.onboarding.models import ImportPreview, ImportSubmissionReceipt

MODULE = 'invoicing'
DEPENDENCIES = ('contracts', 'settlement')
ONBOARDING_MODULE = 'onboarding'
READ_ROLES = {'owner', 'finance', 'auditor'}
WRITE_ROLES = {'owner', 'finance'}
PREVIEW_ROWS = 200


def _fresh(member):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(row)
    return row


def require_reader(member):
    """Owner/finance/auditor history read; kept available when the module is disabled."""
    row = _fresh(member)
    if row.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以查看发票导入')
    return row


def require_writer(member):
    """Owner/finance with invoicing and its import dependencies enabled."""
    row = require_reader(member)
    if row.role not in WRITE_ROLES:
        raise PermissionDenied('仅公司负责人或财务可以导入发票')
    if not is_enabled(row.organization, MODULE):
        raise PermissionDenied('发票模块已停用，不能导入发票')
    for dependency in DEPENDENCIES:
        if not is_enabled(row.organization, dependency):
            raise PermissionDenied('发票导入依赖的合同/结算模块已停用')
    return row


def template_projection():
    return {
        'kind': 'invoicing',
        'label': '发票',
        'columns': list(HEADERS['invoicing']),
        'allocation_kinds': ['%s/%s' % pair for pair in
                             sorted(INVOICING_ALLOCATION_KINDS)],
        'direction': 'sales',
    }


def source_quota_snapshot(member, business_source_ref, *, invoice_date,
                          counterparty_id):
    """Read-only owner quota read (mirrors the PH-252 source rule without writing)."""
    from . import services
    invoice = SimpleNamespace(direction='sales', counterparty_id=counterparty_id,
                              invoice_date=invoice_date)
    return services._source_quota(member, business_source_ref, invoice)


def _preview_row(preview):
    return {'id': str(preview.pk), 'kind': preview.kind, 'status': preview.status,
            'version': preview.version, 'file_name': preview.file_name,
            'rows_digest': preview.rows_digest,
            'row_count': len(preview.rows_json or []),
            'created_at': preview.created_at}


def _receipt_row(receipt):
    return receipt.as_result()


def preview_projection(member, preview_id):
    """One same-org persisted invoice preview with rows and conflicts (read-only)."""
    member = require_reader(member)
    try:
        identifier = uuid.UUID(str(preview_id))
    except (TypeError, ValueError, AttributeError):
        raise PermissionDenied('预览不存在或无权访问')
    preview = ImportPreview.objects.filter(
        organization=member.organization, kind='invoicing', pk=identifier).first()
    if preview is None:
        raise PermissionDenied('预览不存在或无权访问')
    rows = list(preview.rows_json or [])
    return {
        **_preview_row(preview),
        'rows': rows[:PREVIEW_ROWS],
        'truncated': len(rows) > PREVIEW_ROWS,
        'conflicts': [conflict.as_envelope()
                      for conflict in preview.conflicts.all()],
    }


def snapshot(member, *, preview_id=None):
    """Read-only page projection: frozen template, previews, receipts and selection."""
    member = require_reader(member)
    organization = member.organization
    previews = ImportPreview.objects.filter(
        organization=organization, kind='invoicing').order_by(
        '-created_at', 'pk')[:20]
    receipts = ImportSubmissionReceipt.objects.filter(
        organization=organization).order_by('-created_at', 'pk')[:50]
    invoice_receipts = [receipt for receipt in receipts
                        if any(isinstance(ref, dict)
                               and ref.get('namespace') == 'invoicing'
                               for ref in receipt.result_refs or ())]
    selected = None
    if preview_id:
        try:
            selected = preview_projection(member, preview_id)
        except (PermissionDenied, ValidationError):
            selected = None
    enabled = is_enabled(organization, MODULE)
    onboarding_enabled = is_enabled(organization, ONBOARDING_MODULE)
    return {
        'template': template_projection(),
        'module_enabled': enabled,
        'onboarding_enabled': onboarding_enabled,
        'can_write': (member.role in WRITE_ROLES and enabled
                      and onboarding_enabled
                      and all(is_enabled(organization, dependency)
                              for dependency in DEPENDENCIES)),
        'previews': [_preview_row(preview) for preview in previews],
        'receipts': [_receipt_row(receipt) for receipt in invoice_receipts],
        'selected': selected,
    }
