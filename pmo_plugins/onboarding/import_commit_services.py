"""PH-353 onboarding atomic import submission.

Implements ``docs/v1.0-development/import-commit-contract.md`` without copying owner
rules or the PH-354 precheck digest formula:

* preview persistence only stores canonical preview rows/conflicts (no business fact);
* commit re-runs the PH-354 period precheck, compares caller digests, requires all
  blocker conflicts to be explicitly resolved, and executes every row through the
  existing ``core.imports.apply_rows`` owner services inside one company lock and one
  database transaction;
* a request token replays its append-only receipt; the stable import key is unique per
  organization; any row/owner failure rolls back the whole batch (zero half-writes).
"""
import hashlib
import json
import re
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_member
from core.imports import HEADERS, apply_rows
from core.models import Membership
from core.plugin_runtime import is_enabled
from core.services import lock_company
from pmo_plugins.insights.precheck_services import period_precheck_snapshot

from .models import ImportConflict, ImportPreview, ImportSubmissionReceipt

MODULE = 'onboarding'
WRITE_ROLES = {'owner', 'finance'}
DEPENDENCIES = ('contracts', 'settlement', 'cash')
MAX_ROWS = 500
MAX_ROW_ERRORS = 200
MAX_ISSUES = 1000
_ROW_PATTERN = re.compile(r'第(\d+)行')
_BLOCKER_SEVERITIES = {'blocker', 'error', 'blocking'}
_WARNING_SEVERITIES = {'warning', 'warn'}
_SOURCE_REF_KEYS = {'namespace', 'kind', 'id', 'version'}


class ImportCommitRejected(ValidationError):
    """Structured rejection (contract §6); carrying the exact envelope for HTTP/tests."""

    def __init__(self, envelope):
        self.envelope = envelope
        messages = [entry.get('message') or entry.get('issue_code')
                    for entry in envelope.get('row_errors') or ()]
        super().__init__(messages or ['导入提交被拒绝'])


class ImportCommitDenied(PermissionDenied):
    """Permission/ownership/module denial with the same blocker envelope shape."""

    def __init__(self, envelope):
        self.envelope = envelope
        messages = [entry.get('message') or entry.get('issue_code')
                    for entry in envelope.get('row_errors') or ()]
        super().__init__(messages[0] if messages else '导入提交无权执行')


# ---------- canonical helpers (mirror PH-354's canonical JSON rule) ----------

def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), default=str)


def _digest(payload):
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _digest_text(value):
    return str(value or '').strip().lower()


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _as_version(value):
    try:
        version = int(value)
    except (TypeError, ValueError):
        return None
    return version if version >= 1 else None


def _preview_ref(preview):
    return {'namespace': 'onboarding', 'kind': 'import_preview',
            'id': str(preview.pk), 'version': preview.version}


def _source_ref(preview):
    return {'namespace': 'onboarding', 'kind': 'import_preview',
            'id': str(preview.pk), 'version': preview.version}


# ---------- authorization ----------

def _fresh(member):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise ImportCommitDenied(_envelope([_row_error(
            'unauthorized', '公司成员上下文无效')]))
    require_member(row)
    return row


def require_commit_writer(member):
    """Owner/finance only; module and its declared dependencies must be enabled."""
    row = _fresh(member)
    if row.role not in WRITE_ROLES:
        raise ImportCommitDenied(_envelope([_row_error(
            'unauthorized', '仅公司负责人或财务可以提交导入')]))
    if not is_enabled(row.organization, MODULE):
        raise ImportCommitDenied(_envelope([_row_error(
            'module_disabled', '导入与期初模块已停用，不能提交导入')]))
    for dependency in DEPENDENCIES:
        if not is_enabled(row.organization, dependency):
            raise ImportCommitDenied(_envelope([_row_error(
                'module_disabled',
                '导入依赖的合同/结算/现金模块已停用，不能提交导入')]))
    return row


def require_commit_reader(member):
    """History page: owner/finance/auditor, including a disabled module's history_read."""
    row = _fresh(member)
    if row.role not in {'owner', 'finance', 'auditor'}:
        raise ImportCommitDenied(_envelope([_row_error(
            'unauthorized', '仅公司负责人、财务或审阅账户可以查看导入提交')]))
    return row


# ---------- envelope (§6) ----------

