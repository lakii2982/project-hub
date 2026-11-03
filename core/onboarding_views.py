"""Q-40 first-run wizard views: company -> customer -> project -> contract -> first time.

The company step is the only unauthenticated page; once the owner account exists every
step requires that owner and resumes from persisted facts.  No view writes demo rows,
ledger entries or sample data.
"""
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import login as auth_login
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import onboarding
from .access import current_membership, require_member
from .forms import BaseForm, DATE, MONEY
from .services import cents

PASSWORD = {"autocomplete": "new-password"}


class CompanySetupForm(BaseForm):
    company_name = forms.CharField(label="公司名称", max_length=100)
    username = forms.CharField(label="负责人登录名", max_length=32,
                               widget=forms.TextInput(attrs={"autocomplete": "username"}),
                               help_text="3至32位字母、数字、下划线、点或短横线")
    password = forms.CharField(label="登录密码", min_length=onboarding.PASSWORD_MIN,
                               widget=forms.PasswordInput(PASSWORD),
                               help_text="至少12位；不会以明文保存或写入日志")
    password_confirm = forms.CharField(label="再次输入密码", widget=forms.PasswordInput(PASSWORD))

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "两次输入的密码不一致")
        return data


class CustomerSetupForm(BaseForm):
    customer_name = forms.CharField(label="客户名称", max_length=120,
                                    help_text="小微版以首个项目的客户名称作为首客户档案")


class FirstProjectForm(BaseForm):
    name = forms.CharField(label="项目名称", max_length=120)
    code = forms.CharField(label="项目编号", max_length=32, required=False,
                           help_text="留空自动生成；仅字母、数字、短横线或下划线")
    start_date = forms.DateField(label="开始日期", widget=forms.DateInput(format="%Y-%m-%d", attrs=DATE))
    end_date = forms.DateField(label="计划交付日期", widget=forms.DateInput(format="%Y-%m-%d", attrs=DATE))

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "计划交付日期不能早于开始日期")
        return data


class FirstContractForm(BaseForm):
    amount = forms.DecimalField(label="合同额（含税，元）", max_digits=14, decimal_places=2,
                                min_value=Decimal("0.01"), widget=forms.NumberInput(MONEY),
                                help_text="整数分保存；合同额不是收入，后续可追加变更")


class FirstTimeForm(BaseForm):
    work_date = forms.DateField(label="工作日期", widget=forms.DateInput(format="%Y-%m-%d", attrs=DATE))
    minutes = forms.IntegerField(label="工时（分钟）", min_value=1, max_value=1440)
    note = forms.CharField(label="工作内容", max_length=300, widget=forms.Textarea)
    hourly_rate = forms.DecimalField(label="负责人小时成本（可选，元/小时）", required=False,
                                     max_digits=12, decimal_places=2, min_value=0,
                                     widget=forms.NumberInput(MONEY),
                                     help_text="留空时首个工时按缺费率缺口显示；已有费率时不会重复新建")


class SetupLoginView(LoginView):
    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["needs_setup"] = onboarding.setup_required()
        return context


def _owner(request):
    member = current_membership(request)
    require_member(member)
    first = onboarding.setup_organization()
    if first is None or first.pk != member.organization_id:
        raise PermissionDenied("首次启用向导仅对首个工作区开放")
    if member.role != "owner":
        raise PermissionDenied("首次启用向导仅公司负责人可操作")
    return member


def _state(request, member):
    return onboarding.onboarding_state(member, session_customer=request.session.get("setup_customer"))


def _step_url(key):
    return reverse({"customer": "setup-customer", "project": "setup-project",
                    "contract": "setup-contract", "time": "setup-time"}.get(key, "setup"))


def _render_step(request, member, state, *, form=None, step=None):
    key = step or onboarding.next_step(state)
    context = {"nav": "setup", "state": state, "step": key, "form": form,
               "today": timezone.localdate(), "setup_url": reverse("setup")}
    return render(request, "onboarding/setup.html", context)


