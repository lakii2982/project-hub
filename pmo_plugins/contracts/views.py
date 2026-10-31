from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect

from . import services as service
from . import change_services
from . import approval_services
from . import terms_snapshot_services
from . import fx_services
from .forms import ContractDraftForm, FxRateForm
from .party_services import _reader, _writer
from .party_views import _context, url


def _initial(contract):
    return {'contract_id': contract.pk, 'project': contract.project_id, 'party': contract.party_id,
            'contract_code': contract.contract_code, 'signed_on': contract.signed_on,
            'expected_version': contract.version}


def contract_list(context, payload):
    member = _reader(context.member)
    form = ContractDraftForm(member=member) if member.role in {'owner', 'finance'} else None
    return render(context.request, 'contracts/contract_form.html', _context(member,
        contracts=service.list_contracts(member, query=payload.get('q', '')), query=payload.get('q', ''),
        form=form, contract_save_url=url('contract-save')))


def contract_detail(context, payload):
    member = _reader(context.member)
    contract = service.get_contract(member, payload.get('id'))
    if payload.get('edit') == '1':
        member = _writer(member)
        form = ContractDraftForm(member=member, initial=_initial(contract))
        return render(context.request, 'contracts/contract_form.html', _context(member,
            contract=contract, form=form, contract_save_url=url('contract-save')))
    return render(context.request, 'contracts/contract_detail.html', _context(member,
        contract=contract, edit_url=url('contract', id=contract.pk, edit=1),
        party_url=url('party', id=contract.party_id), configuration_url=url('version', contract=contract.pk),
        configuration_versions=contract.versions.all(), pending=contract.versions.filter(status='submitted').exists(),
        changes=change_services.list_contract_changes(member, contract.pk),
        changes_url=url('contract-changes', contract=contract.pk),
        change_impacts=approval_services.contract_change_impact_list(member, contract_id=contract.pk),
        change_impact_url=url('contract-change-impact'),
        new_change_url=url('contract-change', contract=contract.pk)))


def contract_change_impact_detail(context, payload):
    """Read-only change impact page; history_read route so it survives module disablement."""
    member = approval_services._impact_reader(context.member)
    impact = approval_services.contract_change_impact(context.member, payload.get('id'))
    contract = impact.pop('contract')
    return render(context.request, 'contracts/change_detail.html', _context(member,
        contract=contract, impact=impact,
        contract_url=url('contract', id=contract.pk),
        changes_url=url('contract-changes', contract=contract.pk)))


def contract_save(context, payload):
    member = _writer(context.member)
    contract = service.get_contract(member, payload.get('contract_id')) if payload.get('contract_id') else None
    form = ContractDraftForm(payload, member=member)
    if form.is_valid():
        try:
            values = form.business_values()
            if contract:
                contract = service.update_contract_draft(member, contract.pk,
                    expected_version=form.cleaned_data.get('expected_version'), **values)
            else:
                contract = service.create_contract_draft(member,
                    request_token=form.cleaned_data.get('request_token'), **values)
        except ValidationError as error:
            form.add_error(None, '；'.join(error.messages))
        else:
            messages.success(context.request, '合同草稿已保存；尚未生效或形成收入')
            return redirect(url('contract', id=contract.pk))
    return render(context.request, 'contracts/contract_form.html', _context(member,
        contract=contract, contracts=service.list_contracts(member) if contract is None else None,
        form=form, contract_save_url=url('contract-save')), status=400)


def contract_terms_snapshot(context, payload):
    """Read-only approved terms snapshot and settlement reconciliation page."""
    from django.shortcuts import render
    from .forms import TermsSnapshotFilterForm
    member = terms_snapshot_services._reader(context.member)
    allowed = {'contract', 'as_of'}
    if set(payload) - allowed:
        return render(context.request, 'contracts/terms_snapshot.html',
                      _context(member, snapshot=None, issues=[],
                               form=TermsSnapshotFilterForm(member=member),
                               error='包含不允许的查询参数，请刷新后重试',
                               contracts_url=url('contracts'),
                               snapshot_url=url('contract-terms-snapshot')), status=400)
    form = TermsSnapshotFilterForm(payload, member=member)
    as_of = payload.get('as_of') or None
    contract_id = payload.get('contract') or None
    try:
        issues = terms_snapshot_services.terms_settlement_issues(member, as_of=as_of)
        snapshot = (terms_snapshot_services.approved_terms_snapshot(
            member, contract_id=contract_id, as_of=as_of) if contract_id else None)
    except (ValidationError, PermissionDenied) as error:
        return render(context.request, 'contracts/terms_snapshot.html',
                      _context(member, snapshot=None, issues=[],
                               form=form,
                               error='；'.join(getattr(error, 'messages', [str(error)])),
                               contracts_url=url('contracts'),
                               snapshot_url=url('contract-terms-snapshot')), status=400)
    if snapshot is not None:
        contract_ref = snapshot['contract_ref']['id']
        issues = [issue for issue in issues if issue['contract_ref']['id'] == contract_ref]
    return render(context.request, 'contracts/terms_snapshot.html', _context(
        member, snapshot=snapshot, issues=issues, form=form, error='',
        contracts_url=url('contracts'), snapshot_url=url('contract-terms-snapshot'),
        contract_url=url('contract', id=contract_id) if contract_id else '',
        contract_choices=form.fields['contract'].queryset))


def fx_rates(context, payload):
    """Read-only FX rate and contract-line original/book display; writers see the form."""
    member = fx_services._reader(context.member, roles=fx_services.READ_ROLES)
    form = FxRateForm() if member.role in fx_services.WRITE_ROLES else None
    error = ''
    try:
        rates = (fx_services.fx_rate_list(member)
                 if member.role in fx_services.RATE_ROLES else [])
        lines = fx_services.contract_line_fx_snapshots(member)
    except (PermissionDenied, ValidationError) as exc:
        rates, lines = [], []
        error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    return render(context.request, 'contracts/fx_rates.html', _context(
        member, rates=rates, lines=lines, form=form, error=error,
        enabled=member.role in fx_services.WRITE_ROLES,
        fx_rate_save_url=url('fx-rate-save')))


def fx_rate_save(context, payload):
    member = fx_services._writer(context.member)
    form = FxRateForm(payload)
    if form.is_valid():
        try:
            fx_services.record_fx_rate(
                member, quote_currency=form.cleaned_data['quote_currency'],
                rate=form.cleaned_data['rate'], effective_on=form.cleaned_data['effective_on'],
                source=form.cleaned_data['source'], reference=form.cleaned_data['reference'])
        except ValidationError as error:
            form.add_error(None, '；'.join(error.messages))
        else:
            messages.success(context.request, '汇率快照已追加；历史版本不会被改写')
            return redirect(url('fx-rates'))
    return render(
        context.request, 'contracts/fx_rates.html',
        _context(member, rates=fx_services.fx_rate_list(member),
                 lines=fx_services.contract_line_fx_snapshots(member), form=form,
                 error='', enabled=True, fx_rate_save_url=url('fx-rate-save')), status=400)
