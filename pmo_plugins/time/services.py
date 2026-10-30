"""time domain service: customer-confirmed billable minutes.

Only the canonical time.billable.confirm/v1 semantics are implemented here; the
confirmation records minutes against an approved timesheet and an effective
contract line. No revenue, ledger, cash, settlement or open-item fact is written.
"""
import hashlib
import json
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import require_member
from core.models import AccountingPeriod, DomainEvent, Membership, Timesheet
from core.plugin_runtime import is_enabled
from core.services import approved_timesheet_for_billable, audit, lock_company
from pmo_plugins.contracts import approval_services as contract_approvals

from .models import BillableConfirmation, BillableConfirmationReceipt


def _timesheet_key(pk):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'pmo:timesheet:{int(pk)}')

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
TOPIC = 'time.billable_confirmed.v1'
DRAFT_ACTION = 'billable.draft_saved'


def _fail(message):
    raise ValidationError(message)


def _fresh(member, *, write=False):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看客户可计费确认')
    if write and not is_enabled(member.organization, 'time'):
        raise PermissionDenied('工时与可计费模块已停用')
    return member


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _token(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError('请求标识无效，请刷新表单后重试') from error


def _version(value):
    if type(value) is not int or value < 1:
        _fail('记录版本无效，请刷新后重试')
    return value


def _ref(value, kind):
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        _fail('来源引用无效')
    if value['kind'] != kind:
        _fail('来源引用类型无效')
    identifier = str(value['id'])
    if not identifier or len(identifier) > 64:
        _fail('来源引用无效')
    try:
        version = int(value['version'])
    except (TypeError, ValueError):
        _fail('来源引用无效')
    if version < 1:
        _fail('来源引用无效')
    namespace = 'contracts' if kind == 'contract_line' else 'time'
    if value['namespace'] != namespace:
        _fail('来源引用命名空间无效')
    return {'namespace': value['namespace'], 'kind': kind, 'id': identifier, 'version': version}


def _minutes(value):
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 1440:
        _fail('确认分钟须为1至1440的整数')
    return value


def _business_date(value):
    if isinstance(value, date) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            _fail('确认日期须为有效的年月日')
        if parsed.isoformat() != value:
            _fail('确认日期须为有效的年月日')
    else:
        _fail('确认日期须为有效的年月日')
    if parsed > timezone.localdate():
        _fail('确认日期不能在未来')
    return parsed


def _check_period(member, day):
    if member.organization.locked_through and day <= member.organization.locked_through:
        _fail('确认日期所在期间已锁账，不能补记')
    row = AccountingPeriod.objects.filter(organization=member.organization,
                                          period=day.strftime('%Y-%m')).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        _fail('确认日期所在会计期间已关闭')


def _evidence(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        _fail('确认证据须为1至20项')
    result = []
    for item in value:
        if (not isinstance(item, dict) or not item
                or set(item) - {'kind', 'reference', 'sha256', 'evidence_id'}
                or any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in item.values())
                or len(str(item)) > 500):
            _fail('确认证据项无效')
        result.append(item)
    return result


def _validate_targets(member, timesheet_ref, contract_line_ref, minutes, confirmed_on):
    timesheet = approved_timesheet_for_billable(member, timesheet_ref)
    if minutes > timesheet['minutes']:
        _fail('确认分钟不能超过该工时已批准分钟')
    try:
        line = contract_approvals.approved_line_snapshot(member, contract_line_ref,
                                                        confirmed_on.isoformat())
    except (ValidationError, PermissionDenied) as error:
        raise ValidationError('合同条款不可用于本次可计费确认：' + '；'.join(
            getattr(error, 'messages', [str(error)])))
    if line['project_id'] != timesheet['project_id']:
        _fail('工时项目与合同条款项目不一致')
    if line['pricing_configuration'].get('template') != 'confirmed_time':
        _fail('只有客户确认工时模板的合同条款可以确认可计费分钟')
    _check_period(member, confirmed_on)
    return timesheet, line


def _receipt(member, action, request_token, payload, dry_run):
    if dry_run:
        return None, None, None
    token = _token(request_token)
    digest = _digest({'action': action, 'actor': str(member.pk), **payload})
    row = BillableConfirmationReceipt.objects.filter(organization=member.organization,
                                                     request_token=token).first()
    if row:
        if row.action != action or row.input_digest != digest:
            _fail('同一请求标识已用于不同输入')
        try:
            confirmation = BillableConfirmation.objects.get(
                organization=member.organization, pk=row.result_ref['id'])
        except (KeyError, TypeError, ValueError, BillableConfirmation.DoesNotExist):
            raise PermissionDenied('原确认记录不存在或无权访问')
        return token, digest, confirmation
    return token, digest, None


def _record(member, action, token, digest, confirmation):
    BillableConfirmationReceipt.objects.create(
        organization=member.organization, action=action, request_token=token,
        input_digest=digest, result_ref=confirmation.source_ref,
        result_snapshot={'source': confirmation.source_ref, 'status': confirmation.status,
                         'version': confirmation.version},
        created_by=member)


def _snapshot(row):
    return {'id': str(row.pk), 'status': row.status, 'status_display': row.get_status_display(),
            'timesheet_ref': row.timesheet_ref, 'contract_line_ref': row.contract_line_ref,
            'confirmed_minutes': row.confirmed_minutes, 'confirmed_on': row.confirmed_on,
            'evidence': row.evidence, 'version': row.version,
            'created_by': str(row.created_by_member_id),
            'confirmed_by': str(row.confirmed_by_member_id) if row.confirmed_by_member_id else None,
            'source_ref': row.source_ref}


def approved_timesheet_choices(member):
    """Read-only candidates: approved project timesheets visible to the member."""
    member = _fresh(member)
    rows = Timesheet.objects.select_related('project', 'employee').filter(
        organization=member.organization, status='approved', project__isnull=False)
    if member.role == 'pm':
        from core.access import visible_projects
        rows = rows.filter(project__in=visible_projects(member))
    return list(rows.order_by('-work_date', '-id')[:200])


def contract_line_choices(member, *, as_of=None):
    """Read-only confirmed_time contract-line candidates through the contracts owner."""
    member = _fresh(member)
    try:
        rows = contract_approvals.approved_line_choices(member)
    except PermissionDenied:
        raise
    result = []
    for row in rows:
        config = row['pricing_configuration']
        if config.get('template') != 'confirmed_time':
            continue
        result.append({'id': row['line_id'], 'label': (
            f"{row['contract_code']} · 明细{row['line_no']} · 项目{row['project_id']} "
            f"· 单价{config.get('unit_price_cents')}分/{config.get('unit_minutes')}分钟"),
            'project_id': row['project_id']})
    return result


@transaction.atomic
def save_billable_confirmation_draft(member, *, timesheet_ref, contract_line_ref, confirmed_minutes,
                                     confirmed_on, evidence, request_token, confirmation_id=None,
                                     expected_version=None, dry_run=True):
    """Create or revise a draft confirmation; no canonical event and no financial fact."""
    member = _fresh(member, write=True)
    lock_company(member)
    timesheet_ref = _ref(timesheet_ref, 'timesheet')
    contract_line_ref = _ref(contract_line_ref, 'contract_line')
    minutes = _minutes(confirmed_minutes)
    when = _business_date(confirmed_on)
    evidence = _evidence(evidence)
    payload = {'timesheet_ref': timesheet_ref, 'contract_line_ref': contract_line_ref,
               'confirmed_minutes': minutes, 'confirmed_on': str(when), 'evidence': evidence,
               'confirmation_id': str(confirmation_id) if confirmation_id else None,
               'expected_version': expected_version}
    token, digest, replay = _receipt(member, 'draft.save', request_token, payload, dry_run)
    if replay is not None:
        return _snapshot(replay)
    _validate_targets(member, timesheet_ref, contract_line_ref, minutes, when)
    if confirmation_id:
        try:
            row = BillableConfirmation.objects.select_for_update().get(
                pk=confirmation_id, organization=member.organization)
        except (BillableConfirmation.DoesNotExist, ValueError, ValidationError):
            raise PermissionDenied('确认记录不存在或无权访问')
        if row.status != 'draft' or row.created_by_member_id != member.pk:
            _fail('只有本人创建的确认草稿可以修改')
        if row.version != _version(expected_version):
            _fail('确认草稿版本已变化，请刷新后重试')
        row.version += 1
    else:
        if expected_version is not None:
            _fail('新建确认不接受已有版本号')
        row = BillableConfirmation(organization=member.organization, created_by_member=member,
                                   create_token=token, create_digest=digest)
    row.timesheet_ref = timesheet_ref
    row.timesheet_id = _timesheet_key(timesheet_ref['id'])
    row.contract_line_ref = contract_line_ref
    row.contract_line_id = uuid.UUID(contract_line_ref['id'])
    row.confirmed_minutes = minutes
    row.confirmed_on = when
    row.evidence = evidence
    if dry_run:
        return {'status': 'draft', 'confirmed_minutes': minutes,
                'confirmed_on': str(when), 'timesheet_ref': timesheet_ref,
                'contract_line_ref': contract_line_ref}
    row.save()
    audit(member, f'time.{DRAFT_ACTION}', row, {'confirmed_minutes': minutes,
                                                'confirmed_on': str(when)})
    _record(member, 'draft.save', token, digest, row)
    return _snapshot(row)


@transaction.atomic
def confirm_billable(member, confirmation_id, *, expected_version, request_token, dry_run=True):
    """draft -> confirmed; canonical time.billable_confirmed.v1 with exactly three refs."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        # confirmed_by_member is nullable, so a plain select_related() builds an
        # outer join that PostgreSQL refuses to lock; lock only the row itself.
        row = BillableConfirmation.objects.select_for_update(of=('self',)).select_related(
            'created_by_member__user', 'confirmed_by_member__user').get(
            pk=confirmation_id, organization=member.organization)
    except (BillableConfirmation.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('确认记录不存在或无权访问')
    payload = {'confirmation': str(row.pk), 'expected_version': expected_version}
    token, digest, replay = _receipt(member, 'confirm', request_token, payload, dry_run)
    if replay is not None:
        return _snapshot(replay)
    if row.status != 'draft':
        _fail('确认草稿已处理，请刷新后查看')
    if row.version != _version(expected_version):
        _fail('确认草稿版本已变化，请刷新后重试')
    if row.created_by_member_id == member.pk:
        _fail('创建人不能确认自己创建的可计费记录，请由另一位负责人确认')
    _validate_targets(member, row.timesheet_ref, row.contract_line_ref,
                      row.confirmed_minutes, row.confirmed_on)
    if BillableConfirmation.objects.filter(
            organization=member.organization, status='confirmed',
            timesheet_id=row.timesheet_id, contract_line_id=row.contract_line_id).exclude(
            pk=row.pk).exists():
        _fail('该工时与合同条款已存在有效确认')
    if dry_run:
        return {'status': 'confirmed', 'confirmation_id': str(row.pk),
                'timesheet_ref': row.timesheet_ref, 'contract_line_ref': row.contract_line_ref}
    row.status, row.confirmed_by_member, row.confirmed_at = 'confirmed', member, timezone.now()
    row.version += 1
    row.save()
    event_id = uuid.uuid4()
    source_event = audit(member, 'plugin.time.billable.confirmed', row, {
        'confirmed_minutes': row.confirmed_minutes, 'confirmed_on': str(row.confirmed_on)})
    DomainEvent.objects.create(
        event_id=event_id, organization=member.organization, source=source_event, topic=TOPIC,
        data={'event_id': str(event_id), 'topic': TOPIC, 'schema_version': 1,
              'organization_id': str(member.organization_id), 'source': row.source_ref,
              'occurred_at': timezone.now().isoformat(),
              'payload': {'changed_refs': [row.source_ref, row.timesheet_ref,
                                           row.contract_line_ref],
                          'posting_event_refs': []}})
    _record(member, 'confirm', token, digest, row)
    return _snapshot(row)


def list_billable_confirmations(member, *, project_id=None):
    member = _fresh(member)
    rows = BillableConfirmation.objects.select_related(
        'created_by_member__user', 'confirmed_by_member__user').filter(
        organization=member.organization)
    if member.role == 'pm':
        from core.access import visible_projects
        visible = visible_projects(member).values_list('pk', flat=True)
        rows = rows.filter(timesheet_id__in=[
            _timesheet_key(pk) for pk in Timesheet.objects.filter(
                project_id__in=visible).values_list('pk', flat=True)])
    if project_id:
        timesheets = Timesheet.objects.filter(project_id=project_id,
                                              organization=member.organization)
        rows = rows.filter(timesheet_id__in=[
            _timesheet_key(pk) for pk in timesheets.values_list('pk', flat=True)])
    return rows


def get_billable_confirmation(member, confirmation_id):
    member = _fresh(member)
    try:
        row = BillableConfirmation.objects.select_related(
            'created_by_member__user', 'confirmed_by_member__user').get(
            pk=confirmation_id, organization=member.organization)
    except (BillableConfirmation.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('确认记录不存在或无权访问')
    if member.role == 'pm':
        try:
            timesheet_pk = int(row.timesheet_ref['id'])
        except (KeyError, TypeError, ValueError):
            raise PermissionDenied('确认记录不存在或无权访问')
        timesheet = Timesheet.objects.filter(pk=timesheet_pk).first()
        if timesheet is None or timesheet.project_id is None:
            raise PermissionDenied('确认记录不存在或无权访问')
        from core.access import require_project
        require_project(member, timesheet.project)
    return row


REVERSE_ACTION = 'reverse'
REVERSAL_ACTION = 'time.billable_confirmation.reversed'


def _reason(value):
    if not isinstance(value, str):
        _fail('撤销原因须为文本')
    value = value.strip()
    if not 1 <= len(value) <= 500:
        _fail('撤销原因须为1至500个字符')
    return value


@transaction.atomic
def reverse_billable_confirmation(member, confirmation_id, *, reason, request_token,
                                  expected_version, dry_run=True):
    """Append-only confirmed -> reversed transition; no canonical event, no financial write."""
    member = _fresh(member, write=True)
    lock_company(member)
    try:
        row = BillableConfirmation.objects.select_for_update(of=('self',)).get(
            pk=confirmation_id, organization=member.organization)
    except (BillableConfirmation.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('确认记录不存在或无权访问')
    reason = _reason(reason)
    payload = {'confirmation': str(row.pk), 'reason': reason, 'expected_version': expected_version}
    token, digest, replay = _receipt(member, REVERSE_ACTION, request_token, payload, dry_run)
    if replay is not None:
        return _snapshot(replay)
    if row.status != 'confirmed':
        _fail('只有已确认的可计费记录可以撤销')
    if row.version != _version(expected_version):
        _fail('确认记录版本已变化，请刷新后重试')
    if row.created_by_member_id == member.pk:
        _fail('创建人不能撤销自己创建的确认，请由另一位负责人撤销')
    if dry_run:
        return {'confirmation_id': str(row.pk), 'status': 'reversed',
                'version': row.version + 1, 'reason': reason,
                'dry_run': True, 'changed_refs': [row.source_ref, row.timesheet_ref,
                                                  row.contract_line_ref]}
    row.status, row.version = 'reversed', row.version + 1
    row.save(update_fields=['status', 'version'])
    audit(member, REVERSAL_ACTION, row, {
        'reason': reason, 'confirmation_ref': row.source_ref,
        'changed_refs': [row.source_ref, row.timesheet_ref, row.contract_line_ref],
        'confirmed_minutes': row.confirmed_minutes, 'version': row.version})
    _record(member, REVERSE_ACTION, token, digest, row)
    return _snapshot(row)


def _expected_value_cents(member, row):
    try:
        snapshot = contract_approvals.approved_line_snapshot(
            member, row.contract_line_ref, row.confirmed_on.isoformat())
    except (ValidationError, PermissionDenied):
        return None, None
    config = snapshot['pricing_configuration']
    unit_price, unit_minutes = config.get('unit_price_cents'), config.get('unit_minutes')
    if not isinstance(unit_price, int) or not isinstance(unit_minutes, int) or unit_minutes <= 0:
        return None, snapshot
    from decimal import Decimal, ROUND_HALF_UP
    value = (Decimal(row.confirmed_minutes) * Decimal(unit_price) / Decimal(unit_minutes)).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    return int(value), snapshot


def billable_confirmation_pairing(member, confirmation_id):
    """Read-only pairing projection against management revenue and settlement; no writes."""
    member = _fresh(member)
    row = get_billable_confirmation(member, confirmation_id)
    expected_cents, snapshot = _expected_value_cents(member, row)
    project_id = snapshot['project_id'] if snapshot else None
    if project_id is None:
        timesheet = Timesheet.objects.filter(pk=int(row.timesheet_ref['id'])).first()
        project_id = str(timesheet.project_id) if timesheet and timesheet.project_id else None
    revenue_rows = []
    if project_id:
        from pmo_plugins.management_revenue import services as revenue_services
        try:
            revenue_rows = [item for item in revenue_services.revenue_export_rows(
                member, project_id=project_id)
                if item['kind'] == 'revenue_recognition'
                and item['contract_line_id'] == str(row.contract_line_id)
                and not item.get('is_reversed')]
        except (PermissionDenied, ValidationError, AttributeError):
            revenue_rows = []
        if not revenue_rows:
            # The owner export is project-level and expects a posted accounting event; fall
            # back to a read-only line-level projection when that event is not materialised.
            from pmo_plugins.management_revenue.models import RevenueRecognition
            rows = RevenueRecognition.objects.filter(
                organization=member.organization, contract_line_id=row.contract_line_id,
                status='posted').order_by('occurred_on', 'id')
            revenue_rows = [{'kind': 'revenue_recognition', 'amount_cents': item.recognized_net_cents,
                             'source': item.source_ref, 'is_reversed': item.is_reversed}
                            for item in rows]
    settlements = []
    from pmo_plugins.settlement import receivable_services
    from pmo_plugins.settlement.models import Settlement
    for settlement_id in Settlement.objects.filter(
            organization=member.organization, contract_line_id=row.contract_line_id,
            status='active').order_by('created_at', 'id').values_list('pk', flat=True):
        try:
            settlements.append(receivable_services.settlement_snapshot(member, settlement_id))
        except (PermissionDenied, ValidationError):
            continue
    revenue_cents = sum(item['amount_cents'] for item in revenue_rows)
    settlement_cents = sum(item['gross_cents'] for item in settlements)
    if not revenue_rows and not settlements:
        status = 'unpaired'
    elif (revenue_rows and settlements and expected_cents is not None
          and revenue_cents == expected_cents and settlement_cents == expected_cents):
        status = 'paired'
    else:
        status = 'partial'
    return {
        'confirmation_ref': row.source_ref,
        'status': row.status, 'status_display': row.get_status_display(),
        'confirmed_minutes': row.confirmed_minutes,
        'timesheet_ref': row.timesheet_ref, 'contract_line_ref': row.contract_line_ref,
        'expected_value_cents': expected_cents,
        'revenue': {'count': len(revenue_rows), 'net_cents': revenue_cents,
                    'refs': [item['source'] for item in revenue_rows]},
        'settlements': {'count': len(settlements), 'gross_cents': settlement_cents,
                        'refs': [item['settlement_ref'] for item in settlements]},
        'difference_cents': (None if expected_cents is None
                             else expected_cents - revenue_cents - settlement_cents),
        'difference_revenue_cents': (None if expected_cents is None
                                     else expected_cents - revenue_cents),
        'difference_settlement_cents': (None if expected_cents is None
                                        else expected_cents - settlement_cents),
        'pairing_status': status,
    }


def billable_confirmation_pairing_list(member, *, project_id=None):
    member = _fresh(member)
    return [billable_confirmation_pairing(member, row.pk)
            for row in list_billable_confirmations(member, project_id=project_id)]
