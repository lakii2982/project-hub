"""PH-274/PH-353 onboarding import preview and atomic submission browser handlers."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from core.plugin_runtime import is_enabled

from . import import_commit_services as commits
from . import preview_services as previews
from .forms import ImportPreviewForm, ImportSubmitForm
from .models import ImportPreview, ImportSubmissionReceipt

PAGE_URL = '/modules/onboarding/import-submit/'
COMMIT_URL = '/modules/onboarding/import-submit-commit/'


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
    """Read-only preview entry (history_read, GET): renders the upload page.

    A small text payload can be previewed directly via the content query parameter so
    read-only roles (including auditor) have an HTTP preview channel; file uploads use
    the business_write upload route.
    """
    member = _member(context)
    request = context.request
    form = ImportPreviewForm()
    result = None
    error_message = None
    if payload.get('content'):
        try:
            result = previews.preview_import(
                member, kind=payload.get('kind') or None,
                file_name=payload.get('file_name') or 'pasted.csv',
                content=payload.get('content'))
        except ValidationError as exc:
            error_message = '；'.join(exc.messages)
        else:
            messages.success(request, '导入预览完成（只读，未写入任何业务事实）')
    return render(request, 'onboarding/import_preview_list.html', _base(
        member, form=form, templates=previews.import_template_list(member),
        result=result, error_message=error_message),
        status=200 if result or not payload.get('content') else 400)


def import_preview_upload(context, payload):
    member = _member(context)
    request = context.request
    form = ImportPreviewForm(request.POST or None, request.FILES or None)
    result = None
    if form.is_valid():
        data = form.cleaned_data
        upload = data['file']
        save_preview = payload.get('save_preview') == '1'
        if save_preview and data.get('kind') != 'invoicing':
            form.add_error(None, '仅发票导入支持预检并保存')
        elif save_preview and not is_enabled(member.organization, 'invoicing'):
            raise PermissionDenied('发票模块已停用')
        else:
            try:
                result = previews.preview_import(
                    member, kind=data.get('kind') or None, file_name=upload.name,
                    content=upload.read())
            except ValidationError as exc:
                form.add_error(None, '；'.join(exc.messages))
            else:
                if save_preview and (result['summary']['row_count']
                                     != result['summary']['preview_row_count']):
                    form.add_error(None, '完整预览超过200行，请拆分CSV至200行以内后再保存')
                    result = None
                elif save_preview:
                    commits.persist_preview(
                        member, kind='invoicing', file_name=upload.name,
                        rows=result['rows'], conflicts=result['conflicts'])
                    messages.success(
                        request, '发票预检已保存，尚未写入业务事实；请完成期间预检后提交')
                    return redirect(get_script_prefix() + PAGE_URL.lstrip('/'))
                else:
                    messages.success(request, '导入预览完成（只读，未写入任何业务事实）')
    return render(request, 'onboarding/import_preview_list.html', _base(
        member, form=form, templates=previews.import_template_list(member),
        result=result), status=200 if result else 400)


def _submit_context(member, **values):
    return _base(
        member, page_url=PAGE_URL, commit_url=COMMIT_URL,
        module_enabled=is_enabled(member.organization, 'onboarding'),
        previews=ImportPreview.objects.filter(organization=member.organization)
        .order_by('-created_at')[:20],
        receipts=ImportSubmissionReceipt.objects.filter(
            organization=member.organization).order_by('-created_at')[:20],
        **values)


def import_submit_page(context, payload):
    """Read-only submission page/history (auditor included); GET only."""
    member = commits.require_commit_reader(context.member)
    error = None
    if set(payload or ()):
        error = '包含不允许的查询参数，请刷新后重试'
    return render(context.request, 'onboarding/import_submit.html', _submit_context(
        member, form=ImportSubmitForm(), result=None, envelope=None, error=error),
        status=400 if error else 200)


def import_submit_commit(context, payload):
    """Owner/finance atomic import commit; POST only; unknown fields rejected."""
    member = commits.require_commit_writer(context.member)
    form = ImportSubmitForm(payload or None)
    result, envelope, error = None, None, None
    if not form.is_valid():
        error = '；'.join(
            message for errors in form.errors.values() for message in errors)
        status = 400
    else:
        data = form.cleaned_data
        try:
            result = commits.commit_import(
                member, preview_ref=data['preview_ref'],
                rows_digest=data['rows_digest'],
                precheck_digest=data['precheck_digest'],
                precheck_as_of_date=data['precheck_as_of_date'],
                period=data['period'].strip(),
                request_token=data['request_token'],
                expected_version=data['expected_version'],
                dry_run=data['dry_run'])
        except commits.ImportCommitRejected as exc:
            envelope, error, status = exc.envelope, '；'.join(exc.messages), 400
        except PermissionDenied:
            raise
        else:
            if result.get('status') == 'committed' and not result.get('replayed'):
                messages.success(
                    context.request,
                    '导入已原子提交；同批次业务事实与收据只写入一次')
                return redirect(PAGE_URL)
            status = 200
    return render(context.request, 'onboarding/import_submit.html', _submit_context(
        member, form=form, result=result, envelope=envelope, error=error),
        status=status)