def _row_error(issue_code, message, *, row_no=None, field=None,
               original_digest=None, source_ref=None):
    return {'row_no': row_no, 'field': field, 'issue_code': issue_code,
            'original_digest': original_digest, 'normalized_value': None,
            'source_ref': source_ref, 'message': message}


def _precheck_view(snapshot=None, *, blocked=False):
    if snapshot is None:
        return {'digest': None, 'as_of_date': None, 'blocked': blocked}
    summary = snapshot.get('summary') or {}
    return {'digest': snapshot.get('precheck_digest'),
            'as_of_date': snapshot.get('as_of_date'),
            'blocked': blocked,
            'blocking_count': summary.get('blocking_count'),
            'warning_count': summary.get('warning_count'),
            'missing_count': summary.get('missing_count')}


def _envelope(row_errors, conflicts=(), missing=(), precheck=None):
    row_errors = list(row_errors or ())
    conflicts = list(conflicts or ())
    missing = list(missing or ())
    truncated = False
    if len(row_errors) > MAX_ROW_ERRORS:
        row_errors = row_errors[:MAX_ROW_ERRORS]
        truncated = True
    remaining = max(0, MAX_ISSUES - len(row_errors))
    if len(conflicts) > remaining:
        conflicts = conflicts[:remaining]
        truncated = True
    remaining = max(0, remaining - len(conflicts))
    if len(missing) > remaining:
        missing = missing[:remaining]
        truncated = True
    if truncated:
        row_errors = row_errors + [_row_error(
            'result_truncated', '错误或明细超出上限，已截断')]
    return {'status': 'rejected', 'row_errors': row_errors,
            'precheck': precheck or _precheck_view(), 'conflicts': conflicts,
            'missing': missing, 'truncated': truncated}


def _blocked(snapshot):
    summary = snapshot.get('summary') or {}
    if summary.get('blocking_count', 0) > 0:
        return True
    if any(entry.get('severity') == 'blocker'
           for entry in snapshot.get('missing') or ()):
        return True
    precheck = snapshot.get('precheck')
    if isinstance(precheck, dict) and precheck.get('can_close') is False:
        return True
    return False


# ---------- preview persistence ----------

def _normalize_rows(kind, rows):
    if kind not in HEADERS:
        raise ValidationError('不支持的导入类型')
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValidationError('导入批次没有数据行')
    if len(rows) > MAX_ROWS:
        raise ValidationError('每批最多500行，请拆分文件')
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValidationError('导入行格式无效')
        # PH-274 preview rows carry the raw CSV mapping under "values"; accept both.
        values = raw.get('values') if isinstance(raw.get('values'), dict) else raw
        normalized.append({key: str(values.get(key) or '').strip()
                           for key in HEADERS[kind]})
    return normalized


def _conflict_key(organization_id, kind, preview_id, row_no, field, source_ref,
                  conflict_code):
    return _digest({'organization_id': str(organization_id), 'kind': kind,
                    'preview_id': str(preview_id), 'row_no': row_no,
                    'field': field, 'source_ref': source_ref,
                    'conflict_code': conflict_code})


def _conflict_objects(preview, rows, conflicts):
    result, seen = [], set()
    for entry in conflicts or ():
        if not isinstance(entry, dict):
            raise ValidationError('冲突记录格式无效')
        code = entry.get('conflict_code') or entry.get('code')
        if not code:
            raise ValidationError('冲突代码必填')
        severity = str(entry.get('severity') or 'blocker').lower()
        if severity in _BLOCKER_SEVERITIES:
            severity = ImportConflict.SEVERITY_BLOCKER
        elif severity in _WARNING_SEVERITIES:
            severity = ImportConflict.SEVERITY_WARNING
        else:
            raise ValidationError('冲突严重级无效')
        row_no = entry.get('row_no', entry.get('row_number', entry.get('line')))
        try:
            row_no = int(row_no) if row_no is not None else None
        except (TypeError, ValueError):
            raise ValidationError('冲突行号无效')
        field = str(entry.get('field') or '')
        source_ref = entry.get('source_ref') or _source_ref(preview)
        if not isinstance(source_ref, dict):
            raise ValidationError('冲突来源引用无效')
        source_ref = _json_copy(source_ref)
        if not set(source_ref) or not set(source_ref) <= _SOURCE_REF_KEYS:
            raise ValidationError('冲突来源引用格式无效')
        row_digest = str(entry.get('row_digest') or '')
        if not row_digest and row_no and 2 <= row_no <= len(rows) + 1:
            row_digest = _digest(rows[row_no - 2])
        key = _conflict_key(preview.organization_id, preview.kind, preview.pk,
                            row_no, field, source_ref, str(code))
        if key in seen:
            continue
        seen.add(key)
        result.append(ImportConflict(
            organization=preview.organization, preview=preview, conflict_key=key,
            row_no=row_no, field=field[:64], source_ref=source_ref,
            conflict_code=str(code)[:64], severity=severity,
            row_digest=row_digest[:64]))
    return result


