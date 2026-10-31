import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from core.models import Organization, Project, LedgerEntry, Document, Timesheet
from core.services import verify_audit, plan_remaining

class Command(BaseCommand):
    help = "检查项目账来源、反向记录、现金计划未结额和审计链；不会修改数据"
    def add_arguments(self, parser): parser.add_argument("--json", action="store_true")
    def handle(self, *args, **options):
        errors = []
        organizations = list(Organization.objects.all())
        for org in organizations:
            if not verify_audit(org): errors.append(f"organization:{org.pk}:audit_chain")
        ledger_entries = list(LedgerEntry.objects.select_related("document", "timesheet", "reverses", "project"))
        for entry in ledger_entries:
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
        projects = list(Project.objects.all())
        for project in projects:
            for plan in project.cash_plans.all():
                if plan_remaining(plan) < 0: errors.append(f"plan:{plan.pk}:over_settlement")
        from core.models import AccountingEvent, LedgerPosting, SourceConsumption, NonProjectDestination
        from pmo_plugins.costing.models import SalaryPool, AllocationRun
        from django.db.models import Count, Sum
        for event in AccountingEvent.objects.select_related('claim', 'reverses_event'):
            namespace = event.source.get('namespace')
            if namespace == 'management_revenue':
                continue
            if namespace == 'cash':
                # Cash classification bridge events are validated in the dedicated cash block.
                continue
            if namespace == 'costing' and event.source.get('kind') == 'bank_charge':
                # Bank-fee cost facts are validated in the dedicated bank-fee branch below.
                continue
            if namespace != 'costing' or not isinstance(event.labor_origin_ref, dict):
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
        from pmo_plugins.contracts.models import Counterparty
        from core.models import Employee
        for account in FundingAccount.objects.all():
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
            if payee.get('kind') == 'counterparty' and not Counterparty.objects.filter(
                    pk=payee.get('id'), organization_id=transaction.organization_id).exists():
                errors.append(f"cash_transaction:{transaction.pk}:counterparty_missing")
            if payee.get('kind') == 'own_account' and not FundingAccount.objects.filter(
                    pk=payee.get('id'), organization_id=transaction.organization_id).exists():
                errors.append(f"cash_transaction:{transaction.pk}:own_account_missing")
        from core.models import DomainEvent as _CashDomainEvent
        from pmo_plugins.cash.models import CashReceipt as _CashReceipt
        for transaction in CashTransaction.objects.all():
            canonical = _CashDomainEvent.objects.filter(
                organization_id=transaction.organization_id,
                topic='cash.transaction_recorded.v1',
                data__source__id=str(transaction.pk))
            reference = transaction.transaction_ref
            if transaction.status == 'posted':
                if canonical.count() != 1:
                    errors.append(f"cash_transaction:{transaction.pk}:posted_event_count")
                else:
                    data = canonical.get().data or {}
                    payload = data.get('payload') or {}
                    if (data.get('schema_version') != 1
                            or data.get('topic') != 'cash.transaction_recorded.v1'
                            or data.get('organization_id') != str(transaction.organization_id)
                            or data.get('source') != reference
                            or data.get('event_id') != str(canonical.get().event_id)
                            or payload.get('changed_refs') != [reference]
                            or payload.get('posting_event_refs') != []):
                        errors.append(f"cash_transaction:{transaction.pk}:posted_event_envelope")
                if transaction.transaction_date > timezone.localdate():
                    errors.append(f"cash_transaction:{transaction.pk}:future_posted")
                if not _CashReceipt.objects.filter(
                        organization_id=transaction.organization_id, action='cash_transaction.post',
                        result__id=str(transaction.pk)).exists():
                    errors.append(f"cash_transaction:{transaction.pk}:posting_receipt")
            elif canonical.exists():
                errors.append(f"cash_transaction:{transaction.pk}:draft_has_event")
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
            payee = match.identified_payee or {}
            if match.action == 'identify':
                if payee.get('kind') == 'counterparty' and not Counterparty.objects.filter(
                        pk=payee.get('id'), organization_id=match.organization_id).exists():
                    errors.append(f"cash_party_match:{match.pk}:counterparty_missing")
                if payee.get('kind') == 'employee' and not Employee.objects.filter(
                        pk=payee.get('id'), organization_id=match.organization_id).exists():
                    errors.append(f"cash_party_match:{match.pk}:employee_missing")
                if payee.get('kind') == 'own_account' and not FundingAccount.objects.filter(
                        pk=payee.get('id'), organization_id=match.organization_id).exists():
                    errors.append(f"cash_party_match:{match.pk}:own_account_missing")
        for original in CashPartyMatch.objects.filter(action='identify'):
            if original.reversals.count() > 1:
                errors.append(f"cash_party_match:{original.pk}:reverse_count")
        # PH-240 refunds and bank returns: append-only reverse cash facts. Each fact points at
        # same-org posted cash, carries exactly one canonical event, and the derived coverage of
        # every original cash fact never goes negative. No ledger/accounting/unsettled rows.
        from pmo_plugins.cash.models import Refund as _Refund
        from pmo_plugins.cash.models import BankReturn as _BankReturn
        from pmo_plugins.settlement.models import Clearance as _RefundClearance
        refund_topic = 'cash.refunded.v1'
        return_topic = 'cash.returned.v1'

        def _refund_canonical_errors(topic, ref, changed_refs, object_ref, organization_id,
                                     label):
            rows = _CashDomainEvent.objects.filter(
                organization_id=organization_id, topic=topic,
                data__source__id=str(object_ref.pk))
            if rows.count() != 1:
                return [f"{label}:{object_ref.pk}:{topic}:event_count"]
            data = rows.get().data or {}
            payload = data.get('payload') or {}
            if (data.get('schema_version') != 1 or data.get('topic') != topic
                    or data.get('organization_id') != str(organization_id)
                    or data.get('source') != ref
                    or payload.get('changed_refs') != changed_refs
                    or payload.get('posting_event_refs') != []):
                return [f"{label}:{object_ref.pk}:{topic}:event_envelope"]
            return []

        for refund in _Refund.objects.select_related(
                'original_cash_transaction', 'refund_cash_transaction'):
            original = refund.original_cash_transaction
            new_cash = refund.refund_cash_transaction
            if (refund.organization_id != original.organization_id
                    or refund.organization_id != new_cash.organization_id
                    or refund.original_cash_ref != original.transaction_ref):
                errors.append(f"refund:{refund.pk}:refs")
            if original.status != 'posted' or original.direction != 'in':
                errors.append(f"refund:{refund.pk}:original_cash")
            if new_cash.status != 'posted' or new_cash.direction != 'out':
                errors.append(f"refund:{refund.pk}:refund_cash")
            if (new_cash.transaction_date != refund.refund_date
                    or new_cash.gross_cents != refund.gross_cents):
                errors.append(f"refund:{refund.pk}:amount_or_date")
            errors.extend(_refund_canonical_errors(
                refund_topic, refund.refund_ref,
                [refund.refund_ref, new_cash.transaction_ref], refund,
                refund.organization_id, 'refund'))
        for bank_return in _BankReturn.objects.select_related(
                'original_cash_transaction', 'return_cash_transaction'):
            original = bank_return.original_cash_transaction
            new_cash = bank_return.return_cash_transaction
            if (bank_return.organization_id != original.organization_id
                    or bank_return.organization_id != new_cash.organization_id
                    or bank_return.original_cash_ref != original.transaction_ref):
                errors.append(f"bank_return:{bank_return.pk}:refs")
            if original.status != 'posted':
                errors.append(f"bank_return:{bank_return.pk}:original_cash")
            expected_direction = 'in' if original.direction == 'out' else 'out'
            if new_cash.status != 'posted' or new_cash.direction != expected_direction:
                errors.append(f"bank_return:{bank_return.pk}:return_cash")
            if (new_cash.transaction_date != bank_return.return_date
                    or new_cash.gross_cents != bank_return.gross_cents):
                errors.append(f"bank_return:{bank_return.pk}:amount_or_date")
            errors.extend(_refund_canonical_errors(
                return_topic, bank_return.bank_return_ref,
                [bank_return.bank_return_ref, new_cash.transaction_ref], bank_return,
                bank_return.organization_id, 'bank_return'))
        cleared_by_cash = {
            str(row['cash_transaction_id']): row['total'] for row in
            _RefundClearance.objects.filter(status='active').values('cash_transaction_id')
            .annotate(total=Sum('cleared_cents'))}
        refunded_by_cash = {
            str(row['original_cash_transaction_id']): row['total'] for row in
            _Refund.objects.values('original_cash_transaction_id')
            .annotate(total=Sum('gross_cents'))}
        returned_by_cash = {
            str(row['original_cash_transaction_id']): row['total'] for row in
            _BankReturn.objects.values('original_cash_transaction_id')
            .annotate(total=Sum('gross_cents'))}
        for transaction in CashTransaction.objects.all():
            refundable = (transaction.gross_cents
                          - cleared_by_cash.get(str(transaction.pk), 0)
                          - refunded_by_cash.get(str(transaction.pk), 0)
                          - returned_by_cash.get(str(transaction.pk), 0))
            if refundable < 0:
                errors.append(f"cash_transaction:{transaction.pk}:refund_coverage_negative")
        for duplicate in CashTransaction.objects.values(
                'organization_id', 'funding_account_id', 'external_source_key').annotate(
                count=Count('id')).filter(count__gt=1):
            errors.append('cash_transaction:duplicate_external_source_key')
        # PH-240 guard is scoped to refund/bank-return provenance only. PH-243/PH-239 freeze
        # the legitimate cash classification bridge (one AccountingEvent + one
        # cash_in/cash_out funding_account posting per effective classification), so a global
        # 'cash namespace must not post' rule would reject valid PH-239 rows. Reject only
        # events sourced at a refund/return fact or explicitly tagged refund/bank_return;
        # classification-bridge events use source kind cash_classification and stay allowed.
        from django.db.models import Q
        refund_produced_cash_ids = [
            str(row['refund_cash_transaction_id'])
            for row in _Refund.objects.values('refund_cash_transaction_id')]
        refund_produced_cash_ids += [
            str(row['return_cash_transaction_id'])
            for row in _BankReturn.objects.values('return_cash_transaction_id')]
        refund_ledger_events = AccountingEvent.objects.filter(
            source__namespace='cash').filter(
            Q(source__kind__in=['refund', 'bank_return'])
            | Q(source__kind='cash_transaction', source__id__in=refund_produced_cash_ids))
        if refund_ledger_events.exists():
            errors.append('cash_transaction:unexpected_refund_accounting_event')
        if LedgerPosting.objects.filter(accounting_event__in=refund_ledger_events).exists():
            errors.append('cash_transaction:unexpected_refund_cash_posting')
        # PH-239 cash classification facts + cash book bridge (batch queries only).
        from core.models import DomainEvent as _ClassificationEvent
        from pmo_plugins.cash.models import (CashClassification, CashClassificationReversal)
        recorded_topic = 'cash.classification_recorded.v1'
        reversed_topic = 'cash.classification_reversed.v1'
        for row in CashClassification.objects.filter(status='active').values(
                'cash_transaction_id').annotate(count=Count('id')).filter(count__gt=1):
            errors.append(
                f"cash_transaction:{row['cash_transaction_id']}:multiple_active_classification")
        recorded_events = {}
        for row in _ClassificationEvent.objects.filter(topic=recorded_topic).values('data'):
            data = row['data'] or {}
            source_id = str((data.get('source') or {}).get('id'))
            recorded_events.setdefault(source_id, []).append(data)
        reversed_events = {}
        for row in _ClassificationEvent.objects.filter(topic=reversed_topic).values('data'):
            data = row['data'] or {}
            source_id = str((data.get('source') or {}).get('id'))
            reversed_events.setdefault(source_id, []).append(data)
        cash_events = {}
        for event in AccountingEvent.objects.filter(source__namespace='cash').values(
                'id', 'version', 'organization_id', 'source', 'event_type', 'claim_id',
                'claim_effect_cents', 'reverses_event_id', 'occurred_on', 'posting_period'):
            cash_events[str(event['id'])] = event
        postings_by_event = {}
        for posting in LedgerPosting.objects.filter(
                accounting_event__source__namespace='cash').values(
                'id', 'accounting_event_id', 'book', 'amount_cents', 'scope_kind', 'scope_id',
                'reverses_posting_id'):
            postings_by_event.setdefault(str(posting['accounting_event_id']), []).append(posting)
        classification_event_map = {}
        reversal_event_map = {}
        for event in cash_events.values():
            source = event['source'] or {}
            if source.get('kind') == 'cash_classification' and source.get('id'):
                classification_event_map.setdefault(str(source['id']), []).append(event)
            if source.get('kind') == 'cash_classification_reversal' and source.get('id'):
                reversal_event_map.setdefault(str(source['id']), []).append(event)
        # Mirror reversals hang their negative posting on a separate accounting event whose
        # source is the reversal; attribute both sides to the original classification.
        event_to_classification = {}
        for event in cash_events.values():
            source = event['source'] or {}
            if source.get('kind') == 'cash_classification' and source.get('id'):
                event_to_classification[str(event['id'])] = str(source['id'])
        for event in cash_events.values():
            source = event['source'] or {}
            if (source.get('kind') == 'cash_classification_reversal'
                    and event['reverses_event_id'] is not None):
                original_id = event_to_classification.get(str(event['reverses_event_id']))
                if original_id is not None:
                    event_to_classification[str(event['id'])] = original_id
        net_by_classification = {}
        for posting in LedgerPosting.objects.filter(
                accounting_event__source__namespace='cash').values(
                'accounting_event_id', 'amount_cents'):
            classification_id = event_to_classification.get(
                str(posting['accounting_event_id']))
            if classification_id is not None:
                net_by_classification[classification_id] = (
                    net_by_classification.get(classification_id, 0)
                    + posting['amount_cents'])
        active_by_tx = {
            str(row['cash_transaction_id']): row['count'] for row in
            CashClassification.objects.filter(status='active').values(
                'cash_transaction_id').annotate(count=Count('id'))}
        classification_tx = {
            str(row['pk']): str(row['cash_transaction_id']) for row in
            CashClassification.objects.values('pk', 'cash_transaction_id')}
        net_by_tx = {}
        for classification_id, net in net_by_classification.items():
            tx_id = classification_tx.get(classification_id)
            if tx_id is not None:
                net_by_tx[tx_id] = net_by_tx.get(tx_id, 0) + net
        # Prefetch reversal links once so the classification/reversal loops stay constant-query.
        reversals_by_classification = {}
        for row in CashClassificationReversal.objects.values('pk', 'classification_id'):
            reversals_by_classification.setdefault(
                str(row['classification_id']), []).append(row['pk'])
        classification_by_id = {}
        for classification in CashClassification.objects.select_related('cash_transaction'):
            classification_by_id[str(classification.pk)] = classification
            transaction = classification.cash_transaction
            label = f"cash_classification:{classification.pk}"
            reference = classification.classification_ref
            if classification.organization_id != transaction.organization_id:
                errors.append(f"{label}:transaction_org")
            events = classification_event_map.get(str(classification.pk), [])
            if len(events) != 1:
                errors.append(f"{label}:bridge_event_count")
                bridge_event = None
            else:
                bridge_event = events[0]
                postings = postings_by_event.get(str(bridge_event['id']), [])
                expected_book = 'cash_in' if transaction.direction == 'in' else 'cash_out'
                if (bridge_event['organization_id'] != classification.organization_id
                        or bridge_event['event_type'] != 'cash.classification'
                        or bridge_event['claim_effect_cents'] != transaction.gross_cents
                        or bridge_event['occurred_on'] != transaction.transaction_date
                        or bridge_event['posting_period']
                        != transaction.transaction_date.strftime('%Y-%m')
                        or bridge_event['reverses_event_id'] is not None):
                    errors.append(f"{label}:bridge_event")
                if len(postings) != 1:
                    errors.append(f"{label}:posting_count")
                elif (postings[0]['book'] != expected_book
                      or postings[0]['amount_cents'] != transaction.gross_cents
                      or postings[0]['scope_kind'] != 'funding_account'
                      or postings[0]['scope_id'] != str(transaction.funding_account_id)
                      or postings[0]['reverses_posting_id'] is not None):
                    errors.append(f"{label}:posting")
            recorded = recorded_events.get(str(classification.pk), [])
            if len(recorded) != 1:
                errors.append(f"{label}:recorded_event_count")
            else:
                data = recorded[0]
                payload = data.get('payload') or {}
                refs = payload.get('posting_event_refs') or []
                expected_ref = None if bridge_event is None else {
                    'namespace': 'core', 'kind': 'accounting_event',
                    'id': str(bridge_event['id']), 'version': bridge_event['version']}
                if (data.get('schema_version') != 1 or data.get('topic') != recorded_topic
                        or data.get('organization_id') != str(classification.organization_id)
                        or data.get('source') != reference
                        or payload.get('changed_refs') != [reference]
                        or len(refs) != 1 or expected_ref is None or refs[0] != expected_ref):
                    errors.append(f"{label}:recorded_event_envelope")
            expected_net = (transaction.gross_cents
                            if classification.status == 'active' else 0)
            if net_by_classification.get(str(classification.pk), 0) != expected_net:
                errors.append(f"{label}:net")
            reversals = reversals_by_classification.get(str(classification.pk), [])
            if classification.status == 'active' and reversals:
                errors.append(f"{label}:active_has_reversal")
            if classification.status == 'reversed' and len(reversals) != 1:
                errors.append(f"{label}:reversal_count")
        for reversal in CashClassificationReversal.objects.all():
            classification = classification_by_id.get(str(reversal.classification_id))
            if classification is None:
                errors.append(f"cash_classification_reversal:{reversal.pk}:missing_classification")
                continue
            label = f"cash_classification_reversal:{reversal.pk}"
            if (reversal.organization_id != classification.organization_id
                    or reversal.cash_transaction_id != classification.cash_transaction_id):
                errors.append(f"{label}:scope")
            events = reversal_event_map.get(str(reversal.pk), [])
            original_events = classification_event_map.get(str(classification.pk), [])
            original = original_events[0] if len(original_events) == 1 else None
            if len(events) != 1:
                errors.append(f"{label}:bridge_event_count")
                bridge_event = None
            else:
                bridge_event = events[0]
                postings = postings_by_event.get(str(bridge_event['id']), [])
                original_postings = (postings_by_event.get(str(original['id']), [])
                                     if original is not None else [])
                if (bridge_event['organization_id'] != reversal.organization_id
                        or bridge_event['event_type'] != 'cash.classification.reversed'
                        or bridge_event['claim_effect_cents']
                        != -classification.cash_transaction.gross_cents
                        or bridge_event['reverses_event_id']
                        != (original['id'] if original is not None else None)):
                    errors.append(f"{label}:bridge_event")
                if len(postings) != 1:
                    errors.append(f"{label}:posting_count")
                elif len(original_postings) != 1:
                    errors.append(f"{label}:posting")
                elif (postings[0]['book'] != original_postings[0]['book']
                      or postings[0]['amount_cents'] != -original_postings[0]['amount_cents']
                      or postings[0]['scope_kind'] != original_postings[0]['scope_kind']
                      or postings[0]['scope_id'] != original_postings[0]['scope_id']
                      or postings[0]['reverses_posting_id'] != original_postings[0]['id']):
                    errors.append(f"{label}:posting")
            reversed_canonical = reversed_events.get(str(reversal.pk), [])
            if len(reversed_canonical) != 1:
                errors.append(f"{label}:reversed_event_count")
            else:
                data = reversed_canonical[0]
                payload = data.get('payload') or {}
                refs = payload.get('posting_event_refs') or []
                expected_ref = None if bridge_event is None else {
                    'namespace': 'core', 'kind': 'accounting_event',
                    'id': str(bridge_event['id']), 'version': bridge_event['version']}
                if (data.get('schema_version') != 1 or data.get('topic') != reversed_topic
                        or data.get('organization_id') != str(reversal.organization_id)
                        or data.get('source') != reversal.reversal_ref
                        or payload.get('changed_refs') != [reversal.reversal_ref]
                        or len(refs) != 1 or expected_ref is None or refs[0] != expected_ref
                        or payload.get('original_classification_ref')
                        != classification.classification_ref
                        or payload.get('reversed_payroll_link_refs') != []):
                    errors.append(f"{label}:reversed_event_envelope")
        cash_claims = list(SourceConsumption.objects.filter(consumer='cash').values(
            'id', 'organization_id', 'source_id', 'coverage_key', 'event_ref', 'source'))
        claims_by_tx = {}
        for claim in cash_claims:
            claims_by_tx.setdefault(str(claim['source_id']), []).append(claim)
        claim_net = {
            str(row['id']): row['total'] for row in
            SourceConsumption.objects.filter(consumer='cash').values('id').annotate(
                total=Sum('events__claim_effect_cents'))}
        for transaction in CashTransaction.objects.all():
            label = f"cash_transaction:{transaction.pk}"
            expected_net = (transaction.gross_cents if active_by_tx.get(
                str(transaction.pk), 0) == 1 else 0)
            if net_by_tx.get(str(transaction.pk), 0) != expected_net:
                errors.append(f"{label}:classification_book_net")
            claims = claims_by_tx.get(str(transaction.pk), [])
            if len(claims) > 1:
                errors.append(f"{label}:duplicate_bank_effect_claim")
            if active_by_tx.get(str(transaction.pk), 0) and not claims:
                errors.append(f"{label}:missing_bank_effect_claim")
            if claims:
                claim = claims[0]
                if (claim['coverage_key'] != 'bank_effect'
                        or claim['organization_id'] != transaction.organization_id
                        or str((claim['source'] or {}).get('id')) != str(transaction.pk)):
                    errors.append(f"cash_claim:{claim['id']}:shape")
                if claim_net.get(str(claim['id']), 0) != expected_net:
                    errors.append(f"cash_claim:{claim['id']}:net")
                if str((claim['event_ref'] or {}).get('id')) not in cash_events:
                    errors.append(f"cash_claim:{claim['id']}:original_event")
        if AccountingEvent.objects.filter(source__namespace='cash').exclude(
                event_type__in=['cash.classification', 'cash.classification.reversed']).exists():
            errors.append('cash_transaction:unexpected_cash_event_type')
        if LedgerPosting.objects.filter(accounting_event__source__namespace='cash').exclude(
                book__in=['cash_in', 'cash_out']).exists():
            errors.append('cash_transaction:unexpected_cash_posting_book')
        if SourceConsumption.objects.filter(consumer='cash').exclude(
                coverage_key='bank_effect').exists():
            errors.append('cash_transaction:unexpected_source_consumption')
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

        from pmo_plugins.settlement.models import Clearance
        from pmo_plugins.cash.models import CashTransaction as _ClearanceCash
        for clearance in Clearance.objects.select_related('open_item'):
            item = clearance.open_item
            if item.organization_id != clearance.organization_id:
                errors.append(f"clearance:{clearance.pk}:open_item_org")
            cash = _ClearanceCash.objects.filter(pk=clearance.cash_transaction_id,
                organization_id=clearance.organization_id).first()
            if cash is None or cash.status != 'posted' or cash.direction != 'in':
                errors.append(f"clearance:{clearance.pk}:cash")
            if clearance.status not in {'active', 'reversed'} or clearance.cleared_cents <= 0:
                errors.append(f"clearance:{clearance.pk}:status_or_amount")
        for item in OpenItem.objects.all():
            active = item.clearances.filter(status='active')
            total = sum(row.cleared_cents for row in active)
            if total > item.gross_cents:
                errors.append(f"open_item:{item.pk}:over_cleared")
        for cash in _ClearanceCash.objects.filter(status='posted'):
            total = sum(row.cleared_cents for row in Clearance.objects.filter(
                cash_transaction_id=cash.pk, status='active'))
            if total > cash.gross_cents:
                errors.append(f"cash_transaction:{cash.pk}:over_cleared")
        from core.models import DomainEvent as _ClearanceEvent
        def _clearance_envelope(event, reference, label, errors, clearance):
            data = event.data or {}
            payload = data.get('payload') or {}
            if (data.get('schema_version') != 1 or data.get('source') != reference
                    or data.get('organization_id') != str(clearance.organization_id)
                    or payload.get('changed_refs') != [reference]
                    or payload.get('posting_event_refs') != []):
                errors.append(f"clearance:{clearance.pk}:{label}_envelope")
        for clearance in Clearance.objects.all():
            reference = {'namespace': 'settlement', 'kind': 'clearance',
                         'id': str(clearance.pk), 'version': clearance.version}
            cleared = _ClearanceEvent.objects.filter(organization_id=clearance.organization_id,
                topic='settlement.cleared.v1', data__source__id=str(clearance.pk))
            if cleared.count() != 1:
                errors.append(f"clearance:{clearance.pk}:cleared_event_count")
            else:
                _clearance_envelope(cleared.first(), reference, 'cleared', errors, clearance)
            reversed_events = _ClearanceEvent.objects.filter(
                organization_id=clearance.organization_id,
                topic='settlement.clearance_reversed.v1',
                data__source__id=str(clearance.pk))
            if clearance.status == 'reversed':
                if reversed_events.count() != 1:
                    errors.append(f"clearance:{clearance.pk}:reversed_event_count")
                else:
                    _clearance_envelope(reversed_events.first(), reference, 'reversed', errors,
                                        clearance)
            elif reversed_events.exists():
                errors.append(f"clearance:{clearance.pk}:active_has_reversed_event")
        # Clearance is a derived settlement fact: it must never post to the ledger or write an
        # accounting event (LedgerPosting scopes are already constrained to project/nonproject).
        if AccountingEvent.objects.filter(
                source__namespace__in=['settlement', 'clearance']).exists():
            errors.append('clearance:unexpected_accounting_event')
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
        # ----- PH-241 advance offset: append-only derived settlement fact, no ledger/accounting
        # write, no canonical offset topic; advance cash must be posted/in and advance-classified,
        # and a cash fact may not be both cleared and offset (scheme a mutual exclusion).
        from pmo_plugins.settlement.models import Offset as _Offset
        from pmo_plugins.cash.models import CashTransaction as _OffsetCash
        from pmo_plugins.cash.models import CashClassification as _OffsetClassification
        offset_rows = list(_Offset.objects.values(
            'pk', 'organization_id', 'project_id', 'open_item_id',
            'advance_cash_transaction_id', 'advance_ref', 'offset_cents', 'status'))
        if offset_rows:
            offset_advance_ids = {str(row['advance_cash_transaction_id']) for row in offset_rows}
            offset_cash = {str(row['pk']): row for row in _OffsetCash.objects.filter(
                pk__in=offset_advance_ids).values(
                'pk', 'organization_id', 'status', 'direction', 'gross_cents',
                'counterparty_id', 'version')}
            from pmo_plugins.cash.models import CashPartyMatch as _OffsetMatch
            offset_matches = list(_OffsetMatch.objects.filter(
                cash_transaction_id__in=offset_advance_ids).values(
                'pk', 'cash_transaction_id', 'action', 'original_match_id',
                'identified_payee', 'created_at'))
            reversed_match_ids = {str(row['original_match_id']) for row in offset_matches
                                  if row['action'] == 'reverse'}
            effective_counterparty = {}
            for row in sorted(offset_matches, key=lambda item: (
                    str(item['created_at']), str(item['pk']))):
                cash_id = str(row['cash_transaction_id'])
                if cash_id in effective_counterparty or row['action'] != 'identify':
                    continue
                if str(row['pk']) in reversed_match_ids:
                    continue
                payee = row['identified_payee'] or {}
                if payee.get('kind') == 'counterparty' and payee.get('id'):
                    effective_counterparty[cash_id] = str(payee['id'])
            refunded_by_cash = {str(row['original_cash_transaction_id']): row['total'] for row
                                in _Refund.objects.filter(
                                    original_cash_transaction_id__in=offset_advance_ids).values(
                                    'original_cash_transaction_id').annotate(
                                    total=Sum('gross_cents'))}
            returned_by_cash = {str(row['original_cash_transaction_id']): row['total'] for row
                                in _BankReturn.objects.filter(
                                    original_cash_transaction_id__in=offset_advance_ids).values(
                                    'original_cash_transaction_id').annotate(
                                    total=Sum('gross_cents'))}
            advance_classified = {str(row['cash_transaction_id']) for row in
                                  _OffsetClassification.objects.filter(
                                      status='active', classification='advance',
                                      cash_transaction_id__in=offset_advance_ids).values(
                                      'cash_transaction_id')}
            offsets_by_cash = {}
            offsets_by_item = {}
            for row in offset_rows:
                cash_id = str(row['advance_cash_transaction_id'])
                item_id = str(row['open_item_id'])
                offsets_by_cash[cash_id] = (offsets_by_cash.get(cash_id, 0)
                                            + row['offset_cents'])
                offsets_by_item[item_id] = (offsets_by_item.get(item_id, 0)
                                            + row['offset_cents'])
            created_events = {}
            for event in _CashDomainEvent.objects.filter(
                    topic='settlement.offset.created.v1').values('data'):
                data = event['data'] or {}
                created_events[str(data.get('entity_id'))] = (
                    created_events.get(str(data.get('entity_id')), 0) + 1)
            unexpected_topic = _CashDomainEvent.objects.filter(
                topic__startswith='settlement.offset').exclude(
                topic='settlement.offset.created.v1').exists()
            for offset in _Offset.objects.select_related('open_item'):
                label = f"offset:{offset.pk}"
                item = offset.open_item
                if (item.organization_id != offset.organization_id
                        or item.project_id != offset.project_id):
                    errors.append(f"{label}:open_item_scope")
                if item.direction != 'receivable':
                    errors.append(f"{label}:open_item_direction")
                if item.status != 'active':
                    errors.append(f"{label}:open_item_status")
                if offset.status != 'active' or offset.offset_cents <= 0:
                    errors.append(f"{label}:status_or_amount")
                cash_id = str(offset.advance_cash_transaction_id)
                cash = offset_cash.get(cash_id)
                if cash is None:
                    errors.append(f"{label}:missing_advance_cash")
                elif (cash['organization_id'] != offset.organization_id
                      or cash['status'] != 'posted' or cash['direction'] != 'in'):
                    errors.append(f"{label}:advance_cash_state")
                if cash_id not in advance_classified:
                    errors.append(f"{label}:missing_advance_classification")
                advance_ref = offset.advance_ref or {}
                if str(advance_ref.get('id')) != cash_id:
                    errors.append(f"{label}:advance_ref")
                if cash is not None and advance_ref.get('version') != cash['version']:
                    errors.append(f"{label}:advance_ref_version")
                expected_party = (effective_counterparty.get(cash_id)
                                  or (str(cash['counterparty_id'])
                                      if cash is not None and cash['counterparty_id'] else None))
                if expected_party is None or expected_party != str(item.counterparty_id):
                    errors.append(f"{label}:counterparty_mismatch")
                if created_events.get(str(offset.pk), 0) != 1:
                    errors.append(f"{label}:audit_event_count")
            if unexpected_topic:
                errors.append('offset:unexpected_canonical_topic')
            cleared_by_cash = {str(row['cash_transaction_id']): row['total'] for row in
                               Clearance.objects.filter(status='active').values(
                                   'cash_transaction_id').annotate(total=Sum('cleared_cents'))}
            cleared_by_item = {str(row['open_item_id']): row['total'] for row in
                               Clearance.objects.filter(status='active').values(
                                   'open_item_id').annotate(total=Sum('cleared_cents'))}
            for item in OpenItem.objects.all():
                if (cleared_by_item.get(str(item.pk), 0)
                        + offsets_by_item.get(str(item.pk), 0) > item.gross_cents):
                    errors.append(f"open_item:{item.pk}:over_offset")
            for cash_id, total in offsets_by_cash.items():
                cash = offset_cash.get(cash_id)
                if cash is not None and total > cash['gross_cents']:
                    errors.append(f"cash_transaction:{cash_id}:over_offset")
                if cash is not None:
                    covered = (cleared_by_cash.get(cash_id, 0) + total
                               + refunded_by_cash.get(cash_id, 0)
                               + returned_by_cash.get(cash_id, 0))
                    if covered > cash['gross_cents']:
                        errors.append(f"cash_transaction:{cash_id}:offset_over_covered")
                if cash_id in cleared_by_cash:
                    errors.append(f"cash_transaction:{cash_id}:clearance_offset_overlap")
            for cash_id in advance_classified:
                if cash_id in cleared_by_cash:
                    errors.append(f"cash_transaction:{cash_id}:advance_cash_cleared")

        # ----- PH-245 internal transfers: append-only pair of out/in cash legs, both legs carry
        # exactly one internal transfer classification plus canonical classification events, the
        # linked event has no posting refs, and reversal appends two classification reversals and
        # one reversal fact with the canonical reversed event. No ledger/accounting write beyond
        # the PH-239 bridge, no cost/revenue/open-item rows.
        from pmo_plugins.cash.models import (InternalTransfer as _Transfer,
                                             InternalTransferReversal as _TransferReversal)
        transfer_rows = list(_Transfer.objects.values(
            'pk', 'organization_id', 'out_cash_transaction_id', 'in_cash_transaction_id',
            'out_cash_ref', 'in_cash_ref', 'transfer_date', 'gross_cents',
            'out_transaction_date', 'in_transaction_date', 'status', 'version'))
        if transfer_rows:
            from pmo_plugins.cash.models import CashTransaction as _TransferCash
            transfer_cash_ids = {str(row['out_cash_transaction_id']) for row in transfer_rows}
            transfer_cash_ids |= {str(row['in_cash_transaction_id']) for row in transfer_rows}
            transfer_cash = {str(row['pk']): row for row in _TransferCash.objects.filter(
                pk__in=transfer_cash_ids).values(
                'pk', 'organization_id', 'status', 'direction', 'gross_cents',
                'transaction_date', 'funding_account_id')}
            transfer_org_ids = {row['organization_id'] for row in transfer_rows}
            transfer_classifications = list(CashClassification.objects.filter(
                organization_id__in=transfer_org_ids,
                classification='transfer').values(
                'pk', 'organization_id', 'cash_transaction_id', 'status', 'version'))
            transfer_class_by_cash = {}
            for row in transfer_classifications:
                transfer_class_by_cash.setdefault(
                    str(row['cash_transaction_id']), []).append(row)
            transfer_class_by_pk = {str(row['pk']): row for row in transfer_classifications}
            classification_reversals = list(CashClassificationReversal.objects.filter(
                organization_id__in=transfer_org_ids).values(
                'pk', 'organization_id', 'classification_id', 'cash_transaction_id'))
            reversal_by_classification = {}
            for row in classification_reversals:
                reversal_by_classification.setdefault(
                    str(row['classification_id']), []).append(row)
            transfer_reversals = list(_TransferReversal.objects.filter(
                organization_id__in=transfer_org_ids).values(
                'pk', 'organization_id', 'internal_transfer_id', 'internal_transfer_ref',
                'out_classification_reversal_ref', 'in_classification_reversal_ref',
                'occurred_on', 'posting_period', 'status', 'version'))
            reversal_by_transfer = {}
            for row in transfer_reversals:
                reversal_by_transfer.setdefault(
                    str(row['internal_transfer_id']), []).append(row)
            linked_events = {}
            for event in _CashDomainEvent.objects.filter(
                    topic='cash.internal_transfer_linked.v1').values('data'):
                data = event['data'] or {}
                linked_events.setdefault(str((data.get('source') or {}).get('id')),
                                         []).append(data)
            reversed_transfer_events = {}
            for event in _CashDomainEvent.objects.filter(
                    topic='cash.internal_transfer_reversed.v1').values('data'):
                data = event['data'] or {}
                reversed_transfer_events.setdefault(
                    str((data.get('source') or {}).get('id')), []).append(data)
            transfer_class_ids = {str(row['pk']) for row in transfer_classifications}
            transfer_accounting_events = {}
            for event in AccountingEvent.objects.filter(
                    source__namespace='cash', source__kind='cash_classification',
                    source__id__in=transfer_class_ids).values(
                    'id', 'source', 'event_type', 'claim_effect_cents', 'posting_period'):
                transfer_accounting_events.setdefault(
                    str((event['source'] or {}).get('id')), []).append(event)
            transfer_postings = {}
            for posting in LedgerPosting.objects.filter(
                    accounting_event__source__namespace='cash',
                    accounting_event__source__kind='cash_classification',
                    accounting_event__source__id__in=transfer_class_ids).values(
                    'accounting_event__source__id', 'book', 'amount_cents', 'scope_kind',
                    'scope_id'):
                transfer_postings.setdefault(
                    str(posting['accounting_event__source__id']), []).append(posting)
            active_cash_ids = set()
            for row in transfer_rows:
                label = f"internal_transfer:{row['pk']}"
                out_cash = transfer_cash.get(str(row['out_cash_transaction_id']))
                in_cash = transfer_cash.get(str(row['in_cash_transaction_id']))
                if out_cash is None or in_cash is None:
                    errors.append(f"{label}:missing_cash")
                    continue
                if (out_cash['organization_id'] != row['organization_id']
                        or in_cash['organization_id'] != row['organization_id']):
                    errors.append(f"{label}:cash_org")
                if out_cash['status'] != 'posted' or in_cash['status'] != 'posted':
                    errors.append(f"{label}:cash_state")
                if out_cash['direction'] != 'out' or in_cash['direction'] != 'in':
                    errors.append(f"{label}:cash_direction")
                if (out_cash['funding_account_id'] == in_cash['funding_account_id']
                        or out_cash['gross_cents'] != in_cash['gross_cents']
                        or out_cash['gross_cents'] != row['gross_cents']
                        or out_cash['transaction_date'] != in_cash['transaction_date']
                        or out_cash['transaction_date'] != row['transfer_date']
                        or out_cash['transaction_date'] != row['out_transaction_date']
                        or in_cash['transaction_date'] != row['in_transaction_date']):
                    errors.append(f"{label}:pair_mismatch")
                if row['status'] not in {'active', 'reversed'}:
                    errors.append(f"{label}:status")
                if row['status'] == 'active':
                    if str(row['out_cash_transaction_id']) in active_cash_ids:
                        errors.append(f"{label}:duplicate_active")
                    active_cash_ids.add(str(row['out_cash_transaction_id']))
                    active_cash_ids.add(str(row['in_cash_transaction_id']))
                    for direction, cash_id in (('out', row['out_cash_transaction_id']),
                                               ('in', row['in_cash_transaction_id'])):
                        rows = [item for item in transfer_class_by_cash.get(
                            str(cash_id), []) if item['status'] == 'active']
                        if len(rows) != 1:
                            errors.append(f"{label}:{direction}_classification")
                    linked = linked_events.get(str(row['pk']), [])
                    if len(linked) != 1:
                        errors.append(f"{label}:linked_event_count")
                    else:
                        data = linked[0]
                        payload = data.get('payload') or {}
                        refs = payload.get('changed_refs') or []
                        out_classes = [item for item in transfer_class_by_cash.get(
                            str(row['out_cash_transaction_id']), [])
                            if item['status'] == 'active']
                        in_classes = [item for item in transfer_class_by_cash.get(
                            str(row['in_cash_transaction_id']), [])
                            if item['status'] == 'active']
                        expected = [{'namespace': 'cash', 'kind': 'internal_transfer',
                                     'id': str(row['pk']), 'version': row['version']}]
                        if out_classes and in_classes:
                            expected += [
                                {'namespace': 'cash', 'kind': 'cash_classification',
                                 'id': str(out_classes[0]['pk']),
                                 'version': out_classes[0]['version']},
                                {'namespace': 'cash', 'kind': 'cash_classification',
                                 'id': str(in_classes[0]['pk']),
                                 'version': in_classes[0]['version']}]
                        if (data.get('topic') != 'cash.internal_transfer_linked.v1'
                                or data.get('schema_version') != 1
                                or payload.get('posting_event_refs') != []
                                or refs != expected):
                            errors.append(f"{label}:linked_event_envelope")
                    for direction, cash_id, book, amount in (
                            ('out', row['out_cash_transaction_id'], 'cash_out',
                             row['gross_cents']),
                            ('in', row['in_cash_transaction_id'], 'cash_in',
                             row['gross_cents'])):
                        rows = [item for item in transfer_class_by_cash.get(
                            str(cash_id), []) if item['status'] == 'active']
                        if not rows:
                            continue
                        class_id = str(rows[0]['pk'])
                        events = transfer_accounting_events.get(class_id, [])
                        if len(events) != 1 or events[0]['event_type'] != 'cash.classification':
                            errors.append(f"{label}:{direction}_accounting_event")
                        postings = transfer_postings.get(class_id, [])
                        if (len(postings) != 1 or postings[0]['book'] != book
                                or postings[0]['amount_cents'] != amount
                                or postings[0]['scope_kind'] != 'funding_account'
                                or postings[0]['scope_id'] != str(
                                    out_cash['funding_account_id'] if direction == 'out'
                                    else in_cash['funding_account_id'])):
                            errors.append(f"{label}:{direction}_posting")
                    out_classes = [item for item in transfer_class_by_cash.get(
                        str(row['out_cash_transaction_id']), [])
                        if item['status'] == 'active']
                    in_classes = [item for item in transfer_class_by_cash.get(
                        str(row['in_cash_transaction_id']), [])
                        if item['status'] == 'active']
                    if out_classes and in_classes:
                        out_postings = transfer_postings.get(str(out_classes[0]['pk']), [])
                        in_postings = transfer_postings.get(str(in_classes[0]['pk']), [])
                        net = (sum(item['amount_cents'] for item in in_postings)
                               - sum(item['amount_cents'] for item in out_postings))
                        if net != 0:
                            errors.append(f"{label}:company_net")
                else:
                    reversals = reversal_by_transfer.get(str(row['pk']), [])
                    if len(reversals) != 1:
                        errors.append(f"{label}:reversal_count")
                        continue
                    reversal = reversals[0]
                    if (reversal['organization_id'] != row['organization_id']
                            or reversal['status'] != 'posted'):
                        errors.append(f"{label}:reversal_shape")
                    reversed_events = reversed_transfer_events.get(
                        str(reversal['pk']), [])
                    if len(reversed_events) != 1:
                        errors.append(f"{label}:reversed_event_count")
                    else:
                        data = reversed_events[0]
                        payload = data.get('payload') or {}
                        refs = payload.get('changed_refs') or []
                        if (data.get('topic') != 'cash.internal_transfer_reversed.v1'
                                or data.get('schema_version') != 1
                                or payload.get('posting_event_refs') != []
                                or len(refs) != 3
                                or len(payload.get('classification_reversal_refs') or []) != 2
                                or (payload.get('original_internal_transfer_ref') or {}).get(
                                    'id') != str(row['pk'])):
                            errors.append(f"{label}:reversed_event_envelope")
                    for direction, cash_id in (('out', row['out_cash_transaction_id']),
                                               ('in', row['in_cash_transaction_id'])):
                        classes = transfer_class_by_cash.get(str(cash_id), [])
                        if any(item['status'] == 'active' for item in classes):
                            errors.append(f"{label}:{direction}_active_after_reverse")
                        reversed_classes = [item for item in classes
                                            if item['status'] == 'reversed']
                        for item in reversed_classes:
                            if len(reversal_by_classification.get(
                                    str(item['pk']), [])) != 1:
                                errors.append(f"{label}:{direction}_reversal_row")
        # ----- PH-253 bank-fee drafts: one draft fact per posted incoming cash, exact saved-event
        # history per version, and (once PH-250 lands) exactly one conditional cost fact set.
        from pmo_plugins.costing.models import (BankCharge as _BankCharge,
                                                BankChargeReceipt as _BankChargeReceipt)
        from core.models import DomainEvent as _FeeEvent
        from django.db.models import Count as _Count
        fee_topic = 'costing.bank_charge_saved.v1'
        fee_statuses = {'draft', 'pending', 'approved', 'rejected', 'superseded'}
        fee_cost_event_type = 'costing.bank_charge.cost'
        fee_claim_keys = set()

        def _fee_ref(charge, version):
            return {'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk),
                    'version': version}

        def _fee_cost_facts(charge):
            claim_key = f'bank_charge:{charge.pk}'
            fee_claim_keys.add(claim_key)
            claims = list(SourceConsumption.objects.filter(
                organization_id=charge.organization_id, coverage_key=claim_key))
            claim_ids = [claim.pk for claim in claims]
            events = list(AccountingEvent.objects.filter(
                organization_id=charge.organization_id,
                source__namespace='costing', source__kind='bank_charge',
                source__id=str(charge.pk)))
            if claim_ids:
                events += [event for event in AccountingEvent.objects.filter(
                    organization_id=charge.organization_id, claim_id__in=claim_ids)
                    if event.pk not in {row.pk for row in events}]
            postings = list(LedgerPosting.objects.filter(
                accounting_event_id__in=[event.pk for event in events])) if events else []
            return claims, events, postings

        for duplicate in _BankCharge.objects.values('organization_id', 'cash_transaction_id').annotate(
                total=_Count('id')).filter(total__gt=1):
            errors.append(f"bank_charge:{duplicate['organization_id']}:{duplicate['cash_transaction_id']}:duplicate_cash")
        for charge in _BankCharge.objects.all():
            transaction = CashTransaction.objects.filter(
                pk=charge.cash_transaction_id, organization_id=charge.organization_id).first()
            if transaction is None:
                errors.append(f"bank_charge:{charge.pk}:missing_cash")
                continue
            if transaction.status != 'posted' or transaction.direction != 'in':
                errors.append(f"bank_charge:{charge.pk}:cash_state")
            if not 0 < charge.gross_cents <= transaction.gross_cents:
                errors.append(f"bank_charge:{charge.pk}:amount_over_net")
            if charge.status not in fee_statuses:
                errors.append(f"bank_charge:{charge.pk}:unexpected_status")
            if (charge.fee_bearer != 'company' or charge.scope_kind != 'project'
                    or charge.existing_cost_ref is not None):
                errors.append(f"bank_charge:{charge.pk}:unsupported_slice_fields")
            cash_month = transaction.transaction_date.strftime('%Y-%m')
            if charge.occurred_on.strftime('%Y-%m') != cash_month or charge.posting_period != cash_month:
                errors.append(f"bank_charge:{charge.pk}:period")
            # Exactly one canonical saved event per draft version (1..version), each a complete
            # envelope carrying its own version; missing, duplicated or tampered history is corruption.
            saved = list(_FeeEvent.objects.filter(
                organization_id=charge.organization_id, topic=fee_topic,
                data__source__id=str(charge.pk)).order_by('created_at', 'id'))
            if len(saved) != charge.version:
                errors.append(f"bank_charge:{charge.pk}:saved_event_count")
            seen_versions = set()
            for event in saved:
                data = event.data or {}
                payload = data.get('payload') or {}
                source = data.get('source') or {}
                version = source.get('version')
                if (data.get('topic') != fee_topic or data.get('schema_version') != 1
                        or data.get('organization_id') != str(charge.organization_id)):
                    errors.append(f"bank_charge:{charge.pk}:saved_event_envelope")
                    continue
                if (source.get('namespace') != 'costing' or source.get('kind') != 'bank_charge'
                        or str(source.get('id')) != str(charge.pk) or type(version) is not int):
                    errors.append(f"bank_charge:{charge.pk}:saved_event_source")
                    continue
                if (payload.get('changed_refs') != [_fee_ref(charge, version)]
                        or payload.get('posting_event_refs') != []):
                    errors.append(f"bank_charge:{charge.pk}:saved_event_envelope")
                    continue
                if version < 1 or version > charge.version or version in seen_versions:
                    errors.append(f"bank_charge:{charge.pk}:saved_event_version")
                    continue
                seen_versions.add(version)
            if charge.version >= 1 and seen_versions != set(range(1, charge.version + 1)):
                errors.append(f"bank_charge:{charge.pk}:saved_event_version_coverage")
            # PH-250 approval chain invariants: submit/decide must record the right actors and
            # the creator or submitter can never be the approver; rejection needs a reason. The
            # chain requirement applies to rows carrying chain state or its append-only
            # submit/decide receipt, so the PH-253 synthetic transitional fixtures stay valid
            # while every real submitted/decided row is still enforced.
            fee_receipt_actions = set(_BankChargeReceipt.objects.filter(
                organization_id=charge.organization_id,
                result_ref__id=str(charge.pk)).values_list('action', flat=True))
            has_submit_evidence = ('bank_charge.submit' in fee_receipt_actions
                                   or charge.submitted_by_id is not None
                                   or charge.submitted_at is not None)
            has_decide_evidence = ('bank_charge.decide' in fee_receipt_actions
                                   or charge.approved_by_id is not None
                                   or charge.approved_at is not None)
            if charge.status in {'pending', 'approved', 'rejected'} and has_submit_evidence and (
                    charge.submitted_by_id is None or charge.submitted_at is None):
                errors.append(f"bank_charge:{charge.pk}:missing_submitter")
            if charge.status in {'approved', 'rejected'} and has_decide_evidence:
                if charge.approved_by_id is None or charge.approved_at is None:
                    errors.append(f"bank_charge:{charge.pk}:missing_approver")
                elif charge.approved_by_id in {charge.created_by_id, charge.submitted_by_id}:
                    errors.append(f"bank_charge:{charge.pk}:self_approved")
            if charge.status == 'approved' and charge.reject_reason:
                errors.append(f"bank_charge:{charge.pk}:approved_with_reason")
            if charge.status == 'rejected' and not charge.reject_reason:
                errors.append(f"bank_charge:{charge.pk}:rejected_without_reason")
            # Conditional cost facts: unapproved states must be zero; approved may be the PH-250
            # transitional state without facts, or must carry exactly one complete cost fact set.
            claims, cost_events, cost_postings = _fee_cost_facts(charge)
            has_cost_facts = bool(claims or cost_events or cost_postings)
            if charge.status in {'draft', 'pending', 'rejected', 'superseded'}:
                if has_cost_facts:
                    errors.append(f"bank_charge:{charge.pk}:cost_fact_on_unapproved")
                continue
            if not has_cost_facts:
                continue
            claim_key = f'bank_charge:{charge.pk}'
            if len(claims) != 1:
                errors.append(f"bank_charge:{charge.pk}:cost_claim_count")
            claim = claims[0] if len(claims) == 1 else None
            if claim is not None:
                if (claim.consumer != 'cost' or claim.coverage_key != claim_key
                        or claim.amount_cents != charge.gross_cents):
                    errors.append(f"bank_charge:{charge.pk}:cost_claim")
                if str(claim.source_id) != str(charge.pk):
                    errors.append(f"bank_charge:{charge.pk}:cost_claim_source")
            if len(cost_events) != 1:
                errors.append(f"bank_charge:{charge.pk}:cost_event_count")
            else:
                cost_event = cost_events[0]
                if (cost_event.event_type != fee_cost_event_type
                        or cost_event.claim_effect_cents != charge.gross_cents
                        or cost_event.occurred_on != charge.occurred_on
                        or cost_event.posting_period != charge.posting_period):
                    errors.append(f"bank_charge:{charge.pk}:cost_event")
                if claim is not None and cost_event.claim_id != claim.pk:
                    errors.append(f"bank_charge:{charge.pk}:cost_event_claim")
            if len(cost_postings) != 1:
                errors.append(f"bank_charge:{charge.pk}:cost_posting_count")
            else:
                posting = cost_postings[0]
                if (posting.book != 'cost' or posting.origin != 'current'
                        or posting.scope_kind != 'project' or posting.scope_id != charge.scope_id
                        or posting.amount_cents != charge.gross_cents):
                    errors.append(f"bank_charge:{charge.pk}:cost_posting")
        fee_charge_ids = {str(pk) for pk in _BankCharge.objects.values_list('pk', flat=True)}
        for event in AccountingEvent.objects.filter(
                source__namespace='costing', source__kind='bank_charge'):
            if str((event.source or {}).get('id')) not in fee_charge_ids:
                errors.append(f"bank_charge:{event.pk}:orphan_cost_event")
        for claim in SourceConsumption.objects.all():
            is_fee_claim = claim.coverage_key.startswith('bank_charge:') or (
                claim.source or {}).get('kind') == 'bank_charge'
            if is_fee_claim and claim.coverage_key not in fee_claim_keys:
                errors.append(f"bank_charge_claim:{claim.pk}:unexpected_source_consumption")
        for receipt in _BankChargeReceipt.objects.all():
            result_ref = receipt.result_ref or {}
            if result_ref.get('kind') != 'bank_charge' or not _BankCharge.objects.filter(
                    organization_id=receipt.organization_id, pk=result_ref.get('id')).exists():
                errors.append(f"bank_charge_receipt:{receipt.pk}:result_ref")
        # ----- PH-265 contract change/termination: append-only history, one approved
        # version per live contract and canonical change_effective/version_approved envelopes.
        from pmo_plugins.contracts.models import (Contract as _Contract,
                                                  ContractChange as _ContractChange,
                                                  ContractVersion as _ContractVersion)
        from core.models import DomainEvent as _ContractEvent
        change_statuses = {'draft', 'pending_customer', 'approved', 'rejected', 'withdrawn'}
        change_types = {'add_scope', 'reduce_scope', 'price_change', 'extension', 'termination'}

        def _contract_ref(row, kind):
            return {'namespace': 'contracts', 'kind': kind, 'id': str(row.pk),
                    'version': row.version}


        for change in _ContractChange.objects.all():
            change_id_org = change.organization_id
            if change.status not in change_statuses or change.change_type not in change_types:
                errors.append(f"contract_change:{change.pk}:status_or_type")
            if change.contract.organization_id != change.organization_id:
                errors.append(f"contract_change:{change.pk}:organization")
            effective = _ContractEvent.objects.filter(
                organization_id=change.organization_id, topic='contracts.change_effective.v1',
                data__source__id=str(change.pk))
            if change.status == 'draft':
                if change.decided_by_member_id is not None or change.decided_on is not None:
                    errors.append(f"contract_change:{change.pk}:draft_decision_fields")
                if effective.exists():
                    errors.append(f"contract_change:{change.pk}:draft_effective_event")
            if change.status in {'approved', 'rejected', 'withdrawn'}:
                if change.decided_by_member_id is None or change.decided_on is None:
                    errors.append(f"contract_change:{change.pk}:missing_decider")
                if change.decided_by_member_id == change.created_by_member_id:
                    errors.append(f"contract_change:{change.pk}:self_decided")
                if change.status == 'rejected' and not change.decision_note:
                    errors.append(f"contract_change:{change.pk}:rejected_without_reason")
            if change.status != 'approved':
                if effective.exists():
                    errors.append(f"contract_change:{change.pk}:unapproved_effective_event")
                continue
            if effective.count() != 1:
                errors.append(f"contract_change:{change.pk}:effective_event_count")
                continue
            data = effective.get().data or {}
            payload = data.get('payload') or {}
            refs = payload.get('changed_refs') or []
            contract = change.contract
            if (data.get('topic') != 'contracts.change_effective.v1'
                    or data.get('schema_version') != 1
                    or data.get('organization_id') != str(change.organization_id)
                    or data.get('source') != _contract_ref(change, 'contract_change')
                    or payload.get('posting_event_refs') != []
                    or len(refs) != 2
                    or refs[0] != _contract_ref(change, 'contract_change')):
                errors.append(f"contract_change:{change.pk}:effective_event_envelope")
                continue
            if change.change_type == 'termination':
                if (contract.status != 'terminated' or contract.terminated_on != change.requested_on
                        or refs[1] != _contract_ref(contract, 'contract')
                        or any(getattr(change, f'{field}_delta_cents')
                               for field in ('net', 'tax', 'gross'))):
                    errors.append(f"contract_change:{change.pk}:termination_state")
            else:
                versions = _ContractVersion.objects.filter(contract=contract, status='approved')
                if versions.count() != 1 or refs[1].get('kind') != 'contract_version':
                    errors.append(f"contract_change:{change.pk}:effective_version")
                else:
                    expected = _contract_ref(versions.get(), 'contract_version')
                    if refs[1] != expected:
                        errors.append(f"contract_change:{change.pk}:effective_version_ref")
                if contract.status != 'active':
                    errors.append(f"contract_change:{change.pk}:contract_not_active")
        from django.db.models import Count as _ContractCount
        for duplicate in _ContractChange.objects.filter(status='draft').values(
                'contract_id').annotate(total=_ContractCount('id')).filter(total__gt=1):
            errors.append(f"contract:{duplicate['contract_id']}:duplicate_change_draft")
        for contract in _Contract.objects.all():
            approved = _ContractVersion.objects.filter(contract=contract, status='approved')
            if contract.status == 'active' and approved.count() != 1:
                errors.append(f"contract:{contract.pk}:active_approved_count")
            if contract.status == 'terminated':
                if contract.terminated_on is None:
                    errors.append(f"contract:{contract.pk}:terminated_without_date")
                if approved.count() != 1:
                    errors.append(f"contract:{contract.pk}:terminated_approved_count")
            if contract.status not in {'draft', 'active', 'terminated', 'superseded'}:
                errors.append(f"contract:{contract.pk}:unexpected_status")
            if contract.status == 'terminated':
                termination_changes = contract.changes.filter(status='approved',
                                                              change_type='termination')
                if termination_changes.count() != 1:
                    errors.append(f"contract:{contract.pk}:termination_change_count")
                if contract.changes.filter(status='draft').exists():
                    errors.append(f"contract:{contract.pk}:draft_change_after_termination")
                from django.utils import timezone as _contract_timezone
                for change in contract.changes.all():
                    if (change.status != 'approved'
                            and _contract_timezone.localtime(change.created_at).date()
                            >= contract.terminated_on):
                        errors.append(f"contract_change:{change.pk}:created_after_termination")
                    if change.status == 'approved' and change.change_type != 'termination':
                        errors.append(f"contract_change:{change.pk}:approved_after_termination")
            for version in _ContractVersion.objects.filter(contract=contract, status='approved'):
                if (version.approval_audit_id is None or version.approved_by_member_id is None
                        or version.approved_on is None):
                    errors.append(f"contract_version:{version.pk}:approval_chain")
                events = _ContractEvent.objects.filter(
                    organization_id=contract.organization_id,
                    topic='contracts.version_approved.v1',
                    data__source__id=str(version.pk))
                if events.count() != 1:
                    errors.append(f"contract_version:{version.pk}:approved_event_count")
                else:
                    data = events.get().data or {}
                    payload = data.get('payload') or {}
                    if (data.get('source') != _contract_ref(version, 'contract_version')
                            or payload.get('posting_event_refs') != []):
                        errors.append(f"contract_version:{version.pk}:approved_event_envelope")
                    change_id = (version.approval_audit.payload or {}).get('change_id') if version.approval_audit else None
                    if change_id:
                        change = _ContractChange.objects.filter(pk=change_id).first()
                        expected = [_contract_ref(version, 'contract_version')]
                        if change is not None:
                            expected.append(_contract_ref(change, 'contract_change'))
                        if payload.get('changed_refs') != expected:
                            errors.append(f"contract_version:{version.pk}:approved_changed_refs")
        # ----- PH-269 time: customer-confirmed billable minutes are append-only owned
        # facts with exactly one canonical event and no financial write.
        from pmo_plugins.time.models import (BillableConfirmation as _Billable,
                                             BillableConfirmationReceipt as _BillableReceipt)
        from pmo_plugins.contracts.models import ContractLine as _ContractLine
        from core.models import AuditEvent as _ContractAudit, Timesheet as _Timesheet
        billable_statuses = {'draft', 'confirmed'}
        billable_seen = set()
        for row in _Billable.objects.all():
            if row.status not in billable_statuses | {'reversed'}:
                errors.append(f"billable_confirmation:{row.pk}:status")
            if not 1 <= row.confirmed_minutes <= 1440:
                errors.append(f"billable_confirmation:{row.pk}:minutes")
            timesheet = _Timesheet.objects.filter(
                pk=row.timesheet_ref.get('id')).first()
            if timesheet is None or timesheet.organization_id != row.organization_id:
                errors.append(f"billable_confirmation:{row.pk}:timesheet_ref")
            elif (timesheet.status != 'approved' or timesheet.project_id is None
                  or timesheet.version != row.timesheet_ref.get('version')):
                errors.append(f"billable_confirmation:{row.pk}:timesheet_state")
            elif row.confirmed_minutes > timesheet.minutes:
                errors.append(f"billable_confirmation:{row.pk}:minutes_over_timesheet")
            line = _ContractLine.objects.select_related('contract_version__contract').filter(
                pk=row.contract_line_id, organization_id=row.organization_id).first()
            if line is None or line.version != 1:
                errors.append(f"billable_confirmation:{row.pk}:contract_line_ref")
            else:
                version, contract = line.contract_version, line.contract
                if (version.status != 'approved' or line.status != 'approved'
                        or contract.status != 'active'
                        or contract.project_id != (timesheet.project_id if timesheet else None)
                        or row.contract_line_ref.get('version') != line.version):
                    errors.append(f"billable_confirmation:{row.pk}:contract_line_state")
            reversal_audits = _ContractAudit.objects.filter(
                organization_id=row.organization_id, action='time.billable_confirmation.reversed',
                entity_id=str(row.pk))
            if row.status in {'confirmed', 'reversed'}:
                if row.confirmed_by_member_id is None or row.confirmed_at is None:
                    errors.append(f"billable_confirmation:{row.pk}:missing_confirmer")
                if row.confirmed_by_member_id == row.created_by_member_id:
                    errors.append(f"billable_confirmation:{row.pk}:self_confirmed")
                if row.status == 'confirmed':
                    key = (row.organization_id, row.timesheet_id, row.contract_line_id)
                    if key in billable_seen:
                        errors.append(f"billable_confirmation:{row.pk}:duplicate_confirmed")
                    billable_seen.add(key)
                    if reversal_audits.exists():
                        errors.append(f"billable_confirmation:{row.pk}:confirmed_with_reversal_audit")
                events = _ContractEvent.objects.filter(
                    organization_id=row.organization_id, topic='time.billable_confirmed.v1',
                    data__source__id=str(row.pk))
                if events.count() != 1:
                    errors.append(f"billable_confirmation:{row.pk}:confirmed_event_count")
                else:
                    data = events.get().data or {}
                    payload = data.get('payload') or {}
                    # The canonical event was frozen at confirmation time; a later reversal
                    # increments the confirmation version but never rewrites the event.
                    event_source = (dict(row.source_ref, version=row.version - 1)
                                    if row.status == 'reversed' else row.source_ref)
                    expected_refs = [event_source, row.timesheet_ref, row.contract_line_ref]
                    if (data.get('schema_version') != 1
                            or data.get('organization_id') != str(row.organization_id)
                            or data.get('source') != event_source
                            or payload.get('changed_refs') != expected_refs
                            or payload.get('posting_event_refs') != []):
                        errors.append(f"billable_confirmation:{row.pk}:confirmed_event_envelope")
                if row.status == 'reversed':
                    if row.version < 3:
                        errors.append(f"billable_confirmation:{row.pk}:reversed_version")
                    if reversal_audits.count() != 1:
                        errors.append(f"billable_confirmation:{row.pk}:reversal_audit_count")
                    else:
                        audit_row = reversal_audits.get()
                        audit_payload = audit_row.payload or {}
                        if (audit_payload.get('confirmation_ref') != row.source_ref
                                or not audit_payload.get('reason')
                                or audit_payload.get('changed_refs')
                                != [row.source_ref, row.timesheet_ref, row.contract_line_ref]):
                            errors.append(f"billable_confirmation:{row.pk}:reversal_audit_payload")
                    reversals = _BillableReceipt.objects.filter(
                        organization_id=row.organization_id, action='reverse',
                        result_ref__id=str(row.pk))
                    if reversals.count() != 1:
                        errors.append(f"billable_confirmation:{row.pk}:reversal_receipt_count")
            else:
                if row.confirmed_by_member_id is not None or row.confirmed_at is not None:
                    errors.append(f"billable_confirmation:{row.pk}:draft_decision_fields")
                if _ContractEvent.objects.filter(
                        organization_id=row.organization_id, topic='time.billable_confirmed.v1',
                        data__source__id=str(row.pk)).exists():
                    errors.append(f"billable_confirmation:{row.pk}:draft_event")
                if reversal_audits.exists():
                    errors.append(f"billable_confirmation:{row.pk}:draft_reversal_audit")
            if AccountingEvent.objects.filter(
                    organization_id=row.organization_id,
                    source__namespace='time', source__kind='billable_confirmation').exists():
                errors.append(f"billable_confirmation:{row.pk}:financial_event")
            if SourceConsumption.objects.filter(
                    organization_id=row.organization_id,
                    source__kind='billable_confirmation').exists():
                errors.append(f"billable_confirmation:{row.pk}:source_consumption")
        if _ContractEvent.objects.filter(topic='time.billable_reversed.v1').exists():
            errors.append("billable_confirmation:unexpected_canonical_reversed_topic")
        for receipt in _BillableReceipt.objects.all():
            ref = receipt.result_ref or {}
            if ref.get('kind') != 'billable_confirmation' or not _Billable.objects.filter(
                    pk=ref.get('id'), organization_id=receipt.organization_id).exists():
                errors.append(f"billable_receipt:{receipt.pk}:result_ref")
            if receipt.action not in {'draft.save', 'confirm', 'reverse'}:
                errors.append(f"billable_receipt:{receipt.pk}:action")
            if receipt.action == 'reverse' and not _Billable.objects.filter(
                    pk=ref.get('id'), organization_id=receipt.organization_id,
                    status='reversed').exists():
                errors.append(f"billable_receipt:{receipt.pk}:reverse_state")
        from pmo_plugins.invoicing.models import (Invoice, InvoiceAllocation, InvoiceLine,
                                                  InvoiceReversal)
        from pmo_plugins.contracts.models import ContractLine as _InvoiceContractLine
        from pmo_plugins.settlement.models import OpenItem as _InvoiceOpenItem
        for invoice in Invoice.objects.all().order_by('pk'):
            if invoice.gross_cents != invoice.net_cents + invoice.tax_cents:
                errors.append(f"invoice:{invoice.pk}:header_tax_split")
            if invoice.status not in {'active', 'reversed'}:
                errors.append(f"invoice:{invoice.pk}:status")
            lines = list(InvoiceLine.objects.filter(invoice_id=invoice.pk))
            if sum(line.gross_cents for line in lines) != invoice.gross_cents:
                errors.append(f"invoice:{invoice.pk}:line_total")
            if sum(line.net_cents for line in lines) != invoice.net_cents:
                errors.append(f"invoice:{invoice.pk}:line_net_total")
            if sum(line.tax_cents for line in lines) != invoice.tax_cents:
                errors.append(f"invoice:{invoice.pk}:line_tax_total")
            for line in lines:
                if line.gross_cents != line.net_cents + line.tax_cents:
                    errors.append(f"invoice_line:{line.pk}:tax_split")
                allocations = list(InvoiceAllocation.objects.filter(invoice_line_id=line.pk))
                if allocations and sum(row.gross_cents for row in allocations) != line.gross_cents:
                    errors.append(f"invoice_line:{line.pk}:allocation_total")
                for row in allocations:
                    if row.gross_cents != row.net_cents + row.tax_cents:
                        errors.append(f"invoice_allocation:{row.pk}:tax_split")
                    if row.organization_id != invoice.organization_id:
                        errors.append(f"invoice_allocation:{row.pk}:organization")
                    source = row.business_source_ref or {}
                    kind = (source.get('namespace'), source.get('kind'))
                    if kind == ('contracts', 'contract_line'):
                        target = _InvoiceContractLine.objects.filter(
                            pk=source.get('id'), organization_id=invoice.organization_id).first()
                        if target is None or target.status != 'approved':
                            errors.append(f"invoice_allocation:{row.pk}:source_missing")
                        elif str(target.contract_version.contract.party_id) != str(invoice.counterparty_id):
                            errors.append(f"invoice_allocation:{row.pk}:counterparty")
                    elif kind == ('settlement', 'open_item'):
                        target = _InvoiceOpenItem.objects.filter(
                            pk=source.get('id'), organization_id=invoice.organization_id).first()
                        if target is None:
                            errors.append(f"invoice_allocation:{row.pk}:source_missing")
                        elif str(target.counterparty_id) != str(invoice.counterparty_id):
                            errors.append(f"invoice_allocation:{row.pk}:counterparty")
                    else:
                        errors.append(f"invoice_allocation:{row.pk}:source_kind")
            reversals = list(InvoiceReversal.objects.filter(invoice_id=invoice.pk))
            reversed_total = sum(-row.gross_cents for row in reversals)
            if reversed_total > invoice.gross_cents:
                errors.append(f"invoice:{invoice.pk}:over_reversed")
            if invoice.status == 'reversed' and reversed_total != invoice.gross_cents:
                errors.append(f"invoice:{invoice.pk}:reversed_without_full_reversal")
            if invoice.status == 'active' and reversed_total >= invoice.gross_cents and reversals:
                errors.append(f"invoice:{invoice.pk}:full_reversal_but_active")
            line_ids = {str(line.pk) for line in lines}
            allocation_ids = {str(row.pk) for row in InvoiceAllocation.objects.filter(
                invoice_line__invoice_id=invoice.pk)}
            line_reversed, allocation_reversed = {}, {}
            for row in reversals:
                if row.gross_cents >= 0 or row.net_cents > 0 or row.tax_cents > 0:
                    errors.append(f"invoice_reversal:{row.pk}:signed")
                if row.gross_cents != row.net_cents + row.tax_cents:
                    errors.append(f"invoice_reversal:{row.pk}:tax_split")
                line_sum = sum(item.get('gross_cents', 0) for item in row.line_reversals or [])
                allocation_sum = sum(item.get('gross_cents', 0)
                                     for item in row.allocation_reversals or [])
                if line_sum != row.gross_cents or allocation_sum != row.gross_cents:
                    errors.append(f"invoice_reversal:{row.pk}:detail_total")
                for item in row.line_reversals or []:
                    ref = (item.get('original_line_ref') or {}).get('id')
                    if str(ref) not in line_ids:
                        errors.append(f"invoice_reversal:{row.pk}:line_ref")
                    else:
                        line_reversed[str(ref)] = line_reversed.get(str(ref), 0) + abs(
                            item.get('gross_cents', 0))
                for item in row.allocation_reversals or []:
                    ref = (item.get('original_allocation_ref') or {}).get('id')
                    if str(ref) not in allocation_ids:
                        errors.append(f"invoice_reversal:{row.pk}:allocation_ref")
                    else:
                        allocation_reversed[str(ref)] = allocation_reversed.get(str(ref), 0) + abs(
                            item.get('gross_cents', 0))
            for line in lines:
                if line_reversed.get(str(line.pk), 0) > line.gross_cents:
                    errors.append(f"invoice_line:{line.pk}:over_reversed")
            for allocation in InvoiceAllocation.objects.filter(
                    invoice_line__invoice_id=invoice.pk):
                if allocation_reversed.get(str(allocation.pk), 0) > allocation.gross_cents:
                    errors.append(f"invoice_allocation:{allocation.pk}:over_reversed")
            events = _ContractEvent.objects.filter(
                organization_id=invoice.organization_id, topic='invoicing.recorded.v1',
                data__source__id=str(invoice.pk))
            if events.count() != 1:
                errors.append(f"invoice:{invoice.pk}:recorded_event_count")
            if _ContractEvent.objects.filter(
                    organization_id=invoice.organization_id, topic='invoicing.lines_allocated.v1',
                    data__source__id=str(invoice.pk)).count() > 1:
                errors.append(f"invoice:{invoice.pk}:allocated_event_count")
            if AccountingEvent.objects.filter(
                    organization_id=invoice.organization_id,
                    source__namespace='invoicing').exists():
                errors.append(f"invoice:{invoice.pk}:financial_event")
            if SourceConsumption.objects.filter(
                    organization_id=invoice.organization_id,
                    source__kind='invoice').exists():
                errors.append(f"invoice:{invoice.pk}:source_consumption")
        from pmo_plugins.delivery.models import (PeriodCloseRun, ProjectClose,
                                                  DeliveryReceipt)
        from core.models import AccountingPeriod as _ClosePeriod
        periods = list(_ClosePeriod.objects.all())
        period_runs = list(PeriodCloseRun.objects.all().order_by(
            'organization_id', 'period', 'sequence'))
        project_runs = list(ProjectClose.objects.all().order_by(
            'organization_id', 'project_id', 'sequence'))
        close_receipts = list(DeliveryReceipt.objects.filter(
            action__in=['period.close', 'period.reopen', 'project.close', 'project.reopen']))
        by_period, by_project = {}, {}
        for run in period_runs:
            by_period.setdefault((run.organization_id, run.period), []).append(run)
        for run in project_runs:
            by_project.setdefault((run.organization_id, run.project_id), []).append(run)
        period_index = {(row.organization_id, row.period): row for row in periods}
        project_index = {row.pk: row for row in projects}
        for (org_id, period), runs in by_period.items():
            expected = 1
            for index, run in enumerate(runs):
                if run.sequence != expected:
                    errors.append(f"period_close:{org_id}:{period}:sequence")
                expected += 1
                if run.action == 'close' and run.state not in ('closing', 'closed'):
                    errors.append(f"period_close:{run.pk}:action_state")
                if run.action == 'reopen' and run.state != 'reopened':
                    errors.append(f"period_close:{run.pk}:action_state")
                if run.state == 'closed' and run.closed_through is None:
                    errors.append(f"period_close:{run.pk}:closed_date")
                prev = runs[index - 1] if index else None
                if run.action == 'close':
                    if run.state == 'closing' and prev is not None and prev.state != 'reopened':
                        errors.append(f"period_close:{run.pk}:close_transition")
                    if run.state == 'closed':
                        if (prev is None or prev.action != 'close' or prev.state != 'closing'
                                or prev.sequence + 1 != run.sequence):
                            errors.append(f"period_close:{run.pk}:missing_closing_pair")
                if run.action == 'reopen':
                    if prev is None or prev.state != 'closed':
                        errors.append(f"period_close:{run.pk}:reopen_transition")
                    if run.state != 'reopened':
                        errors.append(f"period_close:{run.pk}:reopen_state")
                if prev is not None:
                    if prev.state == 'closed' and run.action != 'reopen':
                        errors.append(f"period_close:{run.pk}:closed_followup")
                    if prev.state == 'reopened' and run.action != 'close':
                        errors.append(f"period_close:{run.pk}:reopened_followup")
                if run.state == 'closing':
                    nxt = runs[index + 1] if index + 1 < len(runs) else None
                    if nxt is None or nxt.sequence != run.sequence + 1 or nxt.state != 'closed':
                        errors.append(f"period_close:{run.pk}:dangling_closing")
            if runs and runs[-1].state == 'closing':
                errors.append(f"period_close:{org_id}:{period}:latest_closing")
            row = period_index.get((org_id, period))
            latest = runs[-1]
            if latest.state == 'closed':
                if row is None or row.status != 'posted' or row.closed_through != latest.closed_through:
                    errors.append(f"period_close:{org_id}:{period}:latest_state")
            if latest.state == 'reopened':
                if row is None or row.status != 'active' or row.closed_through is not None:
                    errors.append(f"period_close:{org_id}:{period}:reopened_state")
        for (org_id, project_id), runs in by_project.items():
            project = project_index.get(project_id)
            expected = 1
            for index, run in enumerate(runs):
                if run.sequence != expected:
                    errors.append(f"project_close:{project_id}:sequence")
                expected += 1
                if run.action == 'close' and run.state not in ('closing', 'closed'):
                    errors.append(f"project_close:{run.pk}:action_state")
                if run.action == 'reopen' and run.state != 'reopened':
                    errors.append(f"project_close:{run.pk}:action_state")
                if run.action == 'reopen' and run.from_stage != 'closed':
                    errors.append(f"project_close:{run.pk}:reopen_stage")
                if run.action == 'close' and run.from_stage not in {
                        choice[0] for choice in Project.Stage.choices}:
                    errors.append(f"project_close:{run.pk}:from_stage")
                prev = runs[index - 1] if index else None
                if run.action == 'close':
                    if run.state == 'closing' and prev is not None and prev.state != 'reopened':
                        errors.append(f"project_close:{run.pk}:close_transition")
                    if run.state == 'closed':
                        if (prev is None or prev.action != 'close' or prev.state != 'closing'
                                or prev.sequence + 1 != run.sequence):
                            errors.append(f"project_close:{run.pk}:missing_closing_pair")
                if run.action == 'reopen':
                    if prev is None or prev.state != 'closed':
                        errors.append(f"project_close:{run.pk}:reopen_transition")
                    if run.state != 'reopened':
                        errors.append(f"project_close:{run.pk}:reopen_state")
                if prev is not None:
                    if prev.state == 'closed' and run.action != 'reopen':
                        errors.append(f"project_close:{run.pk}:closed_followup")
                    if prev.state == 'reopened' and run.action != 'close':
                        errors.append(f"project_close:{run.pk}:reopened_followup")
                if run.state == 'closing':
                    nxt = runs[index + 1] if index + 1 < len(runs) else None
                    if nxt is None or nxt.sequence != run.sequence + 1 or nxt.state != 'closed':
                        errors.append(f"project_close:{run.pk}:dangling_closing")
            if runs and runs[-1].state == 'closing':
                errors.append(f"project_close:{project_id}:latest_closing")
            latest = runs[-1]
            if project is not None and project.stage == 'cancelled' and runs:
                errors.append(f"project_close:{project_id}:cancelled_with_run")
            if latest.state == 'closed':
                if project is None or project.stage != 'closed':
                    errors.append(f"project_close:{project_id}:latest_state")
            if latest.state == 'reopened':
                closed_runs = [run for run in runs if run.state == 'closed']
                if project is None or not closed_runs or project.stage != closed_runs[-1].from_stage:
                    errors.append(f"project_close:{project_id}:reopened_state")
        period_run_ids = {run.pk for run in period_runs}
        project_run_ids = {run.pk for run in project_runs}
        for receipt in close_receipts:
            valid = (receipt.result_id in period_run_ids if receipt.action.startswith('period.')
                     else receipt.result_id in project_run_ids)
            if not valid:
                errors.append(f"close_receipt:{receipt.pk}:result")
        from pmo_plugins.costing.shared_cost_services import integrity_errors as shared_cost_integrity_errors
        from pmo_plugins.costing.budget_services import integrity_errors as budget_integrity_errors
        errors.extend(shared_cost_integrity_errors())
        errors.extend(budget_integrity_errors())
        result = {"ok": not errors, "errors": errors, "organizations": len(organizations), "entries": len(ledger_entries)}
        self.stdout.write(json.dumps(result, ensure_ascii=False) if options["json"] else ("完整性检查通过" if not errors else "\n".join(errors)))
        if errors: raise CommandError("项目账完整性检查未通过")
