import re
import unicodedata
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Organization, Project


def normalize_display(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def normalized_key(value):
    return normalize_display(value).casefold()


def normalized_roles(values):
    return sorted(set(values or []))


class Counterparty(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        INACTIVE = "inactive", "停用"
        MERGED = "merged", "已合并"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="contract_parties")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    display_name = models.CharField("客户／供应商名称", max_length=160)
    normalized_name = models.CharField(max_length=320, editable=False, blank=True)
    roles = models.JSONField(default=list)
    active_from = models.DateField("有效起始日", default=timezone.localdate)
    inactive_on = models.DateField("失效日期", null=True, blank=True)
    create_token = models.UUIDField(null=True, blank=True, editable=False)
    create_digest = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["display_name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "normalized_name"], name="contracts_party_name_unique"),
            models.UniqueConstraint(fields=["organization", "create_token"], name="contracts_party_token_unique"),
        ]

    def clean(self):
        self.normalized_name = normalized_key(self.display_name)
        self.roles = normalized_roles(self.roles)
        if not self.roles or any(role not in {"customer", "supplier"} for role in self.roles):
            raise ValidationError({"roles": "至少选择客户或供应商角色，且不可重复"})
        if self.inactive_on and self.inactive_on < self.active_from:
            raise ValidationError({"inactive_on": "失效日期不能早于有效起始日"})

    def save(self, *args, **kwargs):
        self.normalized_name = normalized_key(self.display_name)
        self.roles = normalized_roles(self.roles)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name


class Contact(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "有效"
        INACTIVE = "inactive", "停用"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="contract_contacts")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    party = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name="contacts")
    name = models.CharField("联系人姓名", max_length=100)
    normalized_name = models.CharField(max_length=320, editable=False, blank=True)
    email = models.EmailField("电子邮箱", blank=True, default="")
    phone = models.CharField("联系电话", max_length=40, blank=True, default="")
    create_token = models.UUIDField(null=True, blank=True, editable=False)
    create_digest = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "create_token"], name="contracts_contact_token_unique"),
        ]

    def clean(self):
        self.normalized_name = normalized_key(self.name)
        if self.party_id and self.organization_id != self.party.organization_id:
            raise ValidationError({"party": "联系人须属于同一公司的客户／供应商"})

    def save(self, *args, **kwargs):
        self.normalized_name = normalized_key(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Contract(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        ACTIVE = "active", "已生效"
        TERMINATED = "terminated", "已终止"
        SUPERSEDED = "superseded", "已替代"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="project_contracts")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="contracts")
    party = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name="contracts")
    contract_code = models.CharField("合同编号", max_length=80)
    signed_on = models.DateField("签署日期", null=True, blank=True)
    terminated_on = models.DateField("终止生效日", null=True, blank=True, editable=False)
    parent_ref = models.JSONField(null=True, blank=True, editable=False)
    create_token = models.UUIDField(null=True, blank=True, editable=False)
    create_digest = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "contract_code"], name="contracts_code_unique"),
            models.UniqueConstraint(fields=["organization", "create_token"], name="contracts_token_unique"),
        ]

    def clean(self):
        errors = {}
        if self.project_id and self.project.organization_id != self.organization_id:
            errors["project"] = "合同项目须属于同一公司"
        if self.party_id:
            if self.party.organization_id != self.organization_id:
                errors["party"] = "合同客户须属于同一公司"
            elif "customer" not in (self.party.roles or []):
                errors["party"] = "合同往来方必须具有客户角色"
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.contract_code


class ContractVersion(models.Model):
    def get_status_display(self):
        return {'draft': '配置草稿', 'submitted': '待独立审批', 'approved': '已批准生效', 'rejected': '已拒绝', 'superseded': '历史批准版本'}.get(self.status, self.status)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name='versions')
    version = models.PositiveIntegerField(default=1)
    version_no = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    effective_on = models.DateField()
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True, related_name='+')
    approved_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True, related_name='+')
    approved_on = models.DateTimeField(null=True)
    approval_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT, null=True)
    review_note = models.TextField(blank=True)
    contract_reference = models.CharField(max_length=240)
    draft_content = models.JSONField(default=dict)
    header_snapshot = models.JSONField(default=dict)
    submitted_digest = models.CharField(max_length=64, blank=True)
    evidence = models.JSONField(default=list)
    action_receipts = models.JSONField(default=dict)

    class Meta:
        ordering = ['-version_no']
        constraints = [
            models.UniqueConstraint(fields=['contract', 'version_no'], name='contracts_version_number_unique'),
            models.CheckConstraint(condition=(models.Q(status__in=['approved', 'superseded'], approved_by_member__isnull=False, approved_on__isnull=False) | models.Q(status__in=['draft', 'submitted', 'rejected'], approved_by_member__isnull=True, approved_on__isnull=True)), name='contracts_version_approval_state'),
        ]


class ContractLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    contract_version = models.ForeignKey(ContractVersion, on_delete=models.PROTECT, related_name='lines')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='approved')
    created_at = models.DateTimeField(auto_now_add=True)
    line_no = models.PositiveIntegerField()
    net_cents = models.PositiveBigIntegerField()
    tax_cents = models.PositiveBigIntegerField()
    gross_cents = models.PositiveBigIntegerField()

    class Meta:
        ordering = ['line_no']
        constraints = [models.UniqueConstraint(fields=['contract_version', 'line_no'], name='contracts_line_number_unique')]

    @property
    def contract(self):
        return self.contract_version.contract

    @property
    def project_id(self):
        return self.contract.project_id

    @property
    def counterparty_id(self):
        return self.contract.party_id

    @property
    def pricing_configuration(self):
        return self.policy_selection.pricing_configuration

    @property
    def pricing_display(self):
        return pricing_display(self.pricing_configuration)

    @property
    def method_policy(self):
        return self.policy_selection.policy_version

    @property
    def pricing_template(self):
        return self.pricing_configuration['template']


