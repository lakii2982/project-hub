"""Owned monthly labor facts. Financial postings remain owned by core."""
import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models import Sum


class SalaryPool(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    employee = models.ForeignKey('core.Employee', on_delete=models.PROTECT)
    period = models.CharField(max_length=7)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    valuation_state = models.CharField(max_length=24)
    covered_through = models.DateField()
    draft_components = models.JSONField(default=list)
    reference = models.CharField(max_length=500)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    submitted_at = models.DateTimeField(null=True)
    approved_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    approved_at = models.DateTimeField(null=True)
    approval_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT, null=True)
    approved_source_version = models.PositiveIntegerField(null=True)
    approved_salary_snapshot = models.JSONField(default=dict)
    original_policy = models.ForeignKey('CostPolicy', on_delete=models.PROTECT, related_name='+', null=True)
    capacity_proposal = models.JSONField(default=dict)
    capacity_proposed_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    recognition_event = models.ForeignKey('core.AccountingEvent', on_delete=models.PROTECT, related_name='+', null=True)
    reversed_event = models.ForeignKey('core.AccountingEvent', on_delete=models.PROTECT, related_name='+', null=True)

    class Meta:
        ordering = ['-period', 'employee_id']
        constraints = [models.UniqueConstraint(fields=['organization', 'employee', 'period'], name='costing_pool_employee_month_unique')]

    def save(self, *args, **kwargs):
        old = type(self).objects.filter(pk=self.pk).first()
        if old and old.approved_salary_snapshot:
            frozen = ('approved_salary_snapshot', 'approved_source_version', 'original_policy_id',
                      'employee_id', 'period', 'covered_through', 'valuation_state', 'draft_components',
                      'reference', 'created_by_id', 'submitted_by_id', 'approved_by_id', 'approval_audit_id')
            if any(getattr(old, key) != getattr(self, key) for key in frozen):
                raise ValidationError('原批准工资快照不可修改')
        super().save(*args, **kwargs)

    @property
    def total_comprehensive_cost_cents(self):
        components = self.approved_salary_snapshot.get('salary_components', self.draft_components)
        return sum(item['amount_cents'] for item in components)

    @property
    def salary_components(self):
        return self.approved_salary_snapshot.get('salary_components', self.draft_components)

    @property
    def allocation_state(self):
        return 'allocated' if self.allocation_runs.filter(status='posted').exists() else 'unallocated'

    @property
    def policy_version(self):
        return self.original_policy.policy_version if self.original_policy_id else None

    @property
    def source_ref(self):
        return {'namespace': 'costing', 'kind': 'salary_pool', 'id': str(self.pk),
                'version': self.approved_source_version or self.version}

    def get_status_display(self):
        if self.status == 'draft':
            return '待独立确认' if self.submitted_at else '草稿'
        return {'approved': '已确认发生', 'reconciled': '已确认实际', 'superseded': '已全额冲销（终态）'}.get(self.status, self.status)


class CostPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    employee = models.ForeignKey('core.Employee', on_delete=models.PROTECT)
    salary_pool = models.ForeignKey(SalaryPool, on_delete=models.PROTECT, related_name='policies')
    predecessor_policy = models.ForeignKey('self', on_delete=models.PROTECT, null=True)
    period = models.CharField(max_length=7)
    covered_through = models.DateField()
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='approved')
    created_at = models.DateTimeField(auto_now_add=True)
    method = models.CharField(max_length=64, default='monthly_comprehensive_capacity_v1')
    algorithm = models.CharField(max_length=64, default='monthly_group_half_up_largest_remainder_v1')
    component_snapshot = models.JSONField(default=list)
    capacity_confirmed = models.BooleanField(default=False)
    denominator_minutes = models.PositiveIntegerField(null=True)
    calendar_basis = models.CharField(max_length=500, blank=True)
    calendar_digest = models.CharField(max_length=64)
    approved_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    approved_at = models.DateTimeField()
    approval_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT)

    class Meta:
        constraints = [models.CheckConstraint(condition=(Q(capacity_confirmed=False, denominator_minutes__isnull=True) | Q(capacity_confirmed=True, denominator_minutes__gte=1, denominator_minutes__lte=1000000)), name='costing_policy_capacity_state')]

    def save(self, *args, **kwargs):
        if type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('批准政策不可修改，请建立新的批准快照')
        super().save(*args, **kwargs)

    @property
    def policy_version(self):
        return {'policy_id': str(self.pk), 'version': self.version, 'status': self.status,
                'effective_from': self.period + '-01', 'approved_by_member_id': str(self.approved_by_id),
                'evidence_ref': {'namespace': 'core', 'kind': 'audit_record', 'id': str(self.approval_audit_id), 'version': 1}}


class AllocationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    salary_pool = models.ForeignKey(SalaryPool, on_delete=models.PROTECT, related_name='allocation_runs')
    capacity_policy = models.ForeignKey(CostPolicy, on_delete=models.PROTECT)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='posted')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    approved_inputs_snapshot = models.JSONField(default=list)
    allocation_snapshot = models.JSONField(default=dict)
    total_cents = models.PositiveBigIntegerField()
    algorithm_version = models.CharField(max_length=64, default='monthly_group_half_up_largest_remainder_v1')
    accounting_event = models.ForeignKey('core.AccountingEvent', on_delete=models.PROTECT, related_name='+', null=True)
    reversal_event = models.ForeignKey('core.AccountingEvent', on_delete=models.PROTECT, related_name='+', null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['salary_pool'], condition=Q(status='posted'), name='costing_single_active_allocation')]

    @property
    def source_ref(self):
        return {'namespace': 'costing', 'kind': 'allocation_run', 'id': str(self.pk), 'version': self.version}

    @property
    def period(self):
        return self.salary_pool.period


class AllocationTarget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    allocation_run = models.ForeignKey(AllocationRun, on_delete=models.PROTECT, related_name='targets')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='posted')
    created_at = models.DateTimeField(auto_now_add=True)
    target_kind = models.CharField(max_length=24)
    target_id = models.CharField(max_length=64)
    weight = models.PositiveIntegerField()
    allocated_cents = models.PositiveBigIntegerField()

    class Meta:
        ordering = ['target_kind', 'target_id']
        constraints = [models.UniqueConstraint(fields=['allocation_run', 'target_kind', 'target_id'], name='costing_target_unique')]


class CostEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    salary_pool = models.ForeignKey(SalaryPool, on_delete=models.PROTECT, related_name='cost_events')
    allocation_run = models.ForeignKey(AllocationRun, on_delete=models.PROTECT, null=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='posted')
    created_at = models.DateTimeField(auto_now_add=True)
    core_accounting_event = models.OneToOneField('core.AccountingEvent', on_delete=models.PROTECT)

    @property
    def gross_cents(self):
        from core.models import LedgerPosting
        return LedgerPosting.objects.filter(accounting_event=self.core_accounting_event).aggregate(total=Sum('amount_cents'))['total'] or 0


class LaborReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    request_token = models.UUIDField()
    normalized_digest = models.CharField(max_length=64)
    salary_pool = models.ForeignKey(SalaryPool, on_delete=models.PROTECT)
    result_ref = models.JSONField()
    result_snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'action', 'request_token'], name='costing_receipt_unique')]
