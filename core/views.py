import csv
import hashlib
import io
import uuid
from decimal import Decimal
from functools import wraps
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied, ObjectDoesNotExist
from django.db import transaction, IntegrityError, DatabaseError
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse as url_reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import forms as ui
from .access import (current_membership, require_member, visible_projects, can_manage, can_read_finance,
                     require_project, require_write, require_finance_write, WRITE_FINANCE)
from .models import (Project, Membership, Employee, CostRate, ProjectMember, Task, Milestone, Risk,
                     CashPlan, Timesheet, Document, LedgerEntry, Evidence, AuditEvent, AIJob)
from .services import (create_project, submit_time, submit_document, create_task, review as review_record,
                       reverse as reverse_record, transition as change_stage, update_task, accept_milestone,
                       cents, audit, lock_company, check_open, plan_remaining, verify_audit, edit_task)
from .reporting import project_summary, portfolio_metrics, cash_forecast, alerts_for, overdue_plan_totals
from .imports import HEADERS, parse_csv, preview_rows as validate_import, commit_rows
from .ai import extract_draft


def workspace_view(fn):
    @login_required
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        request.member = current_membership(request)
        require_member(request.member)
        return fn(request, *args, **kwargs)
    return wrapped


def scoped_project(member, pk):
    try:
        pk = int(pk)
        if not 1 <= pk <= 2**63 - 1: raise ValueError()
    except (TypeError, ValueError):
        raise Http404()
    return get_object_or_404(visible_projects(member), pk=pk)


def error_text(error):
    return "；".join(error.messages) if isinstance(error, ValidationError) else "记录冲突或已更新，请刷新并核对后重试"


def redact_card(card, member):
    if not can_read_finance(member, card["project"]):
        for key in card:
            if key.endswith("_cents"): card[key] = None
    return card


def scoped_metrics(member, projects):
    all_cards = [redact_card(project_summary(p), member) for p in projects]
    metrics, unused = portfolio_metrics([p for p in projects if can_read_finance(member, p)])
    metrics["active_projects"] = sum(p.stage not in {"closed", "cancelled"} for p in projects)
    return metrics, all_cards


@workspace_view
def dashboard(request):
    projects = list(visible_projects(request.member))
    metrics, cards = scoped_metrics(request.member, projects)
    financial_projects = [p for p in projects if can_read_finance(request.member, p)]
    alerts = alerts_for(financial_projects)
    alerts += alerts_for([p for p in projects if p not in financial_projects], include_finance=False)
    return render(request, "dashboard.html", {"nav": "dashboard", "metrics": metrics, "cards": cards[:8],
        "alerts": alerts[:8], "forecast_overdue": overdue_plan_totals(financial_projects), "cashflow": cash_forecast(financial_projects), "today": timezone.localdate()})


@workspace_view
def projects(request):
    qs = visible_projects(request.member)
    query = request.GET.get("q", "").strip()[:100]
    stage = request.GET.get("stage", "")
    if query: qs = qs.filter(Q(name__icontains=query) | Q(code__icontains=query) | Q(client__icontains=query))
    if stage in Project.Stage.values: qs = qs.filter(stage=stage)
    cards = [redact_card(project_summary(p), request.member) for p in qs]
    return render(request, "projects.html", {"nav": "projects", "cards": cards, "query": query, "stage": stage, "stages": Project.Stage.choices})


