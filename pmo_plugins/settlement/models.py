"""Customer receivable settlement facts: one settlement, frozen term partitions, receipts."""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


MAX_CENTS = 10 ** 12


class Settlement(models.Model):
    """Approved terms of one contract line settled once into immutable open items."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='settlements')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, related_name='settlements')
    contract_line = models.ForeignKey('contracts.ContractLine', on_delete=models.PROTECT, related_name='settlements')
    contract_version = models.ForeignKey('contracts.ContractVersion', on_delete=models.PROTECT, related_name='settlements')
    selection = models.ForeignKey('contracts.MethodPolicySelection', on_delete=models.PROTECT, related_name='settlements')
    counterparty = models.ForeignKey('contracts.Counterparty', on_delete=models.PROTECT, related_name='settlements')
    contract_line_ref = models.JSONField()
    source_ref = models.JSONField()
    source_performance = models.JSONField(default=list)
    policy_version = models.JSONField()
    pricing_configuration = models.JSONField()
    terms_snapshot = models.JSONField(default=dict)
    evidence = models.JSONField(default=list)
    settled_on = models.DateField()
    due_date = models.DateField()
    gross_cents = models.PositiveBigIntegerField()
    net_cents = models.PositiveBigIntegerField()
    tax_cents = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True, related_name='+')
    approved_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True, related_name='+')
    approved_at = models.DateTimeField(null=True)
    approved_version = models.PositiveIntegerField(null=True)
    approval_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT, null=True, related_name='+')
    approved_input_snapshot = models.JSONField(default=dict)
    review_note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(gross_cents=F('net_cents') + F('tax_cents')),
                                   name='settlement_gross_net_tax'),
            models.CheckConstraint(condition=Q(gross_cents__lte=MAX_CENTS),
                                   name='settlement_gross_bound'),
            models.CheckConstraint(condition=Q(net_cents__lte=MAX_CENTS, tax_cents__lte=MAX_CENTS),
                                   name='settlement_net_tax_bound'),
            models.CheckConstraint(condition=Q(status__in=['draft', 'submitted', 'active', 'withdrawn']),
                                   name='settlement_status_choices'),
        ]

    @property
    def settlement_ref(self):
        return {'namespace': 'settlement', 'kind': 'settlement', 'id': str(self.pk), 'version': self.version}

    @property
    def direction(self):
        return 'receivable'

    @property
    def scope(self):
        return {'kind': 'project', 'id': str(self.project_id)}

    @property
    def payee_ref(self):
        return {'kind': 'counterparty', 'id': str(self.counterparty_id)}

    def save(self, *args, **kwargs):
        # An active settlement is a frozen fact in this slice: no reversal path exists.
        if self.pk and type(self).objects.filter(pk=self.pk, status='active').exists():
            raise ValidationError('已生效结算不可修改；错误请在草稿或提交阶段更正')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('结算历史不可删除')

    def get_status_display(self):
        return {'draft': '草稿', 'submitted': '待审核', 'active': '已生效', 'withdrawn': '已撤回'}.get(
            self.status, self.status)


class OpenItem(models.Model):
    """Immutable receivable partition inherited from one approved settlement."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='open_items')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, related_name='open_items')
    settlement = models.ForeignKey(Settlement, on_delete=models.PROTECT, related_name='items')
    direction = models.CharField(max_length=16, default='receivable')
    partition_kind = models.CharField(max_length=16)
    partition_key = models.CharField(max_length=100, unique=True)
    term_ref = models.JSONField()
    counterparty = models.ForeignKey('contracts.Counterparty', on_delete=models.PROTECT, related_name='open_items')
    payee_ref = models.JSONField()
    scope = models.JSONField()
    source = models.JSONField()
    gross_cents = models.PositiveBigIntegerField()
    tax_cents = models.PositiveBigIntegerField()
    due_date = models.DateField()
    occurred_on = models.DateField()
    release_condition = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['partition_key'], name='settlement_open_item_partition_unique'),
            models.CheckConstraint(condition=Q(gross_cents__lte=MAX_CENTS), name='settlement_open_item_gross_bound'),
            models.CheckConstraint(condition=Q(tax_cents__lte=MAX_CENTS, gross_cents__gte=F('tax_cents')),
                                   name='settlement_open_item_tax_bound'),
            models.CheckConstraint(condition=Q(direction='receivable'), name='settlement_open_item_receivable'),
            models.CheckConstraint(condition=Q(partition_kind__in=['due', 'retention']),
                                   name='settlement_open_item_partition_kind'),
        ]

    @property
    def item_ref(self):
        return {'namespace': 'settlement', 'kind': 'open_item', 'id': str(self.pk), 'version': self.version}

    @property
    def remaining_cents(self):
        return self.gross_cents

    def save(self, *args, **kwargs):
        # Partitions are only ever created in bulk at independent approval.
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('未清项创建后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('未清项不可删除')


class SettlementReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='+')
    request_token = models.UUIDField()
    action = models.CharField(max_length=40)
    input_digest = models.CharField(max_length=64)
    result = models.JSONField()
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'request_token'],
                                               name='settlement_receipt_token_unique')]
