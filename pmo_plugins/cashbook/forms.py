"""Q-38 account maintenance form; opening balance is parsed as Decimal/ROUND_HALF_UP."""
from decimal import Decimal

from django import forms

MONEY = {"min": "-10000000000", "max": "10000000000", "step": "0.01",
         "inputmode": "decimal"}


class AccountUpdateForm(forms.Form):
    opening_balance = forms.DecimalField(
        label="期初余额（元）", max_digits=14, decimal_places=2,
        min_value=Decimal("-10000000000"), max_value=Decimal("10000000000"),
        widget=forms.NumberInput(MONEY),
        help_text="只调整期初余额；账户余额仍由期初 + 收款 − 付款派生，不能直接改余额。")
    active = forms.BooleanField(label="启用该账户", required=False)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
