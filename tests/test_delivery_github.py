"""PH-101 counterexamples: no real gh, credentials, runtime or customer data."""
from __future__ import annotations

import copy
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from tooling.ai_delivery.github import Bridge, Conflict, Refused, validate_payload
from tooling.ai_delivery.github import client as github_client
from tooling.ai_delivery.github.authorizer import Authorizer
from tooling.ai_delivery.github.client import GhClient, AuthenticationError, RemoteUnknown
from tooling.ai_delivery.github.controller import TrustedGrantController
from tooling.ai_delivery.github.private import PrivateStore, production_store

REPO = 'demo-owner/demo-project'
PRINCIPAL = 'demo-controller'


def payload(revision=1):
    return dict(schema_version=1, request_id='ad9f4f64-6545-4b8b-852d-0c906cb68527',
                repository=REPO, kind='requirement', revision=revision, title='Demo requirement',
                summary='演练摘要', acceptance=['演练验收'],
                source={'conversation_ref': 'demo-instruction-1'}, business_decisions=[],
                links={'task_id': 'PH-101', 'pr': None, 'release': None})


class FakeClient:
    """File-backed remote lets separate real processes observe the same writes."""
    def __init__(self, root, *, mode='ok', principal=PRINCIPAL):
        self.root = Path(root)
        self.mode = mode
        self.login = principal
        self.reads = 0
        self.writes = 0

    def principal(self):
        self.reads += 1
        if self.mode == 'auth':
            raise AuthenticationError('denied')
        return self.login

    def list_issues(self, repository):
        self.reads += 1
        p = self.root / 'remote.json'
        return json.loads(p.read_text()) if p.exists() else []

    def write_issue(self, repository, issue_number, title, body):
        self.writes += 1
        # Prove dispatching is durable before the external effect, via another connection.
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            assert conn.execute("SELECT state FROM operations ORDER BY revision DESC").fetchone()[0] == 'dispatching'
        with (self.root / 'writes').open('a') as f:
            f.write(f'{issue_number or 0}\n')
            f.flush()
            os.fsync(f.fileno())
        if self.mode == 'crash_before':
            os._exit(71)
        if self.mode == 'reject':
            raise github_client.RemoteRejected('definite rejection')
        row = {'number': issue_number or 17, 'title': title, 'body': body,
               'user': {'login': self.login}}
        (self.root / 'remote.json').write_text(json.dumps([row]))
        revision = body.rsplit('<!-- project-hub:v1:', 1)[1].split(':')[2]
        with (self.root / 'remote_revisions').open('a') as f:
            f.write(f'{revision}\n')
            f.flush()
            os.fsync(f.fileno())
        if self.mode == 'crash_after':
            os._exit(72)
        if self.mode == 'lost':
            raise RemoteUnknown('response lost')
        if self.mode == 'slow':
            time.sleep(0.15)
        return row


def child_send(root, value, grant_id, mode, start=None):
    if start:
        start.wait(10)
    store = PrivateStore(Path(root) / 'private')
    bridge = Bridge(store, Authorizer(store), FakeClient(root, mode=mode), {REPO})
    bridge.submit(value, grant_id=grant_id, dry_run=False)


def child_crash_in_initial_authorization(root, value, grant_id):
    """PH-106 F1: dying inside the first authorization must leave no key behind."""
    store = PrivateStore(Path(root) / 'private')
    authorizer = Authorizer(store)
    authorizer.authorize = lambda *args: os._exit(73)
    Bridge(store, authorizer, FakeClient(root), {REPO}).submit(value, grant_id=grant_id, dry_run=False)


def child_crash_before_dispatch(root, value, grant_id):
    """Die after the pending row commits but before the durable dispatching marker."""
    store = PrivateStore(Path(root) / 'private')
    authorizer = Authorizer(store)
    original = authorizer.authorize
    checked = []
    def authorize(*args):
        if checked:
            os._exit(73)
        checked.append(1)
        return original(*args)
    authorizer.authorize = authorize
    Bridge(store, authorizer, FakeClient(root), {REPO}).submit(value, grant_id=grant_id, dry_run=False)


