"""Browser handlers. All reads and writes go through the contracts owner services."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect
from django.urls import get_script_prefix

from . import party_services as service
from .party_forms import PartyForm, ContactForm


def url(page, **query):
    from urllib.parse import urlencode
    base = get_script_prefix() + 'modules/contracts/' + page + '/'
    return base + ('?' + urlencode(query) if query else '')


def _context(member, **values):
    return {'nav': 'contracts', 'can_write': member.role in {'owner', 'finance'},
            'parties_url': url('parties'), 'contracts_url': url('contracts'),
            'party_save_url': url('party-save'), 'contact_save_url': url('contact-save'),
            **values}


def party_list(context, payload):
    member = service._reader(context.member)
    query, role = payload.get('q', ''), payload.get('role', '')
    return render(context.request, 'contracts/party_list.html', _context(member,
        parties=service.list_parties(member, query=query, role=role), query=query, role=role,
        form=PartyForm() if member.role in service.WRITE_ROLES else None))


def _detail(context, party, *, form=None, contact_form=None, status=200):
    member = service._reader(context.member)
    can_write = member.role in service.WRITE_ROLES
    if form is None and can_write:
        form = PartyForm(initial={'party_id': party.pk, 'display_name': party.display_name,
            'roles': party.roles, 'active_from': party.active_from, 'inactive_on': party.inactive_on,
            'expected_version': party.version})
    if contact_form is None and can_write:
        contact_form = ContactForm(initial={'party_id': party.pk}, auto_id='contact_%s')
    return render(context.request, 'contracts/party_detail.html', _context(member,
        party=party, contacts=party.contacts.filter(organization=member.organization),
        form=form, contact_form=contact_form), status=status)


def party_detail(context, payload):
    party = service.get_party(context.member, payload.get('id'))
    contact_form = None
    if payload.get('contact'):
        member = service._writer(context.member)
        try:
            contact = party.contacts.get(pk=payload['contact'], organization=member.organization)
        except (ValueError, ValidationError, service.Contact.DoesNotExist):
            raise PermissionDenied('记录不存在或无权访问')
        contact_form = ContactForm(initial={'contact_id': contact.pk, 'party_id': party.pk,
            'name': contact.name, 'email': contact.email, 'phone': contact.phone,
            'expected_version': contact.version}, auto_id='contact_%s')
    return _detail(context, party, contact_form=contact_form)


def party_save(context, payload):
    member = service._writer(context.member)
    form = PartyForm(payload)
    party = service.get_party(member, payload.get('party_id')) if payload.get('party_id') else None
    if form.is_valid():
        data = form.cleaned_data
        values = {k: data[k] for k in ('display_name', 'roles', 'active_from', 'inactive_on')}
        try:
            if party:
                party = service.update_party(member, party.pk, expected_version=data.get('expected_version'), **values)
            else:
                party = service.create_party(member, request_token=data.get('request_token'), **values)
        except ValidationError as error:
            form.add_error(None, '；'.join(error.messages))
        else:
            messages.success(context.request, '客户／供应商已保存')
            return redirect(url('party', id=party.pk))
    if party:
        return _detail(context, party, form=form, status=400)
    return render(context.request, 'contracts/party_list.html', _context(member,
        parties=service.list_parties(member), form=form), status=400)


def contact_save(context, payload):
    member = service._writer(context.member)
    party = service.get_party(member, payload.get('party_id'))
    form = ContactForm(payload, auto_id='contact_%s')
    if form.is_valid():
        data = form.cleaned_data
        values = {k: data[k] for k in ('name', 'email', 'phone')}
        try:
            if data.get('contact_id'):
                if not party.contacts.filter(pk=data['contact_id'], organization=member.organization).exists():
                    raise PermissionDenied('记录不存在或无权访问')
                service.update_contact(member, data['contact_id'], expected_version=data.get('expected_version'), **values)
            else:
                service.create_contact(member, party_id=party.pk, request_token=data.get('request_token'), **values)
        except ValidationError as error:
            form.add_error(None, '；'.join(error.messages))
        else:
            messages.success(context.request, '联系人已保存')
            return redirect(url('party', id=party.pk))
    return _detail(context, party, contact_form=form, status=400)
