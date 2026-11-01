"""PH-274 onboarding import preview: read-only CSV template detection and row precheck.

This slice writes no business facts, no ledger, no source consumption and no canonical event.
The only durable write is one generic audit row summarising the preview request; ImportPreview /
ImportConflict remain ephemeral in-memory projections (no tables, no migration).
"""
import csv
import io
import json
import uuid
from collections import Counter, OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError

from core.access import require_member, visible_projects
from core.imports import (HEADERS, INVOICING_ALLOCATION_KINDS, MAX_CENTS,
                          owner_issue_code)
from core.models import Employee, Membership
from core.plugin_runtime import is_enabled
from core.services import audit

MODULE = 'onboarding'
READ_ROLES = {'owner', 'finance', 'auditor'}
TEMPLATE_LABELS = {'time': '工时', 'expense': '费用', 'plan': '现金计划',
                   'invoicing': '发票'}
TEMPLATE_KINDS = ('time', 'expense', 'plan', 'invoicing')
# The onboarding template picker keeps its frozen three templates; the invoice
# template is surfaced by the invoicing import page and auto-detected here.
LIST_TEMPLATE_KINDS = ('time', 'expense', 'plan')
MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
PREVIEW_ROWS = 200


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
        if requested == 'invoicing' and set(headers) != set(required):
            raise ValidationError('发票导入CSV列必须精确为：' + ','.join(required))
        if not set(required) <= set(headers):
            raise ValidationError('CSV列必须包含：' + ','.join(required))
        return requested, list(required), [h for h in headers if h not in required]
    matches = [(len(HEADERS[kind]), kind) for kind in TEMPLATE_KINDS
               if set(HEADERS[kind]) <= set(headers)]
    if not matches:
        raise ValidationError('无法识别CSV模板；支持列模板：' +
                              '；'.join(','.join(HEADERS[kind]) for kind in TEMPLATE_KINDS))
    _length, kind = max(matches)
    required = HEADERS[kind]
    if kind == 'invoicing' and set(headers) != set(required):
        raise ValidationError('发票导入CSV列必须精确匹配，不能包含未知列')
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


def _int_cents(value, label, *, positive=False):
    text = str(value or '').strip()
    if not text.isdigit():
        raise ValidationError(f'{label}须为整数分')
    amount = int(text)
    if positive and amount <= 0:
        raise ValidationError(f'{label}须大于0')
    if not 0 <= amount <= MAX_CENTS:
        raise ValidationError(f'{label}超过100亿元上限')
    return amount


def _invoicing_counterparty(organization, name, cache):
    text = str(name or '').strip()
    if not text:
        return None
    if text in cache:
        return cache[text]
    from django.db.models import Q
    from pmo_plugins.contracts.models import Counterparty, normalized_key
    candidates = Counterparty.objects.filter(
        organization=organization, status='active').filter(
        Q(display_name=text) | Q(normalized_name=normalized_key(text)))
    matches = [party for party in candidates if 'customer' in (party.roles or [])]
    cache[text] = matches[0] if len(matches) == 1 else None
    return cache[text]


def _invoicing_period_issue(member, day):
    from pmo_plugins.invoicing import services as invoicing_services
    try:
        invoicing_services._period(member, day)
    except ValidationError as error:
        message = '；'.join(error.messages)
        if '未来' in message:
            return 'future_date', message
        if '锁' in message:
            return 'period_locked', message
        if '关闭' in message:
            return 'period_closed', message
        return 'row_date_invalid', message
    return None


def _invoicing_quota_issue(member, ref, day, party):
    from pmo_plugins.invoicing import invoice_import_services as projections
    try:
        return projections.source_quota_snapshot(
            member, ref, invoice_date=day, counterparty_id=party.pk), None
    except PermissionDenied:
        return None, ('allocation_ref_invalid', '业务来源不存在或无权访问')
    except ValidationError as error:
        message = '；'.join(error.messages)
        return None, (owner_issue_code(message, default='allocation_ref_invalid'),
                      message)


