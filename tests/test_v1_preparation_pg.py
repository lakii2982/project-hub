"""Actual PG primitives only; no future settlement/refund business is accepted."""
import json
import os
from pathlib import Path
from queue import Queue
from threading import Event, Thread

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from core.models import Membership, Organization

ROOT = Path(__file__).resolve().parents[1]


class PostgresPreparationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            if os.getenv('PH135_REQUIRE_PG') == '1':
                self.fail('PostgreSQL qualification required but actual backend is not PostgreSQL')
            self.skipTest('Actual PostgreSQL required; SQLite is not concurrency evidence')
        fixture = json.loads((ROOT/'docs/v1.0-development/fixtures/fixture.json').read_text())
        self.organizations = [Organization.objects.create(name=e['name'],currency='CNY') for e in fixture['enterprises']]
        self.assertEqual(len(self.organizations),2)

    def connect(self):
        import psycopg
        config = connection.settings_dict
        result = psycopg.connect(dbname=config['NAME'], user=config.get('USER') or None,
            password=config.get('PASSWORD') or None, host=config.get('HOST') or None,
            port=config.get('PORT') or None, connect_timeout=5)
        result.execute("SET statement_timeout = '10000ms'")
        result.commit()
        return result

    def test_two_connections_synchronize_and_serialize_company_row_lock(self):
        import psycopg
        from psycopg import sql
        query = sql.SQL('SELECT id FROM {} WHERE id=%s FOR UPDATE').format(sql.Identifier(Organization._meta.db_table))
        locked,release = Event(),Event()
        results = Queue()
        def holder():
            try:
                with self.connect() as first:
                    first.execute(query,(self.organizations[0].pk,))
                    results.put(('pid',first.info.backend_pid))
                    locked.set()
                    if not release.wait(10):raise RuntimeError('Holder release barrier expired')
            except BaseException as error:
                results.put(('error',repr(error)))
                locked.set()
        thread=Thread(target=holder,daemon=True);thread.start()
        try:
            self.assertTrue(locked.wait(10))
            label,pid=results.get(timeout=1)
            self.assertEqual(label,'pid',pid)
            with self.connect() as second:
                self.assertNotEqual(second.info.backend_pid,pid)
                # NOWAIT proves a server-observed conflict while the holder still owns the row.
                # An Event set before issuing SQL would not prove that the second query blocked.
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    second.execute(query+sql.SQL(' NOWAIT'),(self.organizations[0].pk,))
                second.rollback()
                # The lock belongs to this company row, not a fabricated global mutex.
                second.execute(query+sql.SQL(' NOWAIT'),(self.organizations[1].pk,))
                second.rollback()
                release.set();thread.join(timeout=12)
                self.assertFalse(thread.is_alive())
                second.execute(query+sql.SQL(' NOWAIT'),(self.organizations[0].pk,))
            self.assertTrue(results.empty(),'Holder error after acquiring company row')
        finally:
            release.set();thread.join(timeout=12)
            self.assertFalse(thread.is_alive(),'No holder thread may survive qualification')
        print('PH135_PG_BARRIER_EXECUTED connections=2 backend=postgresql',flush=True)

    def test_existing_transaction_rolls_back_across_connections(self):
        from psycopg import sql
        table=sql.Identifier(Organization._meta.db_table)
        query=sql.SQL('SELECT name FROM {} WHERE id=%s').format(table)
        update=sql.SQL('UPDATE {} SET name=%s WHERE id=%s').format(table)
        original=self.organizations[0].name
        with self.connect() as first,self.connect() as second:
            self.assertNotEqual(first.info.backend_pid,second.info.backend_pid)
            first.execute(update,('synthetic-rollback-only',self.organizations[0].pk))
            self.assertEqual(first.execute(query,(self.organizations[0].pk,)).fetchone()[0],'synthetic-rollback-only')
            self.assertEqual(second.execute(query,(self.organizations[0].pk,)).fetchone()[0],original)
            first.rollback()
            self.assertEqual(second.execute(query,(self.organizations[0].pk,)).fetchone()[0],original)
            self.assertEqual(first.execute(query,(self.organizations[0].pk,)).fetchone()[0],original)
        print('PH135_PG_TRANSACTION_EXECUTED rollback=true connections=2',flush=True)

    def test_existing_company_user_unique_constraint_across_connections(self):
        import psycopg
        from psycopg import sql
        user=get_user_model().objects.create_user(username='ph135-unique')
        Membership.objects.create(organization=self.organizations[0],user=user,role=Membership.Role.MEMBER)
        connection.ensure_connection()
        insert=sql.SQL('INSERT INTO {} (organization_id,user_id,role,active) VALUES (%s,%s,%s,true)').format(sql.Identifier(Membership._meta.db_table))
        with self.connect() as other:
            self.assertNotEqual(other.info.backend_pid,connection.connection.info.backend_pid)
            with self.assertRaises(psycopg.errors.UniqueViolation) as caught:
                other.execute(insert,(self.organizations[0].pk,user.pk,Membership.Role.OWNER))
            self.assertEqual(caught.exception.diag.constraint_name,'unique_org_user')
            other.rollback()
            # Same user in another company is legal: test the actual composite scope.
            other.execute(insert,(self.organizations[1].pk,user.pk,Membership.Role.MEMBER))
        self.assertEqual(Membership.objects.filter(organization=self.organizations[0],user=user).count(),1)
        self.assertEqual(Membership.objects.filter(organization=self.organizations[1],user=user).count(),1)
        print('PH135_PG_UNIQUE_EXECUTED constraint=unique_org_user connections=2',flush=True)

    def test_fixture_enterprises_fit_existing_organization_baseline(self):
        self.assertEqual(len({o.pk for o in self.organizations}),2)
        self.assertTrue(all(o.currency=='CNY' for o in self.organizations))
        print('PH135_PG_FIXTURE_EXECUTED enterprises=2',flush=True)
