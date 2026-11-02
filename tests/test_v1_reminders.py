"""PH-355 read-only reminder refs, digest vectors and scoped scanning."""
import uuid
import json
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core import reminder_services as reminders
from core.models import AuditEvent, DomainEvent, PluginState
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
from tests.test_v1_invoice_import import InvoiceImportFixture


class ReminderFixture(InvoiceImportFixture):
    def settlement(self, amount=40000, *, project=None):
        """Create the overdue item at the settlement's legal submitted stage."""
        line, project = self.line, project or self.project
        due_on = self.today - timedelta(days=1)
        settlement = Settlement.objects.create(
            organization=self.org, project=project, contract_line=line,
            contract_version=line.contract_version, selection=line.policy_selection,
            counterparty=self.contract.party,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=self.today, due_date=due_on,
            gross_cents=amount, net_cents=amount, tax_cents=0,
            status='submitted', created_by=self.owner)
        OpenItem.objects.create(
            organization=self.org, project=project, settlement=settlement,
            partition_kind='due', partition_key='reminder:' + uuid.uuid4().hex, term_ref={},
            counterparty=self.contract.party,
            payee_ref={'kind': 'counterparty', 'id': str(self.contract.party_id)},
            scope={'kind': 'project', 'id': str(project.pk)}, source={},
            gross_cents=amount, tax_cents=0, due_date=due_on, occurred_on=self.today,
            status='active')
        Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        settlement.refresh_from_db()
        return settlement

    def setUp(self):
        super().setUp()
        self.yesterday = self.today - timedelta(days=1)

    def scan(self, member=None, **kwargs):
        return reminders.scan_reminders(member or self.owner, **kwargs)

    def items(self, member=None, code='settlement_open_item_overdue', **kwargs):
        return [row for row in self.scan(member, **kwargs)['reminders']
                if row['issue_code'] == code]


class ReminderDigestTests(ReminderFixture, TestCase):
    def test_empty_golden_digest(self):
        envelope = {
            'as_of': '2026-10-31', 'contract': 'core.reminders.read/v1',
            'contract_version': 1, 'issues': [], 'missing': [],
            'notifications_sent': 0, 'period': None, 'read_only': True,
            'reminders': [], 'role': 'owner', 'scan_key': 'a' * 64,
            'truncated': False,
        }
        self.assertEqual(reminders.scan_digest(envelope),
                         '7e9894b14caea9a07a28855a35eb44193a74466775f79fdd518d298ed7921a65')

    def test_changed_source_golden_digest(self):
        envelope = {
            'as_of': '2026-10-31', 'contract': 'core.reminders.read/v1',
            'contract_version': 1, 'issues': [], 'missing': [],
            'notifications_sent': 0, 'period': None, 'read_only': True,
            'reminders': [{
                'as_of': '2026-10-31', 'deeplink': '/modules/settlement/coverage/',
                'due_on': '2026-10-01', 'issue_code': 'settlement_open_item_overdue',
                'missing': [],
                'project_ref': {'id': '33333333-3333-5333-8333-333333333333',
                                'kind': 'project', 'namespace': 'core', 'version': 1},
                'recipient_scope': {'id': 'finance', 'kind': 'role'},
                'refs': [], 'reminder_key': 'b' * 64,
                'reminder_ref': {'id': '11111111-1111-5111-8111-111111111111',
                                 'kind': 'reminder_ref', 'namespace': 'core', 'version': 1},
                'severity': 'warning', 'source_owner': 'settlement',
                'source_ref': {'id': '22222222-2222-5222-8222-222222222222',
                               'kind': 'open_item', 'namespace': 'settlement', 'version': 1},
                'summary': '只读派生', 'title': '未清项逾期',
            }],
            'role': 'owner', 'scan_key': 'a' * 64, 'truncated': False,
        }
        self.assertEqual(reminders.scan_digest(envelope),
                         '42d1a265ac5ce9176fd8938233628cd698cb1f117fc63fd0de3f6847ba4036b8')

    def test_reminder_key_and_ref_are_stable_across_scan_dates(self):
        values = dict(
            member=self.owner, issue_code='settlement_open_item_overdue',
            source_ref=self.open_item.item_ref, source_owner='settlement',
            project_ref={'namespace': 'core', 'kind': 'project',
                         'id': str(self.project.pk), 'version': 1},
            recipient_scope={'kind': 'role', 'id': 'finance'},
            title='未清项逾期', summary='只读派生', due_on=self.yesterday,
            deeplink='/modules/settlement/settlement-coverage-issues/')
        first = reminders.build_reminder(as_of=self.yesterday, **values)
        second = reminders.build_reminder(as_of=self.today, **values)
        self.assertEqual(first['reminder_key'], second['reminder_key'])
        self.assertEqual(first['reminder_ref'], second['reminder_ref'])
        self.assertEqual(first['reminder_ref']['id'], str(uuid.uuid5(
            uuid.NAMESPACE_URL, 'pmo:reminder:' + first['reminder_key'])))
        self.assertNotEqual(first['as_of'], second['as_of'])


