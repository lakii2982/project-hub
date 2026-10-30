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
