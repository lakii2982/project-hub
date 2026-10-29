"""Customer confirmation facts; no income or cash ownership."""
import uuid
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