def _invoicing_validate(member, raw_rows, *, include_owner=True):
    """Read-only row/group precheck shared by preview and commit; writes nothing."""
    errors, parsed, counterparties = [], [], {}
    seen_sources = set()
    for offset, raw in enumerate(raw_rows):
        line = offset + 2
        values = {key: str((raw or {}).get(key) or '').strip()
                  for key in HEADERS['invoicing']}
        entry = {'line': line, 'values': values, 'normalized': {},
                 'party': None, 'valid': True}

        def bad(field, code, message):
            _row_error(errors, line, field, code, message)
            entry['valid'] = False

        if not values['source_id'] or len(values['source_id']) > 100:
            bad('source_id', 'row_missing_required', '来源编号必填且不超过100字符')
        elif values['source_id'] in seen_sources:
            bad('source_id', 'duplicate_source_id', '来源编号在本批次重复')
        else:
            entry['normalized']['source_id'] = values['source_id']
        seen_sources.add(values['source_id'])

        party = _invoicing_counterparty(
            member.organization, values['counterparty_name'], counterparties)
        if party is None:
            bad('counterparty_name', 'counterparty_unresolved',
                '往来方不存在、已停用、非客户或存在歧义')
        else:
            entry['party'] = party
            entry['normalized']['counterparty_name'] = values['counterparty_name']

        if not 1 <= len(values['invoice_number']) <= 80:
            bad('invoice_number', 'row_missing_required', '发票号码须为1至80字符')
        else:
            entry['normalized']['invoice_number'] = values['invoice_number']

        day = None
        try:
            day = date.fromisoformat(values['invoice_date'])
            entry['normalized']['invoice_date'] = day.isoformat()
        except ValueError:
            bad('invoice_date', 'row_date_invalid', '发票日期须为YYYY-MM-DD')
        if day is not None:
            issue = _invoicing_period_issue(member, day)
            if issue:
                bad('invoice_date', issue[0], issue[1])

        amounts = {}
        for field, label, positive in (
                ('gross_cents', '发票含税金额', True),
                ('net_cents', '发票不含税金额', False),
                ('tax_cents', '发票税额', False)):
            try:
                amounts[field] = _int_cents(values[field], label, positive=positive)
            except ValidationError as error:
                bad(field, 'row_amount_invalid', '；'.join(error.messages))
        if len(amounts) == 3:
            if amounts['gross_cents'] != amounts['net_cents'] + amounts['tax_cents']:
                bad('gross_cents', 'invoice_total_mismatch',
                    '发票含税金额必须等于不含税金额加税额')
            else:
                entry['normalized'].update(amounts)

        if not values['evidence_json']:
            bad('evidence_json', 'row_missing_required', '证据JSON必填')
        elif len(values['evidence_json']) > 2000:
            bad('evidence_json', 'row_type_invalid', '证据JSON不能超过2000字符')
        else:
            try:
                json.loads(values['evidence_json'])
            except ValueError:
                bad('evidence_json', 'row_type_invalid', '证据须为有效JSON')

        line_no = None
        try:
            line_no = _int_cents(values['line_no'], '发票行号', positive=True)
            entry['normalized']['line_no'] = line_no
        except ValidationError as error:
            bad('line_no', 'row_type_invalid', '；'.join(error.messages))

        line_amounts = {}
        for field, label, positive in (
                ('line_gross_cents', '发票行含税金额', True),
                ('line_net_cents', '发票行不含税金额', False),
                ('line_tax_cents', '发票行税额', False)):
            try:
                line_amounts[field] = _int_cents(values[field], label,
                                                 positive=positive)
            except ValidationError as error:
                bad(field, 'row_amount_invalid', '；'.join(error.messages))
        if len(line_amounts) == 3:
            if (line_amounts['line_gross_cents']
                    != line_amounts['line_net_cents'] + line_amounts['line_tax_cents']):
                bad('line_gross_cents', 'line_total_mismatch',
                    '发票行含税金额必须等于不含税金额加税额')
            else:
                entry['normalized'].update(line_amounts)

        ref = None
        namespace = values['allocation_namespace']
        kind = values['allocation_kind']
        if (namespace, kind) not in INVOICING_ALLOCATION_KINDS:
            bad('allocation_kind', 'allocation_ref_invalid',
                '业务来源仅支持合同收费明细/结算未清项/结算来源')
        else:
            identifier = None
            try:
                identifier = str(uuid.UUID(values['allocation_id']))
            except (ValueError, AttributeError):
                bad('allocation_id', 'allocation_ref_invalid',
                    '业务来源标识须为有效UUID')
            version = None
            try:
                version = int(values['allocation_version'])
                if version < 1:
                    raise ValueError
            except (TypeError, ValueError):
                bad('allocation_version', 'allocation_ref_invalid',
                    '业务来源版本须为整数且大于0')
            if identifier and version:
                ref = {'namespace': namespace, 'kind': kind, 'id': identifier,
                       'version': version}
                entry['normalized']['business_source_ref'] = ref

        allocation_amounts = {}
        for field, label, positive in (
                ('allocation_gross_cents', '分配含税金额', True),
                ('allocation_net_cents', '分配不含税金额', False),
                ('allocation_tax_cents', '分配税额', False)):
            try:
                allocation_amounts[field] = _int_cents(
                    values[field], label, positive=positive)
            except ValidationError as error:
                bad(field, 'row_amount_invalid', '；'.join(error.messages))
        if len(allocation_amounts) == 3:
            if (allocation_amounts['allocation_gross_cents']
                    != allocation_amounts['allocation_net_cents']
                    + allocation_amounts['allocation_tax_cents']):
                bad('allocation_gross_cents', 'line_total_mismatch',
                    '分配含税金额必须等于不含税金额加税额')

        if len(values['note']) > 500:
            bad('note', 'row_type_invalid', '说明不能超过500字符')
        else:
            entry['normalized']['note'] = values['note']

        entry.update({
            'day': day, 'gross': amounts.get('gross_cents'),
            'net': amounts.get('net_cents'), 'tax': amounts.get('tax_cents'),
            'line_no': line_no,
            'line_gross': line_amounts.get('line_gross_cents'),
            'line_net': line_amounts.get('line_net_cents'),
            'line_tax': line_amounts.get('line_tax_cents'),
            'ref': ref,
            'allocation': ({
                'business_source_ref': ref,
                'gross_cents': allocation_amounts.get('allocation_gross_cents'),
                'net_cents': allocation_amounts.get('allocation_net_cents'),
                'tax_cents': allocation_amounts.get('allocation_tax_cents'),
            } if ref and len(allocation_amounts) == 3 else None),
        })
        parsed.append(entry)

    complete = [entry for entry in parsed if entry['valid']
                and entry['normalized'].get('invoice_number')
                and entry['party'] is not None and entry['day'] is not None
                and entry['gross'] is not None and entry['line_no'] is not None
                and entry['line_gross'] is not None and entry['allocation']]
    grouped = OrderedDict()
    for entry in complete:
        grouped.setdefault(entry['normalized']['invoice_number'], []).append(entry)
    batch_use = {}

    from pmo_plugins.invoicing.models import Invoice
    for number, entries in grouped.items():
        if Invoice.objects.filter(
                organization=member.organization, direction='sales',
                invoice_number=number).exists():
            _row_error(errors, entries[0]['line'], 'invoice_number',
                       'invoice_number_duplicate', '同一方向发票号码已存在')
        header = entries[0]
        header_signature = (header['party'].pk, header['day'], header['gross'],
                            header['net'], header['tax'],
                            header['values']['evidence_json'])
        for entry in entries[1:]:
            signature = (entry['party'].pk, entry['day'], entry['gross'],
                         entry['net'], entry['tax'],
                         entry['values']['evidence_json'])
            if signature != header_signature:
                _row_error(errors, entry['line'], 'invoice_number',
                           'invoice_total_mismatch',
                           '同票多行的票头、证据或金额必须一致')
        line_groups = OrderedDict()
        for entry in entries:
            line_groups.setdefault(entry['line_no'], []).append(entry)
        total_gross = total_net = total_tax = 0
        for line_no, line_entries in line_groups.items():
            first = line_entries[0]
            line_signature = (first['line_gross'], first['line_net'],
                              first['line_tax'])
            for entry in line_entries[1:]:
                if (entry['line_gross'], entry['line_net'],
                        entry['line_tax']) != line_signature:
                    _row_error(errors, entry['line'], 'line_no',
                               'invoice_total_mismatch',
                               '同一发票行号的行金额必须一致')
            sum_gross = sum(item['allocation']['gross_cents']
                            for item in line_entries)
            sum_net = sum(item['allocation']['net_cents'] for item in line_entries)
            sum_tax = sum(item['allocation']['tax_cents'] for item in line_entries)
            if (sum_gross, sum_net, sum_tax) != line_signature:
                _row_error(errors, first['line'], 'line_gross_cents',
                           'line_total_mismatch',
                           '分配金额合计必须等于发票行金额')
            total_gross += first['line_gross']
            total_net += first['line_net']
            total_tax += first['line_tax']
        if (total_gross, total_net, total_tax) != (
                header['gross'], header['net'], header['tax']):
            _row_error(errors, header['line'], 'gross_cents',
                       'invoice_total_mismatch',
                       '发票行合计必须等于发票头金额')
        if include_owner:
            for entry in entries:
                ref = entry['allocation']['business_source_ref']
                key = (ref['namespace'], ref['kind'], ref['id'], ref['version'])
                batch_use[key] = (batch_use.get(key, 0)
                                  + entry['allocation']['gross_cents'])
                quota_used, issue = _invoicing_quota_issue(
                    member, ref, header['day'], header['party'])
                if issue:
                    _row_error(errors, entry['line'], 'allocation_id',
                               issue[0], issue[1])
                    continue
                quota, used = quota_used
                if used + batch_use[key] > quota:
                    _row_error(errors, entry['line'], 'allocation_gross_cents',
                               'source_quota_exceeded',
                               '业务来源可分配额度不足')
    return [{'line': entry['line'], 'values': entry['values'],
             'normalized': entry['normalized']} for entry in parsed], errors


