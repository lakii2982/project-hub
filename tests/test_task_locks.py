from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, Membership, Organization, Project, Task
from core.services import update_task


class TaskDependencyUpdateTests(TestCase):
    """Run unchanged on SQLite and PostgreSQL; the latter exercises real SQL locks."""

    def setUp(self):
        self.today = timezone.localdate()
        user = get_user_model().objects.create_user("task-lock-owner")
        org = Organization.objects.create(name="Task lock test")
        self.owner = Membership.objects.create(organization=org, user=user, role="owner")
        self.project = Project.objects.create(
            organization=org, manager=self.owner, code="LOCK-1", name="Lock test",
            client="Test client", start_date=self.today, end_date=self.today,
        )

    def task(self, name, **kwargs):
        return Task.objects.create(
            project=self.project, owner=self.owner, name=name,
            start_date=self.today, due_date=self.today, **kwargs,
        )

    def test_no_dependency_can_start_and_keeps_version_and_audit(self):
        task = self.task("Independent")
        updated = update_task(self.owner, task.pk, "doing", task.version)
        self.assertEqual((updated.status, updated.version), ("doing", 2))
        self.assertTrue(AuditEvent.objects.filter(action="task.status_changed", entity_id=str(task.pk)).exists())

    def test_unfinished_dependency_rejects_without_partial_update(self):
        dependency = self.task("Before")
        task = self.task("After", dependency=dependency)
        before_audit = AuditEvent.objects.count()
        with self.assertRaisesMessage(ValidationError, "前置任务尚未完成"):
            update_task(self.owner, task.pk, "doing", task.version)
        task.refresh_from_db()
        self.assertEqual((task.status, task.version), ("todo", 1))
        self.assertEqual(AuditEvent.objects.count(), before_audit)

    def test_completed_dependency_allows_start_but_cannot_reopen_after_start(self):
        dependency = self.task("Before", status="done", completion_note="Delivered")
        task = self.task("After", dependency=dependency)
        update_task(self.owner, task.pk, "doing", task.version)
        with self.assertRaisesMessage(ValidationError, "后续任务已经开始"):
            update_task(self.owner, dependency.pk, "doing", dependency.version)
        dependency.refresh_from_db()
        self.assertEqual((dependency.status, dependency.version), ("done", 1))

    def test_stale_version_still_rejects(self):
        task = self.task("Independent")
        update_task(self.owner, task.pk, "doing", task.version)
        with self.assertRaisesMessage(ValidationError, "任务已被其他人更新"):
            update_task(self.owner, task.pk, "done", task.version, "Delivered")
        task.refresh_from_db()
        self.assertEqual((task.status, task.version, task.completion_note), ("doing", 2, ""))
