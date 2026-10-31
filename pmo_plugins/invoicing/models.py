"""Invoicing owned facts: invoice, lines, allocations, reversals and tax qualification.

Append-only: headers/lines/allocations/reversals are immutable after insert; only the invoice
status may transition active -> reversed on a full reversal. No revenue/cash/ledger fact is
written by this plugin.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

MAX_CENTS = 10 ** 12
STATUSES = ['draft', 'active', 'posted', 'reversed', 'superseded']


class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='invoices')
    direction = models.CharField(max_length=8)
    counterparty = models.ForeignKey('contracts.Counterparty', on_delete=models.PROTECT,
                                     related_name='invoices')
    invoice_number = models.CharField(max_length=80)
    invoice_date = models.DateField()
    gross_cents = models.BigIntegerField()
    net_cents = models.BigIntegerField()
    tax_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'direction', 'invoice_number'],
                                    name='invoicing_invoice_number_unique'),
            models.CheckConstraint(condition=Q(direction__in=['sales', 'purchase']),
                                   name='invoicing_invoice_direction'),
            models.CheckConstraint(condition=Q(status__in=STATUSES),
                                   name='invoicing_invoice_status'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='invoicing_invoice_gross_bound'),
            models.CheckConstraint(condition=Q(net_cents__gte=0, tax_cents__gte=0,
                                               gross_cents=F('net_cents') + F('tax_cents')),
                                   name='invoicing_invoice_tax_split'),
        ]

    @property
    def invoice_ref(self):
        return {'namespace': 'invoicing', 'kind': 'invoice', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            persisted = type(self).objects.get(pk=self.pk)
            if (persisted.status == 'reversed'
                    or self.status not in {'active', 'reversed'}
                    or any(getattr(self, field) != getattr(persisted, field) for field in
                           ('organization_id', 'direction', 'counterparty_id', 'invoice_number',
                            'invoice_date', 'gross_cents', 'net_cents', 'tax_cents', 'version'))):
                raise ValidationError('发票登记后不可修改，仅支持全额红冲的状态位变化')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('发票历史不可删除')


class InvoiceLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='invoice_lines')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='lines')
    line_no = models.PositiveIntegerField()
    gross_cents = models.BigIntegerField()
    net_cents = models.BigIntegerField()
    tax_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['invoice', 'line_no'],
                                    name='invoicing_line_no_unique'),
            models.CheckConstraint(condition=Q(line_no__gte=1), name='invoicing_line_no'),
            models.CheckConstraint(condition=Q(status__in=STATUSES), name='invoicing_line_status'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='invoicing_line_gross_bound'),
            models.CheckConstraint(condition=Q(net_cents__gte=0, tax_cents__gte=0,
                                               gross_cents=F('net_cents') + F('tax_cents')),
                                   name='invoicing_line_tax_split'),
        ]

    @property
    def line_ref(self):
        return {'namespace': 'invoicing', 'kind': 'invoice_line', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('发票行登记后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('发票行不可删除')


class InvoiceAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='invoice_allocations')
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT,
                                     related_name='allocations')
    business_source_ref = models.JSONField()
    gross_cents = models.BigIntegerField()
    net_cents = models.BigIntegerField()
    tax_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status__in=STATUSES),
                                   name='invoicing_allocation_status'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='invoicing_allocation_gross_bound'),
            models.CheckConstraint(condition=Q(net_cents__gte=0, tax_cents__gte=0,
                                               gross_cents=F('net_cents') + F('tax_cents')),
                                   name='invoicing_allocation_tax_split'),
        ]

    @property
    def allocation_ref(self):
        return {'namespace': 'invoicing', 'kind': 'invoice_allocation', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('发票分配登记后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('发票分配不可删除')


class InvoiceReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='invoice_reversals')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='reversals')
    reason = models.CharField(max_length=500)
    reversal_date = models.DateField()
    line_reversals = models.JSONField(default=list)
    allocation_reversals = models.JSONField(default=list)
    gross_cents = models.BigIntegerField()
    net_cents = models.BigIntegerField()
    tax_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status__in=STATUSES),
                                   name='invoicing_reversal_status'),
            models.CheckConstraint(condition=Q(gross_cents__lt=0, net_cents__lte=0,
                                               tax_cents__lte=0,
                                               gross_cents=F('net_cents') + F('tax_cents')),
                                   name='invoicing_reversal_signed'),
        ]

    @property
    def reversal_ref(self):
        return {'namespace': 'invoicing', 'kind': 'invoice_reversal', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('发票红冲登记后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('发票红冲不可删除')


class TaxQualification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='tax_qualifications')
    source_ref = models.JSONField()
    tax_cents = models.BigIntegerField()
    status = models.CharField(max_length=16, default='pending')
    qualification_status = models.CharField(max_length=20, default='unknown')
    decided_on = models.DateField(null=True, blank=True)
    evidence = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status__in=['pending', 'confirmed', 'superseded']),
                                   name='invoicing_tax_status'),
            models.CheckConstraint(condition=Q(qualification_status__in=[
                'unknown', 'recoverable', 'not_recoverable']),
                name='invoicing_tax_qualification'),
            models.CheckConstraint(condition=Q(tax_cents__gte=0, tax_cents__lte=MAX_CENTS),
                                   name='invoicing_tax_bound'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('税额资格事实登记后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('税额资格事实不可删除')
