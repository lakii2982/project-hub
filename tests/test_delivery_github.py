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
from tooling.ai_delivery.github.authorizer import Authorizer
from tooling.ai_delivery.github.client import GhClient, AuthenticationError, RemoteUnknown
from tooling.ai_delivery.github.controller import TrustedGrantController
from tooling.ai_delivery.github.private import PrivateStore

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
        row = {'number': issue_number or 17, 'title': title, 'body': body}
        (self.root / 'remote.json').write_text(json.dumps([row]))
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


def child_crash_before_dispatch(root, value, grant_id):
    store = PrivateStore(Path(root) / 'private')
    authorizer = Authorizer(store)
    authorizer.authorize = lambda *args: os._exit(73)
    bridge = Bridge(store, authorizer, FakeClient(root), {REPO})
    bridge.submit(value, grant_id=grant_id, dry_run=False)


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
