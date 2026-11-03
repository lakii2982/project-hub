"""Q-36 simplified project expense facts.

The cost fact stays a core ``Document(kind=expense)`` so the project ledger keeps a
single source of truth: confirming an expense posts one cost entry, while an
``ExpensePayment`` is a child payment record under the same expense and only ever
posts cash.  Nothing here duplicates a cost or replaces the core ledger.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

MAX_CENTS = 10 ** 12  # 100亿元, same bound as the core ledger


class ExpenseDetail(models.Model):
    """Q-36 owned facts that the core ``Document`` table does not carry.

    Only expenses registered through the simplified flow own a detail row; legacy
    professional expense documents stay readable/auditable exactly as they are and
    are never restated into the new 8-week plan.
    """

    document = models.OneToOneField(
        "core.Document", on_delete=models.PROTECT, primary_key=True,
        related_name="expense_detail")
    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="expense_details")
    expected_pay_on = models.DateField("预计付款日", null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ExpensePayment(models.Model):
    """A child payment record under one expense: cash only, never a second cost."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "core.Organization", on_delete=models.PROTECT, related_name="expense_payments")
    expense = models.ForeignKey(
        "core.Document", on_delete=models.PROTECT, related_name="expense_payments")
    payment_document = models.OneToOneField(
        "core.Document", on_delete=models.PROTECT,
        related_name="expense_payment_link")
    amount_cents = models.PositiveBigIntegerField("付款金额（分）")
    paid_on = models.DateField("付款日期")
    # Q-38: cash must leave a funding account. Nullable only for rows written before
    # Q-38; legacy rows stay readable and are surfaced as a gap, never restated.
    account = models.ForeignKey(
        "billing.Account", on_delete=models.PROTECT, null=True, blank=True,
        related_name="expense_payments", verbose_name="资金账户")
    note = models.CharField("付款说明", max_length=240, blank=True)
    token = models.UUIDField(unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_cents__gt=0, amount_cents__lte=MAX_CENTS),
                name="expense_payment_amount_bound"),
        ]
