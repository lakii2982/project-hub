"""Bank funding accounts, bank cash facts and party matches; no posting, balance or ledger rows."""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

MAX_CENTS = 10 ** 12


class FundingAccount(models.Model):
    """Company-owned bank account; draft until the owner activates it, then immutable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='funding_accounts')
    name = models.CharField(max_length=120)
    masked_identifier = models.CharField(max_length=64, blank=True, default='')
    account_owner = models.CharField(max_length=16, default='organization')
    opening_cents = models.BigIntegerField(default=0)
    opening_on = models.DateField()
    status = models.CharField(max_length=16, default='draft')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'],
                                    name='cash_funding_account_name_unique'),
            models.CheckConstraint(condition=Q(account_owner='organization'),
                                   name='cash_funding_account_owner'),
            models.CheckConstraint(condition=Q(status__in=['draft', 'active']),
                                   name='cash_funding_account_status'),
            models.CheckConstraint(condition=Q(opening_cents=0),
                                   name='cash_funding_account_opening_zero'),
        ]

    @property
    def account_ref(self):
        return {'namespace': 'cash', 'kind': 'funding_account', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '草稿', 'active': '已启用'}.get(self.status, self.status)

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.opening_cents != 0:
                raise ValidationError('银行账户期初余额必须为0，期初导入留待后续片')
            super().save(*args, **kwargs)
            return
        persisted = type(self).objects.filter(pk=self.pk).values(
            'status', 'opening_cents', 'opening_on').first()
        if persisted is None:
            super().save(*args, **kwargs)
            return
        if persisted['status'] == 'active':
            raise ValidationError('已启用银行账户不可修改')
        if (self.opening_cents != 0 or persisted['opening_cents'] != 0
                or self.opening_on != persisted['opening_on']):
            raise ValidationError('银行账户期初余额与期初日期在创建后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('银行账户历史不可删除')


class CashTransaction(models.Model):
    """One real bank statement fact; this slice never posts cash to any ledger."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='cash_transactions')
    funding_account = models.ForeignKey(FundingAccount, on_delete=models.PROTECT,
                                        related_name='transactions')
    transaction_date = models.DateField()
    direction = models.CharField(max_length=3)
    gross_cents = models.PositiveBigIntegerField()
    external_source_key = models.CharField(max_length=200)
    payee = models.JSONField()
    # Deliberately no cross-owner FK: same-company existence/status is verified through
    # contracts.party_services.get_party in the service layer (see PH-228 deviations).
    counterparty_id = models.UUIDField(null=True, blank=True)
    evidence = models.JSONField(default=list)
    status = models.CharField(max_length=16, default='draft')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'funding_account', 'external_source_key'],
                                    name='cash_transaction_source_key_unique'),
            models.CheckConstraint(condition=Q(direction__in=['in', 'out']),
                                   name='cash_transaction_direction'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='cash_transaction_gross_bound'),
            models.CheckConstraint(condition=Q(status='draft'),
                                   name='cash_transaction_draft_status'),
        ]

    @property
    def transaction_ref(self):
        return {'namespace': 'cash', 'kind': 'cash_transaction', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '草稿'}.get(self.status, self.status)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(status='draft').exists():
            raise ValidationError('非草稿银行流水不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('银行流水历史不可删除')


class CashPartyMatch(models.Model):
    """Append-only party identification or its single explicit reversal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='cash_party_matches')
    cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                          related_name='matches')
    action = models.CharField(max_length=10)
    identified_payee = models.JSONField(null=True, blank=True)
    original_match = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='reversals')
    reason = models.TextField(null=True, blank=True)
    evidence = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['original_match'],
                                    condition=Q(original_match__isnull=False),
                                    name='cash_party_match_reverse_unique'),
            models.CheckConstraint(condition=Q(action__in=['identify', 'reverse']),
                                   name='cash_party_match_action'),
            models.CheckConstraint(
                condition=(Q(action='identify', original_match__isnull=True)
                           | Q(action='reverse', original_match__isnull=False)),
                name='cash_party_match_reverse_requires_original'),
        ]

    @property
    def match_ref(self):
        return {'namespace': 'cash', 'kind': 'cash_party_match', 'id': str(self.pk),
                'version': self.version}

    def get_action_display(self):
        return {'identify': '识别往来方', 'reverse': '反向匹配'}.get(self.action, self.action)

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('往来方匹配记录不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('往来方匹配记录不可删除')


class CashReceipt(models.Model):
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
                                               name='cash_receipt_token_unique')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('现金收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('现金收据不可删除')
