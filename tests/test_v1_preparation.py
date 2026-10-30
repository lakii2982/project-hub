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


class KernelContractPreparationTests(unittest.TestCase):
    """PH-210 design boundaries only: no product service, database or command executes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.getenv("PH135_REQUIRE_SCHEMAS") == "1" and not SCHEMA_VALIDATION_AVAILABLE:
            raise AssertionError("Strict preparation schema validation required but dependencies unavailable")

    def test_cash_consumption_is_the_permanent_original_bank_claim(self):
        registry = json.loads((PREPARATION / "contracts/source-registry.json").read_text())
        self.assertEqual(
            registry["objects"]["CashTransaction"],
            {"namespace": "cash", "kind": "cash_transaction", "owner": "cash"},
        )
        self.assertNotIn("posting_consumption", registry["objects"])
        self.assertEqual(set(registry["posting_consumption"]), {"cash"})
        policy = registry["posting_consumption"]["cash"]
        self.assertEqual((policy["producer_owner"], policy["claim_owner"]), ("cash", "core"))
        self.assertEqual(policy["source"], {"namespace": "cash", "kind": "cash_transaction"})
        self.assertEqual(policy["coverage_key"], "bank_effect")
        self.assertEqual(policy["claim_lifetime"], "permanent")
        self.assertFalse(policy["classification_and_allocation_create_cash"])
        self.assertFalse(policy["classification_reverse_releases_claim"])
        self.assertEqual(
            policy["identity_components"],
            ["organization_id", "consumer", "source.namespace", "source.kind", "source.id", "coverage_key"],
        )
        self.assertTrue(
            {"source.version", "request_id", "idempotency_key", "channel", "classification_id", "allocation_id"}
            <= set(policy["excluded_components"])
        )
        self.assertTrue(policy["exposure"].startswith("trusted_owner_service_only"))
        self.assertFalse(policy["runtime_implemented"])
        correction = policy["correction"]
        self.assertEqual(correction["mode"], "full_reverse_then_full_replacement")
        self.assertFalse(correction["original_claim_released"])
        self.assertFalse(correction["branching"])
        self.assertFalse(correction["cost_or_revenue_postings"])
        self.assertTrue(correction["net_cash_difference_is_result_only"])
        self.assertEqual(correction["unique_consumer"], "approved_request_and_effective_predecessor")
        self.assertEqual(correction["dependency_policy"], "explicit_owner_reversal_first_no_cascade")
        self.assertTrue(
            {"cash_transaction_id", "external_source_key", "bank_provenance", "original_rows"}
            <= set(correction["preserve"])
        )
        relative, pointer = correction["source_contract_ref"].split("#")
        plan = json.loads((PREPARATION / "contracts" / relative).resolve().read_text())
        self.assertEqual(pointer, "/service_semantics/correction")
        self.assertIsInstance(plan["service_semantics"]["correction"], list)
        self.assertTrue(plan["service_semantics"]["correction"])

        entities = json.loads((PREPARATION / "contracts/entities.schema.json").read_text())
        consumption = entities["$defs"]["SourceConsumption"]
        self.assertFalse(consumption["additionalProperties"])
        self.assertEqual(
            consumption["properties"]["consumer"]["enum"],
            ["cost", "management_revenue", "obligation", "clearance", "invoice_allocation",
             "forecast", "opening", "cash"],
        )
        invariants = " ".join(consumption["x-service-invariants"])
        self.assertIn("bank_effect", invariants)
        self.assertIn("cash consumption is not clearance consumption", invariants)
        self.assertIn("never release bank_effect", invariants)
        self.assertIn("does not branch", invariants)

        relationships = json.loads((PREPARATION / "contracts/relationships.json").read_text())
        relations = relationships["relationships"]
        accounting_event_relations = [
            name for name, row in relations.items() if row["canonical_record"] == "AccountingEvent"
        ]
        self.assertEqual(accounting_event_relations, ["event-reversal"])
        relation = relations["event-reversal"]
        self.assertEqual(
            (relation["owner"], relation["canonical_record"], relation["kind"]),
            ("core", "AccountingEvent", "embedded_reference"),
        )
        projection = relation["x-projection"]
        self.assertEqual((projection["left_ref"], projection["right_ref"]), ("original_event", "reversal_event"))
        self.assertTrue(projection["read_only"])
        self.assertIsNone(projection["write_owner"])
        for field in ("id", "version", "created_at"):
            self.assertEqual(projection[field], "reversal_event." + field)

        catalog = json.loads((PREPARATION / "contracts/catalog-completeness.schema.json").read_text())
        event = catalog["$defs"]["AccountingEvent"]
        self.assertFalse(event["additionalProperties"])
        self.assertNotIn("reverses_event_ref", event["required"])
        reference = event["properties"]["reverses_event_ref"]
        self.assertTrue(reference["readOnly"])
        self.assertTrue(reference["x-server-only"])
        wire = json.dumps(reference)
        self.assertIn('"const": "core"', wire)
        self.assertIn('"const": "accounting_event"', wire)

        commands = json.loads((PREPARATION / "contracts/commands.schema.json").read_text())
        for name in ("core.posting.append/v1", "core.posting.reverse/v1"):
            definition = commands["$defs"][name]
            self.assertFalse(definition["additionalProperties"])
            self.assertFalse(definition["properties"]["payload"]["additionalProperties"])
            invariants = " ".join(definition["x-service-invariants"])
            self.assertIn("reverses_event_ref", invariants)
            self.assertTrue("must not accept" in invariants or "cannot submit" in invariants)

    @unittest.skipUnless(SCHEMA_VALIDATION_AVAILABLE, SCHEMA_SKIP_REASON)
    def test_reversal_reference_and_cash_consumer_strict_schema_boundaries(self):
        from copy import deepcopy
        from jsonschema import ValidationError

        path = PREPARATION / "contracts/validate_contracts.py"
        spec = importlib.util.spec_from_file_location("ph210_contract_validation", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        schemas, registry = module.load()

        def validate(file, definition, value):
            pointer = definition.replace("~", "~0").replace("/", "~1")
            module.validator(
                {"$ref": schemas[file]["$id"] + "#/$defs/" + pointer}, registry
            ).validate(value)

        accepted = rejected = 0

        def accept(file, definition, value):
            nonlocal accepted
            validate(file, definition, value)
            accepted += 1

        def reject(file, definition, value):
            nonlocal rejected
            with self.assertRaises(ValidationError):
                validate(file, definition, value)
            rejected += 1

        accounting_event_ref = {"namespace": "core", "kind": "accounting_event", "id": 1, "version": 1}
        algorithm = {
            "algorithm_id": "fixture_cost",
            "version": 1,
            "rounding": "ROUND_HALF_UP",
            "input_digest": "0" * 64,
        }
        event = {
            "id": 2,
            "organization_id": 1,
            "version": 1,
            "status": "posted",
            "created_at": "2026-09-30T12:00:00+00:00",
            "source": accounting_event_ref,
            "event_type": "reversal",
            "occurred_on": "2026-09-30",
            "posting_period": "2026-09",
            "algorithm_version": algorithm,
        }
        accept("catalog-completeness.schema.json", "AccountingEvent", event)
        accept("catalog-completeness.schema.json", "AccountingEvent", dict(event, reverses_event_ref=None))
        accept(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(event, reverses_event_ref=accounting_event_ref),
        )
        reject(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(event, reverses_event_ref=dict(accounting_event_ref, namespace="cash")),
        )
        reject(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(event, reverses_event_ref=dict(accounting_event_ref, kind="ledger_posting")),
        )
        reject(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(event, reverses_event_ref={"namespace": "core", "kind": "accounting_event", "id": 1}),
        )
        reject(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(
                event,
                reverses_event_ref=dict(accounting_event_ref, client_approved=True),
            ),
        )
        reject(
            "catalog-completeness.schema.json",
            "AccountingEvent",
            dict(event, client_approved=True),
        )

        claim = {
            "id": 1,
            "version": 1,
            "organization_id": 1,
            "source": {"namespace": "cash", "kind": "cash_transaction", "id": 1, "version": 1},
            "consumer": "cash",
            "coverage_key": "bank_effect",
            "amount_cents": 10000,
            "event_ref": accounting_event_ref,
        }
        accept("entities.schema.json", "SourceConsumption", claim)
        reject("entities.schema.json", "SourceConsumption", dict(claim, consumer="unknown"))

        append_payload = {
            "event_type": "cost.confirmed",
            "entries": [
                {"book": "cost", "scope": {"kind": "project", "id": 1}, "amount_cents": 10000}
            ],
            "algorithm_version": algorithm,
        }
        reverse_payload = {"original_event_ref": accounting_event_ref, "reason": "synthetic correction"}
        for name, payload in (
            ("core.posting.append/v1", append_payload),
            ("core.posting.reverse/v1", reverse_payload),
        ):
            command = {
                "contract": name,
                "request_id": 1,
                "idempotency_key": "synthetic-command",
                "source": accounting_event_ref,
                "occurred_on": "2026-09-30",
                "posting_period": "2026-09",
                "payload": payload,
            }
            accept("commands.schema.json", name, command)
            for location in ("top", "payload"):
                bad = deepcopy(command)
                target = bad if location == "top" else bad["payload"]
                target["reverses_event_ref"] = accounting_event_ref
                with self.subTest(command=name, location=location):
                    reject("commands.schema.json", name, bad)
        self.assertEqual((accepted, rejected), (6, 10))
        print(
            "PH210_STRICT_CONTRACT_BOUNDARIES_EXECUTED "
            f"positive={accepted} rejected={rejected} no_runtime_business_claim"
        )


    @unittest.skipUnless(SCHEMA_VALIDATION_AVAILABLE, SCHEMA_SKIP_REASON)
    def test_cash_classification_posting_events_and_bank_effect_invariant(self):
        from copy import deepcopy
        from jsonschema import ValidationError

        path = PREPARATION / "contracts/validate_contracts.py"
        spec = importlib.util.spec_from_file_location("ph243_contract_validation", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        schemas, registry = module.load()

        def validate(file, definition, value):
            pointer = definition.replace("~", "~0").replace("/", "~1")
            module.validator(
                {"$ref": schemas[file]["$id"] + "#/$defs/" + pointer}, registry
            ).validate(value)

        accepted = rejected = 0

        def accept(file, definition, value):
            nonlocal accepted
            validate(file, definition, value)
            accepted += 1

        def reject(file, definition, value):
            nonlocal rejected
            with self.assertRaises(ValidationError):
                validate(file, definition, value)
            rejected += 1

        accounting_event_ref = {"namespace": "core", "kind": "accounting_event", "id": 7, "version": 1}
        classification_ref = {"namespace": "cash", "kind": "cash_classification", "id": 3, "version": 1}
        reversal_ref = {
            "namespace": "cash",
            "kind": "cash_classification_reversal",
            "id": 4,
            "version": 1,
        }

        recorded = {
            "event_id": 11,
            "topic": "cash.classification_recorded.v1",
            "schema_version": 1,
            "organization_id": 1,
            "source": classification_ref,
            "occurred_at": "2026-10-30T12:00:00+00:00",
            "payload": {
                "changed_refs": [classification_ref],
                "posting_event_refs": [accounting_event_ref],
            },
        }
        accept("events.schema.json", "cash.classification_recorded.v1", recorded)
        for bad_refs in ([], [accounting_event_ref, accounting_event_ref]):
            reject(
                "events.schema.json",
                "cash.classification_recorded.v1",
                dict(recorded, payload=dict(recorded["payload"], posting_event_refs=bad_refs)),
            )
        reject(
            "events.schema.json",
            "cash.classification_recorded.v1",
            dict(
                recorded,
                payload=dict(
                    recorded["payload"],
                    posting_event_refs=[dict(accounting_event_ref, namespace="cash")],
                ),
            ),
        )
        reject(
            "events.schema.json",
            "cash.classification_recorded.v1",
            dict(
                recorded,
                payload=dict(
                    recorded["payload"],
                    posting_event_refs=[dict(accounting_event_ref, kind="ledger_posting")],
                ),
            ),
        )
        reject(
            "events.schema.json",
            "cash.classification_recorded.v1",
            dict(
                recorded,
                payload=dict(
                    recorded["payload"],
                    posting_event_refs=[dict(accounting_event_ref, client_approved=True)],
                ),
            ),
        )

        reversed_event = {
            "event_id": 12,
            "topic": "cash.classification_reversed.v1",
            "schema_version": 1,
            "organization_id": 1,
            "source": reversal_ref,
            "occurred_at": "2026-10-30T12:30:00+00:00",
            "payload": {
                "changed_refs": [reversal_ref],
                "posting_event_refs": [accounting_event_ref],
                "original_classification_ref": classification_ref,
                "reversed_payroll_link_refs": [],
            },
        }
        accept("events.schema.json", "cash.classification_reversed.v1", reversed_event)
        for bad_refs in ([], [accounting_event_ref, accounting_event_ref]):
            reject(
                "events.schema.json",
                "cash.classification_reversed.v1",
                dict(reversed_event, payload=dict(reversed_event["payload"], posting_event_refs=bad_refs)),
            )
        reject(
            "events.schema.json",
            "cash.classification_reversed.v1",
            dict(
                reversed_event,
                payload=dict(
                    reversed_event["payload"],
                    posting_event_refs=[dict(accounting_event_ref, namespace="cash")],
                ),
            ),
        )
        reject(
            "events.schema.json",
            "cash.classification_reversed.v1",
            dict(
                reversed_event,
                payload=dict(
                    reversed_event["payload"],
                    posting_event_refs=[dict(accounting_event_ref, kind="ledger_posting")],
                ),
            ),
        )

        # Only the two classification events may carry exactly one posting ref;
        # the other five 0/0 cash events stay unchanged.
        for name in (
            "cash.internal_transfer_linked.v1",
            "cash.internal_transfer_reversed.v1",
            "cash.transaction_draft_saved.v1",
            "cash.party_matched.v1",
            "cash.correction_draft_saved.v1",
        ):
            posting = schemas["events.schema.json"]["$defs"][name]["properties"]["payload"][
                "properties"
            ]["posting_event_refs"]
            self.assertEqual((posting["minItems"], posting["maxItems"]), (0, 0), name)

        frozen_invariant = (
            "The original bank_effect claim is permanent. Each effective classified bank fact has "
            "exactly one effective original cash-book posting group: one AccountingEvent and one "
            "LedgerPosting with book=cash_in for an incoming cash transaction or cash_out for an "
            "outgoing one, origin=current, and scope.kind=funding_account pointing at the original "
            "FundingAccount; at any time a bank fact has at most one effective original posting "
            "group and no second effective group may represent the same bank cash. Classification "
            "reversal appends a mirror reversal AccountingEvent (reverses_event_ref pointing at the "
            "original event) and an equal negative LedgerPosting (reverses_posting_id pointing at "
            "the original posting) with book/scope identical to the original; it never creates a "
            "second positive bank flow. After a complete classification reversal, reclassification "
            "may append a new original posting group and must reuse the same bank_effect claim; it "
            "cannot create two concurrently effective original groups, cannot release the claim and "
            "cannot create or copy a second bank_effect claim. Cash allocation and its release "
            "create no AccountingEvent and no LedgerPosting. The bank_effect claim is permanent and "
            "classification, allocation, reversal and release never release bank_effect. Project "
            "allocation never reposts bank cash, and cash consumption is not clearance consumption. "
            "The consumer enum retains cost, management_revenue, obligation, clearance, "
            "invoice_allocation, forecast, opening and cash."
        )
        consumption = schemas["entities.schema.json"]["$defs"]["SourceConsumption"]
        invariants = consumption["x-service-invariants"]
        self.assertEqual(len(invariants), 8)
        self.assertEqual(invariants[5], frozen_invariant)
        joined = " ".join(invariants)
        for phrase in (
            "bank_effect",
            "never release bank_effect",
            "cash consumption is not clearance consumption",
            "does not branch",
            "at most one effective original posting group",
            "reuse the same bank_effect claim",
        ):
            self.assertIn(phrase, joined)
        self.assertNotIn("create no bank flow, AccountingEvent or LedgerPosting", joined)
        self.assertEqual(
            consumption["properties"]["consumer"]["enum"],
            [
                "cost",
                "management_revenue",
                "obligation",
                "clearance",
                "invoice_allocation",
                "forecast",
                "opening",
                "cash",
            ],
        )
        accepted += 1  # the exact frozen invariant assertion above

        ledger = schemas["catalog-completeness.schema.json"]["$defs"]["LedgerPosting"]
        self.assertIn(
            "Cash classification bridge ownership",
            " ".join(ledger["x-service-invariants"]),
        )
        print(
            "PH243_CLASSIFICATION_EVENT_AND_BANK_EFFECT_BOUNDARIES_EXECUTED "
            f"positive={accepted} rejected={rejected} no_runtime_business_claim"
        )


if __name__ == "__main__":
    unittest.main()