@workspace_view
def project_detail(request, pk):
    project = scoped_project(request.member, pk)
    finance_read = can_read_finance(request.member, project)
    docs = project.documents.select_related("submitted_by").prefetch_related("evidence_files").all()
    if not finance_read: docs = docs.filter(submitted_by=request.user, kind="expense")
    plans = list(project.cash_plans.all()) if finance_read else []
    for plan in plans: plan.remaining_cents = plan_remaining(plan)
    display_entries = project.entries.select_related("document", "timesheet")
    if request.member.role not in WRITE_FINANCE: display_entries = display_entries.filter(timesheet__isnull=True)
    return render(request, "project_detail.html", {"nav": "projects", "project": project,
        "summary": redact_card(project_summary(project), request.member),
        "tasks": project.tasks.select_related("owner__user", "dependency"), "milestones": project.milestones.all(),
        "cash_plans": plans, "documents": docs[:40], "entries": display_entries[:60] if finance_read else [],
        "risks": project.risks.select_related("owner__user"), "members": project.team.select_related("member__user"),
        "can_manage": can_manage(request.member, project), "can_finance": finance_read, "stages": Project.Stage.choices})


FORMS = {"project": ui.ProjectForm, "task": ui.TaskForm, "milestone": ui.MilestoneForm, "plan": ui.PlanForm,
         "time": ui.TimeForm, "document": ui.DocumentForm, "risk": ui.RiskForm, "employee": ui.EmployeeForm,
         "rate": ui.RateForm, "team": ui.TeamForm, "member": ui.MemberForm}
HEADINGS = {"project": "新建项目", "task": "新建任务", "milestone": "设置里程碑", "plan": "新建现金计划",
            "time": "登记工时", "document": "提交项目单据", "risk": "记录风险与问题", "employee": "添加员工",
            "rate": "设置成本费率", "team": "添加项目成员", "member": "开通登录账户"}


@transaction.atomic
def create_from_form(member, kind, data, token):
    lock_company(member)
    require_write(member)
    data = data.copy()
    data.pop("submission_token", None)
    if kind == "project":
        budget = cents(data.pop("initial_budget"))
        data["contract_cents"] = cents(data.pop("contract_amount"))
        data["etc_cents"] = cents(data.pop("etc_amount"))
        project = create_project(member, data)
        from .blueprints import apply_blueprint
        apply_blueprint(member, project)
        submit_document(member, {"project": project, "kind": "baseline", "title": "初始预算基线",
            "amount_cents": budget, "occurred_on": timezone.localdate(), "reference": f"{project.code}-B01", "category": "other"}, token)
        return project
    if kind == "time":
        data["minutes"] = int(data.pop("hours") * 60)
        return submit_time(member, data, token)
    if kind == "document":
        data["amount_cents"] = cents(data.pop("amount"))
        data["tax_cents"] = cents(data.pop("tax"))
        return submit_document(member, data, token)
    if kind == "task":
        data["estimate_minutes"] = int(data.pop("estimate_hours") * 60)
        return create_task(member, data)
    project = data.get("project")
    if project:
        project = Project.objects.select_for_update().get(pk=project.pk)
        data["project"] = project
        require_project(member, project)
        check_open(project)
    if kind == "plan":
        require_finance_write(member, project)
        if data.get("milestone") and data["milestone"].project_id != project.id: raise ValidationError("里程碑不属于此项目")
        data["amount_cents"] = cents(data.pop("amount"))
        existing = CashPlan.objects.filter(token=token).first()
        if existing:
            if existing.project_id != project.pk or existing.created_by_id != member.user_id or any(getattr(existing, key) != value for key, value in data.items()):
                raise ValidationError("提交标识已使用或同一来源内容发生变化，请刷新后核对")
            return existing
        obj = CashPlan(**data, created_by=member.user, token=token)
    elif kind in {"milestone", "risk", "team"}:
        require_project(member, project, manage=True)
        if kind in {"risk", "team"}:
            candidate = data.get("owner") or data.get("member")
            if candidate.organization_id != member.organization_id or not candidate.active: raise ValidationError("成员不属于当前公司")
        obj = {"milestone": Milestone, "risk": Risk, "team": ProjectMember}[kind](**data)
    elif kind in {"employee", "rate"}:
        if member.role not in WRITE_FINANCE: raise PermissionDenied("员工与成本费率只允许负责人和项目财务管理")
        if kind == "employee":
            data["capacity_minutes"] = int(data.pop("capacity_hours") * 60)
            if data.get("member") and data["member"].organization_id != member.organization_id: raise ValidationError("员工账户公司不一致")
            obj = Employee(**data, organization=member.organization)
        else:
            data["hourly_cents"] = cents(data.pop("hourly_amount"))
            if data["employee"].organization_id != member.organization_id: raise ValidationError("员工公司不一致")
            obj = CostRate(**data)
    elif kind == "member":
        if member.role != "owner": raise PermissionDenied("只有公司负责人可以开通账户")
        user = get_user_model().objects.create_user(username=data["username"], password=data["password"], first_name=data["name"])
        obj = Membership.objects.create(organization=member.organization, user=user, role=data["role"])
        Employee.objects.create(organization=member.organization, name=data["name"], member=obj)
        audit(member, "account.created", obj, {"role": obj.role, "username": user.username})
        return obj
    else: raise Http404()
    obj.full_clean()
    obj.save()
    audit(member, f"{kind}.created", obj, {"project_id": project.id if project else None})
    return obj


