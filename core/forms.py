from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from .access import visible_projects, can_manage
from .models import (Project, Membership, Employee, CostRate, Task, Milestone, CashPlan,
                     Document, Risk, Organization, NonProjectDestination)
from .services import cents

DATE = {"type": "date"}
MONEY = {"min": "0", "step": "0.01", "inputmode": "decimal"}

class BaseForm(forms.Form):
    def __init__(self, *args, member=None, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = member
        self.project_context = project
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            if isinstance(field.widget, forms.Textarea): field.widget.attrs["rows"] = 3

class ProjectForm(BaseForm):
    code = forms.RegexField(r"^[A-Za-z0-9_-]{2,32}$", label="项目编号", max_length=32, help_text="字母、数字、短横线或下划线，建立后保留")
    name = forms.CharField(label="项目名称", max_length=120)
    client = forms.CharField(label="客户", max_length=120)
    manager = forms.ModelChoiceField(queryset=Membership.objects.none(), label="项目负责人")
    template = forms.ChoiceField(choices=Project.Template.choices, label="项目类型")
    start_date = forms.DateField(label="开始日期", widget=forms.DateInput(attrs=DATE))
    end_date = forms.DateField(label="计划交付日期", widget=forms.DateInput(attrs=DATE))
    contract_amount = forms.DecimalField(label="立项预估合同额（含税，元）", max_digits=14, decimal_places=2, min_value=0, widget=forms.NumberInput(attrs=MONEY), help_text="仅保留立项时预估。正式合同客户与价款统一在客户与合同维护，不回填此历史值。")
    initial_budget = forms.DecimalField(label="拟定成本预算（元）", max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs=MONEY), help_text="建立后自动提交预算基线，由另一位负责人审批")
    etc_amount = forms.DecimalField(label="预计剩余成本（元）", max_digits=14, decimal_places=2, min_value=0, widget=forms.NumberInput(attrs=MONEY), help_text="用于计算完工成本；可随项目进展更新")
    scope = forms.CharField(label="范围与交付物", widget=forms.Textarea, max_length=8000)
    acceptance_criteria = forms.CharField(label="验收标准", widget=forms.Textarea, max_length=8000)
    contract_reference = forms.CharField(label="合同或立项依据编号", max_length=240)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Membership.objects.filter(organization=self.member.organization, active=True, role__in=["owner", "pm"]).select_related("user")
        self.fields["manager"].queryset = qs if self.member.role == "owner" else qs.filter(pk=self.member.pk)
    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "交付日期不能早于开始日期")
        if Project.objects.filter(organization=self.member.organization, code=data.get("code")).exists():
            self.add_error("code", "此项目编号已经存在")
        return data

class ProjectScopedForm(BaseForm):
    project = forms.ModelChoiceField(queryset=Project.objects.none(), label="所属项目")
    management_only = False
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = visible_projects(self.member)
        if self.management_only and self.member.role not in {"owner", "finance"}:
            qs = qs.filter(manager=self.member)
        if self.project_context:
            qs = qs.filter(pk=self.project_context.pk)
            self.fields["project"].initial = self.project_context
        self.fields["project"].queryset = qs.exclude(stage__in=["closed", "cancelled"])