def child_send_gated(root, value, grant_id, release):
    store = PrivateStore(Path(root) / 'private')
    client = FakeClient(root)
    original = client.write_issue
    def gated_write(*args):
        row = original(*args)
        if not release.wait(10):
            raise RuntimeError('test release timed out')
        return row
    client.write_issue = gated_write
    Bridge(store, Authorizer(store), client, {REPO}).submit(value, grant_id=grant_id, dry_run=False)


class GithubBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ph101-demo-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = PrivateStore(self.root / 'private')
        self.controller = TrustedGrantController(self.store)
        self.client = FakeClient(self.root)
        self.bridge = Bridge(self.store, Authorizer(self.store), self.client, {REPO})

    def grant(self, value=None, **kw):
        value = value or payload()
        kwargs = dict(principal=PRINCIPAL, repository=REPO, request_id=value['request_id'],
                      actions=('issue_create', 'issue_update'), revision=value['revision'],
                      payload_digest=validate_payload(value).digest,
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                      source_ref='demo-user-instruction')
        kwargs.update(kw)
        return self.controller.record(**kwargs).id

    def send(self, value=None, grant_id=None):
        value = value or payload()
        return self.bridge.submit(value, grant_id=grant_id or self.grant(value), dry_run=False)

    def test_dry_run_is_default_and_does_not_network_or_persist_operations(self):
        result = self.bridge.submit(payload())
        self.assertEqual(result['state'], 'dry_run')
        self.assertEqual(self.client.reads + self.client.writes, 0)
        self.assertFalse((self.root / 'writes').exists())

    def test_payload_strict_unknown_and_authorization_fields_rejected(self):
        for name in ['actor', 'approval', 'grant', 'grant_id', 'labels', 'command', 'extra']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_payload(dict(payload(), **{name: {'principal': PRINCIPAL}}))
        for value in [True, 0, -1, '1']:
            with self.subTest(revision=value), self.assertRaises(ValueError):
                validate_payload(dict(payload(), revision=value))
        bad = [dict(payload(), repository='owner/name/../../evil'),
               dict(payload(), request_id='not-a-uuid'), dict(payload(), title=''),
               dict(payload(), schema_version=True), dict(payload(), acceptance='text'),
               dict(payload(), source={'conversation_ref': '/private/secret'}),
               dict(payload(), links={'shell': 'touch /tmp/pwn'}),
               dict(payload(), business_decisions=[{'approval': True}])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_payload(value)

    def test_bug_fields_are_required_and_requirement_cannot_smuggle_bug_fields(self):
        value = dict(payload(), kind='bug')
        with self.assertRaises(ValueError):
            validate_payload(value)
        value.update(observed_version='v0.1-demo', reproduction=['demo step'],
                     expected='demo expected', actual='demo actual', severity='S2')
        self.assertEqual(validate_payload(value).data['kind'], 'bug')
        with self.assertRaises(ValueError):
            validate_payload(dict(value, severity='S4'))
        with self.assertRaises(ValueError):
            validate_payload(dict(value, kind='requirement'))

    def test_same_revision_digest_conflict_stale_revision_and_gap_rejected(self):
        self.assertEqual(self.send()['state'], 'confirmed')
        self.assertEqual(self.send()['state'], 'confirmed')
        with self.assertRaises(Conflict):
            self.send(dict(payload(), summary='changed'))
        self.assertEqual(self.send(payload(2))['state'], 'confirmed')
        for value in [payload(), payload(4)]:
            with self.assertRaises(Conflict):
                self.send(value)
        self.assertEqual(self.client.writes, 2)

    def test_execute_needs_grant_reference_and_allowlisted_repository(self):
        for grant in [None, {}, {'principal': PRINCIPAL}, str(uuid4())]:
            with self.subTest(grant=grant), self.assertRaises((Refused, ValueError)):
                self.bridge.submit(payload(), grant_id=grant, dry_run=False)
        self.bridge.allowed_repositories = frozenset()
        with self.assertRaises(Refused):
            self.send()
        self.assertEqual(self.client.writes, 0)

    def test_unauthorized_initial_binding_allocates_no_key_and_makes_no_remote_call(self):
        # PH-106 F1: expired/revoked/digest-mismatched/random grants must not
        # consume the request/revision key nor touch the remote; a later valid
        # grant for the same request/revision must still succeed.
        value = payload()
        expired = self.grant(value, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        mismatched = self.grant(value, payload_digest='0' * 64)
        revoked = self.grant(value)
        self.controller.revoke(revoked)
        for index, grant_id in enumerate((expired, mismatched, revoked, str(uuid4()))):
            with self.subTest(grant=index), self.assertRaises(Refused):
                self.send(value, grant_id=grant_id)
        self.assertEqual(self.client.reads + self.client.writes, 0)
        self.assertFalse((self.root / 'writes').exists())
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            self.assertIsNone(conn.execute('SELECT * FROM requests').fetchone())
            self.assertIsNone(conn.execute('SELECT * FROM operations').fetchone())
        self.assertEqual(self.send(value, grant_id=self.grant(value))['state'], 'confirmed')
        self.assertEqual(self.client.reads, 1)
        self.assertEqual(self.client.writes, 1)

    def test_crash_inside_initial_authorization_consumes_no_key(self):
        ctx = multiprocessing.get_context('fork')
        gid = self.grant()
        child = ctx.Process(target=child_crash_in_initial_authorization,
                            args=(str(self.root), payload(), gid))
        child.start()
        child.join(15)
        self.assertEqual(child.exitcode, 73)
        self.assertFalse((self.root / 'writes').exists())
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            self.assertIsNone(conn.execute('SELECT * FROM operations').fetchone())
        bridge = Bridge(PrivateStore(self.root / 'private'), Authorizer(self.store), self.client, {REPO})
        self.assertEqual(bridge.submit(payload(), grant_id=gid, dry_run=False)['state'], 'confirmed')
        self.assertEqual((self.root / 'writes').read_text().splitlines(), ['0'])

    def test_grant_binding_expiry_revocation_principal_and_actions(self):
        changes = [dict(repository='other/repo'), dict(principal='other-user'),
                   dict(request_id=str(uuid4())), dict(revision=2),
                   dict(payload_digest='0' * 64), dict(actions=('issue_update',)),
                   dict(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))]
        for changeset in changes:
            value = dict(payload(), request_id=str(uuid4()))
            grant_id = self.grant(value, **changeset)
            with self.subTest(changes=changeset), self.assertRaises(Refused):
                self.send(value, grant_id=grant_id)
        value = dict(payload(), request_id=str(uuid4()))
        grant_id = self.grant(value)
        self.controller.revoke(grant_id)
        with self.assertRaises(Refused):
            self.send(value, grant_id=grant_id)
        self.assertEqual(self.client.writes, 0)

    def test_confirmed_replay_requires_existing_bound_grant(self):
        value = payload()
        self.send(value)
        wrong_request = dict(payload(), request_id=str(uuid4()))
        for grant_id in (str(uuid4()), self.grant(wrong_request)):
            with self.subTest(grant_id=grant_id), self.assertRaises(Refused):
                self.send(value, grant_id=grant_id)
        self.assertEqual(self.client.writes, 1)

    def test_grant_and_principal_rechecked_immediately_before_write(self):
        original = self.client.principal
        calls = []
        gid = self.grant()
        def revoke_on_identity():
            calls.append(1)
            self.controller.revoke(gid)
            return original()
        self.client.principal = revoke_on_identity
        with self.assertRaises(Refused):
            self.send(grant_id=gid)
        self.assertEqual(self.client.writes, 0)
        self.assertTrue(calls)

    def test_authentication_failure_does_not_retry(self):
        self.client.mode = 'auth'
        with self.assertRaises(Refused):
            self.send()
        self.assertEqual(self.client.reads, 1)
        self.assertEqual(self.client.writes, 0)

    def test_lost_response_reconciles_exact_marker_digest_and_duplicate_rejected(self):
        self.client.mode = 'lost'
        first = self.send()
        self.assertEqual(first['state'], 'unknown')
        self.client.mode = 'ok'
        self.assertEqual(self.send()['state'], 'confirmed')
        self.assertEqual(self.client.writes, 1)
        # Different request with two matching markers cannot claim unique success.
        value = dict(payload(), request_id=str(uuid4()))
        self.client.mode = 'lost'
        self.bridge.submit(value, grant_id=self.grant(value), dry_run=False)
        remote = json.loads((self.root / 'remote.json').read_text())
        remote.append(dict(remote[0], number=18))
        (self.root / 'remote.json').write_text(json.dumps(remote))
        with self.assertRaises(Conflict):
            self.send(value)
        with self.assertRaises(Conflict):
            self.send(dict(value, revision=2))

    def test_unknown_confirm_requires_exact_title_body_and_grant_author(self):
        # PH-106 F2: marker+digest identity alone is never enough; a candidate is
        # confirmed only with the exact rendered title, the full body and the grant
        # principal as creator. Anything else stays unknown and never writes again.
        value = payload()
        self.client.mode = 'lost'
        self.assertEqual(self.send(value)['state'], 'unknown')
        self.client.mode = 'ok'
        base = json.loads((self.root / 'remote.json').read_text())[0]
        marker_only = base['body'][base['body'].rfind('<!--'):]
        candidates = [
            dict(base, number=99, title='Copied marker on an unrelated title'),
            dict(base, number=99, body=marker_only),
            dict(base, number=99, user={'login': 'other-user'}),
            dict(base, number=99, body=base['body'].replace('## Summary', '## summary')),
            dict(base, number=99, title=base['title'] + ' (edited)'),
            {key: item for key, item in base.items() if key != 'user'},
        ]
        for candidate in candidates:
            (self.root / 'remote.json').write_text(json.dumps([candidate]))
            with self.subTest(missing='user' if 'user' not in candidate else None,
                              title=candidate['title']):
                self.assertEqual(self.send(value)['state'], 'unknown')
        self.assertEqual(self.client.writes, 1)
        (self.root / 'remote.json').write_text(json.dumps([base]))
        self.assertEqual(self.send(value)['state'], 'confirmed')
        self.assertEqual(self.client.writes, 1)

    def test_duplicate_fully_matching_remote_issues_reject_instead_of_guessing(self):
        value = dict(payload(), request_id=str(uuid4()))
        self.client.mode = 'lost'
        self.bridge.submit(value, grant_id=self.grant(value), dry_run=False)
        base = json.loads((self.root / 'remote.json').read_text())[0]
        (self.root / 'remote.json').write_text(json.dumps([base, dict(base, number=18)]))
        self.client.mode = 'ok'
        with self.assertRaises(Conflict):
            self.bridge.submit(value, grant_id=self.grant(value), dry_run=False)
        self.assertEqual(self.client.writes, 1)

    def test_update_reconcile_never_binds_a_different_issue_number(self):
        self.send()
        self.client.mode = 'lost'
        self.assertEqual(self.send(payload(2))['state'], 'unknown')
        self.client.mode = 'ok'
        base = json.loads((self.root / 'remote.json').read_text())[0]
        (self.root / 'remote.json').write_text(json.dumps([dict(base, number=99)]))
        with self.assertRaises(Conflict):
            self.send(payload(2))
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            row = conn.execute('SELECT issue_number, confirmed_revision FROM requests').fetchone()
        self.assertEqual(tuple(row), (17, 1))
        (self.root / 'remote.json').write_text(json.dumps([base]))
        self.assertEqual(self.send(payload(2))['state'], 'confirmed')
        self.assertEqual(self.send(payload(3))['state'], 'confirmed')

    def test_lost_update_reconciles_after_principal_changes_or_creator_is_deleted(self):
        self.send()
        # The fake principal method is intentionally replaced for this revision;
        # the stored remote creator remains the original principal.
        self.client.principal = lambda: 'operator-b'
        value = payload(2)
        grant = self.grant(value, principal='operator-b')
        self.client.mode = 'lost'
        self.assertEqual(self.send(value, grant_id=grant)['state'], 'unknown')
        remote = json.loads((self.root / 'remote.json').read_text())
        remote[0]['user'] = None
        (self.root / 'remote.json').write_text(json.dumps(remote))
        self.client.mode = 'ok'
        self.assertEqual(self.send(value, grant_id=grant)['state'], 'confirmed')
        self.assertEqual(self.client.writes, 2)

    def test_unknown_not_found_blocks_all_later_revisions_and_never_resends(self):
        self.client.mode = 'lost'
        self.assertEqual(self.send()['state'], 'unknown')
        (self.root / 'remote.json').write_text('[]')
        self.assertEqual(self.send()['state'], 'unknown')
        with self.assertRaises(Conflict):
            self.send(payload(2))
        self.assertEqual(self.client.writes, 1)

    def test_marker_mismatch_digest_and_partial_marker_never_confirm(self):
        self.client.mode = 'lost'
        self.send()
        remote = json.loads((self.root / 'remote.json').read_text())
        remote[0]['body'] = remote[0]['body'].replace(validate_payload(payload()).digest, '0' * 64)
        (self.root / 'remote.json').write_text(json.dumps(remote))
        with self.assertRaises(Conflict):
            self.send()
        self.assertEqual(self.client.writes, 1)

    def test_update_unknown_must_reconcile_before_next_revision(self):
        self.send()
        self.client.mode = 'lost'
        self.assertEqual(self.send(payload(2))['state'], 'unknown')
        with self.assertRaises(Conflict):
            self.send(payload(3))
        self.client.mode = 'ok'
        self.assertEqual(self.send(payload(2))['state'], 'confirmed')
        self.assertEqual(self.send(payload(3))['state'], 'confirmed')
        self.assertEqual((self.root / 'writes').read_text().splitlines(), ['0', '17', '17'])

    def test_production_store_uses_trusted_uid_home_not_process_home(self):
        trusted = self.root / 'trusted-uid-home'
        expected = trusted / '.project-hub-github'
        with (patch('tooling.ai_delivery.github.private.PrivateStore') as store,
              patch('tooling.ai_delivery.github.private.Path.home',
                    side_effect=[self.root / 'home-a', self.root / 'home-b']),
              patch('tooling.ai_delivery.github.private.pwd',
                    unittest.mock.Mock(getpwuid=unittest.mock.Mock(
                        return_value=unittest.mock.Mock(pw_dir=str(trusted)))), create=True)):
            production_store()
            production_store()
        self.assertEqual([call.args[0] for call in store.call_args_list], [expected, expected])

    def test_unknown_reconcile_checks_principal_before_pagination(self):
        value = payload()
        grant_id = self.grant(value)
        self.client.mode = 'lost'
        self.assertEqual(self.bridge.submit(value, grant_id=grant_id, dry_run=False)['state'], 'unknown')
        self.controller.revoke(grant_id)
        pages = []
        original = self.client.list_issues
        def list_issues(repository):
            pages.append(repository)
            return original(repository)
        self.client.list_issues = list_issues
        self.client.mode = 'ok'
        self.client.login = 'wrong-principal'
        self.assertEqual(self.bridge.submit(value, grant_id=grant_id, dry_run=False)['state'], 'unknown')
        self.client.login = PRINCIPAL
        self.client.mode = 'auth'
        self.assertEqual(self.bridge.submit(value, grant_id=grant_id, dry_run=False)['state'], 'unknown')
        self.assertEqual(pages, [])
        self.client.mode = 'ok'
        self.assertEqual(self.bridge.submit(value, grant_id=grant_id, dry_run=False)['state'], 'confirmed')
        self.assertEqual(pages, [REPO])
        self.assertEqual(self.client.writes, 1)

    def test_crash_recovery_commits_unknown_before_conflicts(self):
        ctx = multiprocessing.get_context('fork')
        for change in ('later_revision', 'changed_payload'):
            value = dict(payload(), request_id=str(uuid4()))
            grant_id = self.grant(value)
            child = ctx.Process(target=child_send, args=(str(self.root), value, grant_id, 'crash_before'))
            child.start()
            child.join(15)
            self.assertEqual(child.exitcode, 71)
            bridge = Bridge(PrivateStore(self.root / 'private'), Authorizer(self.store), self.client, {REPO})
            changed = dict(value, revision=2) if change == 'later_revision' else dict(value, summary='changed')
            with self.subTest(change=change), self.assertRaises(Conflict):
                bridge.submit(changed, grant_id=self.grant(changed), dry_run=False)
            with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
                state = conn.execute('SELECT state FROM operations WHERE request_id=?',
                                     (value['request_id'],)).fetchone()[0]
            self.assertEqual(state, 'unknown')

    def test_definite_remote_rejection_is_rejected_and_not_retried(self):
        grant_id = self.grant()
        self.client.mode = 'reject'
        with self.assertRaises(Refused):
            self.send(grant_id=grant_id)
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT state FROM operations').fetchone()[0], 'rejected')
        with self.assertRaises(Refused):
            self.send(grant_id=grant_id)
        self.assertEqual(self.client.writes, 1)

    def test_unrelated_long_marker_ignored_and_related_marker_controlled(self):
        value = payload()
        grant_id = self.grant(value)
        self.client.mode = 'lost'
        self.bridge.submit(value, grant_id=grant_id, dry_run=False)
        unrelated = f'<!-- project-hub:v1:{uuid4()}:issue_create:{"9" * 5000}:{"0" * 64} -->'
        (self.root / 'remote.json').write_text(json.dumps([{'number': 99, 'body': unrelated}]))
        self.client.mode = 'ok'
        self.assertEqual(self.bridge.submit(value, grant_id=grant_id, dry_run=False)['state'], 'unknown')
        related = f'<!-- project-hub:v1:{value["request_id"]}:issue_create:{"9" * 5000}:{"0" * 64} -->'
        (self.root / 'remote.json').write_text(json.dumps([{'number': 99, 'body': related}]))
        with self.assertRaises(Conflict):
            self.bridge.submit(value, grant_id=grant_id, dry_run=False)
        self.assertEqual(self.client.writes, 1)

    def test_multiprocess_revision_two_three_remote_order(self):
        self.send()
        revision_two = payload(2)
        revision_three = payload(3)
        grant_two = self.grant(revision_two)
        grant_three = self.grant(revision_three)
        with self.assertRaises(Conflict):
            self.send(revision_three, grant_id=grant_three)
        ctx = multiprocessing.get_context('fork')
        release = ctx.Event()
        self.addCleanup(release.set)
        child_two = ctx.Process(target=child_send_gated,
                                args=(str(self.root), revision_two, grant_two, release))
        child_two.start()
        deadline = time.monotonic() + 10
        revisions = []
        while time.monotonic() < deadline:
            path = self.root / 'remote_revisions'
            revisions = path.read_text().splitlines() if path.exists() else []
            if revisions == ['1', '2']:
                break
            time.sleep(0.01)
        self.assertEqual(revisions, ['1', '2'])
        child_three = ctx.Process(target=child_send,
                                  args=(str(self.root), revision_three, grant_three, 'ok'))
        child_three.start()
        time.sleep(0.1)
        self.assertEqual((self.root / 'remote_revisions').read_text().splitlines(), ['1', '2'])
        release.set()
        for child in (child_two, child_three):
            child.join(15)
            self.assertFalse(child.is_alive())
            self.assertEqual(child.exitcode, 0)
        self.assertEqual((self.root / 'remote_revisions').read_text().splitlines(), ['1', '2', '3'])

    def test_private_modes_owner_symlink_and_auxiliary_files(self):
        self.grant()
        self.send()
        for item in (self.root / 'private').iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o700 if item.is_dir() else 0o600)
        os.chmod(self.root / 'private' / 'state.sqlite3', 0o644)
        with self.assertRaises(Refused):
            PrivateStore(self.root / 'private')
        os.chmod(self.root / 'private' / 'state.sqlite3', 0o600)
        aux = self.root / 'private' / 'state.sqlite3-wal'
        aux.write_bytes(b'')
        os.chmod(aux, 0o644)
        with self.assertRaises(Refused):
            PrivateStore(self.root / 'private')
        aux.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(self.root / 'private', target_is_directory=True)
        with self.assertRaises(Refused):
            PrivateStore(alias)
        with patch('tooling.ai_delivery.github.private.os.getuid', return_value=os.getuid()+1):
            with self.assertRaises(Refused):
                PrivateStore(self.root / 'private')

    def test_multiprocess_simultaneous_duplicate_only_one_create(self):
        ctx = multiprocessing.get_context('fork')
        event = ctx.Event()
        gid = self.grant()
        children = [ctx.Process(target=child_send, args=(str(self.root), payload(), gid, 'slow', event)) for _ in range(3)]
        for child in children:
            child.start()
        event.set()
        for child in children:
            child.join(15)
            self.assertFalse(child.is_alive())
            self.assertEqual(child.exitcode, 0)
        self.assertEqual((self.root / 'writes').read_text().splitlines(), ['0'])

    def test_pending_create_recovers_before_first_dispatch(self):
        ctx = multiprocessing.get_context('fork')
        gid = self.grant()
        child = ctx.Process(target=child_crash_before_dispatch, args=(str(self.root), payload(), gid))
        child.start()
        child.join(15)
        self.assertEqual(child.exitcode, 73)
        with sqlite3.connect(self.root / 'private' / 'state.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT state FROM operations').fetchone()[0], 'pending')
        self.assertFalse((self.root / 'writes').exists())
        bridge = Bridge(PrivateStore(self.root / 'private'), Authorizer(self.store), self.client, {REPO})
        self.assertEqual(bridge.submit(payload(), grant_id=gid, dry_run=False)['state'], 'confirmed')
        self.assertEqual(bridge.submit(payload(), grant_id=gid, dry_run=False)['state'], 'confirmed')
        self.assertEqual((self.root / 'writes').read_text().splitlines(), ['0'])

    def test_real_process_crash_before_and_after_remote_effect(self):
        ctx = multiprocessing.get_context('fork')
        for mode, expected in [('crash_before', 'unknown'), ('crash_after', 'confirmed')]:
            value = dict(payload(), request_id=str(uuid4()))
            gid = self.grant(value)
            child = ctx.Process(target=child_send, args=(str(self.root), value, gid, mode))
            child.start()
            child.join(15)
            self.assertEqual(child.exitcode, 71 if mode == 'crash_before' else 72)
            bridge = Bridge(PrivateStore(self.root / 'private'), Authorizer(self.store), self.client, {REPO})
            self.assertEqual(bridge.submit(value, grant_id=gid, dry_run=False)['state'], expected)
            self.assertEqual(bridge.submit(value, grant_id=gid, dry_run=False)['state'], expected)
        self.assertEqual(len((self.root / 'writes').read_text().splitlines()), 2)


