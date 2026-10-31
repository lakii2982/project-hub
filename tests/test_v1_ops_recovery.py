"""PH-333 M15 / V1-OPS-01..04 rollup acceptance (L1+L2 evidence only)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLLUP = ROOT / 'scripts' / 'ops' / 'rollup_acceptance.py'
INSTALL = ROOT / 'scripts' / 'ops' / 'install_drill.py'
UPGRADE = ROOT / 'scripts' / 'ops' / 'upgrade_drill.py'


def run(script, *args):
    completed = subprocess.run([sys.executable, str(script), *args], cwd=ROOT,
                               text=True, capture_output=True)
    payload = json.loads(completed.stdout) if completed.stdout.strip() else None
    return completed.returncode, payload


class OpsRecoveryRollupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / 'rollup.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_rollup_emits_l1_l2_evidence_and_marks_l3_l5_not_run(self):
        code, payload = run(ROLLUP, '--out', str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(payload['script'], 'ops_rollup_acceptance')
        self.assertEqual(payload['contract_version'], 1)
        self.assertEqual(payload['status'], 'pass')
        self.assertFalse(payload['release_ready'])
        checks = {item['id']: item for item in payload['checks']}
        self.assertEqual(checks['V1-OPS-01']['status'], 'pass')
        self.assertEqual(checks['V1-OPS-01']['layer'], 'L2')
        self.assertEqual(checks['V1-OPS-02']['status'], 'not_run')
        self.assertEqual(checks['V1-OPS-03']['status'], 'pass')
        self.assertEqual(checks['V1-OPS-04']['status'], 'pass')
        self.assertEqual(checks['release.L3']['status'], 'not_run')
        self.assertEqual(checks['release.L4']['status'], 'not_run')
        self.assertEqual(checks['release.L5']['status'], 'not_run')
        self.assertIn('L3 CI/PG', payload['notes'])
        self.assertIn('not_run', payload['notes'])
        self.assertEqual(payload['redactions'], ['password', 'token', 'payroll', 'customer'])
        self.assertTrue(self.out.exists())

    def test_install_production_execute_refused(self):
        code, payload = run(INSTALL, '--mode', 'execute', '--env', 'production',
                            '--out', str(self.root / 'install.jsonl'))
        self.assertEqual(code, 5)
        self.assertEqual(payload['status'], 'blocked')
        self.assertTrue(any(item['status'] == 'fail' for item in payload['checks']))

    def test_install_missing_migration_evidence_not_run(self):
        code, payload = run(INSTALL, '--mode', 'dry_run', '--env', 'rehearsal',
                            '--facts', json.dumps({'compose_version': '2.29.0',
                                                   'db_engine': 'sqlite'}),
                            '--out', str(self.root / 'install.jsonl'))
        self.assertEqual(code, 2)
        self.assertEqual(payload['status'], 'not_run')
        self.assertTrue(any(item['id'] == 'migration.mode' and item['status'] == 'not_run'
                            for item in payload['checks']))

    def test_upgrade_missing_backup_fails(self):
        code, payload = run(UPGRADE, '--mode', 'dry_run', '--env', 'rehearsal',
                            '--baseline', 'a' * 40, '--candidate', 'b' * 40,
                            '--backup', str(self.root / 'missing.dump'),
                            '--migrations-before', '["core.0001_initial"]',
                            '--migrations-after', '["core.0001_initial"]',
                            '--out', str(self.root / 'upgrade.jsonl'))
        self.assertEqual(code, 3)
        self.assertEqual(payload['status'], 'fail')
        self.assertTrue(any(item['id'] == 'backup.evidence' and item['status'] == 'fail'
                            for item in payload['checks']))

    def test_upgrade_production_execute_refused(self):
        backup = self.root / 'backup.dump'
        backup.write_bytes(b'non-empty backup')
        code, payload = run(UPGRADE, '--mode', 'execute', '--env', 'production',
                            '--baseline', 'a' * 40, '--candidate', 'b' * 40,
                            '--backup', str(backup),
                            '--migrations-before', '["core.0001_initial"]',
                            '--migrations-after', '["core.0001_initial"]',
                            '--out', str(self.root / 'upgrade.jsonl'))
        self.assertEqual(code, 5)
        self.assertEqual(payload['status'], 'blocked')

    def test_rollup_is_idempotent_and_does_not_write_production(self):
        first, payload = run(ROLLUP, '--out', str(self.out))
        second, payload2 = run(ROLLUP, '--out', str(self.out))
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertFalse(payload['release_ready'])
        self.assertFalse(payload2['release_ready'])
        self.assertTrue(self.out.exists())


if __name__ == '__main__':
    unittest.main()
