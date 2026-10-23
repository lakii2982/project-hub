"""Closed, bounded business payload. No authority is accepted from this document."""
from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z')
REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z')
BASE = frozenset('schema_version request_id repository kind revision title summary acceptance source business_decisions links'.split())
BUG = frozenset('observed_version reproduction expected actual severity'.split())


def uuid_text(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('canonical UUID required')
    return value


def repository_name(value):
    if not isinstance(value, str) or not REPOSITORY.fullmatch(value) or value.split('/')[1] in {'.', '..'}:
        raise ValueError('repository must be owner/name')
    # GitHub names are case insensitive; one canonical spelling prevents alternate locks.
    if value != value.lower():
        raise ValueError('repository must be lowercase')
    return value


def text(value, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('bounded nonempty text required')
    return value


def strings(value, *, nonempty=False):
    if type(value) is not list or len(value) > 100 or (nonempty and not value):
        raise ValueError('bounded text list required')
    for item in value:
        text(item, 4000)


@dataclass(frozen=True)
class Payload:
    canonical: str

    @property
    def data(self):
        # Each caller gets an independent snapshot, so mutation cannot change the digest.
        return json.loads(self.canonical)

    @property
    def digest(self):
        return hashlib.sha256(self.canonical.encode('utf-8')).hexdigest()


def validate_payload(value):
    if type(value) is not dict or value.get('kind') not in {'requirement', 'bug'}:
        raise ValueError('business payload object required')
    if set(value) != BASE | (BUG if value['kind'] == 'bug' else frozenset()):
        raise ValueError('missing or unknown payload fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('schema_version must be 1')
    uuid_text(value['request_id'])
    repository_name(value['repository'])
    if type(value['revision']) is not int or not 1 <= value['revision'] <= 2**63-1:
        raise ValueError('revision must be a positive SQLite integer')
    text(value['title'], 200)
    text(value['summary'])
    strings(value['acceptance'], nonempty=True)
    strings(value['business_decisions'])
    source = value['source']
    if type(source) is not dict or set(source) != {'conversation_ref'} or not isinstance(source['conversation_ref'], str) or not REFERENCE.fullmatch(source['conversation_ref']):
        raise ValueError('source must contain a non-private conversation_ref identifier')
    links = value['links']
    if type(links) is not dict or set(links) != {'task_id', 'pr', 'release'}:
        raise ValueError('links must contain task_id/pr/release')
    for key, item in links.items():
        if item is None:
            continue
        text(item, 300)
        if key == 'task_id' and not re.fullmatch(r'PH-[0-9]{3,6}', item):
            raise ValueError('invalid task reference')
        if key == 'pr' and not re.fullmatch(r'https://github\.com/' + re.escape(value['repository']) + r'/pull/[1-9][0-9]*', item):
            raise ValueError('PR must belong to this repository')
        if key == 'release' and not re.fullmatch(r'https://github\.com/' + re.escape(value['repository']) + r'/releases/tag/[A-Za-z0-9._-]+', item):
            raise ValueError('release must belong to this repository')
    if value['kind'] == 'bug':
        for key in ('observed_version', 'expected', 'actual'):
            text(value[key])
        strings(value['reproduction'], nonempty=True)
        if value['severity'] not in {'S0', 'S1', 'S2', 'S3'}:
            raise ValueError('invalid severity')
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(canonical.encode('utf-8')) > 48000:
        raise ValueError('payload too large for issue body')
    return Payload(canonical)
