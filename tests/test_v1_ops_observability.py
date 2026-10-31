"""PH-350 V1-OPS-05 health/capacity/backlog contract tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
health = load('ph350_health', 'scripts/ops/healthcheck.py')


class HealthCapacityTests(unittest.TestCase):
    def facts(self):
        return {
            'db': {'reachable': True, 'migration_pending': 0, 'connections': 5,
                   'max_connections': 100, 'db_password': 'should-not-appear'},
            'cache': {'reachable': True, 'backend': 'redis'},
            'queue': {'worker_alive': True, 'depth': 0},
            'disk': {'free_percent': 50},
            'backup': {'age_hours': 2},
            'capacity': {'largest_table_bytes': 1024 ** 3, 'slow_query_count': 0,
                         'attachment_bytes': 1000},
            'backlog': {'queue_depth': 0, 'reminders': 0, 'todos': 0, 'unsettled': 0},
            'perf': {'samples_ms': [10, 20, 30, 40, 50], 'environment': 'local-sqlite'},
            'api_token': 'secret-value',
        }

    def collect(self, **changes):
        values = dict(profile='rehearsal', mode='dry_run', facts=self.facts())
        values.update(changes)
        return health.run_healthcheck(**values)

    def test_complete_local_facts_pass_and_envelope(self):
        code, evidence = self.collect()
        self.assertEqual(code, 0)
        self.assertEqual(evidence['script'], 'ops_health_capacity')
        self.assertEqual(evidence['contract_version'], 1)
        self.assertEqual(evidence['status'], 'pass')
        self.assertEqual(evidence['exit_code'], 0)
        ids = [c['id'] for c in evidence['checks']]
        for expected in ('health.db', 'health.migrations', 'health.cache', 'health.queue',
                         'health.disk', 'health.backup', 'capacity.connections',
                         'capacity.tables', 'capacity.slow_queries',
                         'capacity.attachments', 'backlog.queue_depth',
                         'backlog.reminders', 'backlog.todos', 'backlog.unsettled',
                         'perf.p50', 'perf.p95'):
            self.assertIn(expected, ids)
        self.assertTrue(evidence['started_at'] and evidence['finished_at'])
        self.assertEqual(evidence['redactions'], health.REDACTIONS)
        self.assertNotIn('should-not-appear', json.dumps(evidence))
        self.assertNotIn('secret-value', json.dumps(evidence))
        self.assertIn('password', evidence['redactions'])

    def test_missing_environment_and_no_samples_not_run(self):
        code, evidence = health.run_healthcheck('rehearsal', 'dry_run', facts={})
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        self.assertIn('environment.supplied',
                      [c['id'] for c in evidence['checks'] if c['status'] == 'not_run'])
        facts = self.facts(); facts.pop('perf')
        code, evidence = self.collect(facts=facts)
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        self.assertIn('perf.samples',
                      [c['id'] for c in evidence['checks'] if c['status'] == 'not_run'])

    def test_execute_refused_outside_rehearsal(self):
        for profile in ('staging', 'production'):
            code, evidence = self.collect(profile=profile, mode='execute')
            self.assertEqual(code, 5)
            self.assertEqual(evidence['status'], 'blocked')
            self.assertIn('safety.execute_profile',
                          [c['id'] for c in evidence['checks']])

    def test_backlog_disk_backup_and_migration_failures(self):
        facts = self.facts(); facts['backlog']['reminders'] = 200
        code, evidence = self.collect(facts=facts)
        self.assertEqual(code, 3)
        self.assertEqual(evidence['status'], 'fail')
        self.assertIn('backlog.reminders',
                      [c['id'] for c in evidence['checks'] if c['status'] == 'fail'])
        facts = self.facts(); facts['disk']['free_percent'] = 5
        self.assertEqual(self.collect(facts=facts)[0], 3)
        facts = self.facts(); facts['backup']['age_hours'] = 80
        self.assertEqual(self.collect(facts=facts)[0], 3)
        facts = self.facts(); facts['db']['migration_pending'] = 2
        self.assertEqual(self.collect(facts=facts)[0], 3)
        facts = self.facts(); facts['capacity']['connections'] = 90
        self.assertEqual(self.collect(facts=facts)[0], 3)

    def test_perf_baseline_values(self):
        code, evidence = self.collect()
        checks = {c['id']: c for c in evidence['checks']}
        self.assertEqual(code, 0)
        self.assertEqual(checks['perf.p50']['actual'], 30.0)
        self.assertEqual(checks['perf.p95']['actual'], 50.0)
        self.assertTrue(any(a['path_or_url'] == 'local-perf-summary.json'
                            for a in evidence['artifacts']))

    def test_evidence_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'health.jsonl'
            code, evidence = self.collect()
            health.append_evidence(out, evidence)
            health.append_evidence(out, evidence)
            self.assertEqual(len(out.read_text().splitlines()), 1)

    def test_cli_writes_evidence_and_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'health.jsonl'
            facts = json.dumps(self.facts())
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/ops/healthcheck.py'),
                 '--mode', 'dry_run', '--env', 'rehearsal', '--facts', facts,
                 '--out', str(out)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0)
            envelope = json.loads(out.read_text().splitlines()[0])
            self.assertEqual(envelope['status'], 'pass')
