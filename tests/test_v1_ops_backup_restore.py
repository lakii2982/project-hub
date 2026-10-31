"""PH-348 V1-OPS-04 backup/restore drill script contracts."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / 'scripts' / 'ops' / 'backup_drill.py'
RESTORE = ROOT / 'scripts' / 'ops' / 'restore_drill.py'
REQUIRED = {'script', 'contract_version', 'mode', 'profile', 'app_sha', 'started_at',
            'finished_at', 'exit_code', 'status', 'checks', 'artifacts', 'redactions',
            'notes'}


def run(script, *args, env=None):
    completed = subprocess.run([sys.executable, str(script), *args], cwd=ROOT,
                               text=True, capture_output=True,
                               env={**os.environ, **(env or {})})
    payload = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else None
    return completed.returncode, payload


class BackupRestoreDrillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'source.sqlite'
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE ledger_entry (id INTEGER PRIMARY KEY, amount_cents INTEGER)')
        conn.execute('CREATE TABLE audit_event (id INTEGER PRIMARY KEY, event_hash TEXT)')
        conn.executemany('INSERT INTO ledger_entry (amount_cents) VALUES (?)',
                         [(100,), (200,), (300,)])
        conn.executemany('INSERT INTO audit_event (event_hash) VALUES (?)', [('a',), ('b',)])
        conn.commit()
        conn.close()
        self.backup_dir = self.root / 'backup'
        self.out = self.root / 'evidence.json'

    def tearDown(self):
        self.tmp.cleanup()

    def backup(self, *args, mode='execute', profile='rehearsal'):
        return run(BACKUP, '--mode', mode, '--profile', profile,
                   '--database', str(self.db), '--backup-dir', str(self.backup_dir),
                   '--out', str(self.out), *args)

    def test_dry_run_checks_preconditions_without_snapshot(self):
        code, payload = self.backup(mode='dry_run')
        self.assertEqual(code, 0)
        self.assertEqual(payload['script'], 'ops_backup_verify')
        self.assertEqual(payload['mode'], 'dry_run')
        self.assertEqual(payload['status'], 'pass')
        self.assertTrue(REQUIRED.issubset(payload))
        self.assertFalse((self.backup_dir / 'database.sqlite').exists())
        self.assertTrue(any(item['id'] == 'V1-OPS-04.integrity' for item in payload['checks']))

    def test_execute_writes_snapshot_manifest_and_envelope(self):
        code, payload = self.backup()
        self.assertEqual(code, 0)
        self.assertEqual(payload['status'], 'pass')
        self.assertTrue(REQUIRED.issubset(payload))
        manifest = json.loads((self.backup_dir / 'manifest.json').read_text())
        snapshot = self.backup_dir / manifest['snapshot']['path']
        self.assertTrue(snapshot.exists())
        self.assertEqual(manifest['database']['tables']['ledger_entry'], 3)
        self.assertEqual(manifest['database']['tables']['audit_event'], 2)
        self.assertEqual(manifest['database']['integrity_check'], 'ok')
        self.assertEqual(len(manifest['snapshot']['sha256']), 64)
        self.assertEqual(payload['redactions'], ['password', 'token', 'payroll', 'customer'])
        # source is untouched and still readable
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM ledger_entry').fetchone()[0], 3)
        conn.close()

    def test_attachments_hashed_and_restored(self):
        attachments = self.root / 'attachments'
        attachments.mkdir()
        (attachments / 'voucher.txt').write_text('凭证', encoding='utf-8')
        code, payload = self.backup('--attachments', str(attachments))
        self.assertEqual(code, 0)
        manifest = json.loads((self.backup_dir / 'manifest.json').read_text())
        self.assertEqual(manifest['attachments']['file_count'], 1)
        target = self.root / 'restored'
        code, restored = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                             '--backup-dir', str(self.backup_dir),
                             '--target-dir', str(target), '--out', str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(restored['status'], 'pass')
        self.assertEqual((target / 'attachments' / 'voucher.txt').read_text(encoding='utf-8'), '凭证')

    def test_restore_rehearsal_verifies_rows_rpo_rto_and_source(self):
        code, _ = self.backup()
        self.assertEqual(code, 0)
        before = self.db.read_bytes()
        target = self.root / 'restored'
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(target), '--out', str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(payload['script'], 'ops_restore_rehearsal')
        self.assertEqual(payload['status'], 'pass')
        checks = {item['id']: item for item in payload['checks']}
        self.assertEqual(checks['V1-OPS-04.tables']['status'], 'pass')
        self.assertEqual(checks['V1-OPS-04.rpo']['status'], 'pass')
        self.assertEqual(checks['V1-OPS-04.rto']['status'], 'pass')
        conn = sqlite3.connect(target / 'database.sqlite')
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM ledger_entry').fetchone()[0], 3)
        conn.close()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual((self.backup_dir / 'manifest.json').exists(), True)

    def test_restore_dry_run_does_not_write_target(self):
        self.backup()
        target = self.root / 'not-created'
        code, payload = run(RESTORE, '--mode', 'dry_run', '--profile', 'rehearsal',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(target), '--out', str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(payload['mode'], 'dry_run')
        self.assertFalse(target.exists())

    def test_missing_database_is_not_run(self):
        code, payload = run(BACKUP, '--mode', 'execute', '--database', str(self.root / 'missing.sqlite'),
                            '--backup-dir', str(self.backup_dir), '--out', str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(payload['status'], 'not_run')
        self.assertFalse((self.backup_dir / 'database.sqlite').exists())

    def test_production_execute_refused(self):
        code, payload = self.backup(profile='production')
        self.assertEqual(code, 5)
        self.assertEqual(payload['status'], 'blocked')
        self.assertEqual(payload['exit_code'], 5)
        self.assertFalse((self.backup_dir / 'database.sqlite').exists())

    def test_restore_tampered_snapshot_fails(self):
        self.backup()
        manifest = json.loads((self.backup_dir / 'manifest.json').read_text())
        snapshot = self.backup_dir / manifest['snapshot']['path']
        snapshot.write_bytes(snapshot.read_bytes() + b'x')
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(self.root / 'restored'), '--out', str(self.out))
        self.assertEqual(code, 3)
        self.assertEqual(payload['status'], 'fail')
        self.assertIn('hash', payload['notes'])

    def test_restore_missing_manifest_is_not_run(self):
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(self.root / 'empty'),
                            '--target-dir', str(self.root / 'restored'), '--out', str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(payload['status'], 'not_run')

    def test_execute_is_idempotent_and_source_unchanged(self):
        before = self.db.read_bytes()
        first, _ = self.backup()
        second, _ = self.backup()
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(self.db.read_bytes(), before)
        manifests = list(self.backup_dir.glob('manifest-*.json'))
        self.assertGreaterEqual(len(manifests), 2)

    def test_restore_production_execute_refused(self):
        self.backup()
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'production',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(self.root / 'restored'), '--out', str(self.out))
        self.assertEqual(code, 5)
        self.assertEqual(payload['status'], 'blocked')
        self.assertFalse((self.root / 'restored').exists())

    def test_restore_non_empty_target_refused(self):
        self.backup()
        target = self.root / 'restored'
        target.mkdir()
        (target / 'existing.txt').write_text('keep', encoding='utf-8')
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(target), '--out', str(self.out))
        self.assertEqual(code, 3)
        self.assertEqual(payload['status'], 'fail')
        self.assertEqual((target / 'existing.txt').read_text(encoding='utf-8'), 'keep')

    def test_restore_target_is_plain_file(self):
        self.backup()
        target = self.root / 'target-file'
        target.write_text('not a directory', encoding='utf-8')
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(self.backup_dir),
                            '--target-dir', str(target), '--out', str(self.out))
        self.assertEqual(code, 3)
        self.assertEqual(payload['status'], 'fail')
        self.assertEqual(target.read_text(encoding='utf-8'), 'not a directory')

    def test_pg_restore_execute_is_honest_not_run(self):
        pg_dir = self.root / 'pgbackup'
        pg_dir.mkdir()
        snapshot = pg_dir / 'database.dump'
        snapshot.write_bytes(b'fake pg dump')
        manifest = {
            'manifest_version': 1, 'script': 'ops_backup_verify', 'profile': 'rehearsal',
            'created_at': '2026-10-31T00:00:00+00:00',
            'source': {'kind': 'postgresql', 'path': 'postgres://user:***@localhost/db'},
            'snapshot': {'path': 'database.dump', 'sha256': hashlib.sha256(b'fake pg dump').hexdigest(),
                         'size': 12},
            'database': None, 'attachments': None, 'retention_days': 7,
            'rpo_target_minutes': 60, 'rto_target_minutes': 60,
            'app_sha': 'test', 'redactions': ['password']}
        (pg_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        marker = self.root / 'pg-restore-called'
        fake = bin_dir / 'pg_restore'
        fake.write_text(f'#!/bin/sh\ntouch {marker}\nexit 0\n', encoding='utf-8')
        fake.chmod(0o755)
        target = self.root / 'restored'
        code, payload = run(RESTORE, '--mode', 'execute', '--profile', 'rehearsal',
                            '--backup-dir', str(pg_dir), '--target-dir', str(target),
                            '--out', str(self.out),
                            env={'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', '')})
        self.assertEqual(code, 2)
        self.assertEqual(payload['status'], 'not_run')
        self.assertFalse(marker.exists())
        self.assertFalse(target.exists())
        self.assertIn('not implemented', payload['notes'])

    def test_pg_dry_run_not_run_without_client_tools(self):
        env = {'PATH': '/nonexistent'}
        completed = subprocess.run([sys.executable, str(BACKUP), '--mode', 'dry_run',
                                    '--pg-url', 'postgres://user:secret@localhost/db',
                                    '--out', str(self.out)],
                                   cwd=ROOT, text=True, capture_output=True, env=env)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload['status'], 'not_run')
        self.assertNotIn('secret', json.dumps(payload))


if __name__ == '__main__':
    unittest.main()
