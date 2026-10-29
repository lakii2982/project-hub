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
