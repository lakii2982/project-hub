"""Objective CI quality gate (PH-121).

Reads the quality policy from the trusted PR BASE (never the candidate HEAD),
then evaluates a measured coverage JSON and the verbatim unittest log.  It
performs no network I/O and never executes candidate code; missing, corrupt or
unknown evidence fails closed.

Usage:
    python scripts/quality_gate.py --root . --base <sha> \
        --coverage coverage.json --test-log test-output.txt
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

POLICY_PATH = 'project/delivery-policy.json'
MAX_FILE_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{40,64}')
MEASURED_PREFIXES = ('core/', 'pmo/', 'pmo_plugins/')
_RAN = re.compile(r'^Ran ([0-9]+) tests? in ', re.MULTILINE)
_SKIPPED = re.compile(r'skipped=(\d+)')
_SKIP_LINE = re.compile(r"skipped ['\"]([^'\"]*)['\"]")


class GateError(ValueError):
    pass


def _fail(message):
    raise GateError(message)


def load_base_policy(root, base_sha):
    if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
        _fail('invalid_base_sha')
    try:
        raw = subprocess.run(
            ['git', '--no-replace-objects', 'show', f'{base_sha}:{POLICY_PATH}'],
            cwd=str(root), check=True, capture_output=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        _fail('base_policy_unavailable')
    try:
        policy = json.loads(raw)
    except (ValueError, UnicodeError):
        _fail('base_policy_invalid_json')
    return validate_quality(policy.get('quality'))


def validate_quality(quality):
    if not isinstance(quality, dict):
        _fail('quality_policy_missing')
    total = quality.get('total_min_percent')
    changed = quality.get('changed_lines_min_percent')
    count = quality.get('test_count_min')
    if type(total) not in (int, float) or not 0 <= total <= 100:
        _fail('quality_total_min')
    if type(changed) not in (int, float) or not 0 <= changed <= 100:
        _fail('quality_changed_min')
    if type(count) is not int or count < 1:
        _fail('quality_test_count_min')
    if quality.get('unknown_skip_blocked') is not True:
        _fail('quality_unknown_skip_blocked')
    allowed = quality.get('allowed_skip_patterns')
    if not isinstance(allowed, list) or not allowed or not all(
            isinstance(item, str) and item and len(item) <= 300 for item in allowed):
        _fail('quality_allowed_skip_patterns')
    for item in allowed:
        try:
            re.compile(item)
        except re.error:
            _fail('quality_allowed_skip_patterns')
    if quality.get('coverage_python') != '3.12':
        _fail('quality_coverage_python')
    return quality


def read_coverage(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        _fail('coverage_missing')
    if len(raw) > MAX_FILE_BYTES:
        _fail('coverage_too_large')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        _fail('coverage_invalid_json')
    totals = data.get('totals') if isinstance(data, dict) else None
    files = data.get('files') if isinstance(data, dict) else None
    percent = totals.get('percent_covered') if isinstance(totals, dict) else None
    if (type(percent) not in (int, float) or not 0 <= percent <= 100
            or not isinstance(files, dict) or not files):
        _fail('coverage_invalid_shape')
    for path, row in files.items():
        if (not isinstance(path, str) or not isinstance(row, dict)
                or not isinstance(row.get('executed_lines'), list)
                or not isinstance(row.get('missing_lines'), list)):
            _fail('coverage_invalid_shape')
    return {'percent': float(percent), 'files': files}


def parse_test_log(text):
    if not isinstance(text, str) or len(text) > MAX_FILE_BYTES:
        _fail('test_log_invalid')
    ran = _RAN.findall(text)
    if not ran:
        _fail('test_count_missing')
    count = int(ran[-1])
    if 'FAILED' in text:
        _fail('tests_failed')
    if not re.search(r'^OK(?: \(skipped=[0-9]+\))?\s*$', text, re.MULTILINE):
        _fail('test_result_not_ok')
    skipped_match = _SKIPPED.search(text)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    reasons = _SKIP_LINE.findall(text)
    return {'tests': count, 'skipped': skipped, 'skip_reasons': reasons}


def unknown_skips(policy, parsed):
    allowed = [re.compile(item) for item in policy['allowed_skip_patterns']]
    unknown = [reason for reason in parsed['skip_reasons']
               if not any(pattern.search(reason) for pattern in allowed)]
    if unknown:
        _fail('unknown_skip:' + unknown[0][:120])
    if parsed['skipped'] and not parsed['skip_reasons']:
        _fail('skip_reasons_unavailable')
    if len(parsed['skip_reasons']) != parsed['skipped']:
        _fail('skip_reason_count_mismatch')


def _changed_lines(root, base_sha):
    try:
        raw = subprocess.run(
            ['git', '--no-replace-objects', 'diff', '--unified=0', '--no-renames',
             '--no-ext-diff', f'{base_sha}...HEAD'],
            cwd=str(root), check=True, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        _fail('changed_lines_unavailable')
    changed, deleted, path = {}, set(), None
    for line in raw.decode('utf-8', 'replace').splitlines():
        if line.startswith('--- a/'):
            path = line[6:]
        elif line.startswith('+++ /dev/null'):
            if path:
                deleted.add(path)
            path = None
        elif line.startswith('+++ b/'):
            path = line[6:]
            changed.setdefault(path, set())
        elif line.startswith('@@') and path is not None:
            match = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if not match:
                continue
            start = int(match.group(1))
            length = int(match.group(2) or 1)
            changed[path].update(range(start, start + length))
    return changed, deleted


def changed_line_coverage(coverage, changed, deleted=None):
    for path in sorted(deleted or ()):
        if path.startswith(MEASURED_PREFIXES) and path.endswith('.py'):
            _fail('source_deleted:' + path)
    total = covered = 0
    for path, lines in changed.items():
        row = coverage['files'].get(path) or coverage['files'].get('/' + path)
        if not isinstance(row, dict):
            # Only changed Python product source absent from coverage is unmeasured;
            # templates/static/JSON are not measured by coverage.py.
            if (path.startswith(MEASURED_PREFIXES) and path.endswith('.py')
                    and '/migrations/' not in path):
                _fail('changed_source_unmeasured:' + path)
            continue
        executed = set(row.get('executed_lines') or ())
        missing = set(row.get('missing_lines') or ())
        measured = executed | missing
        total += len(lines & measured)
        covered += len(lines & executed)
    if total == 0:
        return None
    return 100.0 * covered / total


def evaluate(policy, coverage, parsed, changed=None, deleted=None):
    if parsed['tests'] < policy['test_count_min']:
        _fail('test_count_below_min')
    unknown_skips(policy, parsed)
    if coverage['percent'] < policy['total_min_percent']:
        _fail('coverage_total_below_min')
    changed_percent = None
    if changed is not None or deleted:
        changed_percent = changed_line_coverage(coverage, changed or {}, deleted)
        if changed_percent is not None and changed_percent < policy['changed_lines_min_percent']:
            _fail('coverage_changed_lines_below_min')
    return {
        'ok': True,
        'tests': parsed['tests'],
        'skipped': parsed['skipped'],
        'coverage_total_percent': round(coverage['percent'], 2),
        'coverage_changed_lines_percent': (
            None if changed_percent is None else round(changed_percent, 2)),
        'total_min_percent': policy['total_min_percent'],
        'changed_lines_min_percent': policy['changed_lines_min_percent'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--base', required=True)
    parser.add_argument('--coverage', required=True)
    parser.add_argument('--test-log', required=True)
    parser.add_argument('--changed-since', default=None)
    args = parser.parse_args(argv)
    try:
        root = Path(args.root)
        policy = load_base_policy(root, args.base)
        coverage = read_coverage(args.coverage)
        try:
            log = Path(args.test_log).read_text(encoding='utf-8', errors='replace')
        except OSError:
            _fail('test_log_missing')
        parsed = parse_test_log(log)
        if args.changed_since is not False:
            changed, deleted = _changed_lines(root, args.changed_since or args.base)
        else:
            changed, deleted = None, None
        result = evaluate(policy, coverage, parsed, changed, deleted)
    except (OSError, ValueError, GateError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
