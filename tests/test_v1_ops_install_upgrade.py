"""PH-347 script contract tests: envelope, exit codes, idempotency, safety."""
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
install = load('ph347_install', 'scripts/ops/install_drill.py')
upgrade = load('ph347_upgrade', 'scripts/ops/upgrade_drill.py')


class InstallDrillTests(unittest.TestCase):
    def facts(self):
        return {'compose_version': '2.29.0', 'db_engine': 'postgresql',
                'migrations_append_only': True}

    def test_preflight_envelope_and_pass(self):
        code, evidence = install.preflight('rehearsal', 'dry_run', facts=self.facts())
        self.assertEqual(code, 0)
        self.assertEqual(evidence['script'], 'ops_preflight')
        self.assertEqual(evidence['contract_version'], 1)
        self.assertEqual(evidence['status'], 'pass')
        self.assertEqual(evidence['exit_code'], 0)
        self.assertEqual(evidence['mode'], 'dry_run')
        self.assertEqual(evidence['redactions'], install.REDACTIONS)
        self.assertTrue(all({'id', 'status', 'expected', 'actual', 'evidence_ref'} <= set(c)
                            for c in evidence['checks']))
        self.assertTrue(evidence['started_at'] and evidence['finished_at'])
        self.assertIn('password', evidence['redactions'])

    def test_preflight_missing_preconditions_not_run(self):
        code, evidence = install.preflight('staging', 'dry_run')
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        self.assertEqual(evidence['exit_code'], 2)

    def test_missing_migration_evidence_not_run(self):
        facts = {'compose_version': '2.29.0', 'db_engine': 'postgresql'}
        code, evidence = install.preflight('rehearsal', 'dry_run', facts=facts)
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        migration = [c for c in evidence['checks'] if c['id'] == 'migration.mode'][0]
        self.assertEqual(migration['status'], 'not_run')
        self.assertEqual(migration['actual'], 'missing')

    def test_staging_execute_refused(self):
        code, evidence = install.preflight('staging', 'execute', facts=self.facts())
        self.assertEqual(code, 5)
        self.assertEqual(evidence['status'], 'blocked')
        self.assertIn('safety.execute_profile', [c['id'] for c in evidence['checks']])

    def test_production_execute_refused(self):
        code, evidence = install.preflight('production', 'execute', facts=self.facts(),
                                           confirm_production=True)
        self.assertEqual(code, 5)
        self.assertEqual(evidence['status'], 'blocked')
        self.assertIn('safety.execute_profile',
                      [c['id'] for c in evidence['checks']])

    def test_execute_rehearsal_is_isolated(self):
        code, evidence = install.preflight('rehearsal', 'execute', facts=self.facts())
        self.assertEqual(code, 0)
        self.assertEqual(evidence['status'], 'pass')
        self.assertTrue(any(c['id'] == 'install.rehearsal' for c in evidence['checks']))

    def test_tls_probe_cases(self):
        code, evidence = install.tls_probe('rehearsal', 'dry_run')
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        ok = {'valid': True, 'not_expired': True, 'hostname_ok': True,
              'hsts': True, 'cookie_secure': True}
        code, evidence = install.tls_probe('staging', 'dry_run', domain='example.test',
                                           handshake=ok)
        self.assertEqual(code, 0)
        self.assertEqual(evidence['status'], 'pass')
        expired = dict(ok, not_expired=False)
        code, evidence = install.tls_probe('staging', 'dry_run', domain='example.test',
                                           handshake=expired)
        self.assertEqual(code, 3)
        self.assertEqual(evidence['status'], 'fail')
        code, evidence = install.tls_probe('production', 'execute', domain='example.test',
                                           handshake=ok)
        self.assertEqual(code, 5)

    def test_evidence_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'evidence.jsonl'
            code, evidence = install.preflight('rehearsal', 'dry_run', facts=self.facts())
            install.append_evidence(out, evidence)
            install.append_evidence(out, evidence)
            self.assertEqual(len(out.read_text().splitlines()), 1)
            code2, second = install.preflight('rehearsal', 'dry_run', facts=self.facts())
            install.append_evidence(out, second)
            self.assertEqual(len(out.read_text().splitlines()), 2)


class UpgradeDrillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backup = Path(self.temp.name) / 'backup.dump'
        self.backup.write_bytes(b'backup')
        self.before = ['core.0001_initial']
        self.after = ['core.0001_initial', 'costing.0005_shared_cost_allocation']

    def plan(self, **changes):
        values = dict(profile='rehearsal', mode='dry_run', baseline='a' * 40,
                      candidate='b' * 40, backup=str(self.backup),
                      migrations_before=self.before, migrations_after=self.after)
        values.update(changes)
        return upgrade.upgrade_plan(**values)

    def test_upgrade_dry_run_pass_and_envelope(self):
        code, evidence = self.plan()
        self.assertEqual(code, 0)
        self.assertEqual(evidence['script'], 'ops_upgrade')
        self.assertEqual(evidence['status'], 'pass')
        ids = [c['id'] for c in evidence['checks']]
        self.assertIn('backup.evidence', ids)
        self.assertIn('migration.duplicates', ids)
        self.assertIn('rollback.contract', ids)

    def test_upgrade_requires_backup_and_append_only(self):
        code, evidence = self.plan(backup=None)
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')
        code, evidence = self.plan(migrations_after=['core.0001_initial', 'core.0001_initial'])
        self.assertEqual(code, 3)
        self.assertEqual(evidence['status'], 'fail')
        self.assertIn('migration.duplicates',
                      [c['id'] for c in evidence['checks'] if c['status'] == 'fail'])

    def test_upgrade_production_execute_refused(self):
        code, evidence = self.plan(profile='production', mode='execute')
        self.assertEqual(code, 5)
        self.assertEqual(evidence['status'], 'blocked')

    def test_upgrade_staging_execute_refused(self):
        code, evidence = self.plan(profile='staging', mode='execute')
        self.assertEqual(code, 5)
        self.assertEqual(evidence['status'], 'blocked')
        self.assertIn('safety.execute_profile', [c['id'] for c in evidence['checks']])

    def test_upgrade_execute_without_backup_not_run(self):
        code, evidence = self.plan(mode='execute', backup=None)
        self.assertEqual(code, 2)
        self.assertEqual(evidence['status'], 'not_run')

    def test_upgrade_evidence_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'evidence.jsonl'
            code, evidence = self.plan()
            upgrade.append_evidence(out, evidence)
            upgrade.append_evidence(out, evidence)
            self.assertEqual(len(out.read_text().splitlines()), 1)

    def test_cli_exit_code_and_out(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'install.jsonl'
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/ops/install_drill.py'),
                 '--mode', 'execute', '--env', 'production', '--out', str(out)],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 5)
            lines = out.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            envelope = json.loads(lines[0])
            self.assertEqual(envelope['status'], 'blocked')
