"""PH-330 shared-cost allocation batch/reversal, net-zero ledger and reconciliation."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from core.models import AccountingEvent, AccountingPeriod, LedgerPosting, NonProjectDestination, Organization, Project, SourceConsumption
from pmo_plugins.costing import shared_cost_services as services
from pmo_plugins.costing.models import SharedCostAllocationBatch, SharedCostAllocationLine
from tests.test_v1_contracts import Fixture


class CostFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.plain = self.member('member')
        self.project2 = Project.objects.create(
            organization=self.org, code='P2', name='第二项目', client='客户',
            manager=self.pm, start_date=self.today, end_date=self.today)
        self.period = self.today.strftime('%Y-%m')
        self.destination = NonProjectDestination.objects.create(
            organization=self.org, code='ADMIN', name='管理共享', active=True)
        self.source_event, self.source_posting = self.add_posting(
            10000, scope_kind='nonproject', scope_id=str(self.destination.pk))
        self.source_claim = SourceConsumption.objects.get(
            pk=AccountingEvent.objects.get(pk=self.source_event.pk).claim_id)

    def add_posting(self, amount, *, scope_kind, scope_id, claim_source=None, line_index=1):
        event_id = uuid.uuid4()
        ref = {'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id), 'version': 1}
        claim = SourceConsumption.objects.create(
            organization=self.org, source=claim_source or {'namespace': 'costing', 'kind': 'test_source', 'id': str(uuid.uuid4())},
            source_id=uuid.uuid4(), consumer='cost', coverage_key='test:' + uuid.uuid4().hex,
            amount_cents=abs(amount), event_ref=ref)
        event = AccountingEvent.objects.create(
            id=event_id, organization=self.org, source=claim.source, event_type='costing.test.cost',
            occurred_on=self.today, posting_period=self.period, algorithm_version='test_v1',
            claim=claim, claim_effect_cents=0, status='posted')
        posting = LedgerPosting.objects.create(
            organization=self.org, accounting_event=event, amount_cents=amount, book='cost',
            origin='current', scope_kind=scope_kind, scope_id=scope_id, line_index=line_index,
            status='posted')
        return event, posting

    def weights(self):
        return [{'target_kind': 'project', 'target_id': str(self.project.pk), 'basis_points': 6000},
                {'target_kind': 'project', 'target_id': str(self.project2.pk), 'basis_points': 4000}]

    def allocate(self, member=None, **changes):
        values = dict(period=self.period, pool_kind='labor_nonproject', driver='fixed_weights',
                      fixed_weights=self.weights(), request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.create_shared_cost_allocation_batch(member or self.owner, **values)


class SharedCostTests(CostFixture, TestCase):
    def test_allocate_net_zero_and_reconcile(self):
        event_before = AccountingEvent.objects.count()
        result = self.allocate()
        self.assertEqual(result['amount_cents'], 10000)
        self.assertEqual([row['allocated_cents'] for row in result['allocations']], [6000, 4000])
        batch = SharedCostAllocationBatch.objects.get(pk=result['batch_ref']['id'])
        self.assertEqual(batch.kind, 'allocation')
        self.assertEqual(batch.status, 'posted')
        self.assertEqual(sum(line.allocated_cents for line in batch.lines.all()), 10000)
        self.assertEqual(AccountingEvent.objects.count(), event_before + 1)
        event = AccountingEvent.objects.get(
            source__namespace='costing', source__kind='shared_cost_allocation',
            source__id=str(batch.pk))
        self.assertEqual(event.postings.aggregate(total=__import__('django').db.models.Sum('amount_cents'))['total'], 0)
        postings = event.postings.order_by('line_index')
        self.assertEqual(postings.filter(scope_kind='nonproject', amount_cents=-10000).count(), 1)
        self.assertEqual(postings.filter(scope_kind='project').count(), 2)
        snapshot = services.shared_cost_reconciliation(self.owner, period=self.period)
        self.assertEqual(snapshot['issues'], [])
        self.assertEqual(snapshot['pools'][0]['allocated_cents'], 10000)
        with self.assertRaises(ValidationError):
            self.allocate()
        with self.assertRaises(ValidationError):
            self.allocate(fixed_weights=[{'target_kind': 'project', 'target_id': str(self.project.pk), 'basis_points': 10001}])

    def test_reverse_mirrors_and_is_single(self):
        batch = self.allocate()['batch_ref']
        reversal = services.reverse_shared_cost_allocation_batch(
            self.owner, batch['id'], reason='冲销分摊', request_token=uuid.uuid4(), dry_run=False)
        original = SharedCostAllocationBatch.objects.get(pk=batch['id'])
        reverse_batch = SharedCostAllocationBatch.objects.get(pk=reversal['batch_ref']['id'])
        self.assertEqual(original.status, 'reversed')
        self.assertEqual(reverse_batch.kind, 'reversal')
        self.assertEqual(reverse_batch.amount_cents, -10000)
        for line in reverse_batch.lines.all():
            mirror = original.lines.get(target_id=line.target_id)
            self.assertEqual(line.allocated_cents, -mirror.allocated_cents)
            self.assertEqual(line.sequence, mirror.sequence)
        event = AccountingEvent.objects.get(
            source__kind='shared_cost_allocation', source__id=str(reverse_batch.pk))
        self.assertEqual(event.postings.aggregate(total=__import__('django').db.models.Sum('amount_cents'))['total'], 0)
        with self.assertRaises(ValidationError):
            services.reverse_shared_cost_allocation_batch(
                self.owner, batch['id'], reason='再次', request_token=uuid.uuid4(), dry_run=False)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.allocate(member=self.auditor)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.allocate(period='2000-01')

    def test_request_token_replay_returns_original_result(self):
        token = uuid.uuid4()
        first = self.allocate(request_token=token)
        replay = self.allocate(request_token=token)
        self.assertEqual(first, replay)
        reverse_token = uuid.uuid4()
        first_reverse = services.reverse_shared_cost_allocation_batch(
            self.owner, first['batch_ref']['id'], reason='幂等冲销',
            request_token=reverse_token, dry_run=False)
        replay_reverse = services.reverse_shared_cost_allocation_batch(
            self.owner, first['batch_ref']['id'], reason='幂等冲销',
            request_token=reverse_token, dry_run=False)
        self.assertEqual(first_reverse, replay_reverse)

    def test_append_only_db_guards(self):
        batch = self.allocate()['batch_ref']
        with self.assertRaises(ValidationError):
            SharedCostAllocationBatch.objects.get(pk=batch['id']).delete()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM costing_sharedcostallocationbatch WHERE id=%s',
                                   [batch['id'].replace('-', '')])
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE costing_sharedcostallocationline SET allocated_cents=1 WHERE batch_id=%s',
                                   [batch['id'].replace('-', '')])


class VerificationBranchTests(CostFixture, TestCase):
    def test_verifier_catches_bad_allocation_total(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        batch = SharedCostAllocationBatch.objects.create(
            organization=self.org, period=self.period, pool_key='bad-pool',
            pool_kind='labor_nonproject', driver='fixed_weights', kind='allocation',
            status='draft', amount_cents=100, total_basis_points=10000,
            source_refs=[], source_digest='0' * 64, input_digest='0' * 64,
            created_by=self.owner)
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('shared_allocation:', output.getvalue())
