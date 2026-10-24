"""Fixed gh REST argv, JSON stdin, no shell, no search-index reconciliation."""
import json
from pathlib import Path
import re
import subprocess

from .errors import Refused
from .schema import repository_name


class RemoteUnknown(Exception):
    pass


class AuthenticationError(Refused):
    pass


class RemoteRejected(Refused):
    pass


class GhClient:
    def __init__(self, executable, *, runner=subprocess.run):
        # executable is trusted local startup configuration, never payload text.
        if not Path(executable).is_absolute():
            raise Refused('absolute trusted gh executable required')
        self.executable = str(executable)
        self.runner = runner

    def _call(self, method, endpoint, payload=None):
        argv = [self.executable, 'api', '--hostname', 'github.com', '--method', method, endpoint]
        if payload is not None:
            argv += ['--input', '-']
        attempts = 2 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                result = self.runner(argv, input=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                                     text=True, capture_output=True, shell=False, timeout=30, check=False)
            except (subprocess.TimeoutExpired, OSError):
                if attempt + 1 < attempts:
                    continue
                raise RemoteUnknown('gh transport result unavailable') from None
            if result.returncode:
                if re.search(r'\bHTTP\s+(401|403)\b', result.stderr or '') or 'not logged' in (result.stderr or '').lower():
                    raise AuthenticationError('GitHub authentication/permission denied')
                if re.search(r'\bHTTP\s+(404|422)\b', result.stderr or ''):
                    raise RemoteRejected('GitHub request definitely rejected')
                if method == 'GET' and attempt + 1 < attempts and re.search(r'\bHTTP\s+(429|502|503|504)\b|timeout|connection|network', result.stderr or '', re.I):
                    continue
                raise RemoteUnknown('gh request result unavailable')
            try:
                return json.loads(result.stdout)
            except (ValueError, TypeError):
                raise RemoteUnknown('gh response invalid') from None
        raise AssertionError('unreachable')

    def principal(self):
        row = self._call('GET', 'user')
        if not isinstance(row, dict) or not isinstance(row.get('login'), str):
            raise AuthenticationError('GitHub principal unavailable')
        return row['login']

    def list_issues(self, repository):
        repository_name(repository)
        page = 1
        while True:
            rows = self._call('GET', f'repos/{repository}/issues?state=all&per_page=100&page={page}')
            if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
                raise RemoteUnknown('GitHub issue page invalid')
            for row in rows:
                if 'pull_request' not in row:
                    yield row
            if len(rows) < 100:
                break
            page += 1

    def write_issue(self, repository, issue_number, title, body):
        repository_name(repository)
        endpoint = f'repos/{repository}/issues'
        if issue_number is not None:
            if type(issue_number) is not int or issue_number < 1:
                raise Refused('invalid issue number')
            endpoint += f'/{issue_number}'
        return self._call('POST' if issue_number is None else 'PATCH', endpoint, {'title': title, 'body': body})
