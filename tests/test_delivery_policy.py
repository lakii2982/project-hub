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
    def test_versioned_policy_is_complete_and_has_nine_plus_four(self):
        validated = validate_policy(policy())
        self.assertEqual(validated['schema_version'], 1)
        self.assertEqual(len(validated['required_status_checks']), 9)
        self.assertEqual(len(validated['arbitration_sources']), 4)
        self.assertEqual(validated['required_check_count'], 13)
        self.assertTrue(validated['independent_review']['required'])
        self.assertTrue(validated['merge']['linear_history_required'])
        self.assertFalse(validated['bootstrap']['remote_checks_enforced'])
        self.assertEqual(validated['remote_arbitration']['codex_app_id'], 1144995)

    def test_light_delivery_reference_lines_are_declared(self):
        validated = validate_policy(policy())
        self.assertEqual(validated['task_line']['required_count'], 0)
        self.assertTrue(validated['task_line']['accepted_during_transition'])
        self.assertEqual(validated['queue_line']['required_count'], 0)
        self.assertEqual(validated['queue_line']['queue_doc'], 'project/QUEUE.md')
        self.assertEqual(validated['reference_line']['minimum_count'], 1)
        self.assertEqual(validated['reference_line']['maximum_per_kind'], 1)
        self.assertEqual(validated['workflow']['protocol'],
                         'docs/engineering/light-delivery.md')
        self.assertEqual(validated['workflow']['queue'], 'project/QUEUE.md')

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

    def test_reference_line_configuration_mutations_fail_closed(self):
        mutations = [
            lambda value: value['task_line'].update(required_count=2),
            lambda value: value['task_line'].update(pattern=1),
            lambda value: value['queue_line'].update(required_count=3),
            lambda value: value['queue_line'].update(pattern='('),
            lambda value: value['queue_line'].update(queue_doc='docs/QUEUE.md'),
            lambda value: value['reference_line'].update(minimum_count=-1),
            lambda value: value['reference_line'].update(maximum_per_kind=0),
            lambda value: value.update(reference_line=[]),
        ]
        for mutate in mutations:
            bad = policy()
            mutate(bad)
            with self.assertRaises(GateError):
                validate_policy(bad)
        optional = policy()
        optional['task_line']['required_count'] = 0
        optional['queue_line']['required_count'] = 0
        optional['reference_line']['minimum_count'] = 0
        with self.assertRaises(GateError):
            validate_policy(optional)

    def test_policy_mutations_fail_closed(self):
        mutations = [
            ('schema_version', 2), ('base_policy_source', 'head'),
            ('required_check_count', 12),
        ]
        for key, value in mutations:
            bad = policy()
            bad[key] = value
            with self.assertRaises(GateError, msg=key):
                validate_policy(bad)
        bad = policy()
        bad['required_status_checks'] = bad['required_status_checks'][:8]
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
        self.assertEqual(result['required_check_count'], 13)

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

    def test_null_or_textual_mergeable_is_not_accepted_as_true(self):
        for value in (None, 'MERGEABLE', 'true', 1):
            with self.subTest(value=value), self.assertRaisesRegex(GateError, 'mergeable_required'):
                check_pr_metadata(self.policy, event('Task: PH-120', mergeable=value))

    def test_workflow_refreshes_nullable_mergeable_from_current_pr_api(self):
        workflow = (ROOT / '.github/workflows/delivery.yml').read_text(encoding='utf-8')
        self.assertIn('pull-requests: read', workflow)
        self.assertIn('repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}', workflow)
        self.assertIn("current.get('mergeable') is not True", workflow)
        self.assertIn("current.get('base') or {}).get('sha')", workflow)
        self.assertIn("current.get('head') or {}).get('sha')", workflow)
        self.assertIn('mergeable_unavailable_or_changed', workflow)
        self.assertNotIn('pull_request_target', workflow)

    def test_non_pull_request_and_non_string_body_are_rejected(self):
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, {})
        with self.assertRaises(GateError):
            check_pr_metadata(self.policy, event(None))

    def test_queue_line_is_accepted_alone_or_with_a_transition_task(self):
        result = check_pr_metadata(self.policy, event('Queue: Q-08\n\nbody'))
        self.assertIsNone(result['task_id'])
        self.assertEqual(result['queue_id'], 'Q-08')
        self.assertEqual(result['references'], {'task': None, 'queue': 'Q-08'})
        both = check_pr_metadata(self.policy, event('Queue: Q-08\nTask: PH-120\n\nbody'))
        self.assertEqual(both['task_id'], 'PH-120')
        self.assertEqual(both['queue_id'], 'Q-08')

    def test_queue_and_task_lines_are_capped_at_one_each(self):
        for body in ('Queue: Q-08\nQueue: Q-09\n',
                     'Queue: Q-08\nTask: PH-120\nTask: PH-121\n',
                     'Queue: Q-8\n',
                     'Queue: Q-0800\n'):
            with self.subTest(body=body), self.assertRaises(GateError):
                check_pr_metadata(self.policy, event(body))

    def test_legacy_policy_without_queue_configuration_still_requires_a_task_line(self):
        legacy = policy()
        legacy.pop('queue_line')
        legacy['task_line'] = {'pattern': '^Task: (PH-[0-9]{3,6})\\s*$', 'required_count': 1}
        validated = validate_policy(legacy)
        check_pr_metadata(validated, event('Task: PH-120'))
        with self.assertRaises(GateError):
            check_pr_metadata(validated, event('Queue: Q-08'))
