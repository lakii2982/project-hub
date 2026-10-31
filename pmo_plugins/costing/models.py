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


BANK_CHARGE_MAX_CENTS = 10**12
BANK_CHARGE_STATUSES = ('draft', 'pending', 'approved', 'rejected', 'superseded')
BANK_CHARGE_BEARERS = ('company', 'counterparty', 'unconfirmed')
BANK_CHARGE_SCOPE_KINDS = ('project', 'nonproject')
BANK_CHARGE_RECEIPT_ACTION = 'bank_charge.draft.save'


class BankCharge(models.Model):
    """Draft bank-fee fact for one posted incoming bank transaction (costing owner).

    The slice only produces draft rows; the canonical five-state enum is kept so later
    approval slices extend behavior without rewriting the frozen field domain. No cost,
    ledger posting, accounting event, settlement or open item is created here.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='bank_charges')
    # Deliberately no cross-owner FK: same-company/status/version are verified through
    # the cash owner named read (posted_cash_for_fee) in the service layer.
    cash_transaction_id = models.UUIDField()
    cash_transaction_ref = models.JSONField()
    gross_cents = models.PositiveBigIntegerField()
    tax_cents = models.PositiveBigIntegerField(null=True)
    existing_cost_ref = models.JSONField(null=True, blank=True)
    counterparty_id = models.UUIDField(null=True, blank=True)
    occurred_on = models.DateField()
    posting_period = models.CharField(max_length=7)
    scope_kind = models.CharField(max_length=16, default='project')
    scope_id = models.CharField(max_length=64)
    fee_bearer = models.CharField(max_length=16, default='company')
    evidence = models.JSONField(default=list)
    status = models.CharField(max_length=16, default='draft')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    # PH-250 approval chain: submit records the submitting member; decide records the independent
    # approver and an optional rejection reason. Version keeps tracking saved draft revisions only.
    submitted_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True,
                                     blank=True, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True,
                                    blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=1000, blank=True, default='')

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'cash_transaction_id'],
                                    name='costing_bank_charge_cash_unique'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=BANK_CHARGE_MAX_CENTS),
                                   name='costing_bank_charge_gross_bound'),
            models.CheckConstraint(condition=Q(tax_cents__isnull=True) | Q(
                tax_cents__gte=0, tax_cents__lte=BANK_CHARGE_MAX_CENTS),
                                   name='costing_bank_charge_tax_bound'),
            models.CheckConstraint(condition=Q(status__in=list(BANK_CHARGE_STATUSES)),
                                   name='costing_bank_charge_status'),
            models.CheckConstraint(condition=Q(fee_bearer__in=list(BANK_CHARGE_BEARERS)),
                                   name='costing_bank_charge_bearer'),
            models.CheckConstraint(condition=Q(scope_kind__in=list(BANK_CHARGE_SCOPE_KINDS)),
                                   name='costing_bank_charge_scope_kind'),
        ]

    @property
    def source_ref(self):
        return {'namespace': 'costing', 'kind': 'bank_charge', 'id': str(self.pk),
                'version': self.version}

    @property
    def cash_ref(self):
        return dict(self.cash_transaction_ref)

    @property
    def scope(self):
        return {'kind': self.scope_kind, 'id': self.scope_id}

    def get_status_display(self):
        return {'draft': '未批准草稿', 'pending': '待批准', 'approved': '已批准',
                'rejected': '已驳回', 'superseded': '已取代'}.get(self.status, self.status)

    def get_fee_bearer_display(self):
        return {'company': '公司承担', 'counterparty': '交易对手承担',
                'unconfirmed': '承担方未确认'}.get(self.fee_bearer, self.fee_bearer)

    def delete(self, *args, **kwargs):
        raise ValidationError('银行手续费草稿不可删除')


class BankChargeReceipt(models.Model):
    """Idempotency receipt for one draft save/revision; append-only, never returns another charge."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='bank_charge_receipts')
    action = models.CharField(max_length=32, default=BANK_CHARGE_RECEIPT_ACTION)
    request_token = models.UUIDField()
    input_digest = models.CharField(max_length=64)
    result_ref = models.JSONField()
    result_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['organization', 'action', 'request_token'],
                                               name='costing_bank_charge_receipt_unique')]

    def delete(self, *args, **kwargs):
        raise ValidationError('银行手续费草稿收据不可删除')