@workspace_view
def new_object(request, kind):
    if kind not in FORMS: raise Http404()
    require_write(request.member)
    if kind in {"employee", "rate"} and request.member.role not in WRITE_FINANCE: raise PermissionDenied()
    if kind == "member" and request.member.role != "owner": raise PermissionDenied()
    if kind == "project" and request.member.role not in {"owner", "pm"}: raise PermissionDenied()
    project = scoped_project(request.member, request.GET["project"]) if request.GET.get("project") else None
    if project and kind in {"task", "milestone", "risk", "team"}: require_project(request.member, project, manage=True)
    initial = {}
    if kind == "document" and request.GET.get("kind") in Document.Kind.values: initial["kind"] = request.GET["kind"]
    form = FORMS[kind](request.POST or None, member=request.member, project=project, initial=initial)
    form.fields["submission_token"] = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    if request.method == "POST" and form.is_valid():
        try:
            obj = create_from_form(request.member, kind, form.cleaned_data, form.cleaned_data["submission_token"])
            messages.success(request, "已提交；工时、单据和现金计划须经另一位负责人审批后生效" if kind in {"project", "time", "document", "plan"} else "已保存")
            if kind in {"employee", "rate", "member"}: return redirect("settings")
            obj_project = obj if kind == "project" else getattr(obj, "project", project)
            return redirect("project-detail", pk=obj_project.pk)
        except (ValidationError, IntegrityError) as error: form.add_error(None, error_text(error))
    back = url_reverse("project-detail", args=[project.pk]) if project else url_reverse("settings" if kind in {"employee", "rate", "member"} else "projects")
    return render(request, "form.html", {"nav": "projects", "heading": HEADINGS[kind], "lead": "填写业务事实，系统会保留来源、责任人和变更记录。", "form": form, "back_url": back})


@workspace_view
def project_edit(request, pk):
    project = scoped_project(request.member, pk)
    require_write(request.member)
    require_project(request.member, project, manage=True)
    check_open(project)
    initial = {"version": project.version, "scope": project.scope, "acceptance_criteria": project.acceptance_criteria,
               "end_date": project.end_date, "contract_reference": project.contract_reference, "etc_amount": Decimal(project.etc_cents) / 100}
    form = ui.ProjectEditForm(request.POST or None, initial=initial, member=request.member, project=project)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                lock_company(request.member)
                current = Project.objects.select_for_update().get(pk=project.pk)
                if current.version != form.cleaned_data["version"]: raise ValidationError("项目已经更新，请刷新后重试")
                before = {k: str(getattr(current, k)) for k in ["scope", "acceptance_criteria", "end_date", "contract_reference", "etc_cents"]}
                for key in ["scope", "acceptance_criteria", "end_date", "contract_reference"]: setattr(current, key, form.cleaned_data[key])
                current.etc_cents = cents(form.cleaned_data["etc_amount"])
                current.version += 1
                current.full_clean(); current.save()
                audit(request.member, "project.updated", current, {"before": before, "etc_cents": current.etc_cents, "reason": form.cleaned_data["reason"]})
            messages.success(request, "项目信息与完工预测已更新")
            return redirect("project-detail", pk=pk)
        except (ValidationError, IntegrityError) as error: form.add_error(None, error_text(error))
    return render(request, "form.html", {"heading": "更新项目与剩余成本估算", "form": form, "back_url": url_reverse("project-detail", args=[pk])})


