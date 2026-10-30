"""PostgreSQL multi-connection races for service-period income; SQLite is explicitly not evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase

from core.models import AccountingEvent, AccountingPeriod, LedgerPosting, Membership, SourceConsumption
from core.services import lock_company
from pmo_plugins.delivery import service_period_services as service_period
from pmo_plugins.delivery.models import ServiceConfirmation, ServiceConfirmationReversal
from pmo_plugins.management_revenue import services as revenue
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition
from tests.test_service_period_revenue import ServiceRevenueFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent connections')
class ServicePeriodConcurrencyTests(ServiceRevenueFixture, TransactionTestCase):
    def race(self, *actions):
        barrier = Barrier(len(actions))

        def call(action):
            close_old_connections()
            try:
                member = Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=15)
                try:
                    return ('ok', action(member))
                except (ValidationError, PermissionDenied) as error:
                    return ('rejected', type(error).__name__)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            results = list(pool.map(call, actions))
        self.assertEqual(sum(result[0] == 'ok' for result in results), 1, results)
        return results

    def test_same_frozen_month_different_request_tokens_recognizes_once(self):
        row = self.confirmation(start=self.today.replace(day=1),
            end=self.today.replace(day=28 if self.today.month == 2 else 28),
            schedule=[{'coverage_start': self.today.replace(day=1).isoformat(),
                       'coverage_end': self.today.replace(day=28 if self.today.month == 2 else 28).isoformat(),
                       'allocated_net_cents': 101}])
        month = row.coverage_start.strftime('%Y-%m')

        def action(member):
            return revenue.recognize_service_month(member, row.pk, service_month=month,
                expected_version=row.confirmed_version, request_token=uuid.uuid4(), dry_run=False).pk
        self.race(action, action)
        self.assertEqual(RevenueRecognition.objects.count(), 1)
        self.assertEqual(AccountingEvent.objects.filter(source__namespace='management_revenue').count(), 1)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_two_full_service_reversals_apply_once(self):
        row = self.confirmation(start=self.today.replace(day=1),
            end=self.today.replace(day=28 if self.today.month == 2 else 28),
            schedule=[{'coverage_start': self.today.replace(day=1).isoformat(),
                       'coverage_end': self.today.replace(day=28 if self.today.month == 2 else 28).isoformat(),
                       'allocated_net_cents': 101}])
        recognition = self.recognize(row, row.coverage_start.strftime('%Y-%m'))

        def action(member):
            return revenue.reverse_recognition(member, recognition.pk, reason='并发完整纠错',
                request_token=uuid.uuid4(), dry_run=False).pk
        self.race(action, action)
        self.assertEqual(RevenueAdjustment.objects.count(), 1)
        self.assertEqual(sum(AccountingEvent.objects.filter(source__namespace='management_revenue')
                             .values_list('claim_effect_cents', flat=True)), 0)

    def test_same_month_two_disjoint_parents_post_concurrently(self):
        """One natural month, two disjoint confirmed sub-ranges, concurrent income keeps the total."""
        start, end = date(2026, 10, 1), date(2026, 10, 31)
        self.approved_service_line(net=101, start=start, end=end,
            schedule=[{'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
                       'allocated_net_cents': 101}])
        first = self.confirmation(start=start, end=date(2026, 10, 15))
        second = self.confirmation(start=date(2026, 10, 16), end=end)
        barrier, results = Barrier(2), []

        def call(row):
            close_old_connections()
            try:
                member = Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=15)
                try:
                    results.append(('ok', revenue.recognize_service_month(member, row.pk,
                        service_month='2026-10', expected_version=row.confirmed_version,
                        request_token=uuid.uuid4(), dry_run=False).pk))
                except (ValidationError, PermissionDenied) as error:
                    results.append(('rejected', type(error).__name__))
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(call, [first, second]))
        self.assertEqual([item[0] for item in results], ['ok', 'ok'], results)
        rows = RevenueRecognition.objects.order_by('recognized_net_cents')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(sum(rows.values_list('recognized_net_cents', flat=True)), 101)
        self.assertEqual(set(rows.values_list('posting_period', flat=True)), {'2026-10'})
        claims = SourceConsumption.objects.filter(consumer='management_revenue',
            coverage_key='service_month:2026-10')
        self.assertEqual(claims.count(), 2)
        self.assertEqual(claims.values('source_id').distinct().count(), 2)
        self.assertEqual(LedgerPosting.objects.count(), 0)
        self.assertEqual(AccountingEvent.objects.filter(source__namespace='management_revenue').count(), 2)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_income_competes_with_company_locked_close(self):
        start, end = self.today.replace(day=1), self.today.replace(day=28 if self.today.month == 2 else 28)
        row = self.confirmation(start=start, end=end, schedule=[
            {'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
             'allocated_net_cents': 101}])
        month = start.strftime('%Y-%m')
        AccountingPeriod.objects.get_or_create(organization=self.org, period=month)

        def recognize(member):
            return revenue.recognize_service_month(member, row.pk, service_month=month,
                expected_version=row.confirmed_version, request_token=uuid.uuid4(), dry_run=False).pk

        def close(member):
            from core.revenue_accounting import check_revenue_close
            with transaction.atomic():
                org = lock_company(member)
                check_revenue_close(org, self.today)
                period = AccountingPeriod.objects.get(organization=org, period=month)
                period.status = 'posted'
                period.closed_through = self.today
                period.save()
                return period.pk
        barrier = Barrier(2)

        def call(action):
            close_old_connections()
            try:
                member = Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=15)
                try:
                    return ('ok', action(member))
                except (ValidationError, PermissionDenied):
                    return ('rejected', None)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, [recognize, close]))
        self.assertEqual(results[0][0], 'ok', results)
        self.assertEqual(RevenueRecognition.objects.count(), 1)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_same_real_coverage_across_approved_contracts_confirms_only_once(self):
        start, end = self.today.replace(day=1), self.today.replace(day=28 if self.today.month == 2 else 28)
        first = self.confirmation(start=start, end=end, confirmed=False,
            schedule=[{'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
                       'allocated_net_cents': 101}])
        first = service_period.submit_service_confirmation(self.owner, first.pk,
            expected_version=first.version, request_token=uuid.uuid4(), dry_run=False)
        self.contract = self.make_contract(contract_code='C2-same-service-coverage')
        self.line = None
        self.approved_service_line(net=101, start=start, end=end)
        second = self.confirmation(start=start, end=end, confirmed=False)
        second = service_period.submit_service_confirmation(self.owner, second.pk,
            expected_version=second.version, request_token=uuid.uuid4(), dry_run=False)

        def approve(row):
            def action(_member):
                finance = Membership.objects.get(pk=self.finance.pk)
                return service_period.review_service_confirmation(finance, row.pk,
                    expected_version=row.version, request_token=uuid.uuid4(),
                    decision='approve', note='实际客户依据独立核对', dry_run=False).pk
            return action
        self.race(approve(first), approve(second))
        self.assertEqual(ServiceConfirmation.objects.filter(status='confirmed').count(), 1)
        self.assertEqual(RevenueRecognition.objects.count(), 0)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_two_parent_reversals_apply_once(self):
        row = self.confirmation(start=self.today.replace(day=1),
            end=self.today.replace(day=28 if self.today.month == 2 else 28),
            schedule=[{'coverage_start': self.today.replace(day=1).isoformat(),
                       'coverage_end': self.today.replace(day=28 if self.today.month == 2 else 28).isoformat(),
                       'allocated_net_cents': 101}])

        def action(_member):
            finance = Membership.objects.get(pk=self.finance.pk)
            return service_period.reverse_service_confirmation(finance, row.pk,
                expected_version=row.version, reason='并发反确认', request_token=uuid.uuid4(),
                dry_run=False).pk
        self.race(action, action)
        self.assertEqual(ServiceConfirmationReversal.objects.count(), 1)

    def blocked_recognition(self, change):
        from threading import Event, Thread
        import time
        from core.models import Organization
        start, end = self.today.replace(day=1), self.today.replace(day=28 if self.today.month == 2 else 28)
        row = self.confirmation(start=start, end=end, schedule=[
            {'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
             'allocated_net_cents': 101}])
        month = start.strftime('%Y-%m')
        ready, pids, results = Event(), {}, []

        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['worker'] = cursor.fetchone()[0]
                ready.set()
                try:
                    revenue.recognize_service_month(self.owner, row.pk, service_month=month,
                        expected_version=row.confirmed_version, request_token=uuid.uuid4(),
                        dry_run=False)
                    results.append('unexpected')
                except PermissionDenied:
                    results.append('refused')
                except BaseException as error:
                    results.append(repr(error))
            finally:
                connection.close()
        thread = Thread(target=worker)
        try:
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.org.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    holder = cursor.fetchone()[0]
                thread.start()
                self.assertTrue(ready.wait(5))
                deadline, blockers = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['worker']])
                        blockers = cursor.fetchone()[0]
                    if holder in blockers:
                        break
                    time.sleep(.01)
                self.assertIn(holder, blockers)
                change()
                before = self.facts()
        finally:
            if thread.ident:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['refused'])
        self.assertEqual(before, self.facts())
        self.assertEqual(RevenueRecognition.objects.count(), 0)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_service_income_rereads_revoked_membership_after_actual_company_wait(self):
        self.blocked_recognition(lambda: Membership.objects.filter(pk=self.owner.pk).update(active=False))

    def test_service_income_rereads_disabled_module_after_actual_company_wait(self):
        from core.plugin_runtime import set_enabled
        self.blocked_recognition(lambda: set_enabled(self.owner, 'management_revenue', False))
