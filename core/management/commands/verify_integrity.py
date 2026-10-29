import json
from django.core.management.base import BaseCommand, CommandError
from core.models import Organization, Project, LedgerEntry, Document, Timesheet
from core.services import verify_audit, plan_remaining

class Command(BaseCommand):
    help = "检查项目账来源、反向记录、现金计划未结额和审计链；不会修改数据"
    def add_arguments(self, parser): parser.add_argument("--json", action="store_true")
    def handle(self, *args, **options):
        errors = []
        for org in Organization.objects.all():
            if not verify_audit(org): errors.append(f"organization:{org.pk}:audit_chain")
        for entry in LedgerEntry.objects.select_related("document", "timesheet", "reverses", "project"):
            source = entry.document or entry.timesheet
            if not source or source.project_id != entry.project_id: errors.append(f"ledger:{entry.pk}:source_project")
            if entry.reverses_id:
                original = entry.reverses
                if (entry.amount_cents != -original.amount_cents or entry.book != original.book or entry.project_id != original.project_id
                    or entry.document_id != original.document_id or entry.timesheet_id != original.timesheet_id):
                    errors.append(f"ledger:{entry.pk}:reversal")
            else:
                expected = entry.timesheet.cost_cents if entry.timesheet_id else (
                    entry.document.amount_cents - entry.document.tax_cents if entry.document.kind == "expense" and entry.document.recoverable_tax else entry.document.amount_cents)
                expected_book = "cost" if entry.timesheet_id else {"expense": "cost", "receipt": "cash_in", "payment": "cash_out"}.get(entry.document.kind)
                if entry.amount_cents != expected or entry.book != expected_book: errors.append(f"ledger:{entry.pk}:source_amount_or_book")
        for model, field in [(Timesheet, "timesheet"), (Document, "document")]:
            sources = model.objects.filter(status__in=["approved", "reversed"])
            if model is Timesheet: sources = sources.filter(accounting_mode="legacy_snapshot")
            if model is Document: sources = sources.filter(kind__in=["expense", "receipt", "payment"])
            for source in sources:
                entries = LedgerEntry.objects.filter(**{field: source})
                if entries.filter(reverses__isnull=True).count() != 1: errors.append(f"{field}:{source.pk}:posting_count")
                if source.status == "reversed" and entries.filter(reverses__isnull=False).count() != 1: errors.append(f"{field}:{source.pk}:missing_reversal")
        for project in Project.objects.all():
            for plan in project.cash_plans.all():
                if plan_remaining(plan) < 0: errors.append(f"plan:{plan.pk}:over_settlement")
        from core.models import AccountingEvent, LedgerPosting, SourceConsumption, NonProjectDestination
        from pmo_plugins.costing.models import SalaryPool, AllocationRun
        from django.db.models import Sum
        for event in AccountingEvent.objects.select_related('claim', 'reverses_event'):
            postings = list(event.postings.all())
            if not postings or any(p.organization_id != event.organization_id for p in postings):
                errors.append(f"labor_event:{event.pk}:posting_org_or_empty")
            if event.claim.organization_id != event.organization_id or event.claim.source_id != event.labor_origin_ref.get('id') and str(event.claim.source_id) != event.labor_origin_ref.get('id'):
                errors.append(f"labor_event:{event.pk}:claim_source")
            if event.reverses_event_id:
                original = event.reverses_event
                if event.posting_period != original.posting_period or event.occurred_on != original.occurred_on or event.claim_id != original.claim_id or event.claim_effect_cents != -original.claim_effect_cents:
                    errors.append(f"labor_event:{event.pk}:reverse_event")
                if len(postings) != original.postings.count():
                    errors.append(f"labor_event:{event.pk}:reverse_count")
                for line in postings:
                    old = line.reverses_posting
                    if not old or old.accounting_event_id != original.pk or line.amount_cents != -old.amount_cents or (line.scope_kind, line.scope_id) != (old.scope_kind, old.scope_id):
                        errors.append(f"labor_posting:{line.pk}:reverse_line")
            for line in postings:
                valid = (Project.objects.filter(pk=line.scope_id, organization_id=event.organization_id).exists()
                         if line.scope_kind == 'project' and line.scope_id.isdigit() else
                         NonProjectDestination.objects.filter(pk=line.scope_id, organization_id=event.organization_id).exists()
                         if line.scope_kind == 'nonproject' else False)
                if not valid:
                    errors.append(f"labor_posting:{line.pk}:scope")
        for claim in SourceConsumption.objects.all():
            if not claim.events.filter(pk=claim.event_ref.get('id'), organization_id=claim.organization_id).exists():
                errors.append(f"labor_claim:{claim.pk}:original_event")
        for pool in SalaryPool.objects.exclude(status='draft'):
            if not pool.recognition_event_id or not pool.approved_salary_snapshot:
                errors.append(f"salary_pool:{pool.pk}:missing_confirmation")
                continue
            net = LedgerPosting.objects.filter(accounting_event__labor_origin_ref__id=str(pool.pk)).aggregate(total=Sum('amount_cents'))['total'] or 0
            expected = 0 if pool.status == 'superseded' else pool.total_comprehensive_cost_cents
            if net != expected:
                errors.append(f"salary_pool:{pool.pk}:company_cost_once")
        for run in AllocationRun.objects.all():
            if not run.accounting_event_id or sum(t.allocated_cents for t in run.targets.all()) != run.total_cents:
                errors.append(f"allocation:{run.pk}:target_total")
            if run.accounting_event_id and (run.accounting_event.postings.aggregate(total=Sum('amount_cents'))['total'] or 0) != 0:
                errors.append(f"allocation:{run.pk}:company_net_zero")
        result = {"ok": not errors, "errors": errors, "organizations": Organization.objects.count(), "entries": LedgerEntry.objects.count()}
        self.stdout.write(json.dumps(result, ensure_ascii=False) if options["json"] else ("完整性检查通过" if not errors else "\n".join(errors)))
        if errors: raise CommandError("项目账完整性检查未通过")