@transaction.atomic
def persist_preview(member, *, kind, file_name, rows, conflicts=()):
    """Persist a ready ImportPreview plus its conflicts; never writes business facts."""
    member = require_commit_writer(member)
    normalized = _normalize_rows(kind, rows)
    preview = ImportPreview.objects.create(
        organization=member.organization, kind=kind,
        file_name=str(file_name or '')[:255],
        rows_digest=_digest(normalized), rows_json=normalized,
        status=ImportPreview.STATUS_READY, version=1,
        created_by_member=member)
    for conflict in _conflict_objects(preview, normalized, conflicts):
        conflict.save()
    return preview


@transaction.atomic
def resolve_conflict(member, *, conflict_id, resolution):
    """One-way explicit resolution; the original conflict row is retained."""
    member = require_commit_writer(member)
    identifier = _as_uuid(conflict_id)
    if identifier is None:
        raise ValidationError('冲突记录不存在或无权访问')
    conflict = ImportConflict.objects.select_related('preview').filter(
        organization=member.organization, pk=identifier).first()
    if conflict is None:
        raise PermissionDenied('冲突记录不存在或无权访问')
    if resolution not in {ImportConflict.RESOLUTION_ACCEPT,
                          ImportConflict.RESOLUTION_REJECT,
                          ImportConflict.RESOLUTION_MERGE}:
        raise ValidationError('冲突消解结论无效')
    if conflict.resolution is not None:
        raise ValidationError('冲突已消解，历史记录不可覆盖')
    conflict.resolution = resolution
    conflict.resolved_by_member = member
    conflict.resolved_at = timezone.now()
    conflict.save()
    return conflict


# ---------- commit ----------

def _preview_identifier(preview_ref):
    if isinstance(preview_ref, dict):
        if (preview_ref.get('namespace') not in (None, 'onboarding')
                or preview_ref.get('kind') not in (None, 'import_preview')):
            raise ImportCommitRejected(_envelope([_row_error(
                'preview_not_found', '预览引用无效')]))
        identifier = _as_uuid(preview_ref.get('id'))
        ref_version = _as_version(preview_ref.get('version'))
        if preview_ref.get('version') is not None and ref_version is None:
            raise ImportCommitRejected(_envelope([_row_error(
                'preview_stale', '预览版本无效')]))
        return identifier, ref_version
    return _as_uuid(preview_ref), None


def _input_digest(member, preview, *, expected_version, rows_digest,
                  precheck_digest, as_of, period):
    return _digest({'action': 'import.commit', 'actor': str(member.pk),
                    'preview_id': str(preview.pk),
                    'preview_version': expected_version,
                    'rows_digest': rows_digest,
                    'precheck_digest': precheck_digest,
                    'precheck_as_of_date': as_of.isoformat(),
                    'period': period})


def _import_key(member, preview, rows_digest):
    return _digest({'organization_id': str(member.organization_id),
                    'kind': preview.kind, 'preview_id': str(preview.pk),
                    'rows_digest': rows_digest, 'source_batch_ref': None})


def _result_ref(obj):
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return {'namespace': 'unknown', 'kind': type(obj).__name__,
                'id': str(getattr(obj, 'pk', ''))}
    ref = {'namespace': meta.app_label, 'kind': meta.model_name,
           'id': str(obj.pk)}
    version = getattr(obj, 'version', None)
    if isinstance(version, int):
        ref['version'] = version
    return ref


def _warnings(snapshot, conflicts):
    warnings = []
    for issue in snapshot.get('issues') or ():
        if issue.get('severity') != 'warning':
            continue
        warnings.append({'source': 'precheck', 'issue_code': issue.get('issue_code'),
                         'domain': issue.get('domain'), 'message': issue.get('message'),
                         'source_refs': issue.get('source_refs') or []})
    for entry in snapshot.get('missing') or ():
        if entry.get('severity') != 'warning':
            continue
        warnings.append({'source': 'precheck_missing',
                         'issue_code': entry.get('missing_code'),
                         'domain': entry.get('domain'), 'message': entry.get('reason'),
                         'source_refs': [entry['source_ref']]
                         if entry.get('source_ref') else []})
    for conflict in conflicts:
        if conflict.severity != ImportConflict.SEVERITY_WARNING:
            continue
        warnings.append({'source': 'conflict', 'conflict_key': conflict.conflict_key,
                         'row_no': conflict.row_no, 'field': conflict.field,
                         'conflict_code': conflict.conflict_code,
                         'resolution': conflict.resolution})
    return warnings


