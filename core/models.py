"""Project management subledger. Amounts use integer CNY cents; time uses integer minutes."""
import uuid
from pathlib import Path
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

class Organization(models.Model):
    name = models.CharField("公司名称", max_length=100)
    currency = models.CharField(max_length=3, default="CNY", editable=False)
    locked_through = models.DateField("锁账截止日", null=True, blank=True)
    opening_cash_cents = models.BigIntegerField(default=0)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name

class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "公司负责人"
        PM = "pm", "项目经理"
        FINANCE = "finance", "项目财务"
        MEMBER = "member", "项目成员"
        AUDITOR = "auditor", "只读审阅"
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="unique_org_user")]
    def __str__(self): return self.user.get_full_name() or self.user.username

class Employee(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    name = models.CharField("姓名", max_length=60)
    title = models.CharField("岗位", max_length=80, blank=True)
    member = models.OneToOneField(Membership, on_delete=models.PROTECT, null=True, blank=True)
    capacity_minutes = models.PositiveIntegerField(default=480, validators=[MaxValueValidator(1440)])
    active = models.BooleanField(default=True)
    def __str__(self): return self.name

class CostRate(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="rates")
    effective_from = models.DateField("生效日期")
    hourly_cents = models.PositiveBigIntegerField("小时综合成本（分）")
    basis = models.CharField("计算依据", max_length=240)
    class Meta:
        ordering = ["-effective_from", "-id"]
        constraints = [models.UniqueConstraint(fields=["employee", "effective_from"], name="unique_employee_rate_date")]

class Project(models.Model):
    class Stage(models.TextChoices):
        INITIATION = "initiation", "立项"
        PLANNING = "planning", "规划"
        EXECUTION = "execution", "执行"
        ACCEPTANCE = "acceptance", "验收"
        WARRANTY = "warranty", "维保"
        CLOSED = "closed", "归档"
        ON_HOLD = "on_hold", "暂停"
        CANCELLED = "cancelled", "取消"
    class Template(models.TextChoices):
        SOFTWARE = "software", "软件开发"
        IMPLEMENTATION = "implementation", "系统实施"
        SERVICE = "service", "技术服务"
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    code = models.CharField("项目编号", max_length=32)
    name = models.CharField("项目名称", max_length=120)
    client = models.CharField("客户", max_length=120)
    manager = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="managed_projects")
    template = models.CharField(max_length=24, choices=Template.choices, default=Template.SOFTWARE)
    stage = models.CharField(max_length=24, choices=Stage.choices, default=Stage.INITIATION)
    previous_stage = models.CharField(max_length=24, blank=True)
    start_date = models.DateField("开始日期")
    end_date = models.DateField("计划交付日期")
    contract_cents = models.PositiveBigIntegerField(default=0)
    budget_cents = models.PositiveBigIntegerField(default=0)
    budget_approved = models.BooleanField(default=False)
    etc_cents = models.PositiveBigIntegerField("预计剩余成本（分）", default=0)
    scope = models.TextField("范围和交付物", blank=True)
    acceptance_criteria = models.TextField("验收标准", blank=True)
    contract_reference = models.CharField("合同或立项依据", max_length=240, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="unique_org_project_code"),
                       models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="valid_project_dates")]
    def __str__(self): return f"{self.code} · {self.name}"

class ProjectMember(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="team")
    member = models.ForeignKey(Membership, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "member"], name="unique_project_member")]

class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "待开始"
        DOING = "doing", "进行中"
        BLOCKED = "blocked", "有阻碍"
        DONE = "done", "已完成"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="tasks")
    name = models.CharField("任务", max_length=160)
    owner = models.ForeignKey(Membership, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO)
    start_date = models.DateField()
    due_date = models.DateField()
    estimate_minutes = models.PositiveIntegerField(default=0)
    dependency = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    milestone = models.ForeignKey("Milestone", on_delete=models.PROTECT, null=True, blank=True, related_name="key_tasks")
    cash_plan = models.ForeignKey("CashPlan", on_delete=models.PROTECT, null=True, blank=True, related_name="key_tasks")
    description = models.TextField(blank=True)
    completion_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=Q(due_date__gte=models.F("start_date")), name="valid_task_dates")]

class Milestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="milestones")
    name = models.CharField(max_length=120)
    due_date = models.DateField()
    criteria = models.TextField()
    evidence = models.CharField(max_length=240, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    class Meta:
        ordering = ["due_date", "id"]

class CashPlan(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "计划收款"
        OUT = "out", "计划付款"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="cash_plans")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    title = models.CharField(max_length=140)
    counterparty = models.CharField(max_length=120)
    amount_cents = models.PositiveBigIntegerField()
    due_date = models.DateField()
    milestone = models.ForeignKey(Milestone, on_delete=models.PROTECT, null=True, blank=True)
    reference = models.CharField(max_length=240)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    approved = models.BooleanField(default=False)
    rejected = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    review_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=Q(amount_cents__gt=0), name="positive_cash_plan")]

