"""Offline metadata gate for trusted PR delivery (PH-120).

Reads the versioned policy from the PR BASE (never the candidate HEAD) and
checks only metadata text: exactly one reference line (a ``Queue: Q-nn`` queue
item, or a legacy ``Task: PH-nnn`` during the transition), base/head identity,
the nine existing required status checks plus four arbitration evidence
sources, and linear-history/independent-review policy flags. It performs no network I/O and
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


def _compile(pattern, error_code):
    if not isinstance(pattern, str):
        _fail(error_code)
    try:
        return re.compile(pattern, re.MULTILINE)
    except re.error:
        _fail(error_code)


def _reference_patterns(policy):
    """Return [(kind, compiled_pattern, required_count)] declared by the policy."""
    rows = []
    for kind, key in (('queue', 'queue_line'), ('task', 'task_line')):
        config = policy.get(key)
        if not isinstance(config, dict):
            continue
        rows.append((kind, _compile(config.get('pattern'), 'policy_' + key),
                     config.get('required_count')))
    if not rows:
        _fail('policy_reference_line')
    return rows


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
    if (not isinstance(task_line, dict)
            or type(task_line.get('required_count')) is not int
            or task_line.get('required_count') not in (0, 1)):
        _fail('policy_task_line')
    _compile(task_line.get('pattern'), 'policy_task_line')
    queue_line = policy.get('queue_line')
    if queue_line is not None:
        if (not isinstance(queue_line, dict)
                or type(queue_line.get('required_count')) is not int
                or queue_line.get('required_count') not in (0, 1)):
            _fail('policy_queue_line')
        _compile(queue_line.get('pattern'), 'policy_queue_line')
        queue_doc = queue_line.get('queue_doc')
        if queue_doc is not None and (
                not isinstance(queue_doc, str) or not queue_doc.startswith('project/')
                or not queue_doc.endswith('.md')):
            _fail('policy_queue_line')
    reference = policy.get('reference_line')
    if reference is not None:
        if (not isinstance(reference, dict)
                or type(reference.get('minimum_count')) is not int
                or not 0 <= reference['minimum_count'] <= 4
                or type(reference.get('maximum_per_kind')) is not int
                or not 1 <= reference['maximum_per_kind'] <= 4):
            _fail('policy_reference_line')
    has_required_kind = (task_line.get('required_count') == 1
                         or (isinstance(queue_line, dict)
                             and queue_line.get('required_count') == 1))
    has_minimum = (isinstance(reference, dict) and reference.get('minimum_count') >= 1)
    if not has_required_kind and not has_minimum:
        _fail('policy_reference_line')
    checks = policy.get('required_status_checks')
    sources = policy.get('arbitration_sources')
    if not isinstance(checks, list) or len(checks) != 9 or len(set(checks)) != 9:
        _fail('policy_required_checks')
    if not isinstance(sources, list) or len(sources) != 4 or len(set(sources)) != 4:
        _fail('policy_arbitration_sources')
    if policy.get('required_check_count') != 13:
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
    references = _reference_patterns(policy)
    found = {}
    maximum = policy.get('reference_line', {}).get('maximum_per_kind', 1)
    for kind, pattern, required in references:
        matches = pattern.findall(body)
        if len(matches) > maximum:
            _fail(kind + '_line_count')
        if required == 1 and len(matches) != 1:
            _fail(kind + '_line_count')
        if matches:
            found[kind] = matches[0]
    minimum = policy.get('reference_line', {}).get('minimum_count')
    if minimum is None:
        minimum = 1 if any(row[2] == 1 for row in references) else 0
    if len(found) < minimum:
        _fail('reference_line_missing')
    task_id = found.get('task')
    queue_id = found.get('queue')
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
    return {'ok': True, 'task_id': task_id, 'queue_id': queue_id,
            'references': {'task': task_id, 'queue': queue_id},
            'base_sha': base, 'head_sha': head,
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
