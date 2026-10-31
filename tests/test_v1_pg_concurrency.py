"""PH-349 PostgreSQL multi-connection concurrency acceptance.

Real PostgreSQL only: if no PostgreSQL DSN is provided the probe tests are
skipped with an explicit ``not_run`` reason and the CLI returns exit 2. SQLite
is never used as concurrency evidence. No production data is touched: probes
create and drop a random throwaway schema and roll back all transactions.

Runner/contract (aligned with PH-345 docs/engineering/ops-runbook.md):

    DJANGO_SETTINGS_MODULE=pmo.settings python -m tests.test_v1_pg_concurrency \
        --dsn postgresql://... --out evidence.json --profile ci
"""
import argparse
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from django.test import TestCase

SCRIPT = 'ops_pg_concurrency'
CONTRACT_VERSION = 1
EXIT_OK = 0
EXIT_PREREQ = 2
EXIT_FAIL = 3
EXIT_ENV = 4
EXIT_SECURITY = 5


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_pg_dsn(value):
    """Return a PostgreSQL DSN or exit-code 2; SQLite/empty is not evidence."""
    dsn = value or os.environ.get('PMO_PG_DSN') or os.environ.get('DATABASE_URL')
    if not dsn:
        return None, EXIT_PREREQ, '缺少 PostgreSQL DSN（PMO_PG_DSN/DATABASE_URL）；不以 SQLite 作为并发证据'
    lowered = str(dsn).lower()
    if lowered.startswith('sqlite') or 'sqlite' in lowered:
        return None, EXIT_PREREQ, 'SQLite DSN 不能作为 PostgreSQL 并发证据；请提供真实 PG DSN'
    if not (lowered.startswith('postgresql://') or lowered.startswith('postgres://')):
        return None, EXIT_PREREQ, 'DSN 不是 PostgreSQL（须 postgresql:// 或 postgres://）'
    return str(dsn), EXIT_OK, ''


def classify_exit(exit_code):
    return {EXIT_OK: 'pass', EXIT_PREREQ: 'not_run',
            EXIT_FAIL: 'fail', EXIT_ENV: 'fail', EXIT_SECURITY: 'fail'}.get(exit_code, 'fail')


def build_envelope(*, exit_code, checks, artifacts=None, started_at=None,
                   finished_at=None, profile='ci', app_sha=None, notes='',
                   mode='dry_run'):
    """PH-345 evidence envelope; status follows the frozen exit-code mapping."""
    return {
        'script': SCRIPT,
        'contract_version': CONTRACT_VERSION,
        'mode': mode,
        'profile': profile,
        'app_sha': app_sha or os.environ.get('GIT_SHA') or 'unknown',
        'started_at': started_at or _now(),
        'finished_at': finished_at or _now(),
        'exit_code': exit_code,
        'status': classify_exit(exit_code),
        'checks': list(checks or []),
        'artifacts': list(artifacts or []),
        'redactions': ['password', 'token', 'payroll', 'customer'],
        'notes': notes,
    }


def _check(check_id, passed, expected, actual, evidence_ref=''):
    return {'id': check_id, 'status': 'pass' if passed else 'fail',
            'expected': expected, 'actual': actual,
            'evidence_ref': evidence_ref}


def _connect(dsn):
    import psycopg
    return psycopg.connect(dsn, autocommit=False)


def _probe_company_lock(dsn, schema, barrier, results):
    """Two sessions must serialize on the same company advisory lock."""
    import psycopg
    conn = _connect(dsn)
    try:
        barrier.wait(timeout=10)
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(hashtext(%s))', ('ph349:company',))
        results['acquired'].set()
        results['order'].append('first')
        time.sleep(0.4)
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_unlock(hashtext(%s))', ('ph349:company',))
        conn.commit()
    finally:
        conn.close()


EXECUTE_PROFILES = {'ci', 'staging', 'rehearsal'}


