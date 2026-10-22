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
            if model is Document: sources = sources.filter(kind__in=["expense", "receipt", "payment"])
            for source in sources:
                entries = LedgerEntry.objects.filter(**{field: source})
                if entries.filter(reverses__isnull=True).count() != 1: errors.append(f"{field}:{source.pk}:posting_count")
                if source.status == "reversed" and entries.filter(reverses__isnull=False).count() != 1: errors.append(f"{field}:{source.pk}:missing_reversal")
        for project in Project.objects.all():
            for plan in project.cash_plans.all():
                if plan_remaining(plan) < 0: errors.append(f"plan:{plan.pk}:over_settlement")
        result = {"ok": not errors, "errors": errors, "organizations": Organization.objects.count(), "entries": LedgerEntry.objects.count()}
        self.stdout.write(json.dumps(result, ensure_ascii=False) if options["json"] else ("完整性检查通过" if not errors else "\n".join(errors)))
        if errors: raise CommandError("项目账完整性检查未通过")
