"""Q-36 费用与外包浏览器入口：登记、确认、更正与同一支出下的分次付款。"""
import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.access import current_membership
from core.forms import CATEGORIES

from . import forms as ui
from . import services


def _member(request):
    member = current_membership(request)
    services.readable_projects(member)  # raises PermissionDenied for members/unknown roles
    return member


def _error_text(error):
    if isinstance(error, ValidationError):
        return "；".join(error.messages)
    return "记录冲突或已更新，请刷新并核对后重试"


def _selected_project(request, member):
    raw = request.POST.get("project") if request.method == "POST" else request.GET.get("project")
    if not str(raw or "").isdigit():
        return None
    return services.readable_projects(member).filter(pk=int(raw)).first()


def _yuan(cents):
    return format(Decimal(int(cents)) / Decimal(100), ".2f")


def _page(request, member, *, project=None, expense_form=None, status=200):
    snapshot = services.expense_snapshot(member, project)
    for row in snapshot["rows"]:
        row["amount_yuan"] = _yuan(row["amount_cents"])
        row["payment_token"] = uuid.uuid4()
    return render(request, "expenses/expenses.html", {
        "nav": "expenses",
        "snapshot": snapshot,
        "project": project,
        "projects": services.writable_projects(member).order_by("code"),
        "expense_form": expense_form or ui.ExpenseForm(member=member, project=project),
        "categories": CATEGORIES,
        "can_write": member.role in services.WRITER_ROLES,
        "payment_accounts": services.payment_accounts(member),
        "today": timezone.localdate(),
    }, status=status)


def _back(request):
    project = request.POST.get("project")
    return f"/expenses/?project={int(project)}" if str(project or "").isdigit() else "/expenses/"


@login_required
@require_GET
def expenses(request):
    member = _member(request)
    return _page(request, member, project=_selected_project(request, member))


@login_required
@require_POST
def expense_register(request):
    member = _member(request)
    form = ui.ExpenseForm(request.POST, request.FILES, member=member)
    if not form.is_valid():
        return _page(request, member, status=400, expense_form=form)
    project = form.cleaned_data["project"]
    try:
        services.register_expense(
            member, project=project, category=form.cleaned_data["category"],
            amount_cents=form.amount_cents(), occurred_on=form.cleaned_data["occurred_on"],
            expected_pay_on=form.cleaned_data.get("expected_pay_on"),
            note=form.cleaned_data["note"],
            counterparty=form.cleaned_data.get("counterparty", ""),
            token=form.cleaned_data["submission_token"],
            attachment=form.cleaned_data.get("attachment"))
        messages.success(request, "费用已登记，尚未计入成本；确认后按支出金额计一次成本")
    except (ValidationError, IntegrityError) as error:
        form.add_error(None, _error_text(error))
        return _page(request, member, project=project, expense_form=form, status=400)
    return redirect(f"/expenses/?project={project.pk}")


@login_required
@require_POST
def expense_confirm(request, pk):
    member = _member(request)
    try:
        services.confirm_expense(member, pk)
        messages.success(request, "费用已确认：成本只计一次；未付金额按预计付款日进入 8 周计划")
    except ObjectDoesNotExist:
        raise Http404()
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect(_back(request))


@login_required
@require_POST
def expense_correct(request, pk):
    member = _member(request)
    form = ui.CorrectionForm(request.POST, request.FILES, member=member)
    if not form.is_valid():
        messages.error(request, "没有保存更正：" + "；".join(
            message for errors in form.errors.values() for message in errors))
        return redirect(_back(request))
    try:
        result = services.correct_expense(
            member, pk, category=form.cleaned_data["category"],
            amount_cents=form.amount_cents(), occurred_on=form.cleaned_data["occurred_on"],
            expected_pay_on=form.cleaned_data.get("expected_pay_on"),
            note=form.cleaned_data["note"],
            counterparty=form.cleaned_data.get("counterparty", ""),
            version=form.cleaned_data["version"],
            attachment=form.cleaned_data.get("attachment"))
        messages.success(request, "费用已更正并留下前后对比；已确认支出的成本已按追加冲销重述"
                         if result["changed"] else "费用内容没有变化，未重复记账")
    except ObjectDoesNotExist:
        raise Http404()
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect(_back(request))


@login_required
@require_POST
def expense_payment(request, pk):
    member = _member(request)
    form = ui.PaymentForm(request.POST, member=member)
    if not form.is_valid():
        messages.error(request, "没有登记付款：" + "；".join(
            message for errors in form.errors.values() for message in errors))
        return redirect(_back(request))
    try:
        services.record_payment(
            member, pk, account=form.cleaned_data["account"],
            amount_cents=form.amount_cents(),
            paid_on=form.cleaned_data["paid_on"], note=form.cleaned_data.get("note", ""),
            token=form.cleaned_data["submission_token"])
        messages.success(request, "付款已登记：只计现金、不会重复计成本；未付余额已重算")
    except ObjectDoesNotExist:
        raise Http404()
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect(_back(request))
