"""TRUSTED controller-only grant registration, intentionally no CLI/worker tool.

The caller must first read the actual user's instruction. source_ref records
that decision; it does not cryptographically prove human conversational consent.
Do not expose this module as an Issue, MCP, worker or payload-driven endpoint.
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

from .authorizer import Grant
from .schema import uuid_text


class TrustedGrantController:
    def __init__(self, store):
        self.store = store

    def record(self, *, principal, repository, request_id, actions, revision,
               payload_digest, expires_at, source_ref):
        grant = Grant(str(uuid4()), principal, repository, request_id, tuple(actions), revision,
                      payload_digest, expires_at, None, source_ref)
        with self.store.transaction('grants') as db:
            db.execute('INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (grant.id, grant.principal, grant.repository, grant.request_id,
                        json.dumps(grant.actions), grant.revision, grant.payload_digest,
                        grant.expires_at.isoformat(), None, grant.source_ref))
        return grant

    def revoke(self, grant_id):
        uuid_text(grant_id)
        with self.store.transaction('grants') as db:
            # Revocation is monotonic and does not undo any previously sent write.
            db.execute('UPDATE grants SET revoked_at=COALESCE(revoked_at, ?) WHERE id=?',
                       (datetime.now(timezone.utc).isoformat(), grant_id))
