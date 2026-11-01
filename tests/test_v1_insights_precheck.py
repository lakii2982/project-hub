"""PH-354 期间预检只读服务测试（period.precheck.snapshot/v1 首片）。"""
import hashlib
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         Organization, Project, SourceConsumption)
from pmo_plugins.insights import precheck_services
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

AS_OF = None  # 各用例用 fixture 的 self.today 作为 as_of


class PeriodPrecheckServiceTests(OpsOverviewFixture, TestCase):
    def snapshot(self, member=None, **kwargs):
        kwargs.setdefault('as_of', self.today.isoformat())
        return precheck_services.period_precheck_snapshot(member or self.owner, **kwargs)

    def test_envelope_shape_precheck_null_and_stable_digest(self):
        self.settlement_with_item()
        self.cash()
        first = self.snapshot()
        self.assertEqual(first['contract'], 'period.precheck.snapshot/v1')
        self.assertEqual(first['scope'], {'kind': 'company', 'project_id': None})
        self.assertEqual(first['period'], self.today.strftime('%Y-%m'))
        # 上游无 canonical PeriodPrecheck 实体：precheck=None，不伪造 id/version/status。
        self.assertIsNone(first['precheck'])
        missing_codes = {entry['missing_code'] for entry in first['missing']}
        self.assertIn('period_precheck_missing', missing_codes)
        self.assertIn('offset_not_implemented', missing_codes)
        # 金额差额不复制 owner 规则：envelope 内 difference 恒为 null，不以 0 冒充。
        for issue in first['issues']:
            self.assertIsNone(issue['difference_cents'])
        digest = first['precheck_digest']
        self.assertEqual(digest, hashlib.sha256(json.dumps(
            {key: first[key] for key in
             ('contract', 'scope', 'period', 'as_of_date', 'watermark',
              'issues', 'differences', 'missing')},
            sort_keys=True, ensure_ascii=False, separators=(',', ':'),
            default=str).encode()).hexdigest())
        second = self.snapshot()
        self.assertEqual(digest, second['precheck_digest'])

    def test_zero_write(self):
        self.settlement_with_item()
        before = tuple(model.objects.count() for model in
                       (AuditEvent, DomainEvent, LedgerEntry, SourceConsumption))
        self.snapshot()
        after = tuple(model.objects.count() for model in
                      (AuditEvent, DomainEvent, LedgerEntry, SourceConsumption))
        self.assertEqual(before, after)

    def test_owner_issue_codes_and_severity_passthrough(self):
        settlement = self.settlement_with_item(amount=50000)
        from pmo_plugins.settlement.models import OpenItem
        item = OpenItem.objects.get(settlement=settlement)
        unclassified = self.cash(classify=False)
        over = self.cash(amount=60000)
        self.clearance(item, over.pk, amount=60000)
        data = self.snapshot()
        by_code = {}
        for issue in data['issues']:
            by_code.setdefault(issue['issue_code'], issue)
        # 未分类现金：owner cash 口径 warning，source_refs 带现金事实引用。
        self.assertEqual(by_code['classification_missing']['severity'], 'warning')
        self.assertEqual(by_code['classification_missing']['domain'], 'cash')
        self.assertEqual(by_code['classification_missing']['owner'], 'cash')
        self.assertTrue(any(ref['kind'] == 'cash_transaction' and
                            ref['id'] == str(unclassified.pk)
                            for ref in by_code['classification_missing']['source_refs']))
        # 核销超额：owner settlement 口径，期间预检冻结映射为 blocker。
        self.assertEqual(by_code['cleared_over_gross']['severity'], 'blocker')
        self.assertEqual(by_code['cleared_over_gross']['domain'], 'settlement')
        self.assertTrue(any(ref['kind'] == 'open_item' and ref['id'] == str(item.pk)
                            for ref in by_code['cleared_over_gross']['source_refs']))
        self.assertGreaterEqual(data['summary']['blocking_count'], 1)

    def test_digest_changes_when_facts_change(self):
        before = self.snapshot()
        self.cash(classify=False)
        after = self.snapshot()
        self.assertNotEqual(before['precheck_digest'], after['precheck_digest'])

    def test_member_denied_and_roles(self):
        self.assertRaises(PermissionDenied, lambda: self.snapshot(self.plain))
        self.snapshot(self.finance)
        self.snapshot(self.auditor)

    def test_pm_scope_managed_project_only(self):
        self.settlement_with_item()
        data = self.snapshot(self.pm, scope='project', project_id=self.project.pk)
        self.assertEqual(data['scope']['kind'], 'project')
        self.assertEqual(data['scope']['project_id'], str(self.project.pk))
        # pm 无项目、越权项目、跨 org 项目一律拒绝。
        self.assertRaises(PermissionDenied, lambda: self.snapshot(self.pm))
        self.assertRaises(PermissionDenied,
                          lambda: self.snapshot(self.pm, project_id=self.project2.pk))
        other = Project.objects.create(organization=self.org, code='PX', name='跨org项目',
                                       manager=self.owner, start_date=self.today,
                                       end_date=self.today)
        outsider = Organization.objects.create(name='其他公司')
        from django.contrib.auth import get_user_model
        from core.models import Membership
        outsider_owner = Membership.objects.create(
            organization=outsider,
            user=get_user_model().objects.create_user(username='outsider' + uuid.uuid4().hex),
            role='owner')
        self.assertRaises(PermissionDenied,
                          lambda: self.snapshot(outsider_owner, project_id=other.pk))
        self.assertRaises((PermissionDenied, ValidationError),
                          lambda: self.snapshot(self.pm, scope='bogus'))

    def test_company_scope_with_explicit_project_for_full_roles(self):
        self.settlement_with_item()
        data = self.snapshot(self.owner, scope='project', project_id=self.project.pk)
        self.assertEqual(data['scope']['kind'], 'project')
        bad = self.snapshot(self.owner, scope='company')
        self.assertEqual(bad['scope']['kind'], 'company')

    def test_period_closed_and_locked_are_blockers(self):
        AccountingPeriod.objects.create(organization=self.org,
                                        period=self.today.strftime('%Y-%m'),
                                        status='posted', closed_through=self.today)
        data = self.snapshot()
        codes = [issue['issue_code'] for issue in data['issues']
                 if issue['severity'] == 'blocker']
        self.assertIn('period_closed', codes)
        self.assertIn('period_locked', codes)

    def test_period_not_found_missing_when_tracking_started(self):
        AccountingPeriod.objects.create(organization=self.org, period='2020-01',
                                        status='active')
        data = self.snapshot(period='2020-02')
        entry = [row for row in data['missing'] if row['missing_code'] == 'period_not_found']
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]['severity'], 'blocker')
        # 组织完全未启用期间跟踪时，不对缺失期间作假判定。
        AccountingPeriod.objects.filter(organization=self.org).delete()
        clean = self.snapshot(period='2020-02')
        self.assertFalse(any(row['missing_code'] == 'period_not_found'
                             for row in clean['missing']))

    def test_future_as_of_is_warning(self):
        future = (self.today + timedelta(days=3)).isoformat()
        data = self.snapshot(as_of=future)
        codes = {issue['issue_code']: issue['severity'] for issue in data['issues']}
        self.assertEqual(codes.get('future_as_of'), 'warning')

    def test_sections_subset_and_unknown_rejected(self):
        self.settlement_with_item()
        full = self.snapshot()
        cash_only = self.snapshot(sections=['cash'])
        self.assertTrue(all(check['domain'] == 'cash' for check in cash_only['checks']))
        self.assertLessEqual(len(cash_only['issues']), len(full['issues']))
        self.assertRaises(ValidationError, lambda: self.snapshot(sections=['cash', 'bogus']))

    def test_truncation_flags_result_truncated(self):
        self.settlement_with_item()
        with mock.patch.object(precheck_services, 'ISSUE_LIMIT', 1):
            data = self.snapshot()
        self.assertTrue(any(row['missing_code'] == 'result_truncated'
                            for row in data['missing']))
        self.assertLessEqual(len(data['issues']), 1)

    def test_missing_never_displayed_as_zero(self):
        # 无事实时：checks 的 items 计数为 None（上游缺失），不得出现 0 冒充。
        data = self.snapshot()
        revenue_check = [check for check in data['checks'] if check['domain'] == 'revenue']
        self.assertEqual(revenue_check[0]['counts']['items'], None)
        self.assertEqual(data['summary']['blocking_count'] +
                         data['summary']['warning_count'], len(data['issues']))
