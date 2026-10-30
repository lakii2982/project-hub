"""PH-281 read-only membership/authorization operations view (HTTP)."""
from django.test import Client, TestCase

from core.models import AgentToken, PluginState
from tests.test_v1_identity_access_read import IdentityFixture

URL = '/modules/delivery/identity-access/'


class IdentityAccessHTTPTests(IdentityFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member=None):
        self.client.force_login((member or self.owner).user)

    def get(self, params=None, *, member=None):
        self.login(member)
        return self.client.get(URL, params or {})

    def test_read_roles_open_page(self):
        for member in (self.owner, self.finance, self.auditor, self.pm):
            response = self.get(member=member)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '成员与授权核对')

    def test_member_and_anonymous_denied(self):
        self.assertEqual(self.get(member=self.plain).status_code, 403)
        response = self.client.get(URL)
        self.assertIn(response.status_code, (302, 403))

    def test_page_contains_derived_tables_and_no_token_secret(self):
        self.change_audit(self.pm)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '成员账号与项目范围')
        self.assertContains(response, '授权审计摘要')
        self.assertContains(response, 'identity.membership.changed')
        self.assertNotContains(response, self.token_active.token_hash)
        self.assertNotContains(response, self.token_active.auth_snapshot)

    def test_pm_scope_hides_finance_project_members(self):
        response = self.get(member=self.pm)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.finance.user.username)

    def test_invalid_filters_return_400(self):
        for params in ({'as_of': '2999-01-01'}, {'page': '0'}, {'unknown': '1'}):
            self.assertEqual(self.get(params).status_code, 400)

    def test_post_not_allowed(self):
        self.login()
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_module_disabled_history_still_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.get().status_code, 200)