class TaskForm(ProjectScopedForm):
    management_only = True
    name = forms.CharField(label="关键任务名称", max_length=160)
    milestone = forms.ModelChoiceField(
        queryset=Milestone.objects.none(), label="里程碑（交付节点）", required=False,
        help_text="选择本项目的交付里程碑；里程碑与收付款计划至少选择一项",
    )
    cash_plan = forms.ModelChoiceField(
        queryset=CashPlan.objects.none(), label="收付款计划（收付款节点）", required=False,
        help_text="选择本项目已启用的收付款节点；仅建立关联，不会自动审批或记账",
    )
    owner = forms.ModelChoiceField(queryset=Membership.objects.none(), label="负责人")
    start_date = forms.DateField(label="开始日期", widget=forms.DateInput(attrs=DATE))
    due_date = forms.DateField(label="计划完成日期", widget=forms.DateInput(attrs=DATE))
    dependency = forms.ModelChoiceField(queryset=Task.objects.none(), label="前置关键任务", required=False)
    description = forms.CharField(label="交付要求", widget=forms.Textarea, required=False, max_length=8000)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["milestone"].label_from_instance = lambda obj: f"{obj.name} · {obj.due_date}"
        self.fields["cash_plan"].label_from_instance = lambda obj: f"{obj.get_direction_display()} · {obj.title} · {obj.due_date}"
        self.fields["dependency"].label_from_instance = lambda obj: f"{obj.name} · {obj.due_date}"
        projects = self.fields["project"].queryset
        selected_project = None
        if self.project_context:
            selected_project = projects.filter(pk=self.project_context.pk).first()
        else:
            project_value = self.data.get(self.add_prefix("project")) if self.is_bound else None
            if project_value is None:
                project_value = self.initial.get("project")
            if project_value:
                try:
                    selected_project = projects.filter(pk=getattr(project_value, "pk", project_value)).first()
                except (TypeError, ValueError):
                    pass
        if selected_project:
            projects = projects.filter(pk=selected_project.pk)
        self.fields["owner"].queryset = Membership.objects.filter(organization=self.member.organization, active=True).select_related("user")
        self.fields["dependency"].queryset = Task.objects.filter(project__in=projects)
        self.fields["milestone"].queryset = Milestone.objects.filter(project__in=projects)
        self.fields["cash_plan"].queryset = CashPlan.objects.filter(project__in=projects, active=True)
    def clean(self):
        data = super().clean()
        project = data.get("project")
        milestone = data.get("milestone")
        cash_plan = data.get("cash_plan")
        if not milestone and not cash_plan:
            self.add_error("milestone", "关键任务至少关联一个里程碑或收付款计划")
        if project and milestone and milestone.project_id != project.pk:
            self.add_error("milestone", "里程碑必须属于所选项目")
        if project and cash_plan and cash_plan.project_id != project.pk:
            self.add_error("cash_plan", "收付款计划必须属于所选项目")
        if milestone and cash_plan and cash_plan.milestone_id and cash_plan.milestone_id != milestone.pk:
            self.add_error("cash_plan", "收付款计划已关联其他里程碑，请选择一致的里程碑")
        return data

class MilestoneForm(ProjectScopedForm):
    management_only = True
    name = forms.CharField(label="里程碑", max_length=120)
    due_date = forms.DateField(label="计划验收日", widget=forms.DateInput(attrs=DATE))
    criteria = forms.CharField(label="验收条件", widget=forms.Textarea, max_length=8000)

class PlanForm(ProjectScopedForm):
    management_only = True
    direction = forms.ChoiceField(label="方向", choices=CashPlan.Direction.choices)
    title = forms.CharField(label="收付款计划", max_length=140)
    counterparty = forms.CharField(label="交易对方", max_length=120)
    amount = forms.DecimalField(label="金额（元）", max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs=MONEY))
    due_date = forms.DateField(label="预计收付日期", widget=forms.DateInput(attrs=DATE))
    milestone = forms.ModelChoiceField(queryset=Milestone.objects.none(), label="关联里程碑", required=False)
    reference = forms.CharField(label="合同／采购／支付依据编号", max_length=240, help_text="计划不会增加实际成本或现金，审批通过后进入13周现金预测")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["milestone"].queryset = Milestone.objects.filter(project__in=self.fields["project"].queryset)

class TimeForm(ProjectScopedForm):
    non_project_destination = forms.ModelChoiceField(queryset=NonProjectDestination.objects.none(), label="公司非项目投入", required=False)
    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), label="员工")
    work_date = forms.DateField(label="工作日期", widget=forms.DateInput(attrs=DATE), initial=timezone.localdate)
    hours = forms.DecimalField(label="工时（小时）", max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=24,
                               help_text="最小单位为分钟。审批后按当日有效的综合成本费率自动核算")
    note = forms.CharField(label="工作内容", max_length=300, widget=forms.Textarea)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Employee.objects.filter(organization=self.member.organization, active=True)
        if self.member.role == "member": qs = qs.filter(member=self.member)
        self.fields["employee"].queryset = qs
        self.fields["project"].required = False
        self.fields["non_project_destination"].queryset = NonProjectDestination.objects.filter(organization=self.member.organization, code="ADMIN", active=True)
        self.fields["hours"].help_text = "最小单位为分钟；月度口径审批只确认投入，工资池分配形成成本。"
    def clean(self):
        data = super().clean()
        if bool(data.get("project")) == bool(data.get("non_project_destination")):
            raise ValidationError("项目与公司非项目投入请选择一项")
        return data
    def clean_hours(self):
        hours = self.cleaned_data["hours"]
        if hours * 60 != int(hours * 60):
            raise ValidationError("工时须能精确换算为整数分钟，例如0.25、0.5或1小时")
        return hours

CATEGORIES = [("vendor", "外包与采购"), ("travel", "差旅"), ("software", "软件与云资源"),
              ("equipment", "设备"), ("labor_cash", "项目人工付款"), ("overhead", "分摊费用"), ("other", "其他")]
