"""PH-121 quality gate: trusted BASE policy, coverage and test-log evidence."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.quality_gate import (
    GateError,
    changed_line_coverage,
    evaluate,
    load_base_policy,
    parse_test_log,
    read_coverage,
    validate_quality,
)

ROOT = Path(__file__).resolve().parent.parent


def quality(**changes):
    value = {
        'coverage_python': '3.12',
        'total_min_percent': 50.0,
        'changed_lines_min_percent': 80.0,
        'test_count_min': 100,
        'unknown_skip_blocked': True,
        'allowed_skip_patterns': ['Requires real PostgreSQL', 'platform'],
    }
    value.update(changes)
    return value


def coverage(percent=60.0, files=None):
    return {'percent': percent, 'files': files or {}}


def log(count=200, skipped=0, failed=False, ok=True):
    lines = ['x' * count, '...']
    if failed:
        lines.append('FAILED (failures=1)')
    elif ok:
        lines.append(f'OK (skipped={skipped})' if skipped else 'OK')
    lines.append(f'Ran {count} tests in 1.000s')
    return '\n'.join(reversed(lines)) + '\n'


class PolicyTests(unittest.TestCase):
    def test_valid_policy(self):
        self.assertEqual(validate_quality(quality())['test_count_min'], 100)

    def test_policy_mutations_fail_closed(self):
        for mutation in (
                {'total_min_percent': 101}, {'total_min_percent': '50'},
                {'changed_lines_min_percent': -1}, {'test_count_min': 0},
                {'unknown_skip_blocked': False}, {'allowed_skip_patterns': []},
                {'allowed_skip_patterns': ['*']}, {'coverage_python': '3.11'}):
            with self.subTest(mutation=mutation), self.assertRaises(GateError):
                validate_quality(quality(**mutation))

    def test_base_policy_is_read_from_git_not_head(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'init', '-q'], cwd=directory, check=True)
            (Path(directory) / 'project').mkdir()
            (Path(directory) / 'project/delivery-policy.json').write_text(
                json.dumps({'quality': quality()}), encoding='utf-8')
            subprocess.run(['git', 'add', 'project/delivery-policy.json'], cwd=directory, check=True)
            subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                            'commit', '-qm', 'base'], cwd=directory, check=True)
            base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=directory,
                                  check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(load_base_policy(Path(directory), base)['test_count_min'], 100)
            with self.assertRaises(GateError):
                load_base_policy(Path(directory), '0' * 40)


class EvidenceTests(unittest.TestCase):
    def test_missing_and_corrupt_coverage_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'coverage.json'
            with self.assertRaises(GateError):
                read_coverage(path)
            path.write_text('{bad', encoding='utf-8')
            with self.assertRaises(GateError):
                read_coverage(path)
            path.write_text(json.dumps({'totals': {}, 'files': {}}), encoding='utf-8')
            with self.assertRaises(GateError):
                read_coverage(path)

    def test_test_log_shapes(self):
        parsed = parse_test_log(log(320, skipped=2))
        self.assertEqual((parsed['tests'], parsed['skipped']), (320, 2))
        with self.assertRaises(GateError):
            parse_test_log('no summary')
        with self.assertRaises(GateError):
            parse_test_log('Ran 3 tests in 1s\nFAILED (failures=1)')
        with self.assertRaises(GateError):
            parse_test_log('Ran 3 tests in 1s\nsomething else')

    def test_skip_reasons_are_required_and_whitelisted(self):
        allowed = ("...skipped 'Requires real PostgreSQL'\n\n"
                   "Ran 200 tests in 1s\nOK (skipped=1)\n")
        evaluate(validate_quality(quality()), coverage(), parse_test_log(allowed))
        unknown = ("...skipped 'unexpected quirk from a plugin'\n\n"
                   "Ran 200 tests in 1s\nOK (skipped=1)\n")
        with self.assertRaisesRegex(GateError, 'unknown_skip'):
            evaluate(validate_quality(quality()), coverage(), parse_test_log(unknown))
        missing = 'Ran 200 tests in 1s\nOK (skipped=1)\n'
        with self.assertRaisesRegex(GateError, 'skip_reasons_unavailable'):
            evaluate(validate_quality(quality()), coverage(), parse_test_log(missing))

    def test_floor_zero_tests_and_swallowed_failures(self):
        policy = validate_quality(quality())
        with self.assertRaisesRegex(GateError, 'test_count_below_min'):
            evaluate(policy, coverage(), parse_test_log(log(99)))
        with self.assertRaisesRegex(GateError, 'coverage_total_below_min'):
            evaluate(policy, coverage(49.9), parse_test_log(log(200)))
        with self.assertRaises(GateError):
            evaluate(policy, coverage(), parse_test_log(log(200, failed=True)))

    def test_skip_reason_count_must_match_summary(self):
        text = ("...skipped 'Requires real PostgreSQL'\n\n"
                "Ran 200 tests in 1s\nOK (skipped=2)\n")
        with self.assertRaisesRegex(GateError, 'skip_reason_count_mismatch'):
            evaluate(validate_quality(quality()), coverage(), parse_test_log(text))

    def test_changed_product_source_must_be_measured(self):
        policy = validate_quality(quality())
        with self.assertRaisesRegex(GateError, 'changed_source_unmeasured'):
            evaluate(policy, coverage(), parse_test_log(log(200)), {'core/new_module.py': {1}})

    def test_deleted_product_source_is_rejected(self):
        policy = validate_quality(quality())
        files = {'core/gone.py': {'executed_lines': [1], 'missing_lines': []}}
        with self.assertRaisesRegex(GateError, 'source_deleted'):
            evaluate(policy, coverage(files=files), parse_test_log(log(200)),
                     {}, {'core/gone.py'})

    def test_empty_coverage_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'coverage.json'
            path.write_text(json.dumps({'totals': {'percent_covered': 90},
                                        'files': {}}), encoding='utf-8')
            with self.assertRaises(GateError):
                read_coverage(path)

    def test_changed_lines_threshold(self):
        files = {'core/x.py': {'executed_lines': [1, 2, 3, 9],
                               'missing_lines': [4, 5]}}
        changed = {'core/x.py': {1, 2, 4, 5}}
        self.assertEqual(changed_line_coverage(coverage(files=files), changed), 50.0)
        policy = validate_quality(quality())
        with self.assertRaisesRegex(GateError, 'changed_lines_below_min'):
            evaluate(policy, coverage(files=files), parse_test_log(log(200)), changed)
        policy = validate_quality(quality(changed_lines_min_percent=50.0))
        self.assertEqual(evaluate(policy, coverage(files=files),
                                  parse_test_log(log(200)), changed)['coverage_changed_lines_percent'], 50.0)

    def test_no_changed_executable_lines_is_reported_as_null(self):
        policy = validate_quality(quality())
        result = evaluate(policy, coverage(), parse_test_log(log(200)), {})
        self.assertIsNone(result['coverage_changed_lines_percent'])


class RepositoryPolicyTests(unittest.TestCase):
    def test_repository_policy_contains_quality_section(self):
        policy = json.loads((ROOT / 'project/delivery-policy.json').read_text(encoding='utf-8'))
        validate_quality(policy.get('quality'))
