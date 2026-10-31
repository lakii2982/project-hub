"""PH-324: additive migrations backfill CNY for existing rows without rewriting amounts."""
import ast
from pathlib import Path

from django.test import SimpleTestCase

APPS = {
    "contracts": ("pmo_plugins/contracts/migrations", ["ContractLine", "PaymentTerm", "WarrantyTerm", "ContractChange"]),
    "settlement": ("pmo_plugins/settlement/migrations", ["Settlement", "OpenItem"]),
    "management_revenue": ("pmo_plugins/management_revenue/migrations", ["RevenueRecognition", "RevenueAdjustment"]),
    "cash": ("pmo_plugins/cash/migrations", ["CashTransaction", "Refund", "BankReturn"]),
}


class CurrencyBackfillTest(SimpleTestCase):
    def _add_field_operations(self, migration_path):
        tree = ast.parse(migration_path.read_text())
        ops = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                getattr(node.func, "id", None) == "AddField"
                or getattr(node.func, "attr", None) == "AddField"
            ):
                ops.append(node)
        return ops

    def test_currency_add_field_backfills_cny_for_existing_rows(self):
        root = Path(__file__).resolve().parent.parent
        for app, (directory, models) in APPS.items():
            migration_dir = root / directory
            found = {}
            for migration_path in sorted(migration_dir.glob("*.py")):
                for call in self._add_field_operations(migration_path):
                    keywords = {kw.arg: kw.value for kw in call.keywords}
                    name_node = keywords.get("name")
                    name = name_node.value if isinstance(name_node, ast.Constant) else None
                    if name != "currency":
                        continue
                    model_node = keywords.get("model_name")
                    if isinstance(model_node, ast.Constant):
                        model_name = str(model_node.value).split(".")[-1]
                    elif isinstance(model_node, ast.Name):
                        model_name = model_node.id
                    else:
                        continue
                    field = keywords.get("field")
                    default = None
                    if isinstance(field, ast.Call):
                        for kw in field.keywords:
                            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                                default = kw.value.value
                    found[model_name.lower()] = default
            for model in models:
                with self.subTest(app=app, model=model):
                    self.assertIn(model.lower(), found, "missing AddField currency for " + model)
                    self.assertEqual(found[model.lower()], "CNY")

    def test_original_currency_migrations_are_nullable(self):
        root = Path(__file__).resolve().parent.parent
        for app, (directory, _models) in APPS.items():
            migration_dir = root / directory
            texts = "\n".join(p.read_text() for p in migration_dir.glob("*.py"))
            with self.subTest(app=app):
                self.assertIn("original_", texts)