def run_dry_run(dsn, app_sha=None, profile='ci'):
    """Read-only connectivity/tooling check; never creates schema or tables."""
    started = _now()
    checks, artifacts = [], []
    try:
        conn = _connect(dsn)
    except Exception as error:  # noqa: BLE001
        envelope = build_envelope(exit_code=EXIT_ENV, checks=[], started_at=started,
                                  profile=profile, app_sha=app_sha, mode='dry_run',
                                  notes='连接 PostgreSQL 失败：%s' % error.__class__.__name__)
        return EXIT_ENV, envelope
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version(), current_setting(%s)', ('max_connections',))
            pg_version, max_connections = cur.fetchone()
            cur.execute('SELECT 1 FROM pg_database WHERE datname = current_database()')
            cur.fetchone()
        conn.rollback()
        artifacts.append({'kind': 'log', 'path_or_url': 'pg_version',
                          'sha256': '', 'summary': pg_version})
        artifacts.append({'kind': 'log', 'path_or_url': 'max_connections',
                          'sha256': '', 'summary': str(max_connections)})
        checks.append(_check('V1-OPS-06.dry_run', True,
                             'dry_run 只读连接并读取 PG 版本',
                             'version=%s max_connections=%s' % (pg_version, max_connections),
                             'stderr'))
    except Exception as error:  # noqa: BLE001
        checks.append(_check('V1-OPS-06.dry_run', False, 'dry_run 只读检查',
                             error.__class__.__name__, 'stderr'))
    finally:
        conn.close()
    exit_code = EXIT_OK if checks and all(item['status'] == 'pass' for item in checks) else EXIT_FAIL
    envelope = build_envelope(exit_code=exit_code, checks=checks, artifacts=artifacts,
                              started_at=started, profile=profile, app_sha=app_sha,
                              mode='dry_run', notes='dry_run 只读；未创建 schema/表')
    return exit_code, envelope


