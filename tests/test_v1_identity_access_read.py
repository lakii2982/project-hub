"""PH-281 read-only membership / project-scope / authorization audit view (service)."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core import identity_read_services as identity
from core.models import (AgentToken, AuditEvent, DomainEvent, Employee, Membership,
                         Project, ProjectMember)
from core.services import audit
from tests.test_v1_contracts import Fixture


class IdentityFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.plain = self.member('member')
        self.project2 = Project.objects.create(
            organization=self.org, code='P2', name='财务项目', client='客户',
            manager=self.finance, start_date=self.today, end_date=self.today)
        ProjectMember.objects.create(project=self.project, member=self.pm)
        ProjectMember.objects.create(project=self.project, member=self.plain)
        ProjectMember.objects.create(project=self.project2, member=self.plain)
        self.token_active = self.token(self.owner, 'owner-active', 'active')
        self.token_revoked = self.token(self.owner, 'owner-revoked', 'revoked')
        self.token_expired = self.token(self.owner, 'owner-expired', 'expired', target=self.plain)

    def token(self, member, name, state='active', *, target=None, days=30):
        target = target or member
        prefix = uuid.uuid4().hex[:14]
        return AgentToken.objects.create(
            member=target, name=name, prefix=prefix,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            auth_snapshot=uuid.uuid4().hex + uuid.uuid4().hex,
            scopes=['project:read_snapshot'], project_ids=[],
            expires_at=(timezone.now() - timedelta(days=1) if state == 'expired'
                        else timezone.now() + timedelta(days=days)),
            revoked_at=(timezone.now() if state == 'revoked' else None))

    def change_audit(self, target, payload=None, *, actor=None):
        return audit(actor or self.owner, 'identity.membership.changed', target,
                     payload or {'changed_fields': ['role'], 'role': target.role})

    def counts(self):
        return tuple(model.objects.count() for model in
                     (Membership, ProjectMember, AgentToken, AuditEvent, DomainEvent, Employee))


class IdentityReadServiceTests(IdentityFixture, TestCase):
    def test_owner_snapshot_derived_values_and_token_stats(self):
        self.change_audit(self.pm)
        Membership.objects.filter(pk=self.pm.pk).update(version=2)
        before = self.counts()
        snapshot = identity.member_access_snapshot(self.owner)
        self.assertEqual(before, self.counts())
        self.assertEqual(snapshot['scope'], 'org')
        self.assertEqual(snapshot['total'], 5)
        rows = {row['member_ref']: row for row in snapshot['members']}
        owner_row = rows[str(self.owner.pk)]
        self.assertEqual(owner_row['tokens']['total'], 2)
        self.assertEqual(owner_row['tokens']['active'], 1)
        self.assertEqual(owner_row['tokens']['revoked'], 1)
        self.assertEqual(owner_row['tokens']['expired'], 0)
        plain_row = rows[str(self.plain.pk)]
        self.assertEqual(plain_row['tokens']['active'], 0)
        self.assertEqual(plain_row['tokens']['expired'], 1)
        self.assertEqual(len(plain_row['project_scope']), 2)
        self.assertEqual(plain_row['role'], 'member')
        self.assertNotIn('token_hash', plain_row)
        self.assertNotIn('prefix', plain_row)
        pm_row = rows[str(self.pm.pk)]
        self.assertIsNotNone(pm_row['last_changed_at'])
        self.assertEqual(pm_row['last_changed_action'], 'identity.membership.changed')

    def test_pm_scope_returns_only_managed_project_members_without_token_details(self):
        snapshot = identity.member_access_snapshot(self.pm)
        self.assertEqual(snapshot['scope'], 'project')
        refs = {row['member_ref'] for row in snapshot['members']}
        self.assertIn(str(self.pm.pk), refs)
        self.assertIn(str(self.plain.pk), refs)
        self.assertNotIn(str(self.finance.pk), refs)
        self.assertNotIn(str(self.auditor.pk), refs)
        for row in snapshot['members']:
            self.assertIsNone(row['tokens'])
            self.assertIsNone(row['last_changed_at'])
            self.assertTrue(all(scope['project_id'] == str(self.project.pk)
                                for scope in row['project_scope']))

    def test_permissions(self):
        for member in (self.owner, self.finance, self.auditor):
            self.assertEqual(identity.member_access_snapshot(member)['scope'], 'org')
        self.assertEqual(identity.member_access_snapshot(self.pm)['scope'], 'project')
        with self.assertRaises(PermissionDenied):
            identity.member_access_snapshot(self.plain)
        with self.assertRaises(PermissionDenied):
            identity.member_access_snapshot(None)
        with self.assertRaises(PermissionDenied):
            identity.member_access_audit(self.plain)

    def test_issue_codes(self):
        self.token(self.plain, 'plain-active-before-disable')
        Membership.objects.filter(pk=self.plain.pk).update(active=False)
        self.token(self.finance, 'finance-scope-mismatch')  # finance must not hold project scope
        ProjectMember.objects.create(project=self.project2, member=self.finance)
        other_org = Project.objects.create(organization=self.org, code='X1', name='错位',
                                           client='客户', manager=self.finance,
                                           start_date=self.today, end_date=self.today)
        ProjectMember.objects.create(project=other_org, member=self.plain)
        Membership.objects.filter(pk=self.auditor.pk).update(version=2)
        result = identity.member_access_issues(self.owner)
        codes = {issue['code'] for issue in result['issues']}
        self.assertIn('inactive_member_active_token', codes)
        self.assertIn('expired_token_not_revoked', codes)
        self.assertIn('role_scope_mismatch', codes)
        self.assertIn('audit_gap', codes)
        self.assertEqual(result['issue_count'], len(result['issues']))

    def test_audit_summary_filters_and_no_secrets(self):
        self.change_audit(self.pm)
        token = self.token(self.pm, 'pm-token')
        audit(self.owner, 'agent_token.created', token, {'name': token.name})
        result = identity.member_access_audit(self.owner, member_id=self.pm.pk)
        actions = {event['action'] for event in result['events']}
        self.assertIn('identity.membership.changed', actions)
        self.assertIn('agent_token.created', actions)
        for event in result['events']:
            self.assertNotIn('token_hash', event['summary'])
            self.assertNotIn('auth_snapshot', event['summary'])
        own = identity.member_access_audit(self.owner, member_id=self.finance.pk)
        self.assertEqual(own['events'], [])
        with self.assertRaises(PermissionDenied):
            identity.member_access_audit(self.plain, member_id=self.pm.pk)

    def test_pm_audit_excludes_other_project_member_events(self):
        self.change_audit(self.plain)
        self.change_audit(self.finance)
        finance_token = self.token(self.finance, 'finance-token')
        audit(self.owner, 'agent_token.created', finance_token, {'name': finance_token.name})
        pm_result = identity.member_access_audit(self.pm)
        actions_entities = [(event['action'], event['entity_id']) for event in pm_result['events']]
        self.assertIn(('identity.membership.changed', str(self.plain.pk)), actions_entities)
        self.assertNotIn(('identity.membership.changed', str(self.finance.pk)), actions_entities)
        self.assertFalse([event for event in pm_result['events']
                          if event['entity_id'] == str(finance_token.pk)])
        owner_actions = {(event['action'], event['entity_id'])
                         for event in identity.member_access_audit(self.owner)['events']}
        self.assertIn(('identity.membership.changed', str(self.finance.pk)), owner_actions)
        with self.assertRaises(PermissionDenied):
            identity.member_access_audit(self.pm, member_id=self.finance.pk)

    def test_pagination_and_validation(self):
        page = identity.member_access_snapshot(self.owner, page=1, page_size=2)
        self.assertEqual(len(page['members']), 2)
        self.assertTrue(page['truncated'])
        page2 = identity.member_access_snapshot(self.owner, page=2, page_size=2)
        self.assertEqual(len(page2['members']), 2)
        with self.assertRaises(ValidationError):
            identity.member_access_snapshot(self.owner, page=0)
        with self.assertRaises(ValidationError):
            identity.member_access_snapshot(self.owner, page_size=201)
        future = (self.today + timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError):
            identity.member_access_snapshot(self.owner, as_of=future)
        with self.assertRaises(ValidationError):
            identity.member_access_audit(self.owner, since='bad')

    def test_read_only_purity_no_write_sql(self):
        self.change_audit(self.pm)
        before = self.counts()
        with CaptureQueriesContext(connection) as captured:
            identity.member_access_snapshot(self.owner)
            identity.member_access_audit(self.owner)
            identity.member_access_issues(self.owner)
        statements = [query['sql'].lstrip().upper() for query in captured.captured_queries]
        self.assertFalse([sql for sql in statements
                          if sql.startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(before, self.counts())
