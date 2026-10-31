"""PH-321 multi-currency first slice (contracts-only).

Narrow scope per Lead ruling B: contracts-owned append-only FX rate facts, one-time
ROUND_HALF_UP conversion into the existing CNY book fields, and read-only
original/book/rate display. Non-CNY values without a frozen rate are refused;
there is no fallback to a nearby rate and no historical recomputation.

The rate model is declared here because the PH-321 BASE allowlist does not include
pmo_plugins/contracts/models.py; it is an unmanaged ORM model whose table is
created by the contracts migration. No core migration is touched.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.access import visible_projects
from core.models import Membership, Organization
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company, require_member
from pmo_plugins.contracts.models import Contract, ContractLine, FxRateSnapshot

MAX_CENTS = 10**12
CURRENCY_RE = r'^[A-Z]{3}$'
SOURCES = ('manual', 'bank', 'provider')
SOURCE_LABELS = {'manual': '手工', 'bank': '银行', 'provider': '供应商'}
READ_ROLES = {'owner', 'finance', 'auditor', 'pm'}
RATE_ROLES = {'owner', 'finance', 'auditor'}
WRITE_ROLES = {'owner', 'finance'}
ALGORITHM = 'fx_rate_snapshot_v1'
CONVERSION_ALGORITHM = 'fx_once_half_up_v1'


def _fail(message):
    raise ValidationError(message)


def _reader(member, *, roles=READ_ROLES):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in roles:
        raise PermissionDenied('无权查看汇率事实')
    return member


def _writer(member):
    member = _reader(member, roles=WRITE_ROLES)
    if not is_enabled(member.organization, 'contracts'):
        raise PermissionDenied('合同模块已停用，不能维护汇率')
    return member


def _day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str):
        _fail('生效日期须为有效年月日')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail('生效日期须为有效年月日')
    if parsed.strftime('%Y-%m-%d') != value:
        _fail('生效日期须为有效年月日')
    return parsed


def _currency(value, field='币种'):
    if not isinstance(value, str):
        _fail(f'{field}须为三位 ISO 4217 代码')
    value = value.strip().upper()
    if len(value) != 3 or not value.isalpha() or not value.isascii():
        _fail(f'{field}须为三位 ISO 4217 代码')
    return value


def _decimal(value):
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _fail('汇率须为有效十进制数')
    if not rate.is_finite() or rate <= 0:
        _fail('汇率须大于0')
    if rate > Decimal('1000000'):
        _fail('汇率超出允许范围')
    if rate.as_tuple().exponent < -8:
        _fail('汇率最多8位小数')
    return rate.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)


def _cents(value, label='金额'):
    if type(value) is not int or value < 0 or value > MAX_CENTS:
        _fail(f'{label}须为非负整数分且不超过100亿元')
    return value


def _snapshot_projection(row):
    return {
        'ref': row.source_ref,
        'base_currency': row.base_currency,
        'quote_currency': row.quote_currency,
        'effective_on': row.effective_on.isoformat(),
        'rate': str(row.rate),
        'source': row.source,
        'source_display': SOURCE_LABELS.get(row.source, row.source),
        'reference': row.reference,
        'version': row.version,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'algorithm_version': row.algorithm_version,
    }


def latest_rate(organization, *, quote_currency, effective_on, base_currency='CNY'):
    """Exact business-date/version lookup; never falls back to another date."""
    return FxRateSnapshot.objects.filter(
        organization=organization, base_currency=base_currency,
        quote_currency=_currency(quote_currency, '原币种'),
        effective_on=effective_on).order_by('-version').first()


def resolve_fx_rate(member, *, quote_currency, effective_on, base_currency='CNY'):
    member = _reader(member)
    when = _day(effective_on) if not type(effective_on) is date else effective_on
    row = latest_rate(member.organization, quote_currency=quote_currency,
                      effective_on=when, base_currency=_currency(base_currency, '记账币种'))
    if row is None:
        _fail(f'缺少 {when.isoformat()} 的 {_currency(quote_currency, "原币种")} 汇率，不能换算')
    return _snapshot_projection(row)


def resolve_fx_ref(member, ref):
    if not isinstance(ref, dict) or ref.get('namespace') != 'contracts' \
            or ref.get('kind') != 'fx_rate_snapshot':
        _fail('汇率引用无效')
    try:
        row = FxRateSnapshot.objects.get(
            pk=ref.get('id'), organization=member.organization,
            version=ref.get('version'))
    except (FxRateSnapshot.DoesNotExist, ValueError, ValidationError):
        _fail('汇率引用不存在或版本不匹配')
    return _snapshot_projection(row)


def convert_cents(amount_cents, rate):
    """One-time ROUND_HALF_UP conversion; rate is Decimal book units per original unit."""
    amount = _cents(amount_cents)
    if not isinstance(rate, Decimal):
        rate = Decimal(str(rate))
    if not rate.is_finite() or rate <= 0:
        _fail('汇率须大于0')
    return int((Decimal(amount) * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def convert_original_amounts(*, original_net_cents, original_tax_cents=None,
                             currency, rate):
    """Validate original net/tax and return book net/tax/gross converted once."""
    net = _cents(original_net_cents, '原币净额')
    tax = _cents(original_tax_cents, '原币税额') if original_tax_cents is not None else 0
    original_gross = net + tax
    if original_gross > MAX_CENTS:
        _fail('原币含税金额超出允许范围')
    converted_net = convert_cents(net, rate)
    converted_tax = convert_cents(tax, rate)
    converted_gross = converted_net + converted_tax
    if converted_gross > MAX_CENTS:
        _fail('换算后含税金额超出允许范围')
    return {
        'currency': _currency(currency, '原币种'),
        'original_net_cents': net, 'original_tax_cents': tax,
        'original_gross_cents': original_gross,
        'net_cents': converted_net, 'tax_cents': converted_tax,
        'gross_cents': converted_gross,
        'algorithm_version': CONVERSION_ALGORITHM,
    }


@transaction.atomic
def record_fx_rate(member, *, quote_currency, rate, effective_on, source='manual',
                   reference='', base_currency='CNY', dry_run=False):
    """Record a new append-only rate version for the exact effective business date."""
    member = _writer(member)
    base = _currency(base_currency, '记账币种')
    quote = _currency(quote_currency, '原币种')
    if base != 'CNY':
        _fail('本片记账本位币固定为 CNY')
    if quote == base:
        _fail('原币种不能与记账本位币相同')
    if source not in SOURCES:
        _fail('汇率来源无效')
    when = _day(effective_on)
    value = _decimal(rate)
    ref = reference if isinstance(reference, str) else ''
    ref = ref.strip()
    if len(ref) > 240:
        _fail('汇率参考最多240字')
    if dry_run:
        return {'dry_run': True, 'base_currency': base, 'quote_currency': quote,
                'effective_on': when.isoformat(), 'rate': str(value),
                'source': source, 'reference': ref, 'algorithm_version': ALGORITHM}
    lock_company(member)
    previous = FxRateSnapshot.objects.filter(
        organization=member.organization, base_currency=base,
        quote_currency=quote, effective_on=when).order_by('-version').first()
    version = (previous.version if previous else 0) + 1
    row = FxRateSnapshot.objects.create(
        organization=member.organization, base_currency=base, quote_currency=quote,
        effective_on=when, rate=value, source=source, reference=ref, version=version,
        created_by=member)
    audit(member, 'plugin.contracts.fx_rate_recorded', row, {
        'base_currency': base, 'quote_currency': quote, 'effective_on': when.isoformat(),
        'rate': str(value), 'source': source, 'version': version,
        'previous_version': previous.version if previous else None,
    })
    return _snapshot_projection(row)


def fx_rate_list(member, *, quote_currency=None, as_of=None):
    member = _reader(member, roles=RATE_ROLES)
    rows = FxRateSnapshot.objects.filter(organization=member.organization)
    if quote_currency:
        rows = rows.filter(quote_currency=_currency(quote_currency, '原币种'))
    if as_of is not None:
        rows = rows.filter(effective_on__lte=_day(as_of) if not type(as_of) is date else as_of)
    return [_snapshot_projection(row) for row in rows.order_by(
        '-effective_on', 'quote_currency', '-version')[:200]]


def _line_scope(member, rows):
    if member.role != 'pm':
        return rows
    projects = visible_projects(member).filter(manager=member)
    return rows.filter(contract_version__contract__project__in=projects)


def contract_line_fx_snapshots(member, *, project_id=None, contract_id=None,
                               currency=None, as_of=None):
    """Read-only original/book/rate view over existing contract-line facts."""
    member = _reader(member)
    rows = ContractLine.objects.select_related(
        'contract_version__contract__project').filter(organization=member.organization)
    rows = _line_scope(member, rows)
    if project_id is not None:
        rows = rows.filter(contract_version__contract__project_id=project_id)
    if contract_id is not None:
        rows = rows.filter(contract_version__contract_id=contract_id)
    if currency:
        rows = rows.filter(currency=_currency(currency, '币种'))
    if as_of is not None:
        day = _day(as_of) if not type(as_of) is date else as_of
        rows = rows.filter(contract_version__effective_on__lte=day,
                           contract_version__status__in=('approved', 'superseded'))
    snapshots = []
    for line in rows.order_by('contract_version__contract__contract_code', 'line_no')[:200]:
        item = {
            'line_ref': {'namespace': 'contracts', 'kind': 'contract_line',
                         'id': str(line.pk), 'version': line.version},
            'contract_id': str(line.contract_version.contract_id),
            'contract_code': line.contract_version.contract.contract_code,
            'project_id': str(line.contract_version.contract.project_id),
            'line_no': line.line_no,
            'currency': line.currency,
            'book_currency': 'CNY',
            'original_net_cents': line.original_net_cents,
            'original_tax_cents': line.original_tax_cents,
            'original_gross_cents': line.original_gross_cents,
            'net_cents': line.net_cents, 'tax_cents': line.tax_cents,
            'gross_cents': line.gross_cents,
            'fx_rate_ref': line.fx_rate_ref,
            'conversion': None, 'missing': [], 'issues': [],
        }
        if line.currency == 'CNY':
            item['conversion'] = {'algorithm_version': 'legacy_cny_v1',
                                  'rate': '1', 'original_net_cents': line.net_cents,
                                  'original_tax_cents': line.tax_cents,
                                  'original_gross_cents': line.gross_cents}
        else:
            try:
                if not line.fx_rate_ref:
                    _fail('非 CNY 合同行缺少冻结汇率引用')
                rate = resolve_fx_ref(member, line.fx_rate_ref)
                if date.fromisoformat(rate['effective_on']) > line.contract_version.effective_on:
                    _fail('汇率生效日期晚于合同行业务日期')
            except (ValidationError, PermissionDenied):
                item['missing'].append('fx_rate')
                item['issues'].append({'issue_code': 'fx_rate_missing',
                                       'expected': 'frozen fx_rate_ref on the non-CNY line',
                                       'actual': None})
            else:
                item['conversion'] = {'algorithm_version': CONVERSION_ALGORITHM,
                                      'rate': rate['rate'], 'rate_ref': rate['ref'],
                                      'original_net_cents': line.original_net_cents,
                                      'original_tax_cents': line.original_tax_cents,
                                      'original_gross_cents': line.original_gross_cents}
        snapshots.append(item)
    return snapshots


def prepare_contract_line_fx(member, *, currency, original_net_cents,
                             original_tax_cents=0, effective_on, fx_rate_ref=None):
    """Validate/convert one contract-line amount for the future approval write path.

    This is the contracts-owned write preparation helper: it never creates or
    edits a ContractLine itself. Approval/change services can call it and persist
    the returned currency/original/fx fields when the approved version is built.
    CNY keeps legacy amounts; non-CNY requires an exact frozen rate (or a valid
    rate ref) and is converted once with ROUND_HALF_UP; no fallback is allowed.
    """
    member = _writer(member)
    code = _currency(currency, '原币种')
    when = _day(effective_on) if not type(effective_on) is date else effective_on
    if code == 'CNY':
        if fx_rate_ref is not None:
            _fail('CNY 合同行不得携带汇率引用')
        net = _cents(original_net_cents, '原币净额')
        tax = _cents(original_tax_cents, '原币税额')
        if net + tax > MAX_CENTS:
            _fail('含税金额超出允许范围')
        return {'currency': 'CNY', 'fx_rate_ref': None,
                'original_net_cents': None, 'original_tax_cents': None,
                'original_gross_cents': None,
                'net_cents': net, 'tax_cents': tax, 'gross_cents': net + tax,
                'algorithm_version': 'legacy_cny_v1', 'rate_ref': None}
    if fx_rate_ref is not None:
        rate = resolve_fx_ref(member, fx_rate_ref)
        if rate['quote_currency'] != code or rate['base_currency'] != 'CNY':
            _fail('汇率引用币种对不匹配')
        if date.fromisoformat(rate['effective_on']) > when:
            _fail('汇率生效日期晚于合同业务日期，不能使用未来汇率')
    else:
        rate = resolve_fx_rate(member, quote_currency=code, effective_on=when)
    converted = convert_original_amounts(
        original_net_cents=original_net_cents, original_tax_cents=original_tax_cents,
        currency=code, rate=Decimal(rate['rate']))
    converted['fx_rate_ref'] = rate['ref']
    converted['rate_ref'] = rate['ref']
    return converted
