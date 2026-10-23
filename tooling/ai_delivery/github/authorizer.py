"""Read-only authorization; accepts a grant UUID, never a supplied Grant/dict."""
from dataclasses import dataclass
from datetime import datetime
import json
import re

from .errors import Refused
from .schema import REFERENCE, repository_name, uuid_text

ACTIONS = frozenset({'issue_create', 'issue_update'})


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise Refused('timezone-aware UTC timestamp required')
    return value


@dataclass(frozen=True)
class Grant:
    id: str
    principal: str
    repository: str
    request_id: str
    actions: tuple[str, ...]
    revision: int
    payload_digest: str
    expires_at: datetime
    revoked_at: datetime | None
    source_ref: str

    def __post_init__(self):
        uuid_text(self.id)
        uuid_text(self.request_id)
        repository_name(self.repository)
        if not isinstance(self.principal, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', self.principal):
            raise Refused('GitHub login required')
        if type(self.actions) is not tuple or not self.actions or not set(self.actions) <= ACTIONS or len(set(self.actions)) != len(self.actions):
            raise Refused('invalid grant actions')
        if type(self.revision) is not int or not 1 <= self.revision <= 2**63-1:
            raise Refused('invalid grant revision')
        if not isinstance(self.payload_digest, str) or not re.fullmatch('[0-9a-f]{64}', self.payload_digest):
            raise Refused('invalid payload digest')
        utc(self.expires_at)
        if self.revoked_at is not None:
            utc(self.revoked_at)
        if not isinstance(self.source_ref, str) or not REFERENCE.fullmatch(self.source_ref):
            raise Refused('trusted instruction reference required')


class Authorizer:
    def __init__(self, store):
        self.store = store

    def _grant(self, grant_id, repository, request_id, action, revision, payload_digest):
        uuid_text(grant_id)
        with self.store.connection('grants') as db:
            row = db.execute('SELECT * FROM grants WHERE id=?', (grant_id,)).fetchone()
        if row is None:
            raise Refused('grant not found')
        value = dict(row)
        value['actions'] = tuple(json.loads(value['actions']))
        value['expires_at'] = datetime.fromisoformat(value['expires_at'])
        value['revoked_at'] = datetime.fromisoformat(value['revoked_at']) if value['revoked_at'] else None
        grant = Grant(**value)
        if ((grant.repository, grant.request_id, grant.revision, grant.payload_digest) !=
                (repository, request_id, revision, payload_digest) or action not in grant.actions):
            raise Refused('grant binding mismatch')
        return grant

    def authorize(self, grant_id, repository, request_id, action, revision, payload_digest, now):
        utc(now)
        grant = self._grant(grant_id, repository, request_id, action, revision, payload_digest)
        if grant.revoked_at is not None or grant.expires_at <= now:
            raise Refused('grant expired or revoked')
        return grant

    def authorize_reconciliation(self, grant_id, repository, request_id, action, revision, payload_digest):
        return self._grant(grant_id, repository, request_id, action, revision, payload_digest)