class SharedCostAllocationBatch(models.Model):
    """Append-only shared-cost pool allocation/reversal batch (PH-330)."""

    KINDS = ('allocation', 'reversal')
    STATUSES = ('draft', 'posted', 'reversed')
    POOL_KINDS = ('labor_nonproject', 'labor_unallocated')
    DRIVERS = ('revenue_cents', 'approved_minutes', 'direct_cost_cents', 'fixed_weights')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='shared_cost_allocation_batches')
    period = models.CharField(max_length=7)
    pool_key = models.CharField(max_length=64)
    pool_kind = models.CharField(max_length=32)
    driver = models.CharField(max_length=24)
    kind = models.CharField(max_length=12, default='allocation')
    status = models.CharField(max_length=12, default='draft')
    amount_cents = models.BigIntegerField()
    total_basis_points = models.PositiveIntegerField(default=10000)
    source_refs = models.JSONField(default=list)
    source_digest = models.CharField(max_length=64)
    input_digest = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64,
                                        default='basis_points_clamped_half_up_last_absorb_v1')
    reverses_batch = models.OneToOneField('self', on_delete=models.PROTECT, null=True,
                                          related_name='reversal')
    reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'period', 'pool_key'],
                condition=Q(kind='allocation', status='posted'),
                name='costing_active_shared_allocation'),
            models.UniqueConstraint(fields=['reverses_batch'], condition=Q(kind='reversal'),
                                    name='costing_one_allocation_reversal'),
            models.CheckConstraint(condition=Q(kind__in=('allocation', 'reversal')), name='costing_shared_batch_kind'),
            models.CheckConstraint(condition=Q(status__in=('draft', 'posted', 'reversed')),
                                   name='costing_shared_batch_status'),
            models.CheckConstraint(condition=Q(total_basis_points=10000),
                                   name='costing_shared_batch_basis'),
            models.CheckConstraint(
                condition=Q(kind='allocation', amount_cents__gte=0, reverses_batch__isnull=True)
                | Q(kind='reversal', amount_cents__lte=0, reverses_batch__isnull=False),
                name='costing_shared_batch_sign'),
            models.CheckConstraint(condition=Q(status='reversed', kind='allocation')
                                   | ~Q(status='reversed'),
                                   name='costing_shared_batch_reversed_kind'),
        ]
        indexes = [models.Index(fields=['organization', 'period', 'status'],
                                name='cost_batch_period_idx'),
                   models.Index(fields=['organization', 'pool_key', 'status'],
                                name='cost_batch_pool_idx')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            persisted = type(self).objects.get(pk=self.pk)
            if persisted.status == self.status:
                raise ValidationError('共享分摊批次事实不可修改')
            if (persisted.status == 'draft' and self.status == 'posted'
                    and all(getattr(self, field) == getattr(persisted, field) for field in (
                        'organization_id', 'period', 'pool_key', 'pool_kind', 'driver',
                        'kind', 'amount_cents', 'total_basis_points', 'source_refs',
                        'source_digest', 'input_digest', 'algorithm_version',
                        'reverses_batch_id', 'reason', 'created_by_id', 'created_at'))):
                super().save(update_fields=['status'])
                return
            if (persisted.kind == 'allocation' and persisted.status == 'posted'
                    and self.status == 'reversed'
                    and all(getattr(self, field) == getattr(persisted, field) for field in (
                        'organization_id', 'period', 'pool_key', 'pool_kind', 'driver',
                        'kind', 'amount_cents', 'total_basis_points', 'source_refs',
                        'source_digest', 'input_digest', 'algorithm_version',
                        'reverses_batch_id', 'reason', 'created_by_id', 'created_at'))):
                super().save(update_fields=['status'])
                return
            raise ValidationError('共享分摊批次仅允许 draft→posted 或 posted→reversed')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('共享分摊批次不可删除')

    @property
    def source_ref(self):
        return {'namespace': 'costing', 'kind': 'shared_cost_allocation_batch',
                'id': str(self.pk), 'version': 1}


class SharedCostAllocationLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    batch = models.ForeignKey(SharedCostAllocationBatch, on_delete=models.PROTECT,
                              related_name='lines')
    sequence = models.PositiveIntegerField()
    target_kind = models.CharField(max_length=16, default='project')
    target_id = models.CharField(max_length=64)
    basis_points = models.PositiveIntegerField()
    allocated_cents = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence']
        constraints = [
            models.UniqueConstraint(fields=['batch', 'sequence'],
                                    name='costing_shared_line_sequence'),
            models.UniqueConstraint(fields=['batch', 'target_kind', 'target_id'],
                                    name='costing_shared_line_target'),
            models.CheckConstraint(condition=Q(target_kind='project'),
                                   name='costing_shared_line_target_kind'),
            models.CheckConstraint(condition=Q(basis_points__gte=1, basis_points__lte=10000),
                                   name='costing_shared_line_basis'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('共享分摊明细不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('共享分摊明细不可删除')


class SharedCostAllocationReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='shared_cost_allocation_receipts')
    request_token = models.UUIDField()
    action = models.CharField(max_length=20)
    input_digest = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'request_token'],
                                               name='costing_shared_receipt_unique')]
        ordering = ['created_at', 'id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('共享分摊收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('共享分摊收据不可删除')


class ProjectBudgetVersion(models.Model):
    STATUSES = ('draft', 'approved', 'superseded', 'rejected')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='project_budget_versions')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT,
                                related_name='budget_versions')
    period = models.CharField(max_length=7)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, default='draft')
    effective_from = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True,
                                     blank=True, related_name='+')
    approved_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, null=True,
                                    blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.TextField(blank=True, default='')
    based_on_version = models.ForeignKey('self', on_delete=models.PROTECT, null=True,
                                         blank=True, related_name='derived_versions')
    input_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['version']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'project', 'period', 'version'],
                                    name='costing_budget_version_unique'),
            models.UniqueConstraint(fields=['organization', 'project', 'period'],
                                    condition=Q(status='approved'),
                                    name='costing_active_budget_version'),
            models.CheckConstraint(condition=Q(status__in=('draft', 'approved', 'superseded', 'rejected')),
                                   name='costing_budget_status'),
            models.CheckConstraint(condition=Q(version__gte=1),
                                   name='costing_budget_version_positive'),
            models.CheckConstraint(
                condition=~Q(status='approved')
                | Q(effective_from__isnull=False, approved_by__isnull=False,
                    approved_at__isnull=False),
                name='costing_budget_approved_fields'),
            models.CheckConstraint(condition=~Q(status='rejected') | ~Q(reject_reason=''),
                                   name='costing_budget_rejected_reason'),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            persisted = type(self).objects.get(pk=self.pk)
            immutable = ('organization_id', 'project_id', 'period', 'version',
                         'created_by_id', 'created_at', 'input_digest')
            if any(getattr(self, field) != getattr(persisted, field) for field in immutable):
                raise ValidationError('预算版本身份/期间/版本不可修改')
            if (persisted.status == 'draft' and self.status == 'approved'
                    and self.approved_by_id and self.approved_at and self.effective_from
                    and self.reject_reason == persisted.reject_reason):
                super().save()
                return
            if (persisted.status == 'draft' and self.status == 'rejected'
                    and self.reject_reason.strip()):
                super().save()
                return
            if (persisted.status == 'approved' and self.status == 'superseded'
                    and all(getattr(self, field) == getattr(persisted, field) for field in (
                        'effective_from', 'approved_by_id', 'approved_at',
                        'reject_reason', 'based_on_version_id', 'submitted_by_id'))):
                super().save(update_fields=['status'])
                return
            raise ValidationError('预算版本仅允许 draft→approved/rejected 或 approved→superseded')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('预算版本不可删除')


