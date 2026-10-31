"""PH-324: CNY original-amount backfill and fx_rate_ref migrations are real data ops."""
import ast
import importlib.util
import re
from pathlib import Path

from django.db import connection
from django.test import TestCase

ROOT = Path(__file__).resolve().parent.parent

APPS = {
    "contracts": ("pmo_plugins/contracts/migrations", "0006_ph324_fx_rate_ref_and_cny_backfill.py",
                  ["ContractLine", "PaymentTerm", "WarrantyTerm", "ContractChange"]),
    "settlement": ("pmo_plugins/settlement/migrations", "0008_ph324_fx_rate_ref_and_cny_backfill.py",
                   ["Settlement", "OpenItem"]),
    "management_revenue": ("pmo_plugins/management_revenue/migrations", "0006_ph324_fx_rate_ref_and_cny_backfill.py",
                           ["RevenueRecognition", "RevenueAdjustment"]),
    "cash": ("pmo_plugins/cash/migrations", "0009_ph324_fx_rate_ref_and_cny_backfill.py",
             ["CashTransaction", "Refund", "BankReturn"]),
}


def load_migration(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeEditor:
    def __init__(self, vendor="sqlite"):
        self.connection = type("C", (), {"vendor": vendor})()
        self.sql = []

    def quote_name(self, name):
        return name

    def execute(self, sql, params=None):
        self.sql.append(sql)


class CurrencyBackfillTest(TestCase):
    def test_add_field_backfills_cny_for_existing_rows(self):
        for app, (directory, migration_name, models) in APPS.items():
            path = ROOT / directory / migration_name
            self.assertTrue(path.exists(), migration_name)
            currency_models = set()
            for migration_path in sorted((ROOT / directory).glob("00*.py")):
                if "ph324" not in migration_path.name and "currency" not in migration_path.name:
                    continue
                tree = ast.parse(migration_path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "AddField":
                        keywords = {kw.arg: kw.value for kw in node.keywords}
                        name = keywords["name"].value
                        model = str(keywords["model_name"].value).split(".")[-1]
                        if name == "currency":
                            currency_models.add(model.lower())
            for model in models:
                with self.subTest(app=app, model=model):
                    self.assertIn(model.lower(), currency_models)

    def test_fx_rate_ref_and_backfill_are_real_migration_operations(self):
        for app, (directory, migration_name, models) in APPS.items():
            module = load_migration(ROOT / directory / migration_name)
            editor = _FakeEditor("sqlite")
            module.add_fx_rate_ref(None, editor)
            self.assertTrue(any("ADD COLUMN fx_rate_ref json" in sql for sql in editor.sql))
            self.assertEqual(len(editor.sql), len(models))
            editor = _FakeEditor("postgresql")
            module.add_fx_rate_ref(None, editor)
            self.assertTrue(all("jsonb" in sql for sql in editor.sql))

    def test_backfill_updates_existing_cny_original_amounts(self):
        for app, (directory, migration_name, _models) in APPS.items():
            module = load_migration(ROOT / directory / migration_name)
            editor = _FakeEditor("sqlite")
            module.backfill_original_cny(None, editor)
            self.assertTrue(module.BACKFILL)
            for sql in editor.sql:
                match = re.match(r"UPDATE (\S+) SET (\S+) = (\S+) WHERE (\S+) = 'CNY' AND (\S+) IS NULL", sql)
                self.assertIsNotNone(match, sql)
                table, original, base, currency, guarded = match.groups()
                temp = f"ph324_tmp_{abs(hash((app, table, original))) % 100000}"
                with connection.cursor() as cursor:
                    cursor.execute(f"CREATE TABLE {temp} (currency text, {base} bigint, {original} bigint)")
                    cursor.execute(f"INSERT INTO {temp} VALUES ('CNY', 100, NULL), ('USD', 100, NULL)")
                    cursor.execute(sql.replace(table, temp, 1))
                    cursor.execute(f"SELECT currency, {original} FROM {temp} ORDER BY currency")
                    rows = dict(cursor.fetchall())
                    cursor.execute(f"DROP TABLE {temp}")
                self.assertEqual(rows["CNY"], 100)
                self.assertIsNone(rows["USD"])
