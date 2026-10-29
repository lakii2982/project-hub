"""PostgreSQL multi-connection races; SQLite is explicitly not evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase
from core.models import Membership, AccountingPeriod, AccountingEvent, LedgerPosting
from core.services import lock_company
from pmo_plugins.management_revenue import services as revenue
from pmo_plugins.management_revenue.models import RevenueRecognition, RevenueAdjustment
from tests.test_acceptance_revenue import RevenueFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent connections')
class AcceptanceRevenueConcurrencyTests(RevenueFixture, TransactionTestCase):
    def race(self, *actions):
        barrier=Barrier(len(actions))
        def call(action):
            close_old_connections()
            try:
                member=Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=15)
                try:
                    return ('ok',action(member))
                except (ValidationError,PermissionDenied) as error:
                    return ('rejected',type(error).__name__)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            results=list(pool.map(call,actions))
        self.assertEqual(sum(result[0]=='ok' for result in results),1,results)
        return results

    def test_same_acceptance_different_request_tokens(self):
        source=self.acceptance()
        def action(member):
            return revenue.recognize_acceptance(member,source.pk,expected_version=source.version,request_token=uuid.uuid4(),dry_run=False).pk
        self.race(action,action)
        self.assertEqual(RevenueRecognition.objects.count(),1)
        self.assertEqual(AccountingEvent.objects.filter(source__namespace='management_revenue').count(),1)
        self.assertEqual(LedgerPosting.objects.count(),0)

    def test_two_full_reversals(self):
        row=self.recognize(self.acceptance())
        def action(member):
            return revenue.reverse_recognition(member,row.pk,reason='并发完整纠错',request_token=uuid.uuid4(),dry_run=False).pk
        self.race(action,action)
        self.assertEqual(RevenueAdjustment.objects.count(),1)
        self.assertEqual(sum(AccountingEvent.objects.filter(source__namespace='management_revenue').values_list('claim_effect_cents',flat=True)),0)

    def test_income_competes_with_company_locked_close(self):
        source=self.acceptance()
        # Existing period isolates this race from period creation, and close uses the real pending-source check.
        AccountingPeriod.objects.get_or_create(organization=self.org,period=self.today.strftime('%Y-%m'))
        def recognize(member):
            return revenue.recognize_acceptance(member,source.pk,expected_version=source.version,request_token=uuid.uuid4(),dry_run=False).pk
        def close(member):
            from core.revenue_accounting import check_revenue_close
            with transaction.atomic():
                org=lock_company(member)
                check_revenue_close(org,self.today)
                row=AccountingPeriod.objects.get(organization=org,period=self.today.strftime('%Y-%m'))
                row.status='posted';row.closed_through=self.today;row.save()
                return row.pk
        # Either close refuses pending income first, or income posts before a successful close.
        barrier=Barrier(2)
        def call(action):
            close_old_connections()
            try:
                member=Membership.objects.get(pk=self.owner.pk);barrier.wait(timeout=15)
                try:return ('ok',action(member))
                except (ValidationError,PermissionDenied):return ('rejected',None)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(call,[recognize,close]))
        self.assertEqual(results[0][0],'ok',results)
        self.assertEqual(RevenueRecognition.objects.count(),1)
        self.assertEqual(LedgerPosting.objects.count(),0)

    def test_same_actual_node_across_approved_contracts_confirms_only_once(self):
        from pmo_plugins.delivery import services as delivery
        first=self.acceptance(confirmed=False)
        first=delivery.submit_acceptance(self.owner,first.pk,expected_version=first.version,
            request_token=uuid.uuid4(),dry_run=False)
        self.contract=self.make_contract(contract_code='C2-same-real-node')
        self.line=self.approved_line(101)
        second=self.acceptance(confirmed=False)
        second=delivery.submit_acceptance(self.owner,second.pk,expected_version=second.version,
            request_token=uuid.uuid4(),dry_run=False)
        def approve(row):
            def action(_member):
                finance=Membership.objects.get(pk=self.finance.pk)
                return delivery.review_acceptance(finance,row.pk,expected_version=row.version,
                    decision='approve',note='实际客户依据独立核对',request_token=uuid.uuid4(),dry_run=False).pk
            return action
        self.race(approve(first),approve(second))
        from pmo_plugins.delivery.models import Acceptance
        self.assertEqual(Acceptance.objects.filter(status='confirmed').count(),1)
        self.assertEqual(RevenueRecognition.objects.count(),0)
        self.assertEqual(LedgerPosting.objects.count(),0)

    def blocked_recognition(self, change):
        from threading import Event, Thread
        import time
        from core.models import Organization
        source=self.acceptance()
        ready,pids,results=Event(),{},[]
        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['worker']=cursor.fetchone()[0]
                ready.set()
                try:
                    revenue.recognize_acceptance(self.owner,source.pk,expected_version=source.version,
                        request_token=uuid.uuid4(),dry_run=False)
                    results.append('unexpected')
                except PermissionDenied:
                    results.append('refused')
                except BaseException as error:
                    results.append(repr(error))
            finally:
                connection.close()
        thread=Thread(target=worker)
        try:
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.org.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    holder=cursor.fetchone()[0]
                thread.start()
                self.assertTrue(ready.wait(5))
                deadline,blockers=time.monotonic()+5,[]
                while time.monotonic()<deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)',[pids['worker']])
                        blockers=cursor.fetchone()[0]
                    if holder in blockers:break
                    time.sleep(.01)
                self.assertIn(holder,blockers)
                change()
                before=self.facts()
        finally:
            if thread.ident:thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results,['refused'])
        self.assertEqual(before,self.facts())
        self.assertEqual(RevenueRecognition.objects.count(),0)
        self.assertEqual(LedgerPosting.objects.count(),0)

    def test_income_rereads_revoked_membership_after_actual_company_wait(self):
        self.blocked_recognition(lambda:Membership.objects.filter(pk=self.owner.pk).update(active=False))

    def test_income_rereads_disabled_module_after_actual_company_wait(self):
        from core.plugin_runtime import set_enabled
        self.blocked_recognition(lambda:set_enabled(self.owner,'management_revenue',False))
