"""PH-330 project budget versions, four-eyes approval and budget_residual_v1 ETC."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from core.models import AccountingPeriod, Organization, Project
from pmo_plugins.costing import budget_services as services
from pmo_plugins.costing.models import ProjectBudgetLine, ProjectBudgetVersion
from tests.test_v1_shared_cost_allocation import CostFixture


class BudgetFixture(CostFixture):
    def setUp(self):
        super().setUp()
        self.cost_event, self.cost_posting = self.add_posting(
            40000, scope_kind='project', scope_id=str(self.project.pk), line_index=2)

    def lines(self):
        return [{'subject': 'labor', 'amount_cents': 70000},
                {'subject': 'expense', 'amount_cents': 30000}]

    def create(self, member=None, **changes):
        values = dict(project_id=self.project.pk, period=self.period,
                      effective_from=self.today.isoformat(), lines=self.lines(),
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.create_project_budget(member or self.owner, **values)

    def approve(self, created, member=None, **changes):
        values = dict(request_token=uuid.uuid4(), expected_version=created['version'],
                      dry_run=False)
        values.update(changes)
        return services.approve_project_budget(
            member or self.finance, created['version_ref']['id'], **values)


class BudgetTests(BudgetFixture, TestCase):
    def test_create_approve_and_etc_gold(self):
        created = self.create()
        self.assertEqual(created['status'], 'draft')
        version = ProjectBudgetVersion.objects.get(pk=created['version_ref']['id'])
        self.assertEqual(version.status, 'draft')
        self.assertEqual(sum(line.amount_cents for line in version.lines.all()), 100000)
        approved = self.approve(created)
        version.refresh_from_db()
        self.assertEqual(version.status, 'approved')
        self.assertEqual(approved['approved_by'], str(self.finance.pk))
        snapshot = services.budget_etc_snapshot(self.owner, project_id=self.project.pk,
                                                period=self.period)
        self.assertEqual(snapshot['budget_cents'], 100000)
        self.assertEqual(snapshot['incurred_cost_cents'], 40000)
        self.assertEqual(snapshot['etc_cents'], 60000)
        self.assertEqual(snapshot['eac_cents'], 100000)
        self.assertEqual(snapshot['budget_variance_cents'], 0)
        self.assertEqual(snapshot['over_budget_cents'], 0)
        self.assertEqual(snapshot['remaining_budget_cents'], 60000)
        self.assertEqual(snapshot['algorithm_version'], 'budget_residual_v1')

    def test_four_eyes_reject_supersede_and_duplicates(self):
        created = self.create()
        with self.assertRaises(ValidationError):
            self.approve(created, member=self.owner)
        rejected = services.reject_project_budget(
            self.finance, created['version_ref']['id'], request_token=uuid.uuid4(),
            expected_version=created['version'], reason='口径不符', dry_run=False)
        self.assertEqual(rejected['status'], 'rejected')
        second = self.create()
        self.approve(second)
        third = self.create()
        self.approve(third)
        first_version = ProjectBudgetVersion.objects.get(pk=second['version_ref']['id'])
        self.assertEqual(first_version.status, 'superseded')
        self.assertEqual(ProjectBudgetVersion.objects.filter(
            organization=self.org, project=self.project, period=self.period,
            status='approved').count(), 1)
        with self.assertRaises(ValidationError):
            self.create(lines=[{'subject': 'bad', 'amount_cents': 1}])
        with self.assertRaises(ValidationError):
            self.create(lines=[])

    def test_missing_budget_and_closed_period(self):
        snapshot = services.budget_etc_snapshot(self.owner, project_id=self.project.pk)
        self.assertIsNone(snapshot['budget_cents'])
        self.assertIsNone(snapshot['etc_cents'])
        self.assertIn('budget_missing', [issue['issue_code'] for issue in snapshot['issues']])
        AccountingPeriod.objects.create(organization=self.org, period=self.period,
                                        status='posted', closed_through=self.today)
        with self.assertRaises(ValidationError):
            self.create()
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.create(member=self.auditor)
        with self.assertRaises((PermissionDenied, ValidationError)):
            services.budget_etc_snapshot(self.plain, project_id=self.project.pk)

    def test_append_only_budget_guards(self):
        created = self.create()
        version = ProjectBudgetVersion.objects.get(pk=created['version_ref']['id'])
        with self.assertRaises(ValidationError):
            version.delete()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM costing_projectbudgetversion WHERE id=%s',
                                   [str(version.pk).replace('-', '')])
        approved = self.approve(created)
        version.refresh_from_db()
        with self.assertRaises((ValidationError, DatabaseError)):
            with transaction.atomic():
                ProjectBudgetLine.objects.create(organization=self.org, version=version,
                                                 line_key='x', subject='other', amount_cents=1)
