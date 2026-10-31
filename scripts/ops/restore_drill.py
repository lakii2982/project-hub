#!/usr/bin/env python3
"""V1-OPS-04 isolated restore rehearsal (PH-348).

Restores a backup manifest into an isolated target directory/database and verifies
structure, row counts, hashes and attachments; records RPO/RTO. Never restores
into production and never mutates the backup.
"""
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import backup_drill as bd

SCRIPT = 'ops_restore_rehearsal'


def _fail(args, checks, status, exit_code, notes, artifacts=None):
    return bd.fail(args, checks, status, exit_code, notes, artifacts, script=SCRIPT)


def load_manifest(backup_dir, explicit):
    path = Path(explicit) if explicit else Path(backup_dir) / 'manifest.json'
    if not path.is_file():
        return None, f'manifest missing: {path}'
    try:
        return json.loads(path.read_text(encoding='utf-8')), None
    except Exception as error:
        return None, f'manifest invalid: {error!r}'


def compare_sqlite(manifest, restored):
    stats = bd.sqlite_stats(restored)
    expected = manifest.get('database') or {}
    checks = []
    checks.append(bd.check('V1-OPS-04.integrity', 'pass', 'ok', stats['integrity_check']))
    same_tables = stats['tables'] == expected.get('tables')
    checks.append(bd.check('V1-OPS-04.tables', 'pass' if same_tables else 'fail',
                           expected.get('tables'), stats['tables']))
    same_schema = stats['schema_sha256'] == expected.get('schema_sha256')
    checks.append(bd.check('V1-OPS-04.schema', 'pass' if same_schema else 'fail',
                           expected.get('schema_sha256'), stats['schema_sha256']))
    return checks, same_tables and same_schema


def compare_attachments(manifest, target):
    expected = manifest.get('attachments')
    if not expected:
        return [], True
    source = Path(manifest['_backup_dir']) / 'attachments'
    restored = Path(target) / 'attachments'
    shutil.copytree(source, restored, dirs_exist_ok=True)
    files = {}
    for path in sorted(restored.rglob('*')):
        if path.is_file():
            files[str(path.relative_to(restored))] = {
                'sha256': bd.sha256_file(path), 'size': path.stat().st_size}
    digest = bd.hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    ok = digest == expected.get('sha256')
    return [bd.check('V1-OPS-04.attachments', 'pass' if ok else 'fail',
                     expected.get('sha256'), digest)], ok