@require_http_methods(["GET", "POST"])
def setup(request):
    if onboarding.setup_required():
        form = CompanySetupForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                member, employee = onboarding.start_company(
                    name=form.cleaned_data["company_name"],
                    username=form.cleaned_data["username"],
                    password=form.cleaned_data["password"],
                    password_confirm=form.cleaned_data["password_confirm"])
            except ValidationError as error:
                form.add_error(None, "；".join(error.messages))
            else:
                auth_login(request, member.user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect("setup")
        return render(request, "onboarding/setup_company.html", {
            "form": form, "today": timezone.localdate(), "currency": onboarding.CURRENCY,
            "timezone": onboarding.TIMEZONE, "password_min": onboarding.PASSWORD_MIN,
        }, status=400 if request.method == "POST" and form.errors else 200)
    if request.method == "POST":
        return render(request, "onboarding/setup_company.html", {
            "form": CompanySetupForm(), "closed": True, "today": timezone.localdate(),
            "currency": onboarding.CURRENCY, "timezone": onboarding.TIMEZONE,
            "password_min": onboarding.PASSWORD_MIN,
        }, status=409)
    if not request.user.is_authenticated:
        return redirect("login")
    member = _owner(request)
    state = _state(request, member)
    if request.GET.get("summary") == "1" or state["complete"]:
        return render(request, "onboarding/setup.html", {
            "nav": "setup", "state": state, "step": "ready", "form": None,
            "today": timezone.localdate(), "setup_url": reverse("setup")})
    return redirect(_step_url(onboarding.next_step(state)))


@require_http_methods(["GET", "POST"])
def setup_customer(request):
    if onboarding.setup_required():
        return redirect("setup")
    member = _owner(request)
    state = _state(request, member)
    if state["project"] is not None:
        return redirect("setup")
    form = CustomerSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            request.session["setup_customer"] = onboarding.create_first_customer(
                member, form.cleaned_data["customer_name"])
        except ValidationError as error:
            form.add_error(None, "；".join(error.messages))
        else:
            return redirect("setup")
    return _render_step(request, member, state, form=form, step="customer")


@require_http_methods(["GET", "POST"])
def setup_project(request):
    if onboarding.setup_required():
        return redirect("setup")
    member = _owner(request)
    state = _state(request, member)
    if state["project"] is not None:
        return redirect("setup")
    customer = request.session.get("setup_customer") or state["customer"]
    if not customer:
        return redirect("setup-customer")
    today = timezone.localdate()
    form = FirstProjectForm(request.POST or None, initial={
        "code": onboarding.generated_project_code(member.organization), "start_date": today,
        "end_date": today + timedelta(days=30)})
    if request.method == "POST" and form.is_valid():
        try:
            onboarding.create_first_project(
                member, name=form.cleaned_data["name"], code=form.cleaned_data["code"],
                customer=customer, start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"])
        except ValidationError as error:
            form.add_error(None, "；".join(error.messages))
        else:
            request.session.pop("setup_customer", None)
            return redirect("setup")
    return _render_step(request, member, state, form=form, step="project")


@require_http_methods(["GET", "POST"])
def setup_contract(request):
    if onboarding.setup_required():
        return redirect("setup")
    member = _owner(request)
    state = _state(request, member)
    project = state["project"]
    if project is None:
        return redirect("setup-project")
    if state["contract_cents"] > 0:
        return redirect("setup")
    form = FirstContractForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            onboarding.record_first_contract(member, project, cents(form.cleaned_data["amount"]),
                                             "首次启用向导录入合同额")
        except ValidationError as error:
            form.add_error(None, "；".join(error.messages))
        else:
            return redirect("setup")
    return _render_step(request, member, state, form=form, step="contract")


@require_http_methods(["GET", "POST"])
def setup_time(request):
    if onboarding.setup_required():
        return redirect("setup")
    member = _owner(request)
    state = _state(request, member)
    project = state["project"]
    if project is None or state["contract_cents"] == 0:
        return redirect("setup")
    if state["time_entry"] is not None:
        return redirect("setup")
    today = timezone.localdate()
    form = FirstTimeForm(request.POST or None, initial={
        "work_date": min(max(today, project.start_date), today), "minutes": 60})
    if request.method == "POST" and form.is_valid():
        try:
            onboarding.record_first_time(
                member, project=project, work_date=form.cleaned_data["work_date"],
                minutes=form.cleaned_data["minutes"], note=form.cleaned_data["note"],
                hourly_rate_cents=(cents(form.cleaned_data["hourly_rate"])
                                   if form.cleaned_data["hourly_rate"] is not None else None))
        except ValidationError as error:
            form.add_error(None, "；".join(error.messages))
        else:
            return redirect("setup")
    return _render_step(request, member, state, form=form, step="time")
