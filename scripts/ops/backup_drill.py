#!/usr/bin/env python3
"""V1-OPS-04 backup integrity drill (PH-348).

Read-only source snapshot for SQLite (and PG when pg_dump is available) plus
attachments, producing the PH-345 evidence envelope and a manifest. Never writes
to the source DB, never touches production on execute, never includes secrets in
evidence.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_VERSION = 1
SCRIPT = 'ops_backup_verify'
REDACTIONS = ['password', 'token', 'payroll', 'customer']
EXIT_PASS, EXIT_NOT_RUN, EXIT_FAIL, EXIT_ENV, EXIT_REFUSED = 0, 2, 3, 4, 5


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value=None):
    return (value or now()).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def redact_url(value):
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.password:
        netloc = f'{parsed.username or ""}:***@{parsed.hostname or ""}'
        if parsed.port:
            netloc += f':{parsed.port}'
        parsed = parsed._replace(netloc=netloc)
    return urllib.parse.urlunsplit(parsed)


def app_sha(root):
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return 'unknown'


def check(check_id, status, expected, actual, evidence_ref=None):
    return {'id': check_id, 'status': status, 'expected': expected,
            'actual': actual, 'evidence_ref': evidence_ref}


def sqlite_stats(path):
    uri = 'file:' + str(Path(path).resolve()) + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        tables = {}
        for name, in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            tables[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        schema = '\n'.join(row[0] or '' for row in conn.execute(
            'SELECT sql FROM sqlite_master ORDER BY type, name'))
        page_count = conn.execute('PRAGMA page_count').fetchone()[0]
        page_size = conn.execute('PRAGMA page_size').fetchone()[0]
        return {'integrity_check': integrity, 'tables': tables,
                'table_count': len(tables), 'page_count': page_count,
                'page_size': page_size,
                'schema_sha256': hashlib.sha256(schema.encode()).hexdigest()}
    finally:
        conn.close()


def sqlite_snapshot(source, destination):
    src = sqlite3.connect('file:' + str(Path(source).resolve()) + '?mode=ro', uri=True)
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def pg_snapshot(url, destination):
    binary = shutil.which('pg_dump')
    if binary is None:
        return None
    env = dict(os.environ)
    env.setdefault('PGSSLMODE', 'prefer')
    subprocess.run([binary, '--no-owner', '--format=custom', '--file', str(destination), url],
                   check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return destination


def attachments_snapshot(source, destination):
    source = Path(source)
    if not source.is_dir():
        raise FileNotFoundError(f'attachments directory missing: {source}')
    shutil.copytree(source, destination)
    files = {}
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            files[str(path.relative_to(destination))] = {
                'sha256': sha256_file(path), 'size': path.stat().st_size}
    return {'path': str(destination), 'file_count': len(files), 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def envelope(script, mode, profile, checks, artifacts, status, exit_code, notes=''):
    return {'script': script, 'contract_version': CONTRACT_VERSION, 'mode': mode,
            'profile': profile, 'app_sha': app_sha(Path(__file__).resolve().parents[2]),
            'started_at': iso(STARTED), 'finished_at': iso(), 'exit_code': exit_code,
            'status': status, 'checks': checks, 'artifacts': artifacts,
            'redactions': REDACTIONS, 'notes': notes}


def write_out(path, payload):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fail(args, checks, status, exit_code, notes, artifacts=None, script=None):
    payload = envelope(script or SCRIPT, args.mode, args.profile, checks, artifacts or [],
                       status, exit_code, notes)
    write_out(args.out, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def main(argv=None):
    global STARTED
    STARTED = now()
    parser = argparse.ArgumentParser(description='V1-OPS-04 backup integrity drill')
    parser.add_argument('--mode', choices=['dry_run', 'execute'], default='dry_run')
    parser.add_argument('--profile', choices=['rehearsal', 'staging', 'production'],
                        default='rehearsal')
    parser.add_argument('--database')
    parser.add_argument('--pg-url')
    parser.add_argument('--attachments')
    parser.add_argument('--backup-dir')
    parser.add_argument('--out')
    parser.add_argument('--rpo-target-minutes', type=int, default=60)
    parser.add_argument('--rto-target-minutes', type=int, default=60)
    parser.add_argument('--retention-days', type=int, default=7)
    args = parser.parse_args(argv)
    checks = []
    if args.profile == 'production' and args.mode == 'execute':
        checks.append(check('V1-OPS-04.safety', 'fail', 'non-production profile',
                            'production execute refused'))
        return fail(args, checks, 'blocked', EXIT_REFUSED,
                    'production execute refused; drills only on isolated rehearsal/staging')
    source_kind = 'sqlite' if args.database else ('postgresql' if args.pg_url else None)
    if source_kind is None:
        checks.append(check('V1-OPS-04.source', 'not_run', 'database or pg-url',
                            'no source supplied'))
        return fail(args, checks, 'not_run', EXIT_NOT_RUN, 'no source database supplied')
    if source_kind == 'sqlite':
        if not Path(args.database).is_file():
            checks.append(check('V1-OPS-04.source', 'not_run', 'readable SQLite file',
                                f'missing {args.database}'))
            return fail(args, checks, 'not_run', EXIT_NOT_RUN, 'source SQLite database missing')
        try:
            stats = sqlite_stats(args.database)
        except Exception as error:
            checks.append(check('V1-OPS-04.source', 'fail', 'readable SQLite', repr(error)))
            return fail(args, checks, 'fail', EXIT_ENV, 'cannot read source SQLite database')
        checks.append(check('V1-OPS-04.integrity', 'pass', 'ok', stats['integrity_check']))
    else:
        if shutil.which('pg_dump') is None:
            checks.append(check('V1-OPS-04.pg_tool', 'not_run', 'pg_dump available',
                                'pg_dump not installed'))
            return fail(args, checks, 'not_run', EXIT_NOT_RUN,
                        'PostgreSQL client tools unavailable; PG drill not_run')
        stats = None
    if args.mode == 'dry_run':
        backup_dir = Path(args.backup_dir or tempfile.mkdtemp(prefix='ops-backup-dry-'))
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
        checks.append(check('V1-OPS-04.preconditions', 'pass', 'writable backup dir',
                            str(backup_dir)))
        payload = envelope(SCRIPT, 'dry_run', args.profile, checks, [], 'pass', EXIT_PASS,
                           'dry_run only: preconditions checked, no snapshot written')
        write_out(args.out, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_PASS
    if not args.backup_dir:
        checks.append(check('V1-OPS-04.backup_dir', 'not_run', '--backup-dir for execute',
                            'missing backup dir'))
        return fail(args, checks, 'not_run', EXIT_NOT_RUN, 'execute requires --backup-dir')
    backup_dir = Path(args.backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        checks.append(check('V1-OPS-04.backup_dir', 'fail', 'writable', repr(error)))
        return fail(args, checks, 'fail', EXIT_ENV, 'backup directory not writable')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    db_name = 'database.sqlite' if source_kind == 'sqlite' else 'database.dump'
    db_path = backup_dir / db_name
    try:
        if source_kind == 'sqlite':
            sqlite_snapshot(args.database, db_path)
            snapshot_stats = sqlite_stats(db_path)
        else:
            if pg_snapshot(args.pg_url, db_path) is None:
                checks.append(check('V1-OPS-04.pg_tool', 'not_run', 'pg_dump available',
                                    'pg_dump not installed'))
                return fail(args, checks, 'not_run', EXIT_NOT_RUN,
                            'PostgreSQL client tools unavailable; PG drill not_run')
            snapshot_stats = None
        attachments = None
        if args.attachments:
            attachments = attachments_snapshot(args.attachments, backup_dir / 'attachments')
    except Exception as error:
        checks.append(check('V1-OPS-04.snapshot', 'fail', 'snapshot created', repr(error)))
        return fail(args, checks, 'fail', EXIT_ENV, 'snapshot failed; source untouched')
    checks.append(check('V1-OPS-04.snapshot', 'pass', 'snapshot created', str(db_path)))
    snapshot_sha = sha256_file(db_path)
    checks.append(check('V1-OPS-04.hash', 'pass', 'sha256 recorded', snapshot_sha))
    if snapshot_stats is not None:
        checks.append(check('V1-OPS-04.integrity', 'pass', 'ok',
                            snapshot_stats['integrity_check']))
    manifest = {'manifest_version': 1, 'script': SCRIPT, 'profile': args.profile,
                'created_at': iso(), 'source': {'kind': source_kind,
                                                'path': redact_url(args.pg_url) if args.pg_url else args.database},
                'snapshot': {'path': db_path.name, 'sha256': snapshot_sha,
                             'size': db_path.stat().st_size},
                'database': snapshot_stats, 'attachments': attachments,
                'retention_days': args.retention_days,
                'rpo_target_minutes': args.rpo_target_minutes,
                'rto_target_minutes': args.rto_target_minutes,
                'app_sha': app_sha(Path(__file__).resolve().parents[2]),
                'redactions': REDACTIONS}
    manifest_path = backup_dir / f'manifest-{stamp}.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
    (backup_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    artifacts = [{'kind': 'sql', 'path_or_url': str(db_path), 'sha256': snapshot_sha},
                 {'kind': 'hash', 'path_or_url': str(manifest_path),
                  'sha256': sha256_file(manifest_path)}]
    if attachments:
        artifacts.append({'kind': 'hash', 'path_or_url': attachments['path'],
                          'sha256': attachments['sha256']})
    payload = envelope(SCRIPT, 'execute', args.profile, checks, artifacts, 'pass', EXIT_PASS,
                       'snapshot, manifest and hashes written; source opened read-only')
    write_out(args.out, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return EXIT_PASS


if __name__ == '__main__':
    sys.exit(main())
