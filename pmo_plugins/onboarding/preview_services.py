"""PH-274 onboarding import preview: read-only CSV template detection and row precheck.

This slice writes no business facts, no ledger, no source consumption and no canonical event.
The only durable write is one generic audit row summarising the preview request; ImportPreview /
ImportConflict remain ephemeral in-memory projections (no tables, no migration).
"""
import csv
import io
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError

from core.access import require_member, visible_projects
from core.imports import HEADERS
from core.models import Employee, Membership
from core.plugin_runtime import is_enabled
from core.services import audit

MODULE = 'onboarding'
READ_ROLES = {'owner', 'finance', 'auditor'}
TEMPLATE_LABELS = {'time': '工时', 'expense': '费用', 'plan': '现金计划'}
MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
PREVIEW_ROWS = 200
MAX_CENTS = 10 ** 12


def _fresh(member):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(row)
    if row.role not in READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以预览导入')
    if not is_enabled(row.organization, MODULE):
        raise PermissionDenied('导入与期初模块已停用，不能预览导入')
    # ADR 0012 dependency semantics enforced here while the manifest requires guard is
    # temporarily empty (see plugin.py); contracts/settlement/cash must all be enabled.
    for dependency in ('contracts', 'settlement', 'cash'):
        if not is_enabled(row.organization, dependency):
            raise PermissionDenied('导入依赖的合同/结算/现金模块已停用，不能预览导入')
    return row


def _text(content):
    if isinstance(content, bytes):
        try:
            return content.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValidationError('请保存为UTF-8编码的CSV')
    if isinstance(content, str):
        return content.lstrip('\ufeff')
    raise ValidationError('导入文件内容无效')


def _cents(value, label):
    try:
        amount = Decimal((value or '').strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f'{label}须为数字')
    if not amount.is_finite() or amount < 0:
        raise ValidationError(f'{label}须为非负数字')
    if amount.as_tuple().exponent < -2:
        raise ValidationError(f'{label}最多两位小数')
    cents_value = int((amount * 100).to_integral_value())
    if cents_value > MAX_CENTS:
        raise ValidationError(f'{label}超过100亿元上限')
    return cents_value