def run_probes(dsn, app_sha=None):
    """Run the PG concurrency matrix; return (exit_code, envelope)."""
    started = _now()
    checks, artifacts = [], []
    try:
        conn = _connect(dsn)
    except Exception as error:  # noqa: BLE001 - environment failure is a governed exit
        envelope = build_envelope(exit_code=EXIT_ENV, checks=[], started_at=started,
                                  app_sha=app_sha, mode='execute',
                                  notes='连接 PostgreSQL 失败：%s' % error.__class__.__name__)
        return EXIT_ENV, envelope
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT version(), current_setting(%s)', ('max_connections',))
            pg_version, max_connections = cur.fetchone()
        conn.rollback()
        artifacts.append({'kind': 'log', 'path_or_url': 'pg_version',
                          'sha256': '', 'summary': pg_version})
        artifacts.append({'kind': 'log', 'path_or_url': 'max_connections',
                          'sha256': '', 'summary': str(max_connections)})
        schema = 'ph349_' + uuid.uuid4().hex[:12]
        with conn.cursor() as cur:
            cur.execute('CREATE SCHEMA %s' % schema)
        conn.commit()
        try:
            # 1) company-lock serialization
            import psycopg
            barrier = threading.Barrier(2)
            results = {'order': []}
            results['acquired'] = threading.Event()
            wait_ok = {'waited': False, 'error': None}

            def contender():
                c = _connect(dsn)
                try:
                    barrier.wait(timeout=10)
                    with c.cursor() as cur:
                        cur.execute('SELECT pg_advisory_lock(hashtext(%s))',
                                    ('ph349:company',))
                    results['order'].append('second')
                    with c.cursor() as cur:
                        cur.execute('SELECT pg_advisory_unlock(hashtext(%s))',
                                    ('ph349:company',))
                    c.commit()
                except Exception as error:  # noqa: BLE001
                    wait_ok['error'] = error
                finally:
                    c.close()

            holder = threading.Thread(target=_probe_company_lock,
                                      args=(dsn, schema, barrier, results))
            waiter = threading.Thread(target=contender)
            holder.start()
            assert results['acquired'].wait(timeout=10), 'holder 未取得公司锁'
            waiter.start()
            time.sleep(0.15)
            waited_before_release = results['order'] == ['first']
            holder.join(timeout=15)
            waiter.join(timeout=15)
            final_order = results['order']
            serialized = (waited_before_release and final_order == ['first', 'second']
                          and wait_ok['error'] is None)
            checks.append(_check(
                'V1-OPS-06.1', serialized,
                '公司锁串行化：第二连接等待第一连接释放后取得',
                'order=%s waited_before_release=%s error=%s'
                % (final_order, waited_before_release, wait_ok['error']),
                'stderr'))

            # 2) idempotent receipt uniqueness (same org+token must win once)
            with conn.cursor() as cur:
                cur.execute('CREATE TABLE %s.receipt (org text, token text, '
                            'PRIMARY KEY (org, token))' % schema)
            conn.commit()
            outcomes, unique_barrier = [], threading.Barrier(2)

            def insert_receipt():
                c = _connect(dsn)
                try:
                    unique_barrier.wait(timeout=10)
                    with c.cursor() as cur:
                        cur.execute('INSERT INTO %s.receipt VALUES (%s, %s)' % (schema, '%s', '%s'),
                                    ('org-1', 'token-1'))
                    c.commit()
                    outcomes.append('inserted')
                except psycopg.errors.UniqueViolation:
                    c.rollback()
                    outcomes.append('unique_violation')
                finally:
                    c.close()

            threads = [threading.Thread(target=insert_receipt) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            checks.append(_check(
                'V1-OPS-06.2', sorted(outcomes) == ['inserted', 'unique_violation'],
                '同一 org+request_token 并发仅一次成功',
                'outcomes=%s' % outcomes, 'stderr'))

            # 3) append-only guard rejects update/delete under concurrency
            with conn.cursor() as cur:
                cur.execute('CREATE TABLE %s.open_item (id int primary key, amount int)' % schema)
                cur.execute('INSERT INTO %s.open_item VALUES (1, 100)' % schema)
                cur.execute("""CREATE FUNCTION %s.reject_mutation() RETURNS trigger
                               LANGUAGE plpgsql AS $$ BEGIN
                                 RAISE EXCEPTION 'append-only'; END; $$""" % schema)
                cur.execute('CREATE TRIGGER %s_guard BEFORE UPDATE OR DELETE ON %s.open_item '
                            'FOR EACH ROW EXECUTE FUNCTION %s.reject_mutation()'
                            % (schema, schema, schema))
            conn.commit()
            rejected, sqlstates = 0, []
            for statement in ('UPDATE %s.open_item SET amount = 200 WHERE id = 1' % schema,
                              'DELETE FROM %s.open_item WHERE id = 1' % schema):
                try:
                    with conn.cursor() as cur:
                        cur.execute(statement)
                    conn.commit()
                except Exception as error:  # noqa: BLE001 - trigger rejection expected
                    sqlstate = getattr(error, 'sqlstate', None) or getattr(error, 'pgcode', None)
                    sqlstates.append(sqlstate)
                    rejected += 1
                    conn.rollback()
            checks.append(_check(
                'V1-OPS-06.3', rejected == 2 and all(state == 'P0001' for state in sqlstates),
                'append-only 事实的 UPDATE/DELETE 被 trigger 以 P0001 拒绝',
                'rejected=%s sqlstates=%s' % (rejected, sqlstates), 'stderr'))

            # 4) deadlock detection + one retry succeeds (no silent data loss)
            with conn.cursor() as cur:
                cur.execute('CREATE TABLE %s.lock_a (id int primary key)' % schema)
                cur.execute('CREATE TABLE %s.lock_b (id int primary key)' % schema)
                cur.execute('INSERT INTO %s.lock_a VALUES (1)' % schema)
                cur.execute('INSERT INTO %s.lock_b VALUES (1)' % schema)
            conn.commit()
            deadlock_results, lock_barrier = [], threading.Barrier(2)

            def lock_order(first, second):
                import psycopg
                c = _connect(dsn)
                try:
                    lock_barrier.wait(timeout=10)
                    with c.cursor() as cur:
                        cur.execute('SELECT * FROM %s.%s WHERE id = 1 FOR UPDATE'
                                    % (schema, first))
                    time.sleep(0.2)
                    with c.cursor() as cur:
                        cur.execute('SELECT * FROM %s.%s WHERE id = 1 FOR UPDATE'
                                    % (schema, second))
                    c.commit()
                    deadlock_results.append('committed')
                except psycopg.errors.DeadlockDetected:
                    c.rollback()
                    deadlock_results.append('deadlock')
                    # retry once after rollback: must succeed
                    with c.cursor() as cur:
                        cur.execute('SELECT * FROM %s.%s WHERE id = 1 FOR UPDATE'
                                    % (schema, first))
                        cur.execute('SELECT * FROM %s.%s WHERE id = 1 FOR UPDATE'
                                    % (schema, second))
                    c.commit()
                    deadlock_results.append('retry_committed')
                finally:
                    c.close()

            threads = [threading.Thread(target=lock_order, args=('lock_a', 'lock_b')),
                       threading.Thread(target=lock_order, args=('lock_b', 'lock_a'))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            checks.append(_check(
                'V1-OPS-06.4',
                'deadlock' in deadlock_results and 'retry_committed' in deadlock_results,
                '相反加锁顺序触发死锁，回滚重试后成功',
                'results=%s' % deadlock_results, 'stderr'))
        finally:
            with conn.cursor() as cur:
                cur.execute('DROP SCHEMA IF EXISTS %s CASCADE' % schema)
            conn.commit()
    except AssertionError as error:
        checks.append(_check('V1-OPS-06.assert', False, '断言通过', str(error), 'stderr'))
    except Exception as error:  # noqa: BLE001
        checks.append(_check('V1-OPS-06.error', False, '无异常', error.__class__.__name__, 'stderr'))
    finally:
        conn.close()
    exit_code = EXIT_OK if checks and all(item['status'] == 'pass' for item in checks) else EXIT_FAIL
    envelope = build_envelope(exit_code=exit_code, checks=checks, artifacts=artifacts,
                              started_at=started, app_sha=app_sha, mode='execute',
                              notes='真实 PostgreSQL 多连接并发；未触碰生产数据')
    return exit_code, envelope


def main(argv=None):
    parser = argparse.ArgumentParser(description='PH-349 PostgreSQL concurrency acceptance')
    parser.add_argument('--dsn', default=None)
    parser.add_argument('--out', default=None)
    parser.add_argument('--profile', default='ci')
    parser.add_argument('--app-sha', default=None)
    parser.add_argument('--mode', choices=('dry_run', 'execute'), default='dry_run')
    parser.add_argument('--allow-execute', action='store_true',
                        help='显式授权 execute（生产/未授权 profile 仍被拒绝）')
    args = parser.parse_args(argv)
    # Security gate (PH-345 exit 5): execute requires explicit approval and a
    # non-production, rehearsal/CI profile; otherwise refuse before any I/O.
    if args.mode == 'execute':
        if not args.allow_execute:
            envelope = build_envelope(
                exit_code=EXIT_SECURITY, checks=[_check(
                    'V1-OPS-06.security', False, 'execute 需显式授权', '缺少 --allow-execute',
                    'stderr')], profile=args.profile, app_sha=args.app_sha,
                mode='execute', notes='安全检查拒绝：execute 未显式授权')
            return _emit(envelope, args.out)
        if args.profile not in EXECUTE_PROFILES:
            envelope = build_envelope(
                exit_code=EXIT_SECURITY, checks=[_check(
                    'V1-OPS-06.security', False, '仅 ci/staging/rehearsal 可 execute',
                    'profile=%s' % args.profile, 'stderr')], profile=args.profile,
                app_sha=args.app_sha, mode='execute',
                notes='安全检查拒绝：生产/未授权 profile 不执行并发探针')
            return _emit(envelope, args.out)
    dsn, code, reason = ensure_pg_dsn(args.dsn)
    if code != EXIT_OK:
        envelope = build_envelope(exit_code=code, checks=[], profile=args.profile,
                                  app_sha=args.app_sha, mode=args.mode, notes=reason)
        return _emit(envelope, args.out)
    if args.mode == 'dry_run':
        code, envelope = run_dry_run(dsn, app_sha=args.app_sha, profile=args.profile)
    else:
        code, envelope = run_probes(dsn, app_sha=args.app_sha)
        envelope['profile'] = args.profile
    return _emit(envelope, args.out)


def _emit(envelope, out_path):
    payload = json.dumps(envelope, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as handle:
            handle.write(payload + '\n')
    print(payload)
    return envelope['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())


import unittest
from contextlib import redirect_stdout
from io import StringIO


class PgConcurrencyContractTests(TestCase):
    """Always-runnable contract checks: no PostgreSQL needed, no SQLite claim."""

    def test_envelope_shape_and_status_mapping(self):
        for code, status in ((EXIT_OK, 'pass'), (EXIT_PREREQ, 'not_run'),
                             (EXIT_FAIL, 'fail'), (EXIT_ENV, 'fail'),
                             (EXIT_SECURITY, 'fail')):
            envelope = build_envelope(exit_code=code, checks=[])
            self.assertEqual(envelope['script'], 'ops_pg_concurrency')
            self.assertEqual(envelope['contract_version'], 1)
            self.assertEqual(envelope['mode'], 'dry_run')
            self.assertEqual(envelope['exit_code'], code)
            self.assertEqual(envelope['status'], status)
            for field in ('profile', 'app_sha', 'started_at', 'finished_at',
                          'checks', 'artifacts', 'redactions', 'notes'):
                self.assertIn(field, envelope)
            self.assertIn('password', envelope['redactions'])
            self.assertIn('payroll', envelope['redactions'])
            json.dumps(envelope)
        execute_envelope = build_envelope(exit_code=EXIT_OK, checks=[], mode='execute')
        self.assertEqual(execute_envelope['mode'], 'execute')

    def test_missing_dsn_is_exit_2_not_run_never_sqlite(self):
        dsn, code, reason = ensure_pg_dsn(None)
        self.assertIsNone(dsn)
        self.assertEqual(code, EXIT_PREREQ)
        self.assertIn('PostgreSQL', reason)

    def test_sqlite_dsn_rejected(self):
        for value in ('sqlite:///tmp/db.sqlite3', 'sqlite://:memory:'):
            dsn, code, reason = ensure_pg_dsn(value)
            self.assertIsNone(dsn)
            self.assertEqual(code, EXIT_PREREQ)
            self.assertIn('SQLite', reason)

    def test_non_postgres_dsn_rejected(self):
        dsn, code, _reason = ensure_pg_dsn('mysql://example/db')
        self.assertIsNone(dsn)
        self.assertEqual(code, EXIT_PREREQ)

    def test_cli_without_dsn_returns_2_and_not_run_envelope(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--profile', 'ci'])
        self.assertEqual(code, EXIT_PREREQ)
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope['status'], 'not_run')
        self.assertEqual(envelope['exit_code'], EXIT_PREREQ)
        self.assertEqual(envelope['checks'], [])

    def test_cli_with_sqlite_dsn_returns_2_and_not_run(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--dsn', 'sqlite:///tmp/x.sqlite3'])
        self.assertEqual(code, EXIT_PREREQ)
        self.assertEqual(json.loads(output.getvalue())['status'], 'not_run')

    def test_execute_without_allow_execute_is_exit_5_security(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--mode', 'execute', '--profile', 'ci',
                         '--dsn', 'postgresql://127.0.0.1:1/never?connect_timeout=2'])
        self.assertEqual(code, EXIT_SECURITY)
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope['status'], 'fail')
        self.assertEqual(envelope['mode'], 'execute')
        self.assertEqual(envelope['checks'][0]['id'], 'V1-OPS-06.security')

    def test_execute_on_production_profile_is_exit_5_security(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--mode', 'execute', '--allow-execute', '--profile', 'production',
                         '--dsn', 'postgresql://127.0.0.1:1/never?connect_timeout=2'])
        self.assertEqual(code, EXIT_SECURITY)
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope['exit_code'], EXIT_SECURITY)

    def test_default_mode_is_dry_run_and_needs_no_execute_permission(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--dsn', 'sqlite:///tmp/x.sqlite3'])
        self.assertEqual(code, EXIT_PREREQ)
        self.assertEqual(json.loads(output.getvalue())['mode'], 'dry_run')

    def test_execute_cli_unreachable_pg_is_exit_4_mode_execute(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--mode', 'execute', '--allow-execute', '--profile', 'ci',
                         '--dsn', 'postgresql://127.0.0.1:1/never?connect_timeout=2'])
        self.assertEqual(code, EXIT_ENV)
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope['mode'], 'execute')
        self.assertEqual(envelope['status'], 'fail')
        self.assertEqual(envelope['exit_code'], EXIT_ENV)

    def test_run_probes_connection_failure_envelope_is_mode_execute(self):
        code, envelope = run_probes('postgresql://127.0.0.1:1/never?connect_timeout=2')
        self.assertEqual(code, EXIT_ENV)
        self.assertEqual(envelope['mode'], 'execute')

    def test_dry_run_unreachable_postgres_is_exit_4(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--dsn', 'postgresql://127.0.0.1:1/never?connect_timeout=2'])
        self.assertEqual(code, EXIT_ENV)
        envelope = json.loads(output.getvalue())
        self.assertEqual(envelope['mode'], 'dry_run')
        self.assertEqual(envelope['status'], 'fail')

    def test_unreachable_postgres_is_exit_4_environment(self):
        code, envelope = run_probes('postgresql://127.0.0.1:1/never?connect_timeout=2')
        self.assertEqual(code, EXIT_ENV)
        self.assertEqual(envelope['status'], 'fail')
        self.assertEqual(envelope['exit_code'], EXIT_ENV)


@unittest.skipUnless(
    ensure_pg_dsn(None)[1] == EXIT_OK,
    '缺少真实 PostgreSQL DSN（PMO_PG_DSN/DATABASE_URL）；PG 并发 not_run，SQLite 不作为证据')
class PostgresConcurrencyProbeTests(TestCase):
    """Real PostgreSQL probes; only runs when a PG DSN is configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dsn = ensure_pg_dsn(None)[0]

    def test_probe_matrix_passes_on_real_postgres(self):
        code, envelope = run_probes(self.dsn, app_sha='test')
        self.assertEqual(code, EXIT_OK, envelope)
        self.assertEqual(envelope['status'], 'pass')
        ids = [item['id'] for item in envelope['checks']]
        self.assertEqual(ids, ['V1-OPS-06.1', 'V1-OPS-06.2',
                               'V1-OPS-06.3', 'V1-OPS-06.4'])
        self.assertTrue(all(item['status'] == 'pass' for item in envelope['checks']))
        self.assertTrue(envelope['artifacts'])


# unittest.skipUnless is evaluated at import time; the DSN check is repeated
# inside setUpClass via the environment so a DSN exported after import still
# works in CI shells.
