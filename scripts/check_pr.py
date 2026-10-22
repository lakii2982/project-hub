"""Run the BASE policy on PR changes. No shell evaluation of PR text."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    pr = event.get('pull_request')
    if not pr:
        print(json.dumps({'ok': True, 'scope': 'push-metadata-check-only'}))
        return 0
    body = pr.get('body') or ''
    matches = re.findall(r'^Task:\s*(PH-[0-9]{3,6})\s*$', body, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError('PR body must contain exactly one line: Task: PH-001')
    base = pr['base']['sha']
    if not re.fullmatch(r'[0-9a-f]{40,64}', base):
        raise ValueError('Invalid base commit')
    # The tested policy comes from BASE; modifying HEAD policy cannot bypass it.
    policy = subprocess.run(['git', '--no-replace-objects', 'show', base + ':scripts/projectctl.py'], capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory(prefix='project-hub-policy-') as directory:
        path = Path(directory) / 'projectctl.py'
        path.write_bytes(policy)
        result = subprocess.run([sys.executable, str(path), 'verify-change', '--root', str(Path.cwd()), '--base', base, '--task', matches[0]], check=False, env=dict(os.environ, GIT_NO_REPLACE_OBJECTS="1"))
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        raise SystemExit(1)
