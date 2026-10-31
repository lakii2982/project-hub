#!/usr/bin/env python3
"""PH-333 M15 / V1-OPS-01..04 rollup acceptance (L1+L2 only).

Runs the PH-347/PH-348 local/rehearsal drills, aggregates their envelopes into a
rollup evidence draft, and explicitly marks L3 (CI/PG), L4 (browser) and L5
(production/customer) as not_run. Never performs staging/production operations.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = 'ops_rollup_acceptance'
CONTRACT_VERSION = 1
REDACTIONS = ['password', 'token', 'payroll', 'customer']


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def app_sha():
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return 'unknown'


def run(script, *args):
    completed = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'ops' / script), *args],
                               cwd=ROOT, text=True, capture_output=True)
    text = completed.stdout.strip()
    payload = None
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            lines = [line for line in text.splitlines() if line.strip()]
            for line in reversed(lines):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    return completed.returncode, payload


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE ledger_entry (id INTEGER PRIMARY KEY, amount_cents INTEGER)')
    conn.executemany('INSERT INTO ledger_entry (amount_cents) VALUES (?)', [(100,), (200,)])
    conn.commit()
    conn.close()


def check(check_id, status, expected, actual, layer):
    return {'id': check_id, 'status': status, 'expected': expected, 'actual': actual,
            'layer': layer}


def main(argv=None):
    parser = argparse.ArgumentParser(description='PH-333 M15/V1-OPS rollup acceptance')
    parser.add_argument('--out')
    parser.add_argument('--profile', default='rehearsal')
    args = parser.parse_args(argv)
    started = now()
    checks, artifacts = [], []
    tmp = Path(tempfile.mkdtemp(prefix='ph333-rollup-'))
    try:
        facts = json.dumps({'compose_version': '2.29.0', 'db_engine': 'sqlite',
                            'migrations_append_only': True})
        code, install = run('install_drill.py', '--mode', 'dry_run', '--env', 'rehearsal',
                            '--facts', facts, '--out', str(tmp / 'install.jsonl'))
        checks.append(check('V1-OPS-01', 'pass' if code == 0 and install and
                            install.get('status') == 'pass' else 'fail',
                            'local install preflight pass', f'exit {code}', 'L2'))
        code, tls = run('install_drill.py', '--step', 'tls', '--mode', 'dry_run',
                        '--env', 'staging', '--out', str(tmp / 'tls.jsonl'))
        checks.append(check('V1-OPS-02', 'not_run', 'real domain/TLS probe',
                            f'exit {code}; staging/TLS not executed', 'L3'))
        backup = tmp / 'backup.dump'
        backup.write_bytes(b'ph333 local rehearsal backup placeholder')
        before = json.dumps(['core.0001_initial'])
        after = json.dumps(['core.0001_initial', 'costing.0005_shared_cost_allocation'])
        code, upgrade = run('upgrade_drill.py', '--mode', 'dry_run', '--env', 'rehearsal',
                            '--baseline', app_sha(), '--candidate', 'deadbeef',
                            '--backup', str(backup), '--migrations-before', before,
                            '--migrations-after', after, '--out', str(tmp / 'upgrade.jsonl'))
        checks.append(check('V1-OPS-03', 'pass' if code == 0 and upgrade and
                            upgrade.get('status') == 'pass' else 'fail',
                            'local upgrade/rollback dry_run pass', f'exit {code}', 'L2'))
        db = tmp / 'source.sqlite'
        make_db(db)
        code, backup_payload = run('backup_drill.py', '--mode', 'execute', '--profile',
                                   'rehearsal', '--database', str(db), '--backup-dir',
                                   str(tmp / 'backup'), '--out', str(tmp / 'backup.jsonl'))
        restore_code, restore = run('restore_drill.py', '--mode', 'execute', '--profile',
                                    'rehearsal', '--backup-dir', str(tmp / 'backup'),
                                    '--target-dir', str(tmp / 'restored'),
                                    '--out', str(tmp / 'restore.jsonl'))
        checks.append(check('V1-OPS-04', 'pass' if code == 0 and restore_code == 0
                            and restore and restore.get('status') == 'pass' else 'fail',
                            'isolated SQLite backup+restore pass',
                            f'backup exit {code}, restore exit {restore_code}', 'L2'))
        for layer, label in (('L3', 'CI/PG real environment'), ('L4', 'browser 1440/390'),
                             ('L5', 'production/customer acceptance')):
            checks.append(check(f'release.{layer}', 'not_run', label, 'not executed', layer))
        artifacts.append({'kind': 'log', 'path_or_url': str(tmp), 'sha256': ''})
        status = 'pass' if all(item['status'] in ('pass', 'not_run') for item in checks) else 'fail'
        payload = {'script': SCRIPT, 'contract_version': CONTRACT_VERSION, 'mode': 'execute',
                   'profile': args.profile, 'app_sha': app_sha(), 'started_at': started,
                   'finished_at': now(), 'exit_code': 0 if status == 'pass' else 3,
                   'status': status, 'release_ready': False, 'checks': checks,
                   'artifacts': artifacts, 'redactions': REDACTIONS,
                   'notes': 'L1+L2 local/rehearsal rollup only; L3 CI/PG, L4 browser and '
                            'L5 production/customer acceptance are not_run and must not be '
                            'claimed as passed. No staging/production operation performed.'}
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')
        print(json.dumps(payload, ensure_ascii=False))
        return payload['exit_code']
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
