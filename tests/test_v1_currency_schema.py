"""PH-324: canonical currency extension is materialized in the Django models."""
import json
from pathlib import Path

from django.test import SimpleTestCase

from pmo_plugins.cash.models import BankReturn, CashTransaction, Refund
from pmo_plugins.contracts.models import ContractChange, ContractLine, PaymentTerm, WarrantyTerm
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition
from pmo_plugins.settlement.models import OpenItem, Settlement

CONTRACTS = Path(__file__).resolve().parent.parent / "docs/v1.0-development/contracts"

MODEL_FIELDS = [
    (ContractLine, "ContractLine", ["original_net_cents", "original_tax_cents", "original_gross_cents"]),
    (PaymentTerm, "PaymentTerm", ["original_gross_cents"]),
    (WarrantyTerm, "WarrantyTerm", ["original_gross_cents"]),
    (ContractChange, "ContractChange", ["original_gross_delta_cents", "original_net_delta_cents", "original_tax_delta_cents"]),
    (Settlement, "Settlement", ["original_gross_cents", "original_net_cents", "original_tax_cents"]),
    (OpenItem, "OpenItem", ["original_gross_cents", "original_tax_cents"]),
    (RevenueRecognition, "RevenueRecognition", ["original_recognized_net_cents"]),
    (RevenueAdjustment, "RevenueAdjustment", ["original_adjustment_cents"]),
    (CashTransaction, "CashTransaction", ["original_gross_cents"]),
    (Refund, "Refund", ["original_gross_cents"]),
    (BankReturn, "BankReturn", ["original_gross_cents"]),
]


class CurrencySchemaTest(SimpleTestCase):
    def test_models_have_cny_default_currency_field(self):
        for model, _entity, _originals in MODEL_FIELDS:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("currency")
                self.assertEqual(field.max_length, 3)
                self.assertEqual(field.default, "CNY")
                self.assertFalse(field.editable)

    def test_original_currency_fields_are_nullable(self):
        for model, _entity, originals in MODEL_FIELDS:
            for name in originals:
                with self.subTest(model=model.__name__, field=name):
                    field = model._meta.get_field(name)
                    self.assertTrue(field.null)
                    self.assertIsNone(field.default)

    def test_canonical_contracts_declare_currency_extension(self):
        entities = json.loads((CONTRACTS / "entities.schema.json").read_text())["$defs"]
        for _model, entity, originals in MODEL_FIELDS:
            with self.subTest(entity=entity):
                props = entities[entity]["properties"]
                self.assertIn("currency", props)
                self.assertEqual(props["currency"]["$ref"], "./contract-bundle.schema.json#/$defs/CurrencyCode")
                for name in originals:
                    self.assertIn(name, props)