class Timesheet(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "待审批"
        APPROVED = "approved", "已入账"
        REJECTED = "rejected", "已退回"
        REVERSED = "reversed", "已冲销"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="timesheets")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, null=True, blank=True)
    work_date = models.DateField()
    minutes = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(1440)])
    note = models.CharField(max_length=300)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    hourly_rate_cents = models.PositiveBigIntegerField()
    cost_cents = models.PositiveBigIntegerField()
    rate_basis = models.CharField(max_length=240)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_timesheets")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_timesheets")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-work_date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(minutes__gte=1, minutes__lte=1440), name="valid_time_minutes")]

class Document(models.Model):
    Status = Timesheet.Status
    class Kind(models.TextChoices):
        EXPENSE = "expense", "成本费用"
        RECEIPT = "receipt", "实际收款"
        PAYMENT = "payment", "实际付款"
        BASELINE = "baseline", "预算基线"
        CHANGE = "change", "预算变更"
        ACCEPTANCE = "acceptance", "项目验收"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=160)
    category = models.CharField(max_length=40, default="other")
    amount_cents = models.PositiveBigIntegerField(default=0)
    tax_cents = models.PositiveBigIntegerField(default=0)
    recoverable_tax = models.BooleanField(default=False)
    occurred_on = models.DateField()
    counterparty = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=240)
    note = models.TextField(blank=True)
    plan = models.ForeignKey(CashPlan, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Timesheet.Status.choices, default=Timesheet.Status.SUBMITTED)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_documents")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="reviewed_documents")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=Q(tax_cents__lte=models.F("amount_cents")), name="valid_document_tax")]

class LedgerEntry(models.Model):
    class Book(models.TextChoices):
        COST = "cost", "已发生成本"
        CASH_IN = "cash_in", "实际流入"
        CASH_OUT = "cash_out", "实际流出"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="entries")
    book = models.CharField(max_length=12, choices=Book.choices)
    amount_cents = models.BigIntegerField()
    category = models.CharField(max_length=40)
    occurred_on = models.DateField()
    document = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, blank=True)
    timesheet = models.ForeignKey(Timesheet, on_delete=models.PROTECT, null=True, blank=True)
    reverses = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal")
    posting_key = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["occurred_on", "id"]
        constraints = [models.CheckConstraint(condition=(Q(document__isnull=False, timesheet__isnull=True) | Q(document__isnull=True, timesheet__isnull=False)), name="one_ledger_source")]

class Risk(models.Model):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="risks")
    title = models.CharField(max_length=160)
    severity = models.CharField(max_length=12, choices=[("high", "高"), ("medium", "中"), ("low", "低")])
    owner = models.ForeignKey(Membership, on_delete=models.PROTECT)
    due_date = models.DateField()
    mitigation = models.TextField()
    resolved = models.BooleanField(default=False)
    resolution = models.TextField(blank=True)


def evidence_path(instance, filename):
    return f"{instance.document.project.organization_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"

class Evidence(models.Model):
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="evidence_files")
    file = models.FileField(upload_to=evidence_path)
    original_name = models.CharField(max_length=180)
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditEvent(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=80)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    class Meta:
        ordering = ["-id"]

class AIJob(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=16, default="validated")
    model = models.CharField(max_length=80)
    result = models.JSONField(default=dict)
    input_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

class AgentToken(models.Model):
    member = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="agent_tokens")
    name = models.CharField(max_length=80)
    prefix = models.CharField(max_length=14)
    token_hash = models.CharField(max_length=64, unique=True)
    auth_snapshot = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    project_ids = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    window_start = models.DateTimeField(default=timezone.now)
    window_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class AgentOperation(models.Model):
    member = models.ForeignKey(Membership, on_delete=models.PROTECT)
    operation = models.CharField(max_length=40)
    key_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["member", "operation", "key_hash"], name="unique_agent_operation")]

class PluginState(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    plugin_id = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    version = models.CharField(max_length=32)
    configuration = models.JSONField(default=dict)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "plugin_id"], name="unique_org_plugin")]

class DomainEvent(models.Model):
    event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    source = models.OneToOneField(AuditEvent, on_delete=models.PROTECT)
    topic = models.CharField(max_length=100)
    data = models.JSONField(default=dict)
    expanded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class EventDelivery(models.Model):
    event = models.ForeignKey(DomainEvent, on_delete=models.PROTECT, related_name="deliveries")
    plugin_id = models.CharField(max_length=80)
    status = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(default=timezone.now)
    result = models.JSONField(default=dict)
    last_error = models.CharField(max_length=240, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "plugin_id"], name="unique_event_plugin_delivery")]