@workspace_view
def approvals(request):
    if request.member.role not in {"owner", "finance", "pm"}: raise PermissionDenied()
    projects_qs = visible_projects(request.member)
    if request.member.role == "pm": projects_qs = projects_qs.filter(manager=request.member)
    pending_times = Timesheet.objects.filter(project__in=projects_qs, status="submitted").select_related("project", "employee", "submitted_by")
    can_money = request.member.role in WRITE_FINANCE
    pending_docs = Document.objects.filter(project__in=projects_qs, status="submitted").select_related("project", "submitted_by").prefetch_related("evidence_files") if can_money else []
    pending_plans = CashPlan.objects.filter(project__in=projects_qs, approved=False, rejected=False, active=True).select_related("project", "created_by") if can_money else []
    return render(request, "approvals.html", {"nav": "approvals", "pending_times": pending_times[:200], "pending_documents": pending_docs[:200], "pending_plans": pending_plans[:200]})


@workspace_view
@require_POST
def review(request, kind, pk):
    model = {"time": Timesheet, "document": Document, "plan": CashPlan}.get(kind)
    if model is None: raise Http404()
    get_object_or_404(model, pk=pk, project__in=visible_projects(request.member))
    try:
        review_record(request.member, kind, pk, request.POST.get("decision"), request.POST.get("note", ""))
        messages.success(request, "审批已处理，项目账与预测已同步更新")
    except ObjectDoesNotExist: raise Http404()
    except (ValidationError, IntegrityError) as error: messages.error(request, error_text(error))
    return redirect("approvals")


@workspace_view
@require_POST
def reverse(request, kind, pk):
    model = {"time": Timesheet, "document": Document}.get(kind)
    if model is None: raise Http404()
    get_object_or_404(model, pk=pk, project__in=visible_projects(request.member))
    try:
        row = reverse_record(request.member, kind, pk, request.POST.get("note", ""))
        messages.success(request, "已追加冲销记录，原记录完整保留")
        return redirect("project-detail", pk=row.project_id)
    except ObjectDoesNotExist: raise Http404()
    except (ValidationError, IntegrityError) as error: messages.error(request, error_text(error))
    return redirect("finance")


@workspace_view
@require_POST
def task_update(request, pk):
    task = get_object_or_404(Task, pk=pk, project__in=visible_projects(request.member))
    try:
        update_task(request.member, pk, request.POST.get("status"), int(request.POST.get("version", "0")), request.POST.get("completion_note", ""))
        messages.success(request, "任务状态已更新")
    except (ValueError, ValidationError, IntegrityError) as error: messages.error(request, error_text(error))
    return redirect("project-detail", pk=task.project_id)


@workspace_view
@require_POST
def transition(request, pk):
    project = scoped_project(request.member, pk)
    try:
        change_stage(request.member, pk, request.POST.get("target"), int(request.POST.get("version", "0")), request.POST.get("reason", ""))
        messages.success(request, "项目阶段已更新")
    except (ValueError, ValidationError, IntegrityError) as error: messages.error(request, error_text(error))
    return redirect("project-detail", pk=project.pk)


@workspace_view
@require_POST
def milestone_accept(request, pk):
    milestone = get_object_or_404(Milestone, pk=pk, project__in=visible_projects(request.member))
    try:
        accept_milestone(request.member, pk, request.POST.get("evidence", ""))
        messages.success(request, "里程碑已验收")
    except ValidationError as error: messages.error(request, error_text(error))
    return redirect("project-detail", pk=milestone.project_id)


