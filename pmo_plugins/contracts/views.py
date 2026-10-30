from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect

from . import services as service
from . import change_services
from .forms import ContractDraftForm
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
        new_change_url=url('contract-change', contract=contract.pk)))


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