class ProjectBudgetLine(models.Model):
    SUBJECTS = ('labor', 'expense', 'procurement', 'other')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    version = models.ForeignKey(ProjectBudgetVersion, on_delete=models.PROTECT,
                                related_name='lines')
    line_key = models.CharField(max_length=64)
    subject = models.CharField(max_length=16)
    amount_cents = models.PositiveBigIntegerField()
    contract_line_ref = models.JSONField(null=True, blank=True)
    cost_owner_ref = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subject', 'line_key']
        constraints = [
            models.UniqueConstraint(fields=['version', 'line_key'],
                                    name='costing_budget_line_key_unique'),
            models.UniqueConstraint(fields=['version', 'subject', 'contract_line_ref'],
                                    condition=Q(contract_line_ref__isnull=False),
                                    name='costing_budget_line_contract_unique'),
            models.CheckConstraint(condition=Q(subject__in=('labor', 'expense', 'procurement', 'other')),
                                   name='costing_budget_subject'),
            models.CheckConstraint(condition=Q(amount_cents__lte=10**12),
                                   name='costing_budget_amount_bound'),
        ]

    def save(self, *args, **kwargs):
        if self.version.status != 'draft':
            raise ValidationError('仅草稿预算版本可修改行')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.version.status != 'draft':
            raise ValidationError('仅草稿预算版本可删除行')
        super().delete(*args, **kwargs)


class ProjectBudgetReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='project_budget_receipts')
    request_token = models.UUIDField()
    action = models.CharField(max_length=20)
    input_digest = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'request_token'],
                                               name='costing_budget_receipt_unique')]
        ordering = ['created_at', 'id']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('预算收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('预算收据不可删除')