@workspace_view
@require_POST
def risk_resolve(request, pk):
    with transaction.atomic():
        lock_company(request.member)
        risk = get_object_or_404(Risk, pk=pk, project__in=visible_projects(request.member))
        require_write(request.member)
        if not can_manage(request.member, risk.project) and risk.owner_id != request.member.id: raise PermissionDenied()
        text = request.POST.get("resolution", "").strip()
        if len(text) < 4: messages.error(request, "请填写处理结果和依据")
        else:
            risk.resolved, risk.resolution = True, text[:4000]
            risk.save(update_fields=["resolved", "resolution"])
            audit(request.member, "risk.resolved", risk, {"resolution": text[:4000]})
            messages.success(request, "风险已关闭")
    return redirect("project-detail", pk=risk.project_id)


@workspace_view
@require_POST
def cancel_plan(request, pk):
    with transaction.atomic():
        lock_company(request.member)
        plan = get_object_or_404(CashPlan, pk=pk, project__in=visible_projects(request.member))
        if request.member.role not in WRITE_FINANCE: raise PermissionDenied()
        note = request.POST.get("note", "").strip()
        if plan.created_by_id == request.user.id: messages.error(request, "计划终止须由另一位负责人确认")
        elif len(note) < 4: messages.error(request, "请填写终止未结部分的业务依据")
        elif plan.active:
            plan.active, plan.review_note = False, note[:300]
            plan.save(update_fields=["active", "review_note"])
            audit(request.member, "plan.cancelled", plan, {"reason": note, "remaining_cents": plan_remaining(plan)})
            messages.success(request, "计划未结部分已终止，实际收付款记录保留")
    return redirect("project-detail", pk=plan.project_id)


@workspace_view
def finance(request):
    if not can_read_finance(request.member): raise PermissionDenied()
    qs = visible_projects(request.member)
    if request.member.role == "pm": qs = qs.filter(manager=request.member)
    selected = request.GET.get("project", "")
    if selected:
        if not selected.isdigit() or len(selected) > 18: raise Http404()
        project = get_object_or_404(qs, pk=selected)
        qs = qs.filter(pk=project.pk)
    project_list = list(qs)
    metrics, cards = portfolio_metrics(project_list)
    plans = list(CashPlan.objects.filter(project__in=qs).select_related("project"))
    for plan in plans: plan.remaining_cents = plan_remaining(plan)
    display_entries = LedgerEntry.objects.filter(project__in=qs).select_related("project", "document", "timesheet").order_by("-id")
    if request.member.role not in WRITE_FINANCE: display_entries = display_entries.filter(timesheet__isnull=True)
    return render(request, "finance.html", {"nav": "finance", "metrics": metrics, "forecast_overdue": overdue_plan_totals(project_list), "cashflow": cash_forecast(project_list),
        "documents": Document.objects.filter(project__in=qs).select_related("project", "submitted_by").prefetch_related("evidence_files")[:200],
        "entries": display_entries[:200],
        "plans": plans, "projects": visible_projects(request.member), "selected_project": selected})


@workspace_view
def timesheets(request):
    rows = Timesheet.objects.filter(project__in=visible_projects(request.member)).select_related("project", "employee", "task", "submitted_by")
    if request.member.role == "member": rows = rows.filter(employee__member=request.member)
    elif request.member.role == "pm": rows = rows.filter(project__manager=request.member)
    totals = rows.filter(status="approved").aggregate(minutes=Sum("minutes"), cost=Sum("cost_cents"))
    return render(request, "time.html", {"nav": "time", "timesheets": rows[:300], "employees": Employee.objects.filter(organization=request.member.organization),
        "total_minutes": totals["minutes"] or 0, "total_cost_cents": (totals["cost"] or 0) if request.member.role != "member" else None})