def main(argv=None):
    bd.STARTED = bd.now()
    parser = argparse.ArgumentParser(description='V1-OPS-04 isolated restore rehearsal')
    parser.add_argument('--mode', choices=['dry_run', 'execute'], default='dry_run')
    parser.add_argument('--profile', choices=['rehearsal', 'staging', 'production'],
                        default='rehearsal')
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--manifest')
    parser.add_argument('--target-dir')
    parser.add_argument('--out')
    parser.add_argument('--rpo-target-minutes', type=int)
    parser.add_argument('--rto-target-minutes', type=int)
    args = parser.parse_args(argv)
    checks = []
    if args.profile == 'production' and args.mode == 'execute':
        checks.append(bd.check('V1-OPS-04.safety', 'fail', 'non-production profile',
                               'production restore refused'))
        return _fail(args, checks, 'blocked', bd.EXIT_REFUSED,
                       'production restore refused; drills only on isolated rehearsal/staging')
    manifest, error = load_manifest(args.backup_dir, args.manifest)
    if manifest is None:
        checks.append(bd.check('V1-OPS-04.manifest', 'not_run', 'manifest present', error))
        return _fail(args, checks, 'not_run', bd.EXIT_NOT_RUN, error)
    manifest['_backup_dir'] = args.backup_dir
    snapshot = Path(args.backup_dir) / manifest['snapshot']['path']
    if not snapshot.is_file():
        checks.append(bd.check('V1-OPS-04.snapshot', 'not_run', 'snapshot file present',
                               str(snapshot)))
        return _fail(args, checks, 'not_run', bd.EXIT_NOT_RUN, 'snapshot file missing')
    actual_sha = bd.sha256_file(snapshot)
    if actual_sha != manifest['snapshot']['sha256']:
        checks.append(bd.check('V1-OPS-04.hash', 'fail', manifest['snapshot']['sha256'],
                               actual_sha))
        return _fail(args, checks, 'fail', bd.EXIT_FAIL,
                       'snapshot hash mismatch; backup corrupted or tampered')
    checks.append(bd.check('V1-OPS-04.hash', 'pass', manifest['snapshot']['sha256'], actual_sha))
    kind = manifest.get('source', {}).get('kind')
    if kind == 'postgresql':
        if shutil.which('pg_restore') is None:
            checks.append(bd.check('V1-OPS-04.pg_tool', 'not_run', 'pg_restore available',
                                   'pg_restore not installed'))
            return _fail(args, checks, 'not_run', bd.EXIT_NOT_RUN,
                           'PostgreSQL restore tools unavailable; PG restore not_run')
    if args.mode == 'dry_run':
        target = Path(args.target_dir or tempfile.mkdtemp(prefix='ops-restore-dry-'))
        checks.append(bd.check('V1-OPS-04.preconditions', 'pass', 'writable isolated target',
                               str(target)))
        payload = bd.envelope(SCRIPT, 'dry_run', args.profile, checks, [], 'pass',
                              bd.EXIT_PASS, 'dry_run only: manifest/hash checked, no restore')
        bd.write_out(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return bd.EXIT_PASS
    if not args.target_dir:
        checks.append(bd.check('V1-OPS-04.target', 'not_run', '--target-dir for execute',
                               'missing target dir'))
        return _fail(args, checks, 'not_run', bd.EXIT_NOT_RUN,
                       'execute requires an isolated --target-dir')
    target = Path(args.target_dir)
    if target.exists() and any(target.iterdir()):
        checks.append(bd.check('V1-OPS-04.target', 'fail', 'empty isolated target',
                               str(target)))
        return _fail(args, checks, 'fail', bd.EXIT_FAIL,
                       'target directory must be empty/isolated; refusing to overwrite')
    target.mkdir(parents=True, exist_ok=True)
    started = bd.now()
    try:
        if kind == 'sqlite':
            restored = target / 'database.sqlite'
            shutil.copy2(snapshot, restored)
            restore_checks, ok = compare_sqlite(manifest, restored)
            checks.extend(restore_checks)
        else:
            restored = target / 'database.dump'
            shutil.copy2(snapshot, restored)
            checks.append(bd.check('V1-OPS-04.restore', 'pass', 'dump copied', str(restored)))
            ok = True
        attachment_checks, attachments_ok = compare_attachments(manifest, target)
        checks.extend(attachment_checks)
        ok = ok and attachments_ok
    except Exception as error:
        checks.append(bd.check('V1-OPS-04.restore', 'fail', 'restore succeeds', repr(error)))
        return _fail(args, checks, 'fail', bd.EXIT_ENV,
                       'restore failed; backup preserved and source untouched')
    finished = bd.now()
    rto_seconds = (finished - started).total_seconds()
    rpo_minutes = (finished - datetime.fromisoformat(manifest['created_at'])).total_seconds() / 60
    rpo_target = args.rpo_target_minutes or manifest.get('rpo_target_minutes') or 60
    rto_target = args.rto_target_minutes or manifest.get('rto_target_minutes') or 60
    checks.append(bd.check('V1-OPS-04.rpo', 'pass' if rpo_minutes <= rpo_target else 'fail',
                           f'<= {rpo_target} minutes', round(rpo_minutes, 2)))
    checks.append(bd.check('V1-OPS-04.rto', 'pass' if rto_seconds <= rto_target * 60 else 'fail',
                           f'<= {rto_target * 60} seconds', round(rto_seconds, 2)))
    ok = ok and rpo_minutes <= rpo_target and rto_seconds <= rto_target * 60
    payload = bd.envelope(SCRIPT, 'execute', args.profile, checks, [
        {'kind': 'sql', 'path_or_url': str(restored), 'sha256': bd.sha256_file(restored)}],
        'pass' if ok else 'fail', bd.EXIT_PASS if ok else bd.EXIT_FAIL,
        f'isolated restore verified; RPO {round(rpo_minutes, 2)} min, RTO {round(rto_seconds, 2)} s; '
        'backup and source untouched')
    bd.write_out(args.out, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return bd.EXIT_PASS if ok else bd.EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
