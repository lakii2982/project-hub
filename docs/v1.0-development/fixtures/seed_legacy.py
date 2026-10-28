"""Executed by manage.py shell from git-archived frozen source only."""
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from core.models import (AuditEvent, CostRate, DomainEvent, Employee, LedgerEntry,
                         Membership, Organization, Project, ProjectMember, Timesheet)
from core.services import create_project, review, submit_time, verify_audit

target = Path(os.environ["PMO_DB"]).resolve()
assert Path(settings.DATABASES["default"]["NAME"]).resolve() == target
assert target.name == "legacy.sqlite3"
assert Organization.objects.count() == 0, "Requires newly migrated empty database"

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()

fixed_now = datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)
with patch("django.utils.timezone.now", return_value=fixed_now), transaction.atomic():
    org = Organization.objects.create(name="SYNTHETIC Legacy Enterprise", is_demo=True)
    members = {}
    for role in ("owner", "finance", "member"):
        # Unusable password. No login credentials, customer data or wages are copied.
        user = get_user_model().objects.create(username="synthetic_" + role, password="!")
        members[role] = Membership.objects.create(organization=org, user=user, role=role)
    employee = Employee.objects.create(organization=org, name="SYNTHETIC Employee",
                                       member=members["member"])
    CostRate.objects.create(employee=employee, effective_from=date(2025, 1, 1),
                            hourly_cents=100, basis="SYNTHETIC old annual rate")
    CostRate.objects.create(employee=employee, effective_from=date(2025, 2, 1),
                            hourly_cents=101, basis="SYNTHETIC revised annual rate")
    project = create_project(members["owner"], dict(code="SYN-LEGACY-01", name="SYNTHETIC Legacy Project",
        client="SYNTHETIC Client", manager=members["owner"], template=Project.Template.SOFTWARE,
        stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
        scope="Synthetic frozen costing fixture", acceptance_criteria="Synthetic check only"))
    ProjectMember.objects.create(project=project, member=members["member"])
    # Three one-minute lines expose old row rounding: 2+2+2=6 cents, not 5.
    # Fourth line proves selection by work date (101*31/60 -> 52 cents).
    cases = [(date(2025, 1, 10), 1, 100, 2)] * 3 + [(date(2025, 2, 10), 31, 101, 52)]
    for index, (day, minutes, expected_rate, expected_cost) in enumerate(cases, 1):
        data = dict(project=project, employee=employee, task=None, work_date=day,
                    minutes=minutes, note="SYNTHETIC legacy line %02d" % index)
        token = UUID("00000000-0000-4000-8000-%012d" % index)
        row = submit_time(members["member"], data, token)
        assert (row.hourly_rate_cents, row.cost_cents) == (expected_rate, expected_cost)
        review(members["finance"], "time", row.pk, "approve", "Synthetic independent review")
        # Same source + duplicate approval must keep exactly one posting and two audits.
        assert submit_time(members["member"], data, token).pk == row.pk
        review(members["finance"], "time", row.pk, "approve", "Synthetic independent review")
    assert verify_audit(org)

times = []
for row in Timesheet.objects.order_by("id"):
    times.append(dict(id=row.pk, project=row.project.code, source_token=str(row.token),
                      work_date=row.work_date.isoformat(), minutes=row.minutes,
                      hourly_rate_cents=row.hourly_rate_cents, cost_cents=row.cost_cents,
                      rate_basis=row.rate_basis, status=row.status,
                      submitted_role="member", reviewed_role="finance"))
entries = []
for entry in LedgerEntry.objects.order_by("id"):
    entries.append(dict(id=entry.pk, posting_key=entry.posting_key, timesheet_id=entry.timesheet_id,
                        document_id=entry.document_id, project=entry.project.code,
                        amount_cents=entry.amount_cents, book=entry.book, category=entry.category,
                        occurred_on=entry.occurred_on.isoformat(), reverses_id=entry.reverses_id))
events = []
for event in AuditEvent.objects.order_by("id"):
    events.append(dict(id=event.pk, action=event.action, entity=event.entity, entity_id=event.entity_id,
                       actor_id=event.actor_id, payload=event.payload, previous_hash=event.previous_hash,
                       event_hash=event.event_hash, created_at=event.created_at.isoformat()))
business = {"timesheets": times, "ledger": entries, "audit": events,
            "counts": {"organizations": Organization.objects.count(), "memberships": Membership.objects.count(),
                       "employees": Employee.objects.count(), "rates": CostRate.objects.count(),
                       "projects": Project.objects.count(), "timesheets": len(times),
                       "ledger": len(entries), "audit": len(events), "domain_events": DomainEvent.objects.count()},
            "total_cost_cents": sum(row["amount_cents"] for row in entries),
            "hashes": {"timesheets": digest(times), "ledger": digest(entries), "audit": digest(events)},
            "assertions": {"old_per_row_rounding_three_minutes_cents": 6,
                           "rate_effective_date_snapshot_cents": [100, 100, 100, 101],
                           "fourth_line_cents": 52, "duplicate_submit_review_creates_no_extra_rows": True}}
assert business["counts"] == {"organizations": 1, "memberships": 3, "employees": 1, "rates": 2,
                              "projects": 1, "timesheets": 4, "ledger": 4, "audit": 9, "domain_events": 9}
assert business["total_cost_cents"] == 58
(target.parent / "business.json").write_text(json.dumps(business, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
print(json.dumps({"synthetic_seed": "ok", "total_cost_cents": business["total_cost_cents"]}))