@workspace_view
def assistant(request):
    qs = list(visible_projects(request.member))
    financial = [p for p in qs if can_read_finance(request.member, p)]
    alerts = alerts_for(financial) + alerts_for([p for p in qs if p not in financial], include_finance=False)
    form = ui.AITextForm(request.POST or None, member=request.member)
    candidate = None
    if request.method == "POST" and form.is_valid():
        require_write(request.member)
        if not can_read_finance(request.member): raise PermissionDenied()
        try: candidate = extract_draft(request.member, form.cleaned_data["text"], financial)
        except ValidationError as error: form.add_error(None, error_text(error))
    jobs = AIJob.objects.filter(organization=request.member.organization, requested_by=request.user).order_by("-id")[:10]
    return render(request, "assistant.html", {"nav": "assistant", "alerts": alerts, "provider_enabled": settings.AI_ENABLED and bool(settings.AI_BASE_URL and settings.AI_API_KEY),
        "jobs": jobs, "form": form, "candidate": candidate})


@workspace_view
def settings_page(request):
    if request.member.role not in WRITE_FINANCE: raise PermissionDenied()
    org = request.member.organization
    form = ui.OrgForm(request.POST or None, instance=org)
    if request.method == "POST":
        if request.member.role != "owner": raise PermissionDenied()
        if form.is_valid():
            with transaction.atomic():
                locked = lock_company(request.member)
                # Rebind against the locked row to catch a concurrent period close.
                form = ui.OrgForm(request.POST, instance=locked)
                if form.is_valid():
                    changed = form.save()
                    audit(request.member, "organization.updated", changed, {"name": changed.name, "locked_through": changed.locked_through})
                    messages.success(request, "公司设置与锁账日期已更新")
                    return redirect("settings")
    return render(request, "settings.html", {"nav": "settings", "form": form,
        "employees": Employee.objects.filter(organization=org).select_related("member__user"),
        "memberships": Membership.objects.filter(organization=org).select_related("user"),
        "rates": CostRate.objects.filter(employee__organization=org).select_related("employee")[:200]})


@workspace_view
def audit_page(request):
    if request.member.role not in {"owner", "finance", "auditor"}: raise PermissionDenied()
    return render(request, "audit.html", {"nav": "audit", "events": AuditEvent.objects.filter(organization=request.member.organization).select_related("actor")[:300], "chain_ok": verify_audit(request.member.organization)})


@workspace_view
def import_data(request, kind):
    if kind not in HEADERS: raise Http404()
    require_write(request.member)
    if kind == "plan" and request.member.role not in {"owner", "finance", "pm"}: raise PermissionDenied()
    form = ui.ImportForm(request.POST or None, request.FILES or None, member=request.member)
    preview = None
    count = 0
    key = f"import_{kind}"
    if request.method == "POST" and request.POST.get("action") == "commit":
        cached = request.session.get(key)
        if not cached or cached.get("user_id") != request.user.pk:
            messages.error(request, "导入预览已过期，请重新上传校验")
        else:
            try:
                objects = commit_rows(request.member, kind, cached["rows"])
                del request.session[key]
                messages.success(request, f"已处理{len(objects)}行，全部提交待审批；相同来源编号不会重复入账")
                return redirect("timesheets" if kind == "time" else "finance")
            except (ValidationError, IntegrityError) as error: messages.error(request, error_text(error))
    elif request.method == "POST" and form.is_valid():
        try:
            rows = parse_csv(kind, form.cleaned_data["file"])
            count = validate_import(request.member, kind, rows)
            request.session[key] = {"user_id": request.user.pk, "rows": rows}
            preview = rows[:20]
        except (ValidationError, IntegrityError) as error:
            request.session.pop(key, None)
            form.add_error(None, error_text(error))
    return render(request, "import.html", {"nav": "time" if kind == "time" else "finance", "heading": {"time": "批量导入工时", "expense": "批量导入费用", "plan": "批量导入现金计划"}[kind],
        "kind": kind, "form": form, "headers": HEADERS[kind], "preview_rows": preview, "preview_count": count})


def csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")): return "'" + value
    return value


@workspace_view
def export_data(request, kind):
    projects_qs = visible_projects(request.member)
    if kind in {"ledger", "plans", "time"}:
        if not can_read_finance(request.member): raise PermissionDenied()
        if request.member.role == "pm": projects_qs = projects_qs.filter(manager=request.member)
    selected = request.GET.get("project", "")
    if selected:
        if not selected.isdigit() or len(selected) > 18: raise Http404()
        selected_project = get_object_or_404(projects_qs, pk=selected)
        projects_qs = projects_qs.filter(pk=selected_project.pk)
    rows = []
    if kind == "ledger":
        if request.member.role not in WRITE_FINANCE: raise PermissionDenied("项目账含个人核算信息，完整明细仅向项目财务与公司负责人开放")
        rows.append(["项目编号", "项目名称", "业务日期", "账本", "分类", "金额_分", "来源单据", "来源工时", "冲销原记录"])
        for e in LedgerEntry.objects.filter(project__in=projects_qs).select_related("project"):
            rows.append([e.project.code, e.project.name, e.occurred_on, e.get_book_display(), e.category, e.amount_cents, e.document_id, e.timesheet_id, e.reverses_id])
    elif kind == "projects":
        rows.append(["项目编号", "项目名称", "客户", "阶段", "负责人", "开始日", "交付日"])
        for p in projects_qs: rows.append([p.code, p.name, p.client, p.get_stage_display(), str(p.manager), p.start_date, p.end_date])
    elif kind == "time":
        personal_finance = request.member.role in WRITE_FINANCE
        rows.append(["项目编号", "员工", "日期", "分钟"] + (["成本_分", "费率_分每小时"] if personal_finance else []) + ["状态", "说明"])
        for t in Timesheet.objects.filter(project__in=projects_qs).select_related("project", "employee"):
            rows.append([t.project.code, t.employee.name, t.work_date, t.minutes] + ([t.cost_cents, t.hourly_rate_cents] if personal_finance else []) + [t.get_status_display(), t.note])
    elif kind == "plans":
        rows.append(["项目编号", "方向", "事项", "交易对方", "到期日", "计划_分", "未结_分", "已批准", "有效"])
        for p in CashPlan.objects.filter(project__in=projects_qs).select_related("project"):
            rows.append([p.project.code, p.get_direction_display(), p.title, p.counterparty, p.due_date, p.amount_cents, plan_remaining(p), p.approved, p.active])
    else: raise Http404()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerows([[csv_safe(x) for x in row] for row in rows])
    audit(request.member, "data.exported", request.member.organization, {"kind": kind, "rows": len(rows) - 1})
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="project-hub-{kind}-{timezone.localdate()}.csv"'
    return response


@workspace_view
@require_POST
def upload_evidence(request, pk):
    doc = get_object_or_404(Document, pk=pk, project__in=visible_projects(request.member))
    require_write(request.member)
    if not can_read_finance(request.member, doc.project) and doc.submitted_by_id != request.user.id: raise PermissionDenied()
    uploaded = request.FILES.get("file")
    suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".csv", ".txt", ".xlsx", ".docx"}
    if not uploaded or uploaded.size > 10 * 1024 * 1024 or Path(uploaded.name).suffix.lower() not in suffixes:
        messages.error(request, "请上传10MB以内的PDF、图片、文本或Office凭证")
    else:
        digest = hashlib.sha256()
        for chunk in uploaded.chunks(): digest.update(chunk)
        uploaded.seek(0)
        with transaction.atomic():
            lock_company(request.member)
            evidence = Evidence(document=doc, original_name=Path(uploaded.name).name[:180], sha256=digest.hexdigest(), uploaded_by=request.user)
            evidence.file.save(uploaded.name, uploaded, save=False)
            try:
                evidence.save()
                audit(request.member, "evidence.added", evidence, {"document_id": doc.pk, "sha256": evidence.sha256})
            except Exception:
                evidence.file.delete(save=False)
                raise
        messages.success(request, "凭证已保存并记录校验摘要")
    return redirect("project-detail", pk=doc.project_id)


