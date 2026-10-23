"""Serialized, authorized GitHub issue dispatch with uncertain-result recovery."""
from datetime import datetime, timezone
import re

from .client import AuthenticationError, RemoteUnknown
from .errors import Conflict, Refused
from .schema import repository_name, uuid_text, validate_payload

MARKER_PREFIX = 'project-hub:v1'
MARKER = re.compile(
    r'<!-- project-hub:v1:([0-9a-f-]{36}):(issue_create|issue_update):'
    r'([0-9]+):([0-9a-f]{64}) -->'
)


def marker(payload, action):
    data = payload.data
    return (f'<!-- {MARKER_PREFIX}:{data["request_id"]}:{action}:{data["revision"]}:'
            f'{payload.digest} -->')


def render_issue(payload):
    data = payload.data
    action = 'issue_create' if data['revision'] == 1 else 'issue_update'
    lines = ['## Summary', data['summary'], '', '## Acceptance']
    lines.extend(f'- {item}' for item in data['acceptance'])
    if data['kind'] == 'bug':
        lines.extend(['', '## Observed version', data['observed_version'], '',
                      '## Reproduction'])
        lines.extend(f'1. {item}' for item in data['reproduction'])
        lines.extend(['', '## Expected', data['expected'], '',
                      '## Actual', data['actual'], '',
                      '## Severity', data['severity']])
    lines.extend(['', '## Business decisions'])
    lines.extend(f'- {item}' for item in data['business_decisions'])
    if not data['business_decisions']:
        lines.append('None recorded.')
    lines.extend(['', '## Source', data['source']['conversation_ref'], '', '## Links'])
    for key in ('task_id', 'pr', 'release'):
        lines.append(f'- {key}: {data["links"][key] or "None"}')
    lines.extend(['', marker(payload, action)])
    return '\n'.join(lines)


