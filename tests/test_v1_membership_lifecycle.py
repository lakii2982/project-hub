"""PH-264 membership lifecycle: role/scope changes, deactivation, restore and token revocation."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core import masterdata
from core.models import (AgentToken, AuditEvent, Employee, Membership, Organization,
                         Project, ProjectMember)
from tests.test_v1_contracts import Fixture


class MembershipFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member("finance")
        self.auditor = self.member("auditor")
        self.plain = self.member("member")
        self.second_project = Project.objects.create(
            organization=self.org, code="P2", name="第二项目", client="示例客户",
            manager=self.pm, start_date=self.today, end_date=self.today)

    def change(self, target, *, role=None, project_scope=None, active=None,
               expected_version=None, token=None, dry_run=False, member=None):
        return masterdata.change_membership(
            member or self.owner, membership_id=target.pk,
            role=role or target.role,
            project_scope=[] if project_scope is None else project_scope,
            active=active, expected_version=expected_version,
            request_token=token or uuid.uuid4(), dry_run=dry_run)

    def scope(self, target):
        return sorted(str(project_id) for project_id in ProjectMember.objects.filter(
            member=target).values_list("project_id", flat=True))

    def token(self, target, name="接入"):
        return AgentToken.objects.create(
            member=target, name=name, prefix="ph_test", token_hash=uuid.uuid4().hex,
            auth_snapshot="x", scopes=["project:read_snapshot"],
            project_ids=[str(self.project.pk)],
            expires_at=timezone.now() + timedelta(days=30))

    def facts(self):
        return (Membership.objects.count(), ProjectMember.objects.count(),
                AgentToken.objects.count(), Employee.objects.count(),
                AuditEvent.objects.count())

    def active_owner_count(self):
        return Membership.objects.filter(organization=self.org, role="owner",
                                         active=True).count()


class MembershipLifecycleTests(MembershipFixture, TestCase):
    def test_role_change_and_scope_clear(self):
        before_version = self.pm.version
        result = self.change(self.pm, role="pm", project_scope=[self.project.pk])
        self.pm.refresh_from_db()
        self.assertEqual(result["role"], "pm")
        self.assertEqual(result["project_scope_ids"], [str(self.project.pk)])
        self.assertEqual(self.pm.version, before_version + 1)
        self.assertEqual(self.scope(self.pm), [str(self.project.pk)])
        # company-level role requires an empty scope: explicit clear succeeds
        result = self.change(self.pm, role="finance", project_scope=[])
        self.assertEqual(result["project_scope_ids"], [])
        self.assertEqual(self.scope(self.pm), [])
        audit = AuditEvent.objects.filter(action="identity.membership.changed").latest("id")
        self.assertEqual(audit.payload["role"], "finance")
        self.assertEqual(audit.payload["active"], True)
        self.assertNotIn("password", str(audit.payload))
        self.assertNotIn("token_hash", str(audit.payload))

    def test_project_scope_diff_and_role_rules(self):
        self.change(self.pm, role="pm", project_scope=[self.project.pk, self.second_project.pk])
        self.assertEqual(self.scope(self.pm), sorted([str(self.project.pk),
                                                      str(self.second_project.pk)]))
        self.change(self.pm, role="pm", project_scope=[self.second_project.pk])
        self.assertEqual(self.scope(self.pm), [str(self.second_project.pk)])
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="pm",
                        project_scope=[self.project.pk, self.project.pk])
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="owner", project_scope=[self.project.pk])
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="finance", project_scope=[self.project.pk])
        foreign_org = Organization.objects.create(name="其他公司")
        foreign_project = Project.objects.create(
            organization=foreign_org, code="F1", name="外部项目", client="外部",
            manager=self.owner, start_date=self.today, end_date=self.today)
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="pm", project_scope=[foreign_project.pk])

    def test_deactivation_requires_employee_and_manager_handoff(self):
        employee = masterdata.create_employee(self.owner, {
            "name": "成员档案", "capacity_hours": 8, "active": True, "member": self.plain})
        with self.assertRaises(ValidationError):
            self.change(self.plain, role="member", active=False)
        masterdata.update_employee(self.owner, employee.pk, {"active": False})
        project = Project.objects.create(
            organization=self.org, code="P3", name="负责项目", client="示例客户",
            manager=self.plain, start_date=self.today, end_date=self.today)
        with self.assertRaises(ValidationError):
            self.change(self.plain, role="member", active=False)
        project.manager = self.pm
        project.save(update_fields=["manager"])
        token = self.token(self.plain)
        ProjectMember.objects.create(project=self.project, member=self.plain)
        result = self.change(self.plain, role="member", active=False)
        self.plain.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual((result["active"], self.plain.active), (False, False))
        self.assertIsNotNone(token.revoked_at)
        self.assertEqual(self.scope(self.plain), [])
        with self.assertRaises(ValidationError):
            self.change(self.plain, role="member")
        with self.assertRaises(PermissionDenied):
            masterdata.membership_snapshot(self.plain, self.owner.pk)

    def test_restore_does_not_restore_scope_or_tokens(self):
        token = self.token(self.plain)
        self.change(self.plain, role="member", active=False)
        self.change(self.plain, role="member", active=True, project_scope=[])
        self.plain.refresh_from_db()
        token.refresh_from_db()
        self.assertEqual((self.plain.active, self.plain.version), (True, 3))
        self.assertIsNotNone(token.revoked_at)
        self.assertEqual(self.scope(self.plain), [])

    def test_last_owner_and_self_rules(self):
        with self.assertRaises(ValidationError):
            self.change(self.owner, role="member", project_scope=[])
        with self.assertRaises(ValidationError):
            self.change(self.owner, role="owner", active=False)
        self.assertEqual(self.active_owner_count(), 1)

    def test_non_owner_cannot_write(self):
        for member in (self.finance, self.pm, self.plain, self.auditor):
            with self.assertRaises(PermissionDenied):
                self.change(self.plain, role="member", member=member)

    def test_expected_version_and_replay(self):
        token = uuid.uuid4()
        first = self.change(self.pm, role="finance", token=token)
        replay = self.change(self.pm, role="finance", token=token)
        self.assertEqual(first, replay)
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="finance", token=token,
                        project_scope=[self.project.pk])
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="pm", expected_version=999)
        with self.assertRaises(ValidationError):
            self.change(self.pm, role="pm", expected_version=True)

    def test_dry_run_has_no_side_effects(self):
        before = self.facts()
        result = self.change(self.pm, role="finance", dry_run=True)
        self.assertEqual(result["role"], "finance")
        self.assertEqual(before, self.facts())
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.role, "pm")

    def test_cross_org_member_denied(self):
        other_org = Organization.objects.create(name="其他公司")
        foreign_user = self.member("member")
        foreign = Membership.objects.create(organization=other_org, user=foreign_user.user)
        with self.assertRaises(PermissionDenied):
            self.change(foreign, role="member")

    def test_snapshot_read_permissions(self):
        self.assertEqual(masterdata.membership_snapshot(
            self.finance, self.pm.pk)["role"], "pm")
        self.assertEqual(masterdata.membership_snapshot(
            self.auditor, self.pm.pk)["id"], str(self.pm.pk))
        self.assertEqual(masterdata.membership_snapshot(
            self.pm, self.pm.pk)["id"], str(self.pm.pk))
        with self.assertRaises(PermissionDenied):
            masterdata.membership_snapshot(self.plain, self.pm.pk)
        with self.assertRaises(PermissionDenied):
            masterdata.membership_snapshot(self.pm, self.plain.pk)