@workspace_view
def download_evidence(request, pk):
    evidence = get_object_or_404(Evidence.objects.select_related("document__project"), pk=pk, document__project__in=visible_projects(request.member))
    if not can_read_finance(request.member, evidence.document.project) and evidence.document.submitted_by_id != request.user.pk: raise PermissionDenied()
    try: response = FileResponse(evidence.file.open("rb"), as_attachment=True, filename=evidence.original_name, content_type="application/octet-stream")
    except FileNotFoundError: raise Http404("凭证文件不存在，请核对备份")
    audit(request.member, "evidence.downloaded", evidence, {"sha256": evidence.sha256})
    return response


def health(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor: cursor.execute("SELECT 1")
        from pmo import __version__
        return JsonResponse({"status": "ok", "version": __version__})
    except DatabaseError: return JsonResponse({"status": "unavailable"}, status=503)


@workspace_view
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk, project__in=visible_projects(request.member))
    require_project(request.member, task.project, manage=True)
    require_write(request.member)
    initial = {key: getattr(task, key) for key in ["project", "name", "owner", "start_date", "due_date", "dependency", "description", "version"]}
    initial["estimate_hours"] = Decimal(task.estimate_minutes) / 60
    form = ui.TaskEditForm(request.POST or None, member=request.member, project=task.project, initial=initial)
    form.fields["dependency"].queryset = form.fields["dependency"].queryset.exclude(pk=task.pk)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        version, reason = data.pop("version"), data.pop("reason")
        data["estimate_minutes"] = int(data.pop("estimate_hours") * 60)
        try:
            edit_task(request.member, task.pk, data, version, reason)
            messages.success(request, "任务计划已更新，原值和调整依据已保留")
            return redirect("project-detail", pk=task.project_id)
        except (ValidationError, IntegrityError) as error: form.add_error(None, error_text(error))
    return render(request, "form.html", {"heading": "调整任务计划", "form": form, "back_url": url_reverse("project-detail", args=[task.project_id])})

@workspace_view
def agent_connections(request):
    from .models import AgentToken
    from .agent_auth import issue_token
    form = ui.AgentTokenForm(request.POST or None, member=request.member, request=request)
    raw_token = None
    if request.method == "POST" and form.is_valid():
        try:
            token, raw_token = issue_token(request.member, form.cleaned_data["name"], form.cleaned_data["scopes"],
                                          [p.pk for p in form.cleaned_data["projects"]], form.cleaned_data["days"])
            messages.success(request, "接入已创建，请保存本次显示的令牌。可以随时撤销。")
        except ValidationError as error: form.add_error(None, error_text(error))
    from .agent_auth import SCOPE_CHOICES
    scope_names = dict(SCOPE_CHOICES)
    project_names = {p.pk: str(p) for p in visible_projects(request.member)}
    tokens = list(AgentToken.objects.filter(member=request.member).order_by("-id")[:30])
    for item in tokens:
        item.scope_labels = [scope_names.get(scope, scope) for scope in item.scopes]
        item.project_labels = [project_names.get(pk, "项目权限已变更") for pk in item.project_ids]
        item.state_label = "已撤销" if item.revoked_at else "已过期" if item.expires_at <= timezone.now() else "有效"
    response = render(request, "connections.html", {"nav": "connections", "form": form, "raw_token": raw_token,
        "tokens": tokens})
    response["Cache-Control"] = "no-store"
    return response

@workspace_view
@require_POST
def agent_token_revoke(request, pk):
    from .agent_auth import revoke_token
    try:
        revoke_token(request.member, pk)
        messages.success(request, "接入已撤销，后续调用立即失效")
    except ObjectDoesNotExist: raise Http404()
    return redirect("agent-connections")