class DocumentForm(ProjectScopedForm):
    kind = forms.ChoiceField(label="单据类型", choices=Document.Kind.choices)
    title = forms.CharField(label="事项名称", max_length=160)
    category = forms.ChoiceField(label="分类", choices=CATEGORIES)
    amount = forms.DecimalField(label="含税金额／新预算总额（元）", max_digits=14, decimal_places=2, min_value=0, widget=forms.NumberInput(attrs=MONEY), help_text="预算变更填写调整后的总额；验收记录可填0")
    tax = forms.DecimalField(label="其中税额（元）", max_digits=14, decimal_places=2, min_value=0, initial=0, widget=forms.NumberInput(attrs=MONEY))
    recoverable_tax = forms.BooleanField(label="该成本税额可抵扣", required=False, help_text="以业务确认结果为准；勾选时从项目费用中扣除税额")
    occurred_on = forms.DateField(label="实际发生日期", initial=timezone.localdate, widget=forms.DateInput(attrs=DATE))
    counterparty = forms.CharField(label="交易对方", max_length=120, required=False)
    reference = forms.CharField(label="凭证／流水／合同／验收编号", max_length=240, help_text="保留原始来源编号；同笔分摊用编号加明细后缀，避免重复核算")
    plan = forms.ModelChoiceField(queryset=CashPlan.objects.none(), label="核销现金计划", required=False, help_text="仅实际收款／付款可选；费用另行记录，不会自动变成付款")
    note = forms.CharField(label="业务说明／变更依据", required=False, widget=forms.Textarea, max_length=8000)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.member.role == "member": self.fields["kind"].choices = [("expense", "成本费用")]
        self.fields["plan"].queryset = CashPlan.objects.filter(project__in=self.fields["project"].queryset, approved=True, active=True)
    def clean(self):
        data = super().clean()
        if data.get("tax", 0) > data.get("amount", 0): self.add_error("tax", "税额不能超过含税金额")
        if data.get("kind") != "expense" and (data.get("tax", 0) or data.get("recoverable_tax")):
            self.add_error("tax", "只有成本费用使用税额字段")
        if data.get("kind") == "change" and not data.get("note"):
            self.add_error("note", "预算变更须说明新总额和变更依据")
        return data

class RiskForm(ProjectScopedForm):
    management_only = True
    title = forms.CharField(label="风险／问题", max_length=160)
    severity = forms.ChoiceField(label="影响级别", choices=[("high", "高"), ("medium", "中"), ("low", "低")])
    owner = forms.ModelChoiceField(queryset=Membership.objects.none(), label="处理负责人")
    due_date = forms.DateField(label="处理期限", widget=forms.DateInput(attrs=DATE))
    mitigation = forms.CharField(label="应对措施", widget=forms.Textarea, max_length=8000)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = Membership.objects.filter(organization=self.member.organization, active=True).select_related("user")

class EmployeeForm(BaseForm):
    name = forms.CharField(label="姓名", max_length=60)
    title = forms.CharField(label="岗位", max_length=80, required=False)
    member = forms.ModelChoiceField(queryset=Membership.objects.none(), label="登录账户", required=False)
    capacity_hours = forms.DecimalField(label="每日标准可用工时（小时）", max_digits=4, decimal_places=2, initial=8, min_value=1, max_value=24)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = Membership.objects.filter(organization=self.member.organization, active=True, employee__isnull=True).select_related("user")

class RateForm(BaseForm):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none(), label="员工")
    effective_from = forms.DateField(label="生效日期", widget=forms.DateInput(attrs=DATE), initial=timezone.localdate)
    hourly_amount = forms.DecimalField(label="每小时综合成本（元）", max_digits=12, decimal_places=2, min_value=0, widget=forms.NumberInput(attrs=MONEY))
    basis = forms.CharField(label="费率依据", max_length=240, widget=forms.Textarea, help_text="例如（月工资＋公司承担社保福利＋固定分摊）÷月标准有效工时；只有负责人和项目财务可见")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(organization=self.member.organization, active=True)

class TeamForm(ProjectScopedForm):
    management_only = True
    member = forms.ModelChoiceField(queryset=Membership.objects.none(), label="加入成员")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member"].queryset = Membership.objects.filter(organization=self.member.organization, active=True).select_related("user")

