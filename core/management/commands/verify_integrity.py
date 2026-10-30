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
            if event.source.get('namespace') == 'management_revenue':
                continue
            if event.source.get('namespace') != 'costing' or not isinstance(event.labor_origin_ref, dict):
                errors.append(f'accounting_event:{event.pk}:unknown_source')
                continue
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
        for claim in SourceConsumption.objects.exclude(consumer='settlement'):
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
        from core.revenue_accounting import revenue_integrity_errors
        errors.extend(revenue_integrity_errors())
        from pmo_plugins.cash.models import CashPartyMatch, CashTransaction, FundingAccount
        for account in FundingAccount.objects.filter(status='active'):
            if account.opening_on is None or account.opening_cents != 0:
                errors.append(f"funding_account:{account.pk}:opening")
        for transaction in CashTransaction.objects.all():
            if transaction.direction not in {'in', 'out'} or not 0 < transaction.gross_cents <= 10**12:
                errors.append(f"cash_transaction:{transaction.pk}:amount_or_direction")
            payee = transaction.payee or {}
            if payee.get('kind') == 'counterparty' and str(transaction.counterparty_id or '') != str(payee.get('id')):
                errors.append(f"cash_transaction:{transaction.pk}:counterparty")
            if payee.get('kind') not in {'unidentified', 'counterparty', 'own_account'}:
                errors.append(f"cash_transaction:{transaction.pk}:payee")
        for match in CashPartyMatch.objects.select_related('cash_transaction', 'original_match'):
            if match.cash_transaction.organization_id != match.organization_id:
                errors.append(f"cash_party_match:{match.pk}:transaction_org")
            if match.action == 'reverse' and match.original_match is None:
                errors.append(f"cash_party_match:{match.pk}:missing_original")
            if match.original_match is not None and (
                    match.original_match.organization_id != match.organization_id
                    or match.original_match.cash_transaction_id != match.cash_transaction_id
                    or match.original_match.action != 'identify'):
                errors.append(f"cash_party_match:{match.pk}:original")
        from pmo_plugins.settlement.models import OpenItem, Settlement
        for settlement in Settlement.objects.filter(status='active'):
            items = list(settlement.items.all())
            if not items:
                errors.append(f"settlement:{settlement.pk}:no_partitions")
            if sum(item.gross_cents for item in items) != settlement.gross_cents:
                errors.append(f"settlement:{settlement.pk}:partition_total")
            if settlement.gross_cents != settlement.net_cents + settlement.tax_cents:
                errors.append(f"settlement:{settlement.pk}:net_tax")
            if any(item.organization_id != settlement.organization_id
                   or item.project_id != settlement.project_id or item.direction != 'receivable'
                   for item in items):
                errors.append(f"settlement:{settlement.pk}:partition_scope")
            if not settlement.approved_version or not settlement.approval_audit_id or not settlement.approved_input_snapshot:
                errors.append(f"settlement:{settlement.pk}:missing_approval")
        for item in OpenItem.objects.all():
            if item.tax_cents > item.gross_cents or item.status != 'active':
                errors.append(f"open_item:{item.pk}:amount_or_status")
            if not item.due_date:
                errors.append(f"open_item:{item.pk}:missing_due_date")
        for claim in SourceConsumption.objects.filter(consumer='settlement'):
            parts = claim.coverage_key.split(':')
            if len(parts) != 3 or parts[0] != 'term' or parts[1] not in {'due', 'retention'}:
                errors.append(f"settlement_claim:{claim.pk}:key")
                continue
            found = OpenItem.objects.filter(settlement__contract_line_id=claim.source_id,
                settlement__organization_id=claim.organization_id,
                settlement__status='active', partition_kind=parts[1],
                partition_key=parts[1] + ':' + parts[2]).first()
            if found is None or found.gross_cents != claim.amount_cents:
                errors.append(f"settlement_claim:{claim.pk}:no_matching_partition")
        result = {"ok": not errors, "errors": errors, "organizations": Organization.objects.count(), "entries": LedgerEntry.objects.count()}
        self.stdout.write(json.dumps(result, ensure_ascii=False) if options["json"] else ("完整性检查通过" if not errors else "\n".join(errors)))
        if errors: raise CommandError("项目账完整性检查未通过")
