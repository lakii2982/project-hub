"""PH-274 onboarding read-only import preview browser handlers (no business writes)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from . import preview_services as previews
from .forms import ImportPreviewForm


def _base(member, **values):
    return {'nav': 'onboarding', 'nav_page': 'import',
            'preview_url': '/modules/onboarding/import-preview-list/',
            **values}


def _member(context):
    try:
        return previews.require_preview_reader(context.member)
    except ValidationError:
        raise PermissionDenied('导入预览不可用')


def import_preview_list(context, payload):
    member = _member(context)
    return render(context.request, 'onboarding/import_preview_list.html', _base(
        member, form=ImportPreviewForm(),
        templates=previews.import_template_list(member), result=None))


def import_preview_run(context, payload):
    member = _member(context)
    request = context.request
    form = ImportPreviewForm(request.POST or None, request.FILES or None)
    result = None
    if form.is_valid():
        data = form.cleaned_data
        upload = data['file']
        try:
            result = previews.preview_import(
                member, kind=data.get('kind') or None, file_name=upload.name,
                content=upload.read())
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(request, '导入预览完成（只读，未写入任何业务事实）')
    return render(request, 'onboarding/import_preview_list.html', _base(
        member, form=form, templates=previews.import_template_list(member),
        result=result), status=200 if result else 400)