class Bridge:
    def __init__(self, store, authorizer, client, allowed_repositories):
        self.store = store
        self.authorizer = authorizer
        self.client = client
        self.allowed_repositories = frozenset(allowed_repositories)
        for repository in self.allowed_repositories:
            repository_name(repository)

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _result(row):
        if row is None:
            raise AssertionError('operation must exist')
        return {'state': row['state'], 'action': row['action'], 'revision': row['revision'],
                'payload_digest': row['payload_digest'], 'issue_number': row['issue_number']}

    @staticmethod
    def _operation(db, data):
        return db.execute(
            'SELECT * FROM operations WHERE repository=? AND request_id=? AND revision=?',
            (data['repository'], data['request_id'], data['revision'])).fetchone()

    @staticmethod
    def _request(db, data):
        return db.execute('SELECT * FROM requests WHERE repository=? AND request_id=?',
                          (data['repository'], data['request_id'])).fetchone()

    def _recover(self, data):
        with self.store.transaction() as db:
            db.execute('''UPDATE operations SET state='unknown', updated_at=?
                          WHERE repository=? AND request_id=? AND state='dispatching' ''',
                       (self._now(), data['repository'], data['request_id']))

    def _reject(self, data):
        with self.store.transaction() as db:
            db.execute('''UPDATE operations SET state='rejected', updated_at=?
                          WHERE repository=? AND request_id=? AND revision=?
                          AND state IN ('pending', 'dispatching')''',
                       (self._now(), data['repository'], data['request_id'], data['revision']))

    def _unknown(self, data):
        with self.store.transaction() as db:
            db.execute('''UPDATE operations SET state='unknown', updated_at=?
                          WHERE repository=? AND request_id=? AND revision=? AND state='dispatching' ''',
                       (self._now(), data['repository'], data['request_id'], data['revision']))

    def _authorize_binding(self, grant_id, data, action, digest):
        now = datetime.now(timezone.utc)
        binding = getattr(self.authorizer, 'authorize_reconciliation', None)
        if binding is not None:
            return binding(grant_id, data['repository'], data['request_id'], action,
                           data['revision'], digest)
        return self.authorizer.authorize(grant_id, data['repository'], data['request_id'], action,
                                         data['revision'], digest, now)

    def submit(self, value, *, grant_id=None, dry_run=True):
        payload = validate_payload(value)
        data = payload.data
        digest = payload.digest
        action = 'issue_create' if data['revision'] == 1 else 'issue_update'
        if dry_run:
            return {'state': 'dry_run', 'action': action, 'revision': data['revision'],
                    'payload_digest': digest, 'issue_number': None}
        if not isinstance(grant_id, str):
            raise ValueError('execute requires one grant_id reference')
        uuid_text(grant_id)
        if data['repository'] not in self.allowed_repositories:
            raise Refused('repository is not allowlisted')

        with self.store.request_lock(data['repository'], data['request_id']):
            self._recover(data)
            with self.store.transaction() as db:
                request = self._request(db, data)
                operation = self._operation(db, data)
                if operation is not None:
                    if operation['payload_digest'] != digest or operation['action'] != action:
                        raise Conflict('revision payload conflicts with recorded operation')
                    if operation['state'] == 'rejected':
                        raise Refused('operation was rejected')
                    if operation['state'] == 'confirmed':
                        self._authorize_binding(grant_id, data, action, digest)
                        if request['confirmed_revision'] != data['revision']:
                            raise Conflict('stale revision')
                        return self._result(operation)
                else:
                    blocked = db.execute('''SELECT 1 FROM operations WHERE repository=? AND request_id=?
                                            AND state='unknown' LIMIT 1''',
                                         (data['repository'], data['request_id'])).fetchone()
                    if blocked is not None:
                        raise Conflict('an earlier operation has an unknown result')
                    confirmed = request['confirmed_revision'] if request is not None else 0
                    if data['revision'] != confirmed + 1:
                        raise Conflict('revisions must be consecutive and cannot be stale')
                    if action == 'issue_update' and (request is None or request['issue_number'] is None):
                        raise Conflict('an update requires a confirmed issue')
                    if request is None:
                        db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                                   (data['repository'], data['request_id'], None, 0, digest))
                    db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                               (data['repository'], data['request_id'], action, data['revision'],
                                digest, 'pending', request['issue_number'] if request else None,
                                self._now()))

            if operation is not None and operation['state'] == 'unknown':
                grant = self._authorize_binding(grant_id, data, action, digest)
                try:
                    principal = self.client.principal()
                except (AuthenticationError, RemoteUnknown):
                    return self._result(operation)
                if principal != grant.principal:
                    return self._result(operation)
                return self._reconcile(data, payload, operation)

            try:
                self.authorizer.authorize(grant_id, data['repository'], data['request_id'], action,
                                          data['revision'], digest, datetime.now(timezone.utc))
            except Refused:
                self._reject(data)
                raise

            with self.store.transaction() as db:
                changed = db.execute('''UPDATE operations SET state='dispatching', updated_at=?
                                        WHERE repository=? AND request_id=? AND revision=?
                                        AND state='pending' AND payload_digest=?''',
                                     (self._now(), data['repository'], data['request_id'],
                                      data['revision'], digest)).rowcount
                if changed != 1:
                    raise Conflict('operation changed before dispatch')

            try:
                principal = self.client.principal()
                grant = self.authorizer.authorize(
                    grant_id, data['repository'], data['request_id'], action,
                    data['revision'], digest, datetime.now(timezone.utc))
                if grant.principal != principal:
                    raise Refused('GitHub principal does not match grant')
            except AuthenticationError:
                self._reject(data)
                raise Refused('GitHub authentication/permission denied') from None
            except RemoteUnknown:
                self._unknown(data)
                return self._result(self._current_operation(data))
            except Refused:
                self._reject(data)
                raise

            try:
                row = self.client.write_issue(data['repository'],
                                              request['issue_number'] if request else None,
                                              data['title'], render_issue(payload))
                if not isinstance(row, dict) or type(row.get('number')) is not int or row['number'] < 1:
                    raise RemoteUnknown('GitHub write response invalid')
                if (request is not None and request['issue_number'] is not None
                        and row['number'] != request['issue_number']):
                    raise RemoteUnknown('GitHub write response issue mismatch')
            except AuthenticationError:
                self._reject(data)
                raise Refused('GitHub authentication/permission denied') from None
            except RemoteUnknown:
                self._unknown(data)
                return self._result(self._current_operation(data))
            except Refused:
                self._reject(data)
                raise
            return self._confirm(data, row['number'])

    def _current_operation(self, data):
        with self.store.connection('state') as db:
            return self._operation(db, data)

    def _confirm(self, data, issue_number):
        with self.store.transaction() as db:
            operation = self._operation(db, data)
            if operation['state'] not in {'dispatching', 'unknown'}:
                raise Conflict('operation cannot be confirmed from its current state')
            request = self._request(db, data)
            if request['issue_number'] is not None and request['issue_number'] != issue_number:
                raise Conflict('reconciled issue does not match confirmed issue')
            if data['revision'] != request['confirmed_revision'] + 1:
                raise Conflict('reconciled revision is not next')
            db.execute('''UPDATE operations SET state='confirmed', issue_number=?, updated_at=?
                          WHERE repository=? AND request_id=? AND revision=?''',
                       (issue_number, self._now(), data['repository'], data['request_id'],
                        data['revision']))
            db.execute('''UPDATE requests SET issue_number=?, confirmed_revision=?, payload_digest=?
                          WHERE repository=? AND request_id=?''',
                       (issue_number, data['revision'], operation['payload_digest'],
                        data['repository'], data['request_id']))
            return self._result(self._operation(db, data))

    def _reconcile(self, data, payload, operation):
        try:
            issues = list(self.client.list_issues(data['repository']))
        except AuthenticationError:
            raise Refused('GitHub authentication/permission denied') from None
        except RemoteUnknown:
            return self._result(operation)

        target_action = 'issue_create' if data['revision'] == 1 else 'issue_update'
        identity = []
        exact = []
        for issue in issues:
            if not isinstance(issue, dict) or type(issue.get('number')) is not int:
                raise Conflict('GitHub issue listing is invalid')
            body = issue.get('body')
            if not isinstance(body, str):
                continue
            for found in MARKER.finditer(body):
                request_id, action, revision_text, digest = found.groups()
                if (request_id, action) != (data['request_id'], target_action):
                    continue
                if len(revision_text) > 19 or revision_text[0] == '0':
                    raise Conflict('related remote marker has an invalid revision')
                revision = int(revision_text)
                if revision == data['revision']:
                    identity.append((issue['number'], digest))
                    if digest == payload.digest:
                        exact.append(issue['number'])
        if any(digest != payload.digest for _, digest in identity) or (exact and len(exact) != 1):
            raise Conflict('remote marker/digest is not unique or does not match')
        if not exact:
            return self._result(operation)
        request = self._request_for_reconcile(data)
        if target_action == 'issue_update' and (request is None or request['issue_number'] is None):
            raise Conflict('an update requires a confirmed issue')
        return self._confirm(data, exact[0])

    def _request_for_reconcile(self, data):
        with self.store.connection('state') as db:
            return self._request(db, data)
