"""Customer confirmation facts; no income or cash ownership."""
import uuid
from django.core.exceptions import ValidationError
from django.db import models


class CustomerConfirmationEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT)
    contract_line = models.ForeignKey('contracts.ContractLine', on_delete=models.PROTECT)
    contact = models.ForeignKey('contracts.Contact', on_delete=models.PROTECT)
    contact_snapshot = models.JSONField()
    confirmed_on = models.DateField()
    channel = models.CharField(max_length=100)
    reference = models.CharField(max_length=200)
    statement = models.TextField(max_length=2000)
    sha256 = models.CharField(max_length=64)
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Acceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT)
    contract_line = models.ForeignKey('contracts.ContractLine', on_delete=models.PROTECT)
    contract_version = models.ForeignKey('contracts.ContractVersion', on_delete=models.PROTECT)
    selection = models.ForeignKey('contracts.MethodPolicySelection', on_delete=models.PROTECT)
    line_ref = models.JSONField()
    policy_version = models.JSONField()
    pricing_configuration = models.JSONField()
    approved_digest = models.CharField(max_length=64)
    evidence = models.JSONField()
    confirmed_on = models.DateField()
    accepted_net_cents = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    confirmed_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    confirmed_at = models.DateTimeField(null=True)
    confirmed_version = models.PositiveIntegerField(null=True)
    confirmation_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT, null=True)
    approved_input_snapshot = models.JSONField(default=dict)
    review_note = models.TextField(blank=True)
    replaces_acceptance = models.ForeignKey('self', on_delete=models.PROTECT, null=True, related_name='replacements')

    def get_status_display(self):
        return {'draft':'草稿', 'submitted':'待独立确认', 'confirmed':'已确认', 'reversed':'已反冲', 'withdrawn':'已撤回'}.get(self.status,self.status)


class AcceptanceCoverage(models.Model):
    acceptance = models.ForeignKey(Acceptance, on_delete=models.PROTECT, related_name='coverages')
    milestone = models.ForeignKey('core.Milestone', on_delete=models.PROTECT)
    from_basis_points = models.PositiveIntegerField()
    to_basis_points = models.PositiveIntegerField()
    accepted_net_cents = models.PositiveBigIntegerField()
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(from_basis_points__lt=models.F('to_basis_points'), to_basis_points__lte=10000), name='delivery_valid_range')]


class AcceptanceReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    acceptance = models.OneToOneField(Acceptance, on_delete=models.PROTECT, related_name='reversal')
    reason = models.TextField()
    actor = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class DeliveryReceipt(models.Model):
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT)
    request_token = models.UUIDField()
    action = models.CharField(max_length=40)
    digest = models.CharField(max_length=64)
    result_id = models.UUIDField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','request_token'], name='delivery_receipt_unique')]

class ServiceConfirmation(models.Model):
    """Customer-confirmed service performance with frozen monthly allocation rows."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='service_confirmations')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT, related_name='service_confirmations')
    contract_line = models.ForeignKey('contracts.ContractLine', on_delete=models.PROTECT, related_name='service_confirmations')
    contract_version = models.ForeignKey('contracts.ContractVersion', on_delete=models.PROTECT, related_name='service_confirmations')
    selection = models.ForeignKey('contracts.MethodPolicySelection', on_delete=models.PROTECT, related_name='service_confirmations')
    contract_line_ref = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    confirmed_on = models.DateField()
    coverage_start = models.DateField()
    coverage_end = models.DateField()
    schedule_index = models.PositiveIntegerField()
    schedule_snapshot = models.JSONField()
    pricing_configuration = models.JSONField()
    policy_version = models.JSONField()
    approved_digest = models.CharField(max_length=64)
    evidence = models.JSONField()
    confirmed_net_cents = models.PositiveBigIntegerField()
    confirmed_version = models.PositiveIntegerField(null=True)
    confirmed_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+', null=True)
    confirmed_at = models.DateTimeField(null=True)
    confirmation_audit = models.ForeignKey('core.AuditEvent', on_delete=models.PROTECT, null=True)
    approved_input_snapshot = models.JSONField(default=dict)
    review_note = models.TextField(blank=True)
    replaces_confirmation = models.ForeignKey('self', on_delete=models.PROTECT, null=True, related_name='replacements')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(coverage_start__lte=models.F('coverage_end')), name='delivery_service_confirmation_coverage_order'),
            models.CheckConstraint(condition=models.Q(confirmed_net_cents__lte=10**12), name='delivery_service_confirmation_amount_bound'),
        ]

    def get_status_display(self):
        return {'draft': '草稿', 'submitted': '待独立确认', 'confirmed': '已确认', 'reversed': '已反冲', 'withdrawn': '已撤回'}.get(self.status, self.status)


class ServiceConfirmationMonth(models.Model):
    """Private frozen month intersection of one confirmed service performance."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service_confirmation = models.ForeignKey(ServiceConfirmation, on_delete=models.PROTECT, related_name='months')
    service_month = models.CharField(max_length=7)
    coverage_start = models.DateField()
    coverage_end = models.DateField()
    allocated_net_cents = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['service_confirmation', 'service_month'], name='delivery_service_month_unique'),
            models.CheckConstraint(condition=models.Q(coverage_start__lte=models.F('coverage_end')), name='delivery_service_month_range'),
            models.CheckConstraint(condition=models.Q(allocated_net_cents__lte=10**12), name='delivery_service_month_amount_bound'),
        ]


class ServiceConfirmationReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    confirmation = models.OneToOneField(ServiceConfirmation, on_delete=models.PROTECT, related_name='reversal')
    reason = models.TextField()
    actor = models.ForeignKey('core.Membership', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class PeriodCloseRun(models.Model):
    """Append-only period close/reopen run; closing and closed rows are a committed pair."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='period_close_runs')
    accounting_period = models.ForeignKey('core.AccountingPeriod', on_delete=models.PROTECT,
                                          related_name='close_runs')
    period = models.CharField(max_length=7)
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=12)
    state = models.CharField(max_length=12)
    from_period_version = models.PositiveIntegerField()
    closed_through = models.DateField(null=True, blank=True)
    blockers = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    precheck_digest = models.CharField(max_length=64)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    request_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'period', 'sequence'],
                                    name='delivery_period_close_sequence_unique'),
            models.CheckConstraint(condition=models.Q(action__in=['close', 'reopen']),
                                   name='delivery_period_close_action'),
            models.CheckConstraint(condition=models.Q(state__in=['closing', 'closed', 'reopened']),
                                   name='delivery_period_close_state'),
            models.CheckConstraint(
                condition=(models.Q(action='close', state__in=['closing', 'closed'])
                           | models.Q(action='reopen', state='reopened')),
                name='delivery_period_close_action_state'),
            models.CheckConstraint(condition=~models.Q(state='closed', closed_through__isnull=True),
                                   name='delivery_period_close_closed_date'),
        ]
        indexes = [models.Index(fields=['organization', 'accounting_period', 'created_at'],
                                name='delivery_period_close_lookup')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('关期 run 只追加，不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('关期 run 不可删除')


class ProjectClose(models.Model):
    """Append-only project close/reopen run; closing and closed rows are a committed pair."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='project_close_runs')
    project = models.ForeignKey('core.Project', on_delete=models.PROTECT,
                                related_name='close_runs')
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=12)
    state = models.CharField(max_length=12)
    from_stage = models.CharField(max_length=24)
    from_project_version = models.PositiveIntegerField()
    blockers = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    precheck_digest = models.CharField(max_length=64)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    request_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'project', 'sequence'],
                                    name='delivery_project_close_sequence_unique'),
            models.CheckConstraint(condition=models.Q(action__in=['close', 'reopen']),
                                   name='delivery_project_close_action'),
            models.CheckConstraint(condition=models.Q(state__in=['closing', 'closed', 'reopened']),
                                   name='delivery_project_close_state'),
            models.CheckConstraint(
                condition=(models.Q(action='close', state__in=['closing', 'closed'])
                           | models.Q(action='reopen', state='reopened')),
                name='delivery_project_close_action_state'),
            models.CheckConstraint(condition=~models.Q(action='reopen') | models.Q(from_stage='closed'),
                                   name='delivery_project_close_reopen_stage'),
        ]
        indexes = [models.Index(fields=['organization', 'project', 'created_at'],
                                name='delivery_project_close_lookup')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('结项 run 只追加，不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('结项 run 不可删除')
