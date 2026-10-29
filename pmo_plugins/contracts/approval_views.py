import uuid
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.utils import timezone
from . import approval_services as service
from .approval_forms import VersionForm, ActionForm, ReviewForm, build_formsets, candidate_lines
from .party_services import _reader, _writer
from .party_views import _context, url
from .services import get_contract
from .models import pricing_display


def _render(context, contract, version=None, *, form=None, sections=None, error='', status=200, bound_review=None):
    member = _reader(context.member)
    pending = contract.versions.filter(status='submitted').exists()
    editable = not pending and member.role in {'owner', 'finance'} and contract.status == 'draft' and (version is None or version.status == 'draft')
    if editable and form is None:
        form = VersionForm(initial={'contract_id': contract.pk, 'version_id': version.pk if version else None,
            'expected_version': version.version if version else None, 'request_token': uuid.uuid4(),
            'effective_on': version.effective_on if version else timezone.localdate(),
            'contract_reference': version.contract_reference if version else ''})
    if editable and sections is None:
        sections = build_formsets(contract, content=version.draft_content if version else None)
    action = {'version_id': version.pk, 'expected_version': version.version, 'request_token': uuid.uuid4()} if version else {}
    can_review = version and version.status == 'submitted' and member.role in {'owner', 'finance'} and member.pk not in {version.created_by_member_id, version.submitted_by_member_id}
    return render(context.request, 'contracts/contract_approval.html', _context(member,
        contract=contract, version=version, versions=service.list_versions(member, contract.pk),
        configuration_form=form if editable else None, rejected_review=bound_review.cleaned_data if bound_review and not can_review else None, sections=sections if editable else None,
        submit_form=ActionForm(initial=action) if version and version.status == 'draft' and editable else None,
        review_form=(bound_review or ReviewForm(initial=action)) if can_review else None,
        approved_lines=version.lines.select_related('policy_selection').prefetch_related('payment_terms', 'warranty_terms') if version and version.status == 'approved' else [],
        candidate=[{**item, 'pricing_display': pricing_display(item['pricing_configuration'])} for item in version.draft_content.get('lines', [])] if version and version.status != 'approved' else [],
        error=error, version_save_url=url('version-save'), version_submit_url=url('version-submit'),
        version_review_url=url('version-review'), new_version_url=url('version', contract=contract.pk),
        contract_url=url('contract', id=contract.pk)), status=status)


def version_detail(context, payload):
    member = _reader(context.member)
    version = service.get_version(member, payload['id']) if payload.get('id') else None
    contract = version.contract if version else get_contract(member, payload.get('contract'))
    return _render(context, contract, version)


def version_save(context, payload):
    member = _writer(context.member)
    contract = get_contract(member, payload.get('contract_id'))
    version = service.get_version(member, payload['version_id']) if payload.get('version_id') else None
    # Parent form sees only its fields; all formset keys are strictly validated below.
    form = VersionForm({k: payload[k] for k in payload if k in VersionForm.base_fields or k == 'csrfmiddlewaretoken'})
    sections = build_formsets(contract, payload, version.draft_content if version else None)
    allowed = set(VersionForm.base_fields) | {'csrfmiddlewaretoken'}
    for name, _, group in sections:
        allowed.update(name + '-' + key for key in group.management_form.fields)
        for index in range(group.total_form_count()):
            allowed.update(f'{name}-{index}-{key}' for key in (set(group.form.base_fields) | {'DELETE'}))
    error = ''
    valid = form.is_valid()
    valid = all([group.is_valid() for _, _, group in sections]) and valid
    if set(payload) - allowed:
        error, valid = '包含不允许的字段，请刷新后重试', False
    if valid:
        try:
            data = form.cleaned_data
            version = service.save_version(member, contract.pk, lines=candidate_lines(sections), effective_on=data['effective_on'],
                contract_reference=data['contract_reference'], request_token=data['request_token'],
                version_id=data.get('version_id'), expected_version=data.get('expected_version'))
        except ValidationError as exc:
            error = '；'.join(exc.messages)
        else:
            messages.success(context.request, '合同配置草稿已保存，请核对后提交')
            return redirect(url('version', id=version.pk))
    return _render(context, contract, version, form=form, sections=sections, error=error, status=400)


def _action(context, payload, reviewing=False):
    member = _writer(context.member)
    version = service.get_version(member, payload.get('version_id'))
    form = ReviewForm(payload) if reviewing else ActionForm(payload)
    error = ''
    if form.is_valid():
        data = form.cleaned_data
        try:
            kwargs = {'expected_version': data['expected_version'], 'request_token': data['request_token']}
            if reviewing:
                service.review_version(member, version.pk, decision=data['decision'], review_note=data['review_note'], **kwargs)
            else:
                service.submit_version(member, version.pk, **kwargs)
        except ValidationError as exc:
            error = '；'.join(exc.messages)
        else:
            return redirect(url('version', id=version.pk))
    else:
        error = '；'.join(str(item) for errors in form.errors.values() for item in errors)
    return _render(context, version.contract, version, error=error, status=400, bound_review=form if reviewing else None)


def version_submit(context, payload):
    return _action(context, payload)


def version_review(context, payload):
    return _action(context, payload, True)