class PaymentTerm(models.Model):
    def get_condition_type_display(self):
        return {'unconditional': '无条件', 'conditional': '附条件', 'warranty': '质保款'}.get(self.condition_type, '')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    contract_line = models.ForeignKey(ContractLine, on_delete=models.PROTECT, related_name='payment_terms')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    condition_type = models.CharField(max_length=16)
    gross_cents = models.PositiveBigIntegerField()
    due_date = models.DateField(null=True)
    release_condition = models.CharField(max_length=500, blank=True)


class WarrantyTerm(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    contract_line = models.ForeignKey(ContractLine, on_delete=models.PROTECT, related_name='warranty_terms')
    payment_term = models.ForeignKey(PaymentTerm, on_delete=models.PROTECT, related_name='warranty_terms')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    release_condition = models.CharField(max_length=500)
    release_on = models.DateField(null=True)
    gross_cents = models.PositiveBigIntegerField()


class MethodPolicySelection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    contract_line = models.OneToOneField(ContractLine, on_delete=models.PROTECT, related_name='policy_selection')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='approved')
    created_at = models.DateTimeField(auto_now_add=True)
    selected_on = models.DateField()
    approved_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    approval_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT)
    pricing_configuration = models.JSONField()

    @property
    def policy_version(self):
        return {'policy_id': str(self.pk), 'version': self.version, 'status': self.status,
                'effective_from': self.selected_on.isoformat(),
                'approved_by_member_id': str(self.approved_by_member_id),
                'evidence_ref': {'namespace': 'core', 'kind': 'audit_record', 'id': str(self.approval_audit_id), 'version': 1}}


def pricing_display(configuration):
    return {**configuration,
        'template_label': {'fixed_milestone': '固定价节点', 'confirmed_time': '客户确认工时', 'fixed_service_period': '固定服务期'}.get(configuration['template'], ''),
        'time_unit_label': {'hour': '小时', 'workday': '人天'}.get(configuration.get('time_unit'), ''),
        'period_unit_label': {'calendar_month': '自然月', 'calendar_day': '自然日'}.get(configuration.get('period_unit'), ''),
        'partial_period_rule_label': {'no_partial': '不允许部分期间', 'calendar_days': '按自然日'}.get(configuration.get('partial_period_rule'), '')}


CHANGE_TYPES = ('add_scope', 'reduce_scope', 'price_change', 'extension', 'termination')
CHANGE_STATUSES = ('draft', 'pending_customer', 'approved', 'rejected', 'withdrawn')
CHANGE_DECISIONS = ('approved', 'rejected', 'withdrawn')


class ContractChange(models.Model):
    """Append-only contract change request; approval creates or terminates a version."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='contract_changes')
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name='changes')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft', choices=[(x, x) for x in CHANGE_STATUSES])
    created_at = models.DateTimeField(auto_now_add=True)
    change_type = models.CharField(max_length=16, choices=[(x, x) for x in CHANGE_TYPES])
    requested_on = models.DateField()
    net_delta_cents = models.BigIntegerField(default=0)
    tax_delta_cents = models.BigIntegerField(default=0)
    gross_delta_cents = models.BigIntegerField(default=0)
    evidence = models.JSONField(default=list)
    draft_content = models.JSONField(default=dict)
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    decided_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True,
                                          blank=True, related_name='+')
    decided_on = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, default='')
    create_token = models.UUIDField(null=True, blank=True, editable=False)
    create_digest = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['contract'], condition=models.Q(status='draft'),
                                    name='contracts_change_one_draft'),
            models.UniqueConstraint(fields=['organization', 'create_token'],
                                    name='contracts_change_token_unique'),
            models.CheckConstraint(condition=models.Q(status__in=CHANGE_STATUSES),
                                   name='contracts_change_status'),
            models.CheckConstraint(condition=models.Q(change_type__in=CHANGE_TYPES),
                                   name='contracts_change_type'),
            models.CheckConstraint(condition=models.Q(net_delta_cents__gte=-(10**12),
                                                      net_delta_cents__lte=10**12,
                                                      tax_delta_cents__gte=-(10**12),
                                                      tax_delta_cents__lte=10**12,
                                                      gross_delta_cents__gte=-(10**12),
                                                      gross_delta_cents__lte=10**12),
                                   name='contracts_change_delta_bound'),
        ]

    @property
    def source_ref(self):
        return {'namespace': 'contracts', 'kind': 'contract_change', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '变更草稿', 'approved': '变更已生效', 'rejected': '变更已驳回',
                'withdrawn': '变更已撤回', 'pending_customer': '待客户确认'}.get(self.status, self.status)

    def get_change_type_display(self):
        return {'add_scope': '新增范围', 'reduce_scope': '缩减范围', 'price_change': '价格变更',
                'extension': '延期', 'termination': '终止'}.get(self.change_type, self.change_type)

    def delete(self, *args, **kwargs):
        raise ValidationError('合同变更历史不可删除')


class ContractChangeReceipt(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=24)
    request_token = models.UUIDField()
    input_digest = models.CharField(max_length=64)
    result_ref = models.JSONField()
    result_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'request_token'],
                                    name='contracts_change_receipt_token_unique'),
            models.CheckConstraint(condition=models.Q(action__in=['draft.save', 'decide']),
                                   name='contracts_change_receipt_action'),
        ]