class MemberForm(BaseForm):
    username = forms.RegexField(r"^[\w.@+-]+$", label="登录名", max_length=80)
    name = forms.CharField(label="姓名", max_length=60)
    role = forms.ChoiceField(label="角色", choices=Membership.Role.choices)
    password = forms.CharField(label="初始密码", widget=forms.PasswordInput, help_text="至少12位；由本人首次登录后修改，不会通过系统发送")
    def clean_username(self):
        value = self.cleaned_data["username"]
        if get_user_model().objects.filter(username=value).exists(): raise ValidationError("此登录名已经存在")
        return value
    def clean(self):
        data = super().clean()
        if data.get("password"):
            validate_password(data["password"], get_user_model()(username=data.get("username", ""), first_name=data.get("name", "")))
        return data

class OrgForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "locked_through"]
        labels = {"name": "公司名称", "locked_through": "锁账截止日"}
        widgets = {"locked_through": forms.DateInput(attrs=DATE)}
        help_texts = {"locked_through": "截止日及之前的成本和现金不再允许补录；冲销追加到开放期间。锁账日期只能向前推进。"}
    def clean_locked_through(self):
        value = self.cleaned_data["locked_through"]
        if self.instance.locked_through and (not value or value < self.instance.locked_through):
            raise ValidationError("锁账日期不能回退；如需处理历史差错，请在开放期间补记或冲销")
        if value and value > timezone.localdate(): raise ValidationError("不能锁定未来日期")
        if value:
            from .models import Timesheet
            if Document.objects.filter(project__organization=self.instance, status="submitted", occurred_on__lte=value).exists() or Timesheet.objects.filter(project__organization=self.instance, status="submitted", work_date__lte=value).exists():
                raise ValidationError("截止日前还有待审批单据或工时，请先处理再锁账")
            from .labor_cutover import check_monthly_close
            check_monthly_close(self.instance, value)
            from .revenue_accounting import check_revenue_close
            check_revenue_close(self.instance, value)
        return value

class ImportForm(BaseForm):
    file = forms.FileField(label="CSV文件（UTF-8，最多500行）", help_text="先校验预览，确认后全部提交待审批；任何错误都会回滚本批，不会直接入账")

class AITextForm(BaseForm):
    text = forms.CharField(label="原始业务描述", max_length=8000, widget=forms.Textarea, help_text="只提交你允许发送给已配置模型的业务文字；AI生成草稿后仍通过正常表单、凭证和审批处理")

class ProjectEditForm(BaseForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    scope = forms.CharField(label="范围与交付物", widget=forms.Textarea, max_length=8000)
    acceptance_criteria = forms.CharField(label="验收标准", widget=forms.Textarea, max_length=8000)
    end_date = forms.DateField(label="计划交付日期", widget=forms.DateInput(attrs=DATE))
    contract_reference = forms.CharField(label="合同或立项依据编号", max_length=240)
    etc_amount = forms.DecimalField(label="最新预计剩余成本（元）", max_digits=14, decimal_places=2, min_value=0, widget=forms.NumberInput(attrs=MONEY))
    reason = forms.CharField(label="更新依据", max_length=300, widget=forms.Textarea, min_length=4)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.project_context.stage not in {"initiation", "planning"}:
            for key in ["scope", "acceptance_criteria", "end_date", "contract_reference"]:
                self.fields[key].disabled = True
                self.fields[key].help_text = "执行期间保留已批准的范围；涉及范围和期限的变更须另行记录审批依据"

class TaskEditForm(TaskForm):
    version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(label="计划调整依据", min_length=4, max_length=300, widget=forms.Textarea)

class AgentTokenForm(BaseForm):
    name = forms.CharField(label="办公工具名称", max_length=80, help_text="例如个人WorkBuddy或个人Codex")
    scopes = forms.MultipleChoiceField(label="允许的操作", widget=forms.CheckboxSelectMultiple)
    projects = forms.ModelMultipleChoiceField(label="允许访问的项目", queryset=Project.objects.none(), widget=forms.CheckboxSelectMultiple)
    days = forms.TypedChoiceField(label="有效期", choices=[(7, "7天"), (30, "30天"), (90, "90天")], coerce=int, initial=30)
    password = forms.CharField(label="确认当前登录密码", widget=forms.PasswordInput)
    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        from .agent_auth import SCOPE_CHOICES, allowed_scopes
        allowed = allowed_scopes(self.member)
        self.fields["scopes"].choices = [(k, label) for k, label in SCOPE_CHOICES if k in allowed]
        self.fields["projects"].queryset = visible_projects(self.member)
    def clean_password(self):
        from django.contrib.auth import authenticate
        user = authenticate(request=self.request, username=self.member.user.username, password=self.cleaned_data["password"])
        if user is None or user.pk != self.member.user_id:
            raise ValidationError("登录密码不正确或密码验证暂时被锁定")
        return self.cleaned_data["password"]
