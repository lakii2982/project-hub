"""Insights-owned read models.

``ReconciliationCase`` is the canonical owned fact frozen by ADR 0014/PH-343: only
the canonical fields are exposed, its status machine is append-only in the sense
that replaced cases become ``superseded`` rows and no row is ever deleted.

``MetricSnapshot`` and ``ReconciliationRefresh`` are internal insights read-model
tables, never canonical business facts. They only append; corrections are new
rows or new receipts.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


MAX_CENTS = 10 ** 12
ACTIVE_STATUSES = ('draft', 'pending', 'approved')
STATUS_CHOICES = (
    ('draft', '草稿'),
    ('pending', '待确认'),
    ('approved', '已确认'),
    ('rejected', '已驳回'),
    ('superseded', '已替代'),
    ('closed', '已结案'),
)
# Frozen canonical state machine (ADR 0014). rejected/closed/superseded are terminal.
TRANSITIONS = {
    'draft': {'pending', 'superseded'},
    'pending': {'approved', 'rejected', 'superseded'},
    'approved': {'closed'},
    'rejected': set(),
    'superseded': set(),
    'closed': set(),
}


class ReconciliationCase(models.Model):
    """Canonical reconciliation difference case; fields mirror ADR 0014 exactly."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='insights_reconciliation_cases')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    source_refs = models.JSONField(default=list)
    difference_cents = models.BigIntegerField()
    owner_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                     related_name='+')
    resolution_note = models.TextField(null=True, blank=True, default=None)
    case_key = models.CharField(max_length=128)
    rule_code = models.CharField(max_length=80)
    coverage_key = models.CharField(max_length=300)
    snapshot_watermark = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                   related_name='+', null=True)
    updated_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                   related_name='+', null=True)

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=[choice for choice, label in STATUS_CHOICES]),
                name='insights_case_status_valid'),
            models.CheckConstraint(condition=Q(version__gte=1),
                                   name='insights_case_version_positive'),
            models.CheckConstraint(
                condition=Q(difference_cents__gte=-MAX_CENTS,
                            difference_cents__lte=MAX_CENTS),
                name='insights_case_difference_bound'),
            # At most one effective case per case_key; closed/superseded history is kept.
            models.UniqueConstraint(
                fields=['organization', 'case_key'],
                condition=Q(status__in=ACTIVE_STATUSES),
                name='insights_case_active_unique'),
        ]
        indexes = [models.Index(fields=['organization', 'status', 'rule_code'],
                                name='insights_case_status_idx')]

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.status != 'draft':
                raise ValidationError('新核对差异单只能以 draft 开始')
            self.version = 1
        else:
            old = type(self).objects.get(pk=self.pk)
            if old.organization_id != self.organization_id:
                raise ValidationError('核对差异单不可跨公司移动')
            frozen = ('case_key', 'rule_code', 'coverage_key', 'source_refs',
                      'difference_cents', 'snapshot_watermark', 'created_by_id')
            if any(getattr(old, field) != getattr(self, field) for field in frozen):
                raise ValidationError('核对差异单的派生字段不可原地修改；请刷新生成新单')
            if self.status != old.status:
                if self.status not in TRANSITIONS.get(old.status, set()):
                    raise ValidationError(
                        f'核对差异单状态不允许从 {old.status} 变为 {self.status}')
                self.version = old.version + 1
            else:
                self.version = old.version
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('核对差异历史不可删除')

    def get_status_display(self):
        return dict(STATUS_CHOICES).get(self.status, self.status)


class MetricSnapshot(models.Model):
    """Internal append-only metric read model; never a canonical business fact."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='insights_metric_snapshots')
    metric_key = models.CharField(max_length=80)
    period = models.CharField(max_length=7, blank=True, default='')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, null=True,
                                blank=True, related_name='insights_metric_snapshots')
    as_of = models.DateField()
    # Null means the upstream value is missing; never coerce a missing value to zero.
    value_cents = models.BigIntegerField(null=True, default=None)
    source_version = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['metric_key', 'as_of', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(value_cents__isnull=True) | Q(
                    value_cents__gte=-MAX_CENTS, value_cents__lte=MAX_CENTS),
                name='insights_metric_value_bound'),
        ]
        indexes = [models.Index(fields=['organization', 'metric_key', 'as_of'],
                                name='insights_metric_lookup_idx')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('指标快照仅追加，不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('指标快照不可删除')


class ReconciliationRefresh(models.Model):
    """Internal idempotency receipt for refresh/update requests (org+token unique)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='insights_reconciliation_refreshes')
    request_token = models.UUIDField()
    input_digest = models.CharField(max_length=64)
    watermark = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                   related_name='+', null=True)

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'request_token'],
                                    name='insights_refresh_token_unique'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('刷新收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('刷新收据不可删除')