def invoicing_row_issues(member, rows, *, include_owner=True):
    """Shared invoice row/group issues for commit revalidation (read-only)."""
    _rows, errors = _invoicing_validate(member, rows, include_owner=include_owner)
    return errors


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
    rows, errors, seen, raw_rows = [], [], set(), []
    for index, raw in enumerate(reader, 2):
        if len(raw_rows) >= MAX_ROWS:
            raise ValidationError('每批最多500行，请拆分文件')
        if None in raw or any(value is None or len(value) > 8000 for value in raw.values()):
            raise ValidationError(f'第{index}行列数不符或字段过长')
        values = {key: (raw.get(key) or '').strip() for key in required}
        source_id = values.get('source_id', '')
        if kind != 'invoicing' and source_id and source_id in seen:
            _row_error(errors, index, 'source_id', 'duplicate_source_id',
                       '来源编号在本批次重复')
        seen.add(source_id)
        raw_rows.append(raw)
        if kind != 'invoicing':
            rows.append(_normalize_row(kind, raw, index, errors, projects, employee_names))
    if kind == 'invoicing':
        rows, invoicing_errors = _invoicing_validate(member, raw_rows)
        errors.extend(invoicing_errors)
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
             'columns': list(HEADERS[kind])} for kind in LIST_TEMPLATE_KINDS]


def require_preview_reader(member):
    """Public read gate used by the browser layer."""
    return _fresh(member)
