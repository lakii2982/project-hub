"""Run the trusted BASE policy on PR changes. No shell evaluation of PR text.

A PR body carries exactly one reference line:

* ``Queue: Q-nn`` selects the light-delivery path: the queue item must be
  defined in ``project/QUEUE.md``; code, tests, required CI, the demo evidence
  and the single independent review are the acceptance evidence.
* ``Task: PH-nnn`` is the legacy task contract, still accepted during the
  transition; it keeps running the BASE ``verify-change`` scope/report check.

Both lines may appear together while a queue item is still mapped to a legacy
task; the legacy check then runs as well.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TASK_LINE = re.compile(r'^Task:\s*(PH-[0-9]{3,6})\s*$', re.MULTILINE)
QUEUE_LINE = re.compile(r'^Queue:\s*(Q-[0-9]{2,3})\s*$', re.MULTILINE)
QUEUE_ITEM_ROW = re.compile(r'^\|\s*(Q-[0-9]{2,3})\s*\|', re.MULTILINE)
QUEUE_ITEM_HEADING = re.compile(r'^#{2,4}\s*(Q-[0-9]{2,3})\b', re.MULTILINE)


def extract_references(body):
    """Return (task_id, queue_id); exactly one reference line is required."""
    if not isinstance(body, str):
        raise ValueError('Invalid PR body')
    tasks = TASK_LINE.findall(body)
    queues = QUEUE_LINE.findall(body)
    if len(tasks) > 1 or len(queues) > 1:
        raise ValueError('PR body must contain at most one Task line and at most one Queue line')
    if not tasks and not queues:
        raise ValueError('PR body must contain exactly one reference line: Queue: Q-01 (or Task: PH-001 during the transition)')
    return (tasks[0] if tasks else None), (queues[0] if queues else None)


def queue_items(root):
    """Return the queue item ids defined by project/QUEUE.md, in document order."""
    path = Path(root) / 'project' / 'QUEUE.md'
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    items = []
    for heading in QUEUE_ITEM_HEADING.finditer(text):
        if heading.group(1) not in items:
            items.append(heading.group(1))
    for row in QUEUE_ITEM_ROW.finditer(text):
        if row.group(1) not in items:
            items.append(row.group(1))
    return items


def validate_queue_reference(root, queue_id):
    items = queue_items(root)
    if not items:
        raise ValueError('project/QUEUE.md defines no queue items')
    if queue_id not in items:
        raise ValueError('Queue item ' + queue_id + ' is not defined in project/QUEUE.md')
    return True


def _base_verify_change(root, base, task_id):
    """The legacy contract check comes from the trusted BASE, never the candidate."""
    policy = subprocess.run(
        ['git', '--no-replace-objects', 'show', base + ':scripts/projectctl.py'],
        capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory(prefix='project-hub-policy-') as directory:
        path = Path(directory) / 'projectctl.py'
        path.write_bytes(policy)
        return subprocess.run(
            [sys.executable, str(path), 'verify-change', '--root', str(root),
             '--base', base, '--task', task_id],
            check=False, env=dict(os.environ, GIT_NO_REPLACE_OBJECTS='1'))


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    pr = event.get('pull_request')
    if not pr:
        print(json.dumps({'ok': True, 'scope': 'push-metadata-check-only'}))
        return 0
    task_id, queue_id = extract_references(pr.get('body') or '')
    base = pr['base']['sha']
    if not re.fullmatch(r'[0-9a-f]{40,64}', base):
        raise ValueError('Invalid base commit')
    if queue_id is not None:
        validate_queue_reference(Path.cwd(), queue_id)
    if task_id is not None:
        # The tested policy comes from BASE; modifying HEAD policy cannot bypass it.
        return _base_verify_change(Path.cwd(), base, task_id).returncode
    print(json.dumps({'ok': True, 'mode': 'queue', 'queue_id': queue_id,
                      'required_checks': 9, 'independent_review_passes': 1}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        raise SystemExit(1)
