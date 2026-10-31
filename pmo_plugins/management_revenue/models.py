"""Append-only management revenue facts; core owns only their accounting bridge."""
import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class FrozenPosted(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status='posted').exists():
            raise ValidationError('已确认收入事实不可修改，请追加完整冲销')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('收入事实不可删除')


class RevenueRecognition(FrozenPosted):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT)
    acceptance = models.OneToOneField('delivery.Acceptance', on_delete=models.PROTECT, null=True, related_name='revenue_recognition')
    service_confirmation = models.ForeignKey('delivery.ServiceConfirmation', on_delete=models.PROTECT, null=True, related_name='+')
    service_confirmation_month = models.OneToOneField('delivery.ServiceConfirmationMonth', on_delete=models.PROTECT, null=True, related_name='revenue_recognition')
    contract_line = models.ForeignKey('contracts.ContractLine', on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    contract_line_ref = models.JSONField()
    performance_ref = models.JSONField()
    policy_version = models.JSONField()
    pricing_snapshot = models.JSONField()
    accepted_snapshot = models.JSONField()
    algorithm_version = models.CharField(max_length=64, default='fixed_milestone_cumulative_half_up_v1')
    recognized_net_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default='CNY', editable=False)
    fx_rate_ref = models.JSONField(null=True, default=None)
    original_recognized_net_cents = models.BigIntegerField(null=True, default=None)
    occurred_on = models.DateField()
    posting_period = models.CharField(max_length=7)
    accounting_event = models.OneToOneField('core.AccountingEvent', on_delete=models.PROTECT, null=True, related_name='+')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(recognized_net_cents__lte=10**12), name='revenue_recognition_amount_bound'),
            models.CheckConstraint(
                condition=(
                    Q(acceptance__isnull=False, service_confirmation__isnull=True, service_confirmation_month__isnull=True)
                    | Q(acceptance__isnull=True, service_confirmation__isnull=False, service_confirmation_month__isnull=False)
                ),
                name='revenue_recognition_one_source',
            ),
        ]

    @property
    def source_ref(self):
        return {'namespace': 'management_revenue', 'kind': 'revenue_recognition', 'id': str(self.pk), 'version': 1}

    @property
    def is_reversed(self):
        return RevenueAdjustment.objects.filter(recognition=self, status='posted').exists()


class RevenueAdjustment(FrozenPosted):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT)
    recognition = models.OneToOneField(RevenueRecognition, on_delete=models.PROTECT, related_name='adjustment')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    adjustment_cents = models.BigIntegerField()
    currency = models.CharField(max_length=3, default='CNY', editable=False)
    fx_rate_ref = models.JSONField(null=True, default=None)
    original_adjustment_cents = models.BigIntegerField(null=True, default=None)
    reason = models.CharField(max_length=500)
    occurred_on = models.DateField()
    posting_period = models.CharField(max_length=7)
    accounting_event = models.OneToOneField('core.AccountingEvent', on_delete=models.PROTECT, null=True, related_name='+')

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(adjustment_cents__gte=-10**12, adjustment_cents__lte=0), name='revenue_adjustment_amount_bound')]

    @property
    def source_ref(self):
        return {'namespace': 'management_revenue', 'kind': 'revenue_adjustment', 'id': str(self.pk), 'version': 1}


class RevenueReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    request_token = models.UUIDField()
    action = models.CharField(max_length=40)
    input_digest = models.CharField(max_length=64)
    result = models.JSONField()
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'request_token'], name='revenue_receipt_token_unique')]
