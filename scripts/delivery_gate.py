"""Offline metadata gate for trusted PR delivery (PH-120).

Reads the versioned policy from the PR BASE (never the candidate HEAD) and
checks only metadata text: exactly one Task line, base/head identity, the eight
existing required status checks plus four arbitration evidence sources, and
linear-history/independent-review policy flags. It performs no network I/O and
never executes candidate code; GitHub evidence remains authoritative in the
controller/GitHubDelivery path.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


POLICY_PATH = 'project/delivery-policy.json'
MAX_EVENT_BYTES = 4 * 1024 * 1024


class GateError(ValueError):
    pass


def _fail(message):
    raise GateError(message)


def load_base_policy(root, base_sha):
    if not isinstance(base_sha, str) or not re.fullmatch(r'[0-9a-f]{40,64}', base_sha):
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
    return validate_policy(policy)


def validate_policy(policy):
    if not isinstance(policy, dict):
        _fail('policy_not_object')
    if policy.get('schema_version') != 1:
        _fail('policy_schema_version')
    if policy.get('policy_id') != 'project-hub.delivery-policy/v1':
        _fail('policy_id')
    if policy.get('base_policy_source') != 'git-show-base':
        _fail('policy_base_source')
    task_line = policy.get('task_line')
    if (not isinstance(task_line, dict) or task_line.get('required_count') != 1
            or not isinstance(task_line.get('pattern'), str)):
        _fail('policy_task_line')
    try:
        re.compile(task_line['pattern'])
    except re.error:
        _fail('policy_task_line')
    checks = policy.get('required_status_checks')
    sources = policy.get('arbitration_sources')
    if not isinstance(checks, list) or len(checks) != 8 or len(set(checks)) != 8:
        _fail('policy_required_checks')
    if not isinstance(sources, list) or len(sources) != 4 or len(set(sources)) != 4:
        _fail('policy_arbitration_sources')
    if policy.get('required_check_count') != 12:
        _fail('policy_required_check_count')
    authorization = policy.get('s_authorization')
    grants = authorization.get('tasks') if isinstance(authorization, dict) else None
    if not isinstance(authorization, dict) or not isinstance(grants, list) or not grants:
        _fail('policy_s_authorization')
    seen_grants = set()
    for grant in grants:
        if (not isinstance(grant, dict)
                or set(grant) != {'task_id', 'contract_digest', 'source'}
                or not isinstance(grant['task_id'], str)
                or not re.fullmatch(r'PH-[0-9]{3,6}', grant['task_id'])
                or grant['task_id'] in seen_grants
                or not isinstance(grant['source'], str) or not grant['source']
                or len(grant['source']) > 200
                or not isinstance(grant['contract_digest'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', grant['contract_digest'])):
            _fail('policy_s_authorization')
        seen_grants.add(grant['task_id'])
    remote = policy.get('remote_arbitration')
    if (not isinstance(remote, dict)
            or remote.get('codex_app_id') != 1144995
            or remote.get('codex_bot_user_id') != 199175422
            or remote.get('codex_bot_login') != 'chatgpt-codex-connector[bot]'
            or remote.get('workflow_path') != '.github/workflows/delivery.yml'
            or remote.get('workflow_event') != 'pull_request'):
        _fail('policy_remote_arbitration')
    bootstrap = policy.get('bootstrap')
    if (not isinstance(bootstrap, dict)
            or type(bootstrap.get('remote_checks_enforced')) is not bool):
        _fail('policy_bootstrap')
    review = policy.get('independent_review')
    if (not isinstance(review, dict) or review.get('required') is not True
            or review.get('reviewer_must_differ_from_source_actors') is not True
            or review.get('current_head_only') is not True
            or review.get('latest_complete_result_only') is not True
            or review.get('reject_unresolved_findings') is not True
            or review.get('official_bot_identity_required') is not True):
        _fail('policy_independent_review')
    merge = policy.get('merge')
    if (not isinstance(merge, dict) or merge.get('linear_history_required') is not True
            or merge.get('strict_required') is not True
            or merge.get('administrator_protection_required') is not True
            or merge.get('mergeable_required') is not True
            or merge.get('unresolved_discussions_rejected') is not True
            or merge.get('self_reported_pass_rejected') is not True):
        _fail('policy_merge')
    return policy


def authorize_task(policy, task_id, contract_digest):
    """Bind S-scope authorization to the exact frozen BASE task/spec contract."""
    grants = policy['s_authorization']['tasks']
    grant = next((row for row in grants if row['task_id'] == task_id), None)
    if grant is None:
        _fail('s_task_not_authorized')
    if not isinstance(contract_digest, str) or grant['contract_digest'] != contract_digest:
        _fail('s_task_contract_changed')
    return True


def check_pr_metadata(policy, event, *, base_sha=None):
    pr = event.get('pull_request') if isinstance(event, dict) else None
    if not isinstance(pr, dict):
        _fail('pull_request_required')
    body = pr.get('body') or ''
    if not isinstance(body, str):
        _fail('invalid_body')
    pattern = re.compile(policy['task_line']['pattern'], re.MULTILINE)
    matches = pattern.findall(body)
    if len(matches) != 1:
        _fail('task_line_count')
    task_id = matches[0]
    base = (pr.get('base') or {}).get('sha')
    head = (pr.get('head') or {}).get('sha')
    if not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{40,64}', base):
        _fail('invalid_base_sha')
    if not isinstance(head, str) or not re.fullmatch(r'[0-9a-f]{40,64}', head):
        _fail('invalid_head_sha')
    if base_sha is not None and base != base_sha:
        _fail('base_mismatch')
    if pr.get('mergeable') is not True:
        _fail('mergeable_required')
    if policy['merge']['linear_history_required'] is not True:
        _fail('linear_history_required')
    return {'ok': True, 'task_id': task_id, 'base_sha': base, 'head_sha': head,
            'required_check_count': policy['required_check_count'],
            'arbitration_sources': list(policy['arbitration_sources'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--base', required=True)
    parser.add_argument('--event', required=True)
    args = parser.parse_args(argv)
    try:
        policy = load_base_policy(Path(args.root), args.base)
        raw = Path(args.event).read_bytes()
        if len(raw) > MAX_EVENT_BYTES:
            _fail('event_too_large')
        event = json.loads(raw)
        result = check_pr_metadata(policy, event, base_sha=args.base)
    except (OSError, ValueError, GateError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