_PERIOD_DATE_FIELD = {'time': 'work_date', 'expense': 'occurred_on', 'plan': 'due_date'}


def _period_row_errors(kind, rows, period):
    """Reject rows whose business date is outside the submitted posting period."""
    field = _PERIOD_DATE_FIELD.get(kind)
    if field is None or not isinstance(rows, list):
        return []
    errors = []
    for index, row in enumerate(rows, 2):
        raw = row.get(field) if isinstance(row, dict) else None
        try:
            day = date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            errors.append(_row_error(
                'row_date_invalid', f'第{index}行日期无效', row_no=index,
                field=field, original_digest=_digest(row)))
            continue
        if day.strftime('%Y-%m') != period:
            errors.append(_row_error(
                'row_date_invalid', f'第{index}行业务日期不属于导入期间 {period}',
                row_no=index, field=field, original_digest=_digest(row)))
        if len(errors) >= MAX_ROW_ERRORS:
            break
    return errors


def _reject_owner_error(exc, preview, rows, *, precheck=None):
    message = '；'.join(exc.messages)
    match = _ROW_PATTERN.search(message)
    row_no = int(match.group(1)) if match else None
    row_digest = ''
    if row_no and 2 <= row_no <= len(rows) + 1:
        row_digest = _digest(rows[row_no - 2])
    return ImportCommitRejected(_envelope(
        [_row_error('owner_validation_failed', message, row_no=row_no,
                    original_digest=row_digest, source_ref=_source_ref(preview))],
        precheck=precheck))