class GhClientTests(unittest.TestCase):
    def test_fixed_argv_json_stdin_no_shell_and_untrusted_text_is_data(self):
        runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"number":17}', ''))
        client = GhClient('/trusted/bin/gh', runner=runner)
        title = '--help $(touch /tmp/NEVER)'
        client.write_issue(REPO, None, title, 'external label; `echo NEVER`')
        args, kw = runner.call_args
        self.assertEqual(args[0], ['/trusted/bin/gh', 'api', '--hostname', 'github.com', '--method', 'POST',
                                   'repos/demo-owner/demo-project/issues', '--input', '-'])
        self.assertFalse(kw['shell'])
        self.assertEqual(json.loads(kw['input'])['title'], title)
        self.assertNotIn(title, args[0])

    def test_pagination_not_search_and_read_network_retries_only_once(self):
        pages = [[{'number': i, 'body': 'unrelated'} for i in range(1,101)], [{'number': 101, 'body': 'match'}]]
        runner = unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired('gh', 1)] +
                                    [subprocess.CompletedProcess([], 0, json.dumps(p), '') for p in pages])
        client = GhClient('/trusted/bin/gh', runner=runner)
        self.assertEqual(len(list(client.list_issues(REPO))), 101)
        self.assertEqual(runner.call_count, 3)
        endpoints = [call.args[0][6] for call in runner.call_args_list]
        self.assertIn('page=2', endpoints[-1])
        self.assertTrue(all('state=all' in endpoint and '/search/' not in endpoint for endpoint in endpoints))
        runner.side_effect = subprocess.TimeoutExpired('gh', 1)
        with self.assertRaises(RemoteUnknown):
            client.write_issue(REPO, None, 'title', 'body')
        self.assertEqual(runner.call_count, 4)

    def test_authentication_never_retried_or_returned_raw_stderr(self):
        runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 1, '', 'HTTP 401 secret-demo-token'))
        client = GhClient('/trusted/bin/gh', runner=runner)
        with self.assertRaises(AuthenticationError) as error:
            client.principal()
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn('secret-demo-token', str(error.exception))

    def test_definite_http_rejections_are_redacted_and_not_retried(self):
        runner = unittest.mock.Mock()
        client = GhClient('/trusted/bin/gh', runner=runner)
        for status in (404, 422):
            runner.reset_mock()
            runner.return_value = subprocess.CompletedProcess([], 1, '', f'HTTP {status} secret-demo-token')
            with self.subTest(status=status):
                with self.assertRaises(Refused) as error:
                    client.write_issue(REPO, None, 'title', 'body')
                self.assertEqual(type(error.exception).__name__, 'RemoteRejected')
                self.assertNotIn('secret-demo-token', str(error.exception))
                self.assertEqual(runner.call_count, 1)
