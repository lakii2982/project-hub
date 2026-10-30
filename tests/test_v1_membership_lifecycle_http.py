"""PH-264 HTTP settings-page boundaries for membership lifecycle changes."""
import uuid

from django.test import Client, TestCase

from core.models import AgentToken, AuditEvent, Membership, ProjectMember
from tests.test_v1_membership_lifecycle import MembershipFixture


class MembershipHTTPFixture(MembershipFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def data(self, target, **changes):
        data = {"membership_id": str(target.pk), "role": target.role,
                "project_scope": [], "active": "",
                "request_token": str(uuid.uuid4())}
        data.update(changes)
        return data

    def post(self, target, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post("/settings/", self.data(target, **changes))


class MembershipHTTPTests(MembershipHTTPFixture, TestCase):
    def test_owner_changes_role_and_scope_from_settings(self):
        response = self.post(self.pm, role="pm", project_scope=[str(self.project.pk)])
        self.assertEqual(response.status_code, 302)
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.role, "pm")
        self.assertEqual(list(ProjectMember.objects.filter(
            member=self.pm).values_list("project_id", flat=True)),
            [self.project.pk])
        self.assertTrue(AuditEvent.objects.filter(
            action="identity.membership.changed").exists())

    def test_owner_deactivates_and_restores_member(self):
        token = self.token(self.plain)
        ProjectMember.objects.create(project=self.project, member=self.plain)
        response = self.post(self.plain, active="0", role="member")
        self.assertEqual(response.status_code, 302)
        self.plain.refresh_from_db()
        token.refresh_from_db()
        self.assertFalse(self.plain.active)
        self.assertIsNotNone(token.revoked_at)
        self.assertFalse(ProjectMember.objects.filter(member=self.plain).exists())
        response = self.post(self.plain, active="1", role="member")
        self.assertEqual(response.status_code, 302)
        self.plain.refresh_from_db()
        self.assertTrue(self.plain.active)

    def test_non_owner_write_denied(self):
        for member in (self.finance, self.pm, self.plain, self.auditor):
            self.assertEqual(self.post(self.plain, member=member).status_code, 403)

    def test_settings_page_visibility(self):
        self.login(self.owner)
        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "成员账号生命周期")
        self.login(self.finance)
        self.assertEqual(self.client.get("/settings/").status_code, 200)
        self.login(self.pm)
        self.assertEqual(self.client.get("/settings/").status_code, 403)

    def test_invalid_input_returns_readable_form_error(self):
        response = self.post(self.pm, role="owner", project_scope=[str(self.project.pk)])
        self.assertEqual(response.status_code, 400)
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.role, "pm")
        response = self.post(self.pm, role="pm",
                             project_scope=[str(self.project.pk), str(self.project.pk)])
        self.assertEqual(response.status_code, 400)
        response = self.post(self.pm, role="pm", unknown_field="1")
        self.assertEqual(response.status_code, 400)

    def test_last_owner_cannot_be_demoted_from_settings(self):
        response = self.post(self.owner, role="member")
        self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, "owner")

    def test_deactivated_member_denied_everywhere(self):
        self.post(self.plain, active="0", role="member")
        self.login(self.plain)
        self.assertEqual(self.client.get("/settings/").status_code, 403)