@transaction.atomic
def commit_import(member, *, preview_ref, rows_digest, precheck_digest,
                  precheck_as_of_date, period, request_token, expected_version,
                  dry_run=True):
    """All-or-nothing import commit; dry_run validates without receipts or facts."""
    member = require_commit_writer(member)
    token = _as_uuid(request_token)
    if token is None:
        raise ImportCommitRejected(_envelope([_row_error(
            'row_type_invalid', '请求标识无效')]))
    version = _as_version(expected_version)
    if version is None:
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_stale', '预览版本无效')]))
    as_of = _as_date(precheck_as_of_date)
    if as_of is None:
        raise ImportCommitRejected(_envelope([_row_error(
            'precheck_stale', '预检基准日无效')]))
    if as_of > timezone.localdate():
        raise ImportCommitRejected(_envelope([_row_error(
            'future_date', '预检基准日不能在未来')]))
    period = str(period or '').strip()
    if not period:
        raise ImportCommitRejected(_envelope([_row_error(
            'precheck_stale', '导入期间必填')]))
    identifier, ref_version = _preview_identifier(preview_ref)
    if identifier is None:
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_not_found', '预览不存在或无权访问')]))

    lock_company(member)
    preview = ImportPreview.objects.select_for_update().filter(pk=identifier).first()
    if preview is None:
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_not_found', '预览不存在或无权访问')]))
    if preview.organization_id != member.organization_id:
        raise ImportCommitDenied(_envelope([_row_error(
            'cross_org', '预览不属于当前公司')]))
    if preview.version != version or (ref_version is not None
                                     and ref_version != preview.version):
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_stale', '预览已被修改，请重新预览并确认')]))
    rows = preview.rows_json or []
    requested_rows_digest = _digest_text(rows_digest)
    requested_precheck_digest = _digest_text(precheck_digest)

    digest = _input_digest(member, preview, expected_version=version,
                           rows_digest=requested_rows_digest,
                           precheck_digest=requested_precheck_digest,
                           as_of=as_of, period=period)
    existing = ImportSubmissionReceipt.objects.filter(
        organization=member.organization, request_token=token).first()
    if existing is not None:
        if existing.action != 'import.commit' or existing.input_digest != digest:
            raise ImportCommitRejected(_envelope([_row_error(
                'request_token_conflict',
                '同一请求标识已用于不同输入，请使用新的请求标识')]))
        return existing.as_result(replayed=True)

    if preview.status == ImportPreview.STATUS_CONSUMED:
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_consumed', '预览已提交，不能重复消费')]))
    if preview.status not in {ImportPreview.STATUS_DRAFT, ImportPreview.STATUS_READY}:
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_stale', '预览状态不可提交')]))
    recomputed_rows_digest = _digest(rows)
    if (requested_rows_digest != recomputed_rows_digest
            or preview.rows_digest != recomputed_rows_digest):
        raise ImportCommitRejected(_envelope([_row_error(
            'preview_stale', '预览行已变化或摘要不一致，请重新预览')]))

    import_key = _import_key(member, preview, recomputed_rows_digest)
    if ImportSubmissionReceipt.objects.filter(
            organization=member.organization, import_key=import_key,
            status=ImportSubmissionReceipt.STATUS_COMMITTED).exists():
        raise ImportCommitRejected(_envelope([_row_error(
            'import_key_duplicate', '同一导入批次已提交，不能重复写入')]))

    try:
        snapshot = period_precheck_snapshot(member, scope='company', period=period,
                                            as_of=as_of)
    except ValidationError as exc:
        raise ImportCommitRejected(_envelope(
            [_row_error('precheck_stale', '；'.join(exc.messages))]))
    blocked = _blocked(snapshot)
    precheck = _precheck_view(snapshot, blocked=blocked)
    if (snapshot.get('precheck_digest') != requested_precheck_digest
            or snapshot.get('as_of_date') != as_of.isoformat()):
        raise ImportCommitRejected(_envelope(
            [_row_error('precheck_stale',
                        '预检摘要或基准日已变化，请重新预检并确认')],
            precheck=precheck))
    if blocked:
        raise ImportCommitRejected(_envelope(
            [_row_error('precheck_blocked', '期间预检存在阻塞项，不能提交导入')],
            missing=snapshot.get('missing') or (), precheck=precheck))

    conflicts = list(ImportConflict.objects.filter(preview=preview))
    unresolved = [conflict for conflict in conflicts
                  if conflict.resolution is None
                  and conflict.severity == ImportConflict.SEVERITY_BLOCKER]
    if unresolved:
        raise ImportCommitRejected(_envelope(
            [_row_error('import_conflict_unresolved', '存在未消解的阻塞冲突')],
            conflicts=[conflict.as_envelope() for conflict in unresolved],
            missing=snapshot.get('missing') or (), precheck=precheck))

    warnings = _warnings(snapshot, conflicts)
    missing = snapshot.get('missing') or []
    resolved = [conflict.as_envelope() for conflict in conflicts
                if conflict.resolution is not None]

    # Contract §2/§9: every row's business date must belong to the submitted period;
    # a valid precheck for the open month must not admit a closed prior-month row.
    period_errors = _period_row_errors(preview.kind, rows, period)
    if period_errors:
        raise ImportCommitRejected(_envelope(
            period_errors, conflicts=resolved, missing=missing, precheck=precheck))

    if dry_run:
        return {'status': 'dry_run', 'dry_run': True, 'would_commit': True,
                'preview_ref': _preview_ref(preview), 'kind': preview.kind,
                'period': period, 'row_count': len(rows),
                'import_key': import_key, 'precheck': precheck,
                'conflicts_resolved': resolved, 'warnings': warnings,
                'missing': missing, 'replayed': False}

    try:
        objects = apply_rows(member, preview.kind, rows)
    except ValidationError as exc:
        raise _reject_owner_error(exc, preview, rows, precheck=precheck)

    result_refs = [_result_ref(obj) for obj in objects]
    receipt = ImportSubmissionReceipt.objects.create(
        organization=member.organization, request_token=token,
        action='import.commit', input_digest=digest, import_key=import_key,
        preview_ref=_preview_ref(preview), period=period,
        status=ImportSubmissionReceipt.STATUS_COMMITTED,
        precheck_digest=snapshot.get('precheck_digest') or '',
        precheck_as_of_date=as_of, result_refs=result_refs,
        conflicts_resolved=resolved, warnings=warnings, missing=missing,
        created_by=member)
    ImportPreview.objects.filter(pk=preview.pk, version=version,
                                 status__in=[ImportPreview.STATUS_DRAFT,
                                             ImportPreview.STATUS_READY]).update(
        status=ImportPreview.STATUS_CONSUMED)
    return receipt.as_result(replayed=False)