class ReminderScanTests(ReminderFixture, TestCase):
    def test_repeat_scan_is_idempotent_and_has_no_writes(self):
        before = (AuditEvent.objects.count(), DomainEvent.objects.count())
        first = self.scan()
        second = self.scan()
        rows = self.items()
        self.assertTrue(rows)
        self.assertEqual(len(rows), len({row['reminder_key'] for row in rows}))
        self.assertEqual(first['scan_digest'], second['scan_digest'])
        self.assertEqual(first['scan_key'], second['scan_key'])
        self.assertTrue(first['read_only'])
        self.assertEqual(first['notifications_sent'], 0)
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count()))

    def test_fact_resolution_removes_active_reminder(self):
        self.assertTrue(self.items())
        Clearance.objects.create(
            organization=self.org, open_item=self.open_item,
            cash_transaction_id=uuid.uuid4(), cash_transaction_ref={},
            cleared_cents=self.open_item.gross_cents, occurred_on=self.today,
            status='active', created_by=self.owner)
        self.assertEqual(self.items(), [])

    def test_role_scope_and_revocation(self):
        owner_rows = self.items()
        pm_rows = self.items(self.pm)
        self.assertTrue(pm_rows)
        self.assertTrue(all(row['recipient_scope']['kind'] == 'project' for row in pm_rows))
        self.assertEqual(self.items(self.plain), [])
        self.assertEqual({row['reminder_key'] for row in self.items(self.auditor)},
                         {row['reminder_key'] for row in owner_rows})

        from core.models import Project
        other_pm = self.member('pm')
        other_project = Project.objects.create(
            organization=self.org, code='OTHER', name='其他项目', manager=other_pm,
            start_date=self.today, end_date=self.today)
        other_settlement = ReminderFixture.settlement(self, project=other_project)
        other_item = other_settlement.items.first()
        self.assertNotIn(str(other_item.pk),
                         {row['source_ref']['id'] for row in self.items(self.pm)})
        self.assertIn(str(other_item.pk),
                      {row['source_ref']['id'] for row in self.items(other_pm)})

    def test_cross_org_source_never_leaks(self):
        other = ReminderFixture()
        other.setUp()
        other_open_item = other.open_item
        own_ids = {row['source_ref']['id'] for row in self.items()}
        self.assertNotIn(str(other_open_item.pk), own_ids)
        self.assertIn(str(other_open_item.pk),
                      {row['source_ref']['id'] for row in self.items(other.owner)})

    def test_disabled_module_returns_missing_without_new_reminders(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        result = self.scan()
        self.assertIn('module_disabled_history', result['missing'])
        self.assertEqual(self.items(), [])

    def test_invalid_dates_and_revoked_member_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.scan(as_of=self.today + timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.scan(period='2026-13')
        self.plain.active = False
        self.plain.save()
        with self.assertRaises(PermissionDenied):
            self.scan(self.plain)


class ReminderOwnerAdapterTests(ReminderFixture, TestCase):
    def test_cash_plan_overdue_and_period_filter(self):
        from core.models import CashPlan
        plan = CashPlan.objects.create(
            project=self.project, direction='in', title='逾期计划', counterparty='演练客户',
            amount_cents=10000, due_date=self.yesterday, reference='REM-CASH',
            created_by=self.owner.user, approved=True, active=True)
        rows = self.items(code='cash_plan_overdue')
        self.assertTrue(rows)
        self.assertTrue(all(row['due_on'] == self.yesterday.isoformat() for row in rows))
        self.assertTrue(self.items(code='cash_plan_overdue', period=self.yesterday.strftime('%Y-%m')))
        other_month = (self.today - timedelta(days=40)).strftime('%Y-%m')
        self.assertEqual(self.items(code='cash_plan_overdue', period=other_month), [])

    def test_contract_pending_and_terminated(self):
        from tests.test_v1_contract_change import ContractChangeFixture
        change = self.draft_change(lines=[ContractChangeFixture.line(self, 11000)])
        pending = self.items(code='contract_change_pending')
        self.assertIn(str(change.pk), {row['source_ref']['id'] for row in pending})
        self.decide(change)
        termination = self.draft_change(change_type='termination')
        self.decide(termination)
        terminated = self.items(code='contract_terminated')
        self.assertIn(str(self.contract.pk), {row['source_ref']['id'] for row in terminated})
        self.assertTrue(all(row['severity'] == 'blocker' for row in terminated))

    def test_billable_confirmation_uses_approved_project_time(self):
        from core.models import CostRate, Employee
        from core.services import review, submit_time
        from pmo_plugins.time.models import BillableConfirmation
        employee = Employee.objects.create(
            organization=self.org, name='演练成员', title='工程师', member=self.plain)
        CostRate.objects.create(
            employee=employee, effective_from=self.today - timedelta(days=365),
            hourly_cents=10000, basis='演练费率')
        timesheet = submit_time(self.owner, {
            'project': self.project, 'employee': employee, 'work_date': self.today,
            'minutes': 60, 'note': '演练工时', 'task': None}, uuid.uuid4())
        review(self.pm, 'time', timesheet.pk, 'approve', '工作内容已确认')
        row = BillableConfirmation.objects.create(
            organization=self.org, status='draft',
            timesheet_ref={'namespace': 'core', 'kind': 'timesheet',
                           'id': str(timesheet.pk), 'version': 1},
            timesheet_id=uuid.uuid4(), contract_line_ref={},
            contract_line_id=self.line.pk, confirmed_minutes=60,
            confirmed_on=self.today, created_by_member=self.owner)
        rows = self.items(self.finance, code='billable_confirmation_pending')
        self.assertIn(str(row.pk), {item['source_ref']['id'] for item in rows})
        self.assertEqual(self.items(self.plain, code='billable_confirmation_pending'), [])

    def test_tax_qualification_and_clearance_pending(self):
        from pmo_plugins.invoicing.models import TaxQualification
        invoice = self.record()
        qualification = TaxQualification.objects.create(
            organization=self.org, source_ref=invoice['invoice_ref'], tax_cents=100,
            status='pending')
        tax_rows = self.items(self.finance, code='tax_qualification_pending')
        self.assertIn(str(qualification.pk), {row['source_ref']['id'] for row in tax_rows})
        clearance = Clearance.objects.create(
            organization=self.org, open_item=self.open_item,
            cash_transaction_id=uuid.uuid4(), cash_transaction_ref={}, cleared_cents=1,
            occurred_on=self.today, status='active', created_by=self.owner)
        clearance_rows = self.items(self.finance, code='settlement_clearance_pending')
        self.assertIn(str(clearance.pk), {row['source_ref']['id'] for row in clearance_rows})

    def test_budget_missing_and_overrun(self):
        missing = self.items(self.finance, code='budget_missing')
        self.assertTrue(missing)
        self.assertTrue(all(row['missing'] == ['approved_budget'] for row in missing))
        snapshot = {'missing': [], 'budget_missing': False, 'over_budget_cents': 1}
        with mock.patch('pmo_plugins.costing.budget_services.budget_etc_snapshot',
                        return_value=snapshot):
            rows = self.items(self.finance, code='budget_overrun')
        self.assertTrue(rows)
        self.assertTrue(all(row['severity'] == 'blocker' for row in rows))

    def test_close_blocker_uses_existing_project_ref(self):
        project_ref = {'namespace': 'core', 'kind': 'project',
                       'id': str(self.project.pk), 'version': 1}
        snapshot = {'missing': [], 'issues': [{
            'severity': 'blocker', 'issue_code': 'period_closed', 'source_refs': [project_ref]}]}
        with mock.patch('pmo_plugins.insights.precheck_services.period_precheck_snapshot',
                        return_value=snapshot):
            rows = self.items(code='close_precheck_blocker')
            pm_rows = self.items(self.pm, code='close_precheck_blocker')
        self.assertTrue(rows)
        self.assertTrue(pm_rows)
        self.assertTrue(all(row['project_ref'] == project_ref for row in pm_rows))

    def test_import_conflict_pending(self):
        self.persist(
            self.invoice_rows('INV-REMINDER'), conflicts=[{
                'row_number': 2, 'field': 'amount', 'code': 'bad',
                'message': '演练冲突', 'severity': 'blocker'}])
        rows = self.items(self.finance, code='import_conflict_pending')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_ref']['namespace'], 'onboarding')

    def test_agent_token_is_private_to_current_member(self):
        from core.models import AgentToken
        token = AgentToken.objects.create(
            member=self.plain, name='演练令牌', prefix='pmo-test', token_hash='a' * 64,
            auth_snapshot='b' * 64, scopes=[], project_ids=[],
            expires_at=timezone.now() + timedelta(days=2))
        rows = self.items(self.plain, code='agent_token_expiring')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_ref']['id'], str(token.pk))
        self.assertEqual(rows[0]['recipient_scope'], {'kind': 'member', 'id': str(self.plain.pk)})
        self.assertEqual(self.items(self.owner, code='agent_token_expiring'), [])

    def test_scan_is_stably_truncated_to_fifty_items(self):
        for _ in range(17):
            ReminderFixture.settlement(self, project=self.project)
        first = self.scan()
        second = self.scan()
        self.assertEqual(len(first['reminders']), 50)
        self.assertTrue(first['truncated'])
        self.assertEqual(first['scan_digest'], second['scan_digest'])
        self.assertEqual([row['reminder_key'] for row in first['reminders']],
                         [row['reminder_key'] for row in second['reminders']])

    def test_scan_due_command_outputs_read_only_envelope(self):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        call_command('scan_due', member=self.owner.pk, stdout=output)
        result = json.loads(output.getvalue())
        self.assertTrue(result['read_only'])
        self.assertEqual(result['notifications_sent'], 0)
        self.assertTrue(any(row['issue_code'] == 'settlement_open_item_overdue'
                            for row in result['reminders']))


class ReminderSecurityRegressionTests(ReminderFixture, TestCase):
    def test_cross_org_conflict_ref_is_removed_and_own_project_reaches_pm(self):
        other = ReminderFixture()
        other.setUp()
        external_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                        'id': str(other.line.pk), 'version': 1}
        self.persist(self.invoice_rows('INV-EXT-REF'), conflicts=[{
            'row_number': 2, 'field': 'amount', 'code': 'external',
            'message': '外部来源', 'severity': 'blocker', 'source_ref': external_ref}])
        rows = self.items(self.finance, code='import_conflict_pending')
        self.assertTrue(rows)
        self.assertNotIn(str(other.line.pk),
                         {ref['id'] for row in rows for ref in row['refs']})

        own_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                   'id': str(self.line.pk), 'version': 1}
        self.persist(self.invoice_rows('INV-OWN-REF'), conflicts=[{
            'row_number': 2, 'field': 'amount', 'code': 'own',
            'message': '本项目来源', 'severity': 'blocker', 'source_ref': own_ref}])
        pm_rows = self.items(self.pm, code='import_conflict_pending')
        self.assertEqual(len(pm_rows), 1)
        self.assertEqual(pm_rows[0]['project_ref']['id'], str(self.project.pk))

    def test_delivery_refs_cannot_cross_org(self):
        other = ReminderFixture()
        other.setUp()
        external_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                        'id': str(other.line.pk), 'version': 1}
        snapshot = {'missing': [], 'issues': [{
            'severity': 'blocker', 'issue_code': 'period_closed',
            'source_refs': [external_ref]}]}
        with mock.patch('pmo_plugins.insights.precheck_services.period_precheck_snapshot',
                        return_value=snapshot):
            rows = self.items(code='close_precheck_blocker')
        self.assertTrue(rows)
        self.assertTrue(all(row['source_ref'] == {
            'namespace': 'core', 'kind': 'organization',
            'id': str(self.org.pk), 'version': 1} for row in rows))
        self.assertNotIn(str(other.line.pk),
                         {ref['id'] for row in rows for ref in row['refs']})

    def test_multi_project_invoice_reaches_each_pm(self):
        from core.models import Project
        from pmo_plugins.invoicing import services
        from pmo_plugins.invoicing.models import TaxQualification
        other_pm = self.member('pm')
        other_project = Project.objects.create(
            organization=self.org, code='P-MULTI', name='第二项目', manager=other_pm,
            start_date=self.today, end_date=self.today)
        other_settlement = ReminderFixture.settlement(self, project=other_project)
        other_item = other_settlement.items.first()
        invoice = self.record()
        lines = self.lines()
        lines[1]['allocations'][0]['business_source_ref'] = other_item.item_ref
        services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'], lines=lines,
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        qualification = TaxQualification.objects.create(
            organization=self.org, source_ref=invoice['invoice_ref'], tax_cents=100,
            status='pending')
        for pm, project in ((self.pm, self.project), (other_pm, other_project)):
            rows = self.items(pm, code='tax_qualification_pending')
            self.assertIn(str(qualification.pk), {row['source_ref']['id'] for row in rows})
            self.assertTrue(any(row['project_ref']['id'] == str(project.pk) for row in rows))

    def test_future_clearance_does_not_change_historical_scan(self):
        self.assertEqual(len(self.items(as_of=self.today)), 3)
        Clearance.objects.create(
            organization=self.org, open_item=self.open_item,
            cash_transaction_id=uuid.uuid4(), cash_transaction_ref={},
            cleared_cents=self.open_item.gross_cents,
            occurred_on=self.today + timedelta(days=1), status='active', created_by=self.owner)
        self.assertEqual(len(self.items(as_of=self.today)), 3)
        self.assertEqual(self.items(
            self.finance, code='settlement_clearance_pending', as_of=self.today), [])

    def test_cash_plan_uses_ledger_as_of(self):
        from core.models import CashPlan
        from core.services import review, submit_document
        historical_day = self.today - timedelta(days=1)
        plan = CashPlan.objects.create(
            project=self.project, direction='in', title='历史计划', counterparty='演练客户',
            amount_cents=10000, due_date=historical_day - timedelta(days=1),
            reference='REM-HISTORY', created_by=self.owner.user, approved=True, active=True)
        receipt = submit_document(self.owner, {
            'project': self.project, 'kind': 'receipt', 'title': '当日到账',
            'amount_cents': 10000, 'tax_cents': 0, 'occurred_on': self.today,
            'reference': 'REM-RECEIPT', 'counterparty': '演练客户', 'plan': plan,
            'category': 'other'}, uuid.uuid4())
        review(self.finance, 'document', receipt.pk, 'approve', '到账已核对')
        rows = self.items(code='cash_plan_overdue', as_of=historical_day)
        self.assertIn(str(plan.pk), {row['source_ref']['id'] for row in rows})

    def test_member_does_not_scan_disabled_company_sources(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        result = self.scan(self.plain)
        self.assertEqual(result['reminders'], [])
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['issues'], [])

    def test_insights_disabled_suppresses_precheck_reminders(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='insights',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        snapshot = {'missing': [], 'issues': [{
            'severity': 'blocker', 'issue_code': 'period_closed', 'source_refs': []}]}
        with mock.patch('pmo_plugins.insights.precheck_services.period_precheck_snapshot',
                        return_value=snapshot) as source:
            result = self.scan()
        self.assertNotIn('close_precheck_blocker',
                         {row['issue_code'] for row in result['reminders']})
        self.assertIn('module_disabled_history', result['missing'])
        source.assert_not_called()

    def test_budget_source_refs_use_registered_derived_kinds(self):
        missing = self.items(self.finance, code='budget_missing')
        self.assertTrue(all(row['source_ref']['namespace'] == 'costing'
                            and row['source_ref']['kind'] == 'project_budget_version'
                            for row in missing))
        snapshot = {'missing': [], 'budget_missing': False, 'over_budget_cents': 1}
        with mock.patch('pmo_plugins.costing.budget_services.budget_etc_snapshot',
                        return_value=snapshot):
            overrun = self.items(self.finance, code='budget_overrun')
        self.assertTrue(all(row['source_ref'] == {
            'namespace': 'costing', 'kind': 'budget_etc_snapshot',
            'id': row['source_ref']['id'], 'version': 1} for row in overrun))

    def test_agent_token_last_day_is_inclusive(self):
        from datetime import datetime, time
        from core.models import AgentToken
        due_day = self.today + timedelta(days=30)
        token = AgentToken.objects.create(
            member=self.plain, name='第30天令牌', prefix='pmo-last-day',
            token_hash='c' * 64, auth_snapshot='d' * 64, scopes=[], project_ids=[],
            expires_at=timezone.make_aware(datetime.combine(due_day, time(23, 59))))
        rows = self.items(self.plain, code='agent_token_expiring')
        self.assertIn(str(token.pk), {row['source_ref']['id'] for row in rows})
