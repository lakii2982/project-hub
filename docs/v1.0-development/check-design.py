"""Read-only consistency check for the 1.0 design; grants no task authority."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
DESIGN = Path(__file__).resolve().parent


def check(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    registry = json.loads((DESIGN / "work-packages.json").read_text())
    trace = json.loads((DESIGN / "requirement-trace.json").read_text())
    source = (ROOT / "docs/v1.0-delivery-plan.md").read_text()
    original = {}
    for line in source.splitlines():
        cells = [x.strip() for x in line.split("|")[1:-1]]
        if cells and re.fullmatch(r"V1-[A-Z]+-\d+", cells[0]):
            check(cells[0] not in original, f"Duplicate source ID: {cells[0]}")
            original[cells[0]] = cells[1:]
    check(len(original) == 137, "Expected the reviewed 137-requirement scope")
    check(trace["product_plan_sha256"] == hashlib.sha256(source.encode()).hexdigest(),
          "Trace snapshot no longer matches the product plan")
    packs = registry["packages"]
    by_id = {p["id"]: p for p in packs}
    check(len(by_id) == len(packs) == 62, "Duplicate or changed work-package scope")
    check(sum(p["kind"] == "preparation" for p in packs) == 7, "Expected seven preparation packages")
    check(registry["global_dispatch_allowed"] is False, "Design must not authorize dispatch")
    owners = {}
    for p in packs:
        check(p["design_state"] == "design_candidate" and p["dispatch_allowed"] is False,
              f"Design package became dispatchable: {p['id']}")
        check(not p["ph_task_ids"], "PH promotion requires a reviewed design schema/authority transition")
        for field in ("entry_checks", "exit_checks", "specific_deliverables", "contract_to_freeze"):
            check(bool(p[field]), f"Missing {field}: {p['id']}")
        check(len(set(p["depends_on"])) == len(p["depends_on"]), "Duplicate dependency")
        for dep in p["depends_on"]:
            check(dep in by_id and dep != p["id"], f"Invalid dependency: {p['id']} -> {dep}")
        if p["kind"] == "preparation":
            check("related_decisions_and_schema_frozen" not in p["entry_checks"],
                  f"Preparation cannot require its own future schema: {p['id']}")
        expected_cross_domain = len(p["domains"]) > 1 or len(p["contracts"]) > 1
        check(p["verification_design"]["cross_domain_checks"] == expected_cross_domain,
              f"Cross-domain applicability mismatch: {p['id']}")
        check(p["scope_candidate"]["status"] == "proposal_not_a_BASE_allowlist", "Scope is only a proposal")
        for req in p["requirement_ids"]:
            check(req in original and req not in owners, f"Unknown or duplicate primary owner: {req}")
            owners[req] = p["id"]
    check(set(owners) == set(original), "Requirements missing a primary package")
    reached, active = set(), set()

    def visit(package_id):
        check(package_id not in active, f"Dependency cycle at {package_id}")
        if package_id in reached:
            return
        active.add(package_id)
        for dep in by_id[package_id]["depends_on"]:
            visit(dep)
        active.remove(package_id)
        reached.add(package_id)

    visit("WP-FINAL-RELEASE")
    check(reached == set(by_id), "Some packages are not included in final closure")
    order = registry["topological_order"]
    check(len(order) == len(set(order)) == len(packs) and set(order) == set(by_id), "Invalid package order")
    positions = {name: index for index, name in enumerate(order)}
    check(all(positions[d] < positions[p["id"]] for p in packs for d in p["depends_on"]),
          "Topological order violates dependencies")
    rows = trace["requirements"]
    by_req = {r["requirement_id"]: r for r in rows}
    check(len(by_req) == len(rows) == 137 and set(by_req) == set(original), "Trace requirement set differs")
    uats = {u["uat_id"]: u for u in trace["uat_scenarios"]}
    check(len(trace["uat_scenarios"]) == 18 and set(uats) == {f"UAT-{i:02}" for i in range(1, 19)},
          "UAT set differs from reviewed scope")
    for req, row in by_req.items():
        check(row["primary_package"] == owners[req], f"Trace primary owner differs: {req}")
        check([row["title"], row["business_rule"], row["acceptance_expectation"]] == original[req],
              f"Trace text differs from product source: {req}")
        check(row["specialized_acceptance"] == original[req][2] and not row["evidence_refs"],
              f"Design cannot claim execution evidence: {req}")
        check(not row["ph_task_ids"], "Design has not been promoted into live PH contracts")
        for uat in row["uat_ids"]:
            check(uat in uats and req in uats[uat]["requirement_ids"], f"Inconsistent UAT relation: {req}")
    for uat, row in uats.items():
        check(bool(row["requirement_ids"]) and not row["evidence_refs"], "Missing UAT mapping or claimed execution")
        check(row["fixture_and_command_tests_owner"] in by_id and row["integration_owner_package"] in by_id,
              f"Unknown UAT owner: {uat}")
        for req in row["requirement_ids"]:
            check(req in by_req and uat in by_req[req]["uat_ids"], f"UAT reverse mapping differs: {uat}")
    for data in (registry, trace):
        for field in ("authority", "product_plan"):
            check((DESIGN / data[field]).resolve().exists(), f"Broken metadata reference: {field}")
    for file in sorted(DESIGN.glob("*.md")) + [ROOT / "docs/v1.0-delivery-plan.md", ROOT / "docs/v1.0-feature-list.md"]:
        text = file.read_text()
        check(text.count("```") % 2 == 0, f"Unclosed fence: {file.name}")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if not link.startswith(("http://", "https://", "#")):
                check((file.parent / link.split("#")[0]).resolve().exists(), f"Broken link in {file.name}: {link}")
        widths, in_code = [], False
        for line in text.splitlines() + [""]:
            if line.startswith("```"):
                in_code = not in_code
            if line.startswith("|") and not in_code:
                widths.append(line.count("|"))
            elif widths:
                check(len(set(widths)) == 1, f"Inconsistent table in {file.name}")
                widths = []
    print(json.dumps({"result": "pass", "scope": "design_consistency_only", "packages": len(packs),
                      "preparation_packages": 7, "requirements": len(original), "uat_scenarios": len(uats),
                      "stages": dict(sorted(Counter(p["stage"] for p in packs).items())),
                      "dispatch_authorized": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
