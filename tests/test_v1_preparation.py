import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PREPARATION = ROOT / "docs" / "v1.0-development"
SCHEMA_VALIDATION_AVAILABLE = all(
    importlib.util.find_spec(module) is not None
    for module in ("jsonschema", "referencing")
)
SCHEMA_SKIP_REASON = (
    "PH-135 full schema validation requires the pinned integrations/mcp "
    "requirements; the preparation workflow supplies them without adding "
    "production dependencies"
)


class PreparationContractTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.getenv("PH135_REQUIRE_SCHEMAS") == "1" and not SCHEMA_VALIDATION_AVAILABLE:
            raise AssertionError("Strict preparation schema validation required but dependencies unavailable")


    def run_preparation_script(self, relative_path):
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / relative_path)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"{relative_path} failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        return completed.stdout

    @unittest.skipUnless(SCHEMA_VALIDATION_AVAILABLE, SCHEMA_SKIP_REASON)
    def test_contract_schemas_and_rejection_cases(self):
        self.run_preparation_script(
            "docs/v1.0-development/contracts/validate_contracts.py"
        )
        self.run_preparation_script(
            "docs/v1.0-development/contracts/check_contract_cases.py"
        )
        print("PH135_CONTRACT_SCHEMAS_EXECUTED validator=strict_jsonschema")

    @unittest.skipUnless(SCHEMA_VALIDATION_AVAILABLE, SCHEMA_SKIP_REASON)
    def test_fixture_structure_hashes_arithmetic_and_samples(self):
        output = self.run_preparation_script(
            "docs/v1.0-development/fixtures/check_fixture.py"
        )
        result = json.loads(output)
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["positive_schema_samples"], 99)
        self.assertEqual(result["negative_schema_samples"], 8)
        self.assertEqual(result["sources"], 92)
        self.assertEqual(result["uat_sequences"], 18)
        self.assertEqual(result["requirements"], 137)
        self.assertFalse(result["product_commands_executed"])
        self.assertFalse(result["product_uat_passed"])
        self.assertFalse(result["database_read"])
        print("PH135_FIXTURE_SCHEMA_EXECUTED positive=99 negative=8")

    @unittest.skipUnless(SCHEMA_VALIDATION_AVAILABLE, SCHEMA_SKIP_REASON)
    def test_tampered_fixture_inputs_are_rejected(self):
        for kind in ("attachment", "duplicated_base", "boolean_money"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="ph135-negative-") as directory:
                docs=Path(directory)/"docs"
                preparation=docs/"v1.0-development"
                shutil.copytree(PREPARATION/"fixtures",preparation/"fixtures")
                shutil.copytree(PREPARATION/"contracts",preparation/"contracts")
                shutil.copy2(ROOT/"docs/v1.0-delivery-plan.md",docs/"v1.0-delivery-plan.md")
                fixture_dir=preparation/"fixtures"
                if kind=="attachment":
                    (fixture_dir/"attachments/contract.txt").write_text("tampered synthetic attachment")
                    expected_error="ATT-CONTRACT hash"
                elif kind=="duplicated_base":
                    path=fixture_dir/"fixture.json"
                    fixture=json.loads(path.read_text())
                    fixture["bases"][1]["id"]=fixture["bases"][0]["id"]
                    path.write_text(json.dumps(fixture))
                    expected_error="18 independent bases"
                else:
                    path=fixture_dir/"command-samples.json"
                    samples=json.loads(path.read_text())
                    cash=next(row for row in samples["samples"] if row["value"].get("contract")=="cash.transaction.record/v1")
                    cash["value"]["payload"]["gross_cents"]=True
                    path.write_text(json.dumps(samples))
                    expected_error=cash["id"]+":"
                outcome=subprocess.run([sys.executable,"-B",str(fixture_dir/"check_fixture.py")],capture_output=True,text=True,timeout=60,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"))
                self.assertEqual(outcome.returncode,1,outcome.stderr)
                result=json.loads(outcome.stdout)
                self.assertEqual(result["result"],"fail")
                self.assertTrue(any(expected_error in message for message in result["errors"]),result)

    def test_preparation_assets_do_not_claim_product_execution(self):
        fixture = json.loads(
            (PREPARATION / "fixtures" / "fixture.json").read_text()
        )
        coverage = json.loads(
            (PREPARATION / "fixtures" / "requirements-coverage.json").read_text()
        )
        prototype = json.loads(
            (PREPARATION / "ux" / "ux-contract.json").read_text()
        )

        self.assertEqual(fixture["status"], "preparation_fixture_only")
        self.assertEqual(len(fixture["enterprises"]), 2)
        self.assertEqual(len(fixture["uat_sequences"]), 18)
        self.assertTrue(
            all(
                scenario["execution_status"] == "not_run"
                for scenario in fixture["uat_sequences"]
            )
        )
        self.assertTrue(
            all(row["execution_status"] == "not_run" for row in coverage["requirements"])
        )
        self.assertTrue(prototype["offline"])
        self.assertFalse(prototype["external_assets"])
        self.assertEqual(prototype["viewports"], [1440, 390])

    def test_pg_allocation_race_remains_a_future_business_scenario(self):
        fixture = json.loads(
            (PREPARATION / "fixtures" / "fixture.json").read_text()
        )
        scenario = next(
            item for item in fixture["uat_sequences"] if item["id"] == "UAT-14"
        )
        race = scenario["steps"][1]

        self.assertEqual(scenario["execution_status"], "not_run")
        self.assertEqual(race["oracle_inputs"]["scheduled_winner"], "close")
        self.assertEqual(race["expected"]["company_cost_cents"], 0)
        self.assertEqual(
            race["alternative_branches"][0]["expected"]["company_cost_cents"],
            10000,
        )


if __name__ == "__main__":
    unittest.main()
