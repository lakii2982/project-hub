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
            models.CheckConstraint(condition=Q(status__in=['draft', 'posted']),
                                   name='cash_transaction_status'),
        ]

    @property
    def transaction_ref(self):
        return {'namespace': 'cash', 'kind': 'cash_transaction', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '草稿', 'posted': '已过账'}.get(self.status, self.status)

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


class Refund(models.Model):
    """Append-only refund fact: posted incoming cash actually flowed back out."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='cash_refunds')
    original_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                                  related_name='refunds')
    refund_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                               related_name='refund_source')
    original_cash_ref = models.JSONField()
    refund_date = models.DateField()
    gross_cents = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'refund_cash_transaction'],
                                    name='cash_refund_cash_unique'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='cash_refund_amount_bound'),
            models.CheckConstraint(condition=Q(status='active'),
                                   name='cash_refund_status'),
        ]

    @property
    def refund_ref(self):
        return {'namespace': 'cash', 'kind': 'refund', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if self._state.adding:
            super().save(*args, **kwargs)
            return
        raise ValidationError('退款事实不可修改')

    def delete(self, *args, **kwargs):
        raise ValidationError('退款事实不可删除')


class BankReturn(models.Model):
    """Append-only bank return fact: the original bank flow came back in reverse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='cash_bank_returns')
    original_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                                  related_name='bank_returns')
    return_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                               related_name='return_source')
    original_cash_ref = models.JSONField()
    return_date = models.DateField()
    gross_cents = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'return_cash_transaction'],
                                    name='cash_bank_return_cash_unique'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='cash_bank_return_amount_bound'),
            models.CheckConstraint(condition=Q(status='active'),
                                   name='cash_bank_return_status'),
        ]

    @property
    def bank_return_ref(self):
        return {'namespace': 'cash', 'kind': 'bank_return', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if self._state.adding:
            super().save(*args, **kwargs)
            return
        raise ValidationError('银行退回事实不可修改')

    def delete(self, *args, **kwargs):
        raise ValidationError('银行退回事实不可删除')


CLASSIFICATION_KINDS = ('receipt', 'payment', 'advance', 'tax', 'transfer')
CLASSIFICATION_STATUSES = ('active', 'reversed')
CLASSIFICATION_LABELS = {'receipt': '收款', 'payment': '付款', 'advance': '预收/预付',
                         'tax': '税费', 'transfer': '内部互转'}


class CashClassification(models.Model):
    """One effective classification of a posted bank fact; reversed by an append-only row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='cash_classifications')
    cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                         related_name='classifications')
    classification = models.CharField(max_length=16)
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    evidence = models.JSONField(default=list)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(classification__in=list(CLASSIFICATION_KINDS)),
                                   name='cash_classification_kind'),
            models.CheckConstraint(condition=Q(status__in=list(CLASSIFICATION_STATUSES)),
                                   name='cash_classification_status'),
            # At most one effective classification per bank fact; reversed rows stay as history.
            models.UniqueConstraint(fields=['organization', 'cash_transaction'],
                                    condition=Q(status='active'),
                                    name='cash_classification_single_active'),
        ]

    @property
    def classification_ref(self):
        return {'namespace': 'cash', 'kind': 'cash_classification', 'id': str(self.pk),
                'version': self.version}

    def get_classification_display(self):
        return CLASSIFICATION_LABELS.get(self.classification, self.classification)

    def get_status_display(self):
        return {'active': '有效', 'reversed': '已撤分类'}.get(self.status, self.status)

    def save(self, *args, **kwargs):
        # Creation only; reversal is a conditional status update in the owner service.
        if not self._state.adding:
            raise ValidationError('现金分类创建后不可修改；撤分类请使用撤分类服务')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('现金分类历史不可删除')


class CashClassificationReversal(models.Model):
    """Append-only full reversal of one classification; the original row is never rewritten."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    classification = models.ForeignKey(CashClassification, on_delete=models.PROTECT,
                                       related_name='reversals')
    cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                         related_name='classification_reversals')
    status = models.CharField(max_length=16, default='posted')
    reason = models.TextField()
    evidence = models.JSONField(default=list)
    occurred_on = models.DateField()
    posting_period = models.CharField(max_length=7)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['classification'],
                                    name='cash_classification_reversal_unique'),
            models.CheckConstraint(condition=Q(status='posted'),
                                   name='cash_classification_reversal_status'),
        ]

    @property
    def reversal_ref(self):
        return {'namespace': 'cash', 'kind': 'cash_classification_reversal', 'id': str(self.pk),
                'version': 1}

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('撤分类记录不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('撤分类记录不可删除')


INTERNAL_TRANSFER_STATUSES = ('draft', 'active', 'posted', 'reversed', 'superseded')


class InternalTransfer(models.Model):
    """Append-only pairing of one out leg and one in leg of the same company transfer."""

    STATUSES = INTERNAL_TRANSFER_STATUSES
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='internal_transfers')
    out_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                             related_name='out_transfers')
    in_cash_transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT,
                                            related_name='in_transfers')
    out_cash_ref = models.JSONField()
    in_cash_ref = models.JSONField()
    transfer_date = models.DateField()
    gross_cents = models.PositiveBigIntegerField()
    out_transaction_date = models.DateField()
    in_transaction_date = models.DateField()
    status = models.CharField(max_length=16, default='active')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Unconditional lifetime uniqueness: a cash fact never participates in a second
            # transfer, not even after the first transfer is reversed.
            models.UniqueConstraint(fields=['out_cash_transaction'],
                                    name='cash_internal_transfer_out_unique'),
            models.UniqueConstraint(fields=['in_cash_transaction'],
                                    name='cash_internal_transfer_in_unique'),
            models.CheckConstraint(condition=Q(gross_cents__gt=0, gross_cents__lte=MAX_CENTS),
                                   name='cash_internal_transfer_gross_bound'),
            models.CheckConstraint(condition=Q(status__in=list(INTERNAL_TRANSFER_STATUSES)),
                                   name='cash_internal_transfer_status'),
        ]

    @property
    def internal_transfer_ref(self):
        return {'namespace': 'cash', 'kind': 'internal_transfer', 'id': str(self.pk),
                'version': self.version}

    def get_status_display(self):
        return {'draft': '草稿', 'active': '有效', 'posted': '已过账',
                'reversed': '已撤转', 'superseded': '已替代'}.get(self.status, self.status)

    def save(self, *args, **kwargs):
        # Creation only; the single active->reversed transition is a conditional update.
        if not self._state.adding:
            raise ValidationError('内部互转创建后不可修改；撤转请使用撤转服务')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('内部互转历史不可删除')


class InternalTransferReversal(models.Model):
    """Append-only reversal of one active internal transfer; originals stay untouched."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    internal_transfer = models.ForeignKey(InternalTransfer, on_delete=models.PROTECT,
                                          related_name='reversals', unique=True)
    internal_transfer_ref = models.JSONField()
    out_classification_reversal_ref = models.JSONField()
    in_classification_reversal_ref = models.JSONField()
    occurred_on = models.DateField()
    posting_period = models.CharField(max_length=7)
    status = models.CharField(max_length=16, default='posted')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status='posted'),
                                   name='cash_internal_transfer_reversal_status'),
        ]

    @property
    def reversal_ref(self):
        return {'namespace': 'cash', 'kind': 'internal_transfer_reversal', 'id': str(self.pk),
                'version': self.version}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('内部撤转记录创建后不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('内部撤转记录不可删除')


class InternalTransferReceipt(models.Model):
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
                                               name='cash_internal_transfer_receipt_token_unique')]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('内部互转收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('内部互转收据不可删除')
