"""time domain owned facts: customer-confirmed billable minutes only."""
import uuid

from django.core.exceptions import ValidationError
from django.db import models

MAX_MINUTES = 1440


class BillableConfirmation(models.Model):
    """Append-only customer confirmation of billable minutes for one approved timesheet and contract line."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='billable_confirmations')
    status = models.CharField(max_length=16, default='draft')
    timesheet_ref = models.JSONField()
    timesheet_id = models.UUIDField()
    contract_line_ref = models.JSONField()
    contract_line_id = models.UUIDField()
    confirmed_minutes = models.PositiveIntegerField()
    confirmed_on = models.DateField()
    evidence = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    confirmed_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                            null=True, blank=True, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    create_token = models.UUIDField(null=True, blank=True, editable=False)
    create_digest = models.CharField(max_length=64, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'timesheet_id', 'contract_line_id'],
                                    condition=models.Q(status='confirmed'),
                                    name='time_billable_one_confirmed'),
            models.UniqueConstraint(fields=['organization', 'create_token'],
                                    name='time_billable_token_unique'),
            models.CheckConstraint(condition=models.Q(status__in=['draft', 'confirmed', 'reversed']),
                                   name='time_billable_status'),
            models.CheckConstraint(condition=models.Q(confirmed_minutes__gte=1,
                                                      confirmed_minutes__lte=MAX_MINUTES),
                                   name='time_billable_minutes'),
        ]

    @property
    def source_ref(self):
        return {'namespace': 'time', 'kind': 'billable_confirmation', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '确认草稿', 'confirmed': '客户已确认', 'reversed': '已撤销'}.get(self.status, self.status)

    def delete(self, *args, **kwargs):
        raise ValidationError('客户可计费确认不可删除')


class BillableConfirmationReceipt(models.Model):
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=16)
    request_token = models.UUIDField()
    input_digest = models.CharField(max_length=64)
    result_ref = models.JSONField()
    result_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'request_token'],
                                    name='time_billable_receipt_token'),
            models.CheckConstraint(condition=models.Q(action__in=['draft.save', 'confirm', 'reverse']),
                                   name='time_billable_receipt_action'),
        ]