def _minutes(value):
    try:
        hours = Decimal((value or '').strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('工时须为数字（小时）')
    if not hours.is_finite() or hours <= 0:
        raise ValidationError('工时须为大于0的小时数')
    minutes = hours * 60
    if minutes != minutes.to_integral_value():
        raise ValidationError('工时须按整数分钟维护')
    minutes = int(minutes)
    if not 1 <= minutes <= 1440:
        raise ValidationError('单条工时须在1到1440分钟之间')
    return minutes


def _day(value, label):
    try:
        return date.fromisoformat((value or '').strip()).isoformat()
    except (TypeError, ValueError):
        raise ValidationError(f'{label}须为YYYY-MM-DD日期')


def _header(text):
    reader = csv.DictReader(io.StringIO(text, newline=''))
    headers = reader.fieldnames or []
    if not headers:
        raise ValidationError('CSV缺少表头')
    if len(headers) != len(set(headers)):
        raise ValidationError('CSV表头存在重复列')
    return reader, [str(header).strip() for header in headers]


def _template(headers, requested):
    if requested is not None:
        if requested not in HEADERS:
            raise ValidationError('不支持的导入类型')
        required = HEADERS[requested]
        if not set(required) <= set(headers):
            raise ValidationError('CSV列必须包含：' + ','.join(required))
        return requested, list(required), [h for h in headers if h not in required]
    matches = [(len(HEADERS[kind]), kind) for kind in HEADERS
               if set(HEADERS[kind]) <= set(headers)]
    if not matches:
        raise ValidationError('无法识别CSV模板；支持列模板：' +
                              '；'.join(','.join(HEADERS[kind]) for kind in HEADERS))
    _length, kind = max(matches)
    required = HEADERS[kind]
    return kind, list(required), [h for h in headers if h not in required]


def _row_error(errors, line, field, code, message):
    errors.append({'line': line, 'field': field, 'code': code, 'message': message})


def _normalize_row(kind, raw, line, errors, projects, employee_names):
    values = {key: (raw.get(key) or '').strip() for key in HEADERS[kind]}
    normalized = {}
    if not values['source_id'] or len(values['source_id']) > 100:
        _row_error(errors, line, 'source_id', 'source_id', '来源编号必填且不超过100字符')
    else:
        normalized['source_id'] = values['source_id']
    if not values.get('project_code'):
        _row_error(errors, line, 'project_code', 'required', '项目编号必填')
    elif values['project_code'] not in projects:
        _row_error(errors, line, 'project_code', 'unknown_project',
                   '项目编号不存在或没有权限')
    else:
        normalized['project_code'] = values['project_code']
    try:
        if kind == 'time':
            if not values.get('employee_name'):
                _row_error(errors, line, 'employee_name', 'required', '员工姓名必填')
            elif employee_names.get(values['employee_name'], 0) != 1:
                _row_error(errors, line, 'employee_name', 'unknown_employee',
                           '员工姓名不存在或重名，请先核对员工资料')
            else:
                normalized['employee_name'] = values['employee_name']
            normalized['work_date'] = _day(values.get('work_date'), '工作日期')
            normalized['minutes'] = _minutes(values.get('hours'))
            normalized['note'] = values.get('note', '')
        elif kind == 'expense':
            if not values.get('title'):
                _row_error(errors, line, 'title', 'required', '费用标题必填')
            else:
                normalized['title'] = values['title']
            normalized['amount_cents'] = _cents(values.get('amount'), '金额')
            normalized['tax_cents'] = _cents(values.get('tax'), '税额')
            if values.get('recoverable_tax') not in {'0', '1'}:
                _row_error(errors, line, 'recoverable_tax', 'enum',
                           '抵扣标记仅可填0或1')
            else:
                normalized['recoverable_tax'] = values['recoverable_tax'] == '1'
            normalized['occurred_on'] = _day(values.get('occurred_on'), '发生日期')
            normalized['counterparty'] = values.get('counterparty', '')
            normalized['reference'] = values.get('reference', '')
            normalized['note'] = values.get('note', '')
        elif kind == 'plan':
            if values.get('direction') not in {'in', 'out'}:
                _row_error(errors, line, 'direction', 'enum',
                           '收付款方向只能为in或out')
            else:
                normalized['direction'] = values['direction']
            if not values.get('title'):
                _row_error(errors, line, 'title', 'required', '计划标题必填')
            else:
                normalized['title'] = values['title']
            normalized['amount_cents'] = _cents(values.get('amount'), '金额')
            normalized['due_date'] = _day(values.get('due_date'), '到期日期')
            normalized['counterparty'] = values.get('counterparty', '')
            normalized['reference'] = values.get('reference', '')
    except ValidationError as error:
        field = None
        message = '；'.join(error.messages)
        for key in HEADERS[kind]:
            if key in message or (key == 'hours' and '工时' in message):
                field = key
                break
        _row_error(errors, line, field, 'invalid', message)
    return {'line': line, 'values': values, 'normalized': normalized}


def preview_import(member, *, kind=None, file_name, content):
    """Read-only CSV template/mapping/row precheck; returns an ephemeral preview projection."""
    member = _fresh(member)
    text = _text(content)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValidationError('CSV文件不能超过1MB')
    reader, headers = _header(text)
    kind, required, unmapped = _template(headers, kind)
    projects = set(visible_projects(member).values_list('code', flat=True))
    employee_names = Counter(Employee.objects.filter(
        organization=member.organization, active=True).values_list('name', flat=True))
    rows, errors, seen = [], [], set()
    for index, raw in enumerate(reader, 2):
        if len(rows) >= MAX_ROWS:
            raise ValidationError('每批最多500行，请拆分文件')
        if None in raw or any(value is None or len(value) > 8000 for value in raw.values()):
            raise ValidationError(f'第{index}行列数不符或字段过长')
        values = {key: (raw.get(key) or '').strip() for key in required}
        source_id = values.get('source_id', '')
        if source_id and source_id in seen:
            _row_error(errors, index, 'source_id', 'duplicate_source_id',
                       '来源编号在本批次重复')
        seen.add(source_id)
        rows.append(_normalize_row(kind, raw, index, errors, projects, employee_names))
    if not rows:
        raise ValidationError('CSV没有数据行')
    row_errors = list(errors)
    conflicts = [{'id': str(uuid.uuid4()), 'kind': 'import_conflict',
                  'ephemeral': True, 'row_number': error['line'],
                  'code': error['code'], 'field': error['field'],
                  'message': error['message'], 'severity': 'error'}
                 for error in row_errors]
    result = {
        'template': kind, 'template_label': TEMPLATE_LABELS.get(kind, kind),
        'file_name': str(file_name or ''), 'columns': headers,
        'mapped_fields': [{'column': column, 'field': column} for column in required],
        'unmapped_columns': unmapped,
        'rows': rows[:PREVIEW_ROWS],
        'row_errors': row_errors,
        'conflicts': conflicts,
        'summary': {'row_count': len(rows), 'preview_row_count': len(rows[:PREVIEW_ROWS]),
                    'error_count': len(row_errors), 'conflict_count': len(conflicts),
                    'unmapped_count': len(unmapped), 'preview_only': True,
                    'persisted': False},
    }
    audit(member, 'onboarding.import_previewed', member, {
        'kind': kind, 'file_name': str(file_name or ''),
        'row_count': len(rows), 'error_count': len(row_errors),
        'unmapped_count': len(unmapped)})
    return result


def import_template_list(member):
    member = _fresh(member)
    return [{'kind': kind, 'label': TEMPLATE_LABELS.get(kind, kind),
             'columns': list(HEADERS[kind])} for kind in HEADERS]


def require_preview_reader(member):
    """Public read gate used by the browser layer."""
    return _fresh(member)
