"""PH-120 trusted PR delivery policy and BASE metadata gate."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.delivery_gate import (
    GateError,
    authorize_task,
    check_pr_metadata,
    load_base_policy,
    validate_policy,
)


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / 'project/delivery-policy.json'


def policy():
    return json.loads(POLICY.read_text(encoding='utf-8'))


def event(body, *, base='a' * 40, head='b' * 40, mergeable=True):
    return {'pull_request': {'body': body, 'mergeable': mergeable,
                             'base': {'sha': base}, 'head': {'sha': head}}}


class PolicyShapeTests(unittest.TestCase):
    def test_versioned_policy_is_complete_and_has_eight_plus_four(self):
        validated = validate_policy(policy())
        self.assertEqual(validated['schema_version'], 1)
        self.assertEqual(len(validated['required_status_checks']), 8)
        self.assertEqual(len(validated['arbitration_sources']), 4)
        self.assertEqual(validated['required_check_count'], 12)
        self.assertTrue(validated['independent_review']['required'])
        self.assertTrue(validated['merge']['linear_history_required'])
        self.assertFalse(validated['bootstrap']['remote_checks_enforced'])
        self.assertEqual(validated['remote_arbitration']['codex_app_id'], 1144995)

    def test_s_authorization_is_bound_to_exact_task_contract(self):
        validated = validate_policy(policy())
        grant = validated['s_authorization']['tasks'][0]
        self.assertTrue(authorize_task(validated, 'PH-120', grant['contract_digest']))
        with self.assertRaisesRegex(GateError, 'contract_changed'):
            authorize_task(validated, 'PH-120', '0' * 64)
        with self.assertRaisesRegex(GateError, 'not_authorized'):
            authorize_task(validated, 'PH-398', grant['contract_digest'])

    def test_remote_identity_and_authorization_mutations_fail_closed(self):
        for mutate in (
                lambda value: value['remote_arbitration'].update(codex_app_id=1),
                lambda value: value['remote_arbitration'].update(codex_bot_user_id=1),
                lambda value: value['s_authorization'].update(tasks=[]),
                lambda value: value['s_authorization']['tasks'][0].update(contract_digest='bad'),
                lambda value: value['bootstrap'].update(remote_checks_enforced=None)):
            bad = policy()
            mutate(bad)
            with self.assertRaises(GateError):
                validate_policy(bad)

    def test_policy_mutations_fail_closed(self):
        mutations = [
            ('schema_version', 2), ('base_policy_source', 'head'),
            ('required_check_count', 8),
        ]
        for key, value in mutations:
            bad = policy()
            bad[key] = value
            with self.assertRaises(GateError, msg=key):
                validate_policy(bad)
        bad = policy()
        bad['required_status_checks'] = bad['required_status_checks'][:7]
        with self.assertRaises(GateError):
            validate_policy(bad)
        bad = policy()
        bad['arbitration_sources'] = bad['arbitration_sources'][:3]
        with self.assertRaises(GateError):
            validate_policy(bad)
        bad = policy()
        bad['independent_review']['reviewer_must_differ_from_source_actors'] = False
        with self.assertRaises(GateError):
            validate_policy(bad)

    def test_base_policy_is_read_from_git_not_head(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['git', 'init', '-q'], cwd=directory, check=True)
            (Path(directory) / 'project').mkdir()
            (Path(directory) / 'project/delivery-policy.json').write_text(
                json.dumps(policy()), encoding='utf-8')
            subprocess.run(['git', 'add', 'project/delivery-policy.json'], cwd=directory, check=True)
            subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
                            'commit', '-qm', 'base policy'], cwd=directory, check=True)
            base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=directory,
                                  check=True, capture_output=True, text=True).stdout.strip()
            loaded = load_base_policy(Path(directory), base)
            self.assertEqual(loaded['policy_id'], policy()['policy_id'])
            with self.assertRaises(GateError):
                load_base_policy(Path(directory), '0' * 40)


class PRMetadataTests(unittest.TestCase):
    def setUp(self):
        self.policy = validate_policy(policy())

    def test_exactly_one_task_line_and_identity(self):
        result = check_pr_metadata(self.policy, event('Task: PH-120\n\nbody'))
        self.assertEqual(result['task_id'], 'PH-120')
        self.assertEqual(result['required_check_count'], 12)

    def test_zero_or_multiple_task_lines_are_rejected(self):
        for body in ('no task', 'Task: PH-120\nTask: PH-121\n', 'Task: PH-120\nTask: PH-121\n'):
            with self.assertRaises(GateError):
                check_pr_metadata(self.policy, event(body))

    def test_invalid_base_head_mergeable_and_base_mismatch_are_rejected(self):
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event('Task: PH-120', base='bad'))
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event('Task: PH-120', head='bad'))
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event('Task: PH-120', mergeable=False))
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event('Task: PH-120', base='a' * 40),
                              base_sha='c' * 40)

    def test_non_pull_request_and_non_string_body_are_rejected(self):
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, {})
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event(None))
