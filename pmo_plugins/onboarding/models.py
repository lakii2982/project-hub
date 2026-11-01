"""PH-353 onboarding import preview/conflict persistence and append-only submission receipt.

ImportPreview/ImportConflict are the canonical onboarding ephemeral_preview projections
(*x-owner=onboarding*); preview persistence never writes business facts, ledger, source
consumption or canonical events. ImportSubmissionReceipt is an internal (non-canonical)
append-only receipt used for request-token idempotency and stable import_key uniqueness.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ImportPreview(models.Model):
    """Persisted import preview; rows are normalized CSV mappings awaiting one commit."""

    STATUS_DRAFT = 'draft'
    STATUS_READY = 'ready'
    STATUS_CONSUMED = 'consumed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, '草稿'),
        (STATUS_READY, '待提交'),
        (STATUS_CONSUMED, '已提交'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    kind = models.CharField('导入类型', max_length=16)
    file_name = models.CharField('文件名', max_length=255, blank=True, default='')
    rows_digest = models.CharField('规范行摘要', max_length=64)
    rows_json = models.JSONField('规范行', default=list)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)
    version = models.PositiveIntegerField(default=1)
    created_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                          related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=['draft', 'ready', 'consumed']),
                name='onboarding_import_preview_status'),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name='onboarding_import_preview_version'),
        ]
        indexes = [models.Index(fields=['organization', 'status', 'created_at'],
                                name='onboarding_preview_org_status')]

    def __str__(self):
        return f'{self.kind}:{self.pk}'


class ImportConflict(models.Model):
    """A per-row/field preview conflict; blockers must be explicitly resolved before commit.

    Configuration fields are immutable after creation. The only allowed transition is a
    one-way ``resolution is null -> accept/reject/merge`` with operator and timestamp;
    resolved rows are never updated again or deleted, so the decision history is retained.
    """

    RESOLUTION_ACCEPT = 'accept'
    RESOLUTION_REJECT = 'reject'
    RESOLUTION_MERGE = 'merge'
    RESOLUTION_CHOICES = [
        (RESOLUTION_ACCEPT, '接受'),
        (RESOLUTION_REJECT, '拒绝'),
        (RESOLUTION_MERGE, '合并'),
    ]
    SEVERITY_BLOCKER = 'blocker'
    SEVERITY_WARNING = 'warning'
    SEVERITY_CHOICES = [(SEVERITY_BLOCKER, '阻塞'), (SEVERITY_WARNING, '警告')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    preview = models.ForeignKey(ImportPreview, on_delete=models.PROTECT,
                                related_name='conflicts')
    conflict_key = models.CharField('冲突去重键', max_length=64)
    row_no = models.PositiveIntegerField('行号', null=True, blank=True)
    field = models.CharField('字段', max_length=64, blank=True, default='')
    source_ref = models.JSONField('来源引用', default=dict)
    conflict_code = models.CharField('冲突代码', max_length=64)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES,
                                default=SEVERITY_BLOCKER)
    row_digest = models.CharField('行摘要', max_length=64, blank=True, default='')
    resolution = models.CharField(max_length=16, choices=RESOLUTION_CHOICES,
                                  null=True, blank=True)
    resolved_by_member = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                           null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # At most one unresolved (effective) conflict per organization + conflict_key.
            models.UniqueConstraint(
                fields=['organization', 'conflict_key'],
                condition=Q(resolution__isnull=True),
                name='onboarding_active_conflict_unique'),
            models.CheckConstraint(
                condition=Q(severity__in=['blocker', 'warning']),
                name='onboarding_import_conflict_severity'),
            models.CheckConstraint(
                condition=Q(resolution__isnull=True)
                | Q(resolution__in=['accept', 'reject', 'merge']),
                name='onboarding_import_conflict_resolution'),
        ]
        indexes = [models.Index(fields=['organization', 'preview', 'severity'],
                                name='onboarding_conflict_preview')]

    _IMMUTABLE_FIELDS = (
        'organization_id', 'preview_id', 'conflict_key', 'row_no', 'field',
        'source_ref', 'conflict_code', 'severity', 'row_digest',
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.resolution is not None:
                raise ValidationError('冲突必须先以未消解状态创建')
            super().save(*args, **kwargs)
            return
        previous = ImportConflict.objects.filter(pk=self.pk).values(
            'resolution', *self._IMMUTABLE_FIELDS).first()
        if previous is None:
            raise ValidationError('冲突记录不存在')
        if previous['resolution'] is not None:
            raise ValidationError('冲突已消解，历史记录不可覆盖')
        for field in self._IMMUTABLE_FIELDS:
            if previous[field] != getattr(self, field):
                raise ValidationError('冲突记录创建后不可修改')
        if self.resolution is None:
            raise ValidationError('冲突记录创建后不可修改')
        if self.resolution not in {self.RESOLUTION_ACCEPT, self.RESOLUTION_REJECT,
                                   self.RESOLUTION_MERGE}:
            raise ValidationError('冲突消解结论无效')
        if self.resolved_by_member_id is None:
            raise ValidationError('冲突消解必须记录操作人')
        if self.resolved_at is None:
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('冲突记录不可删除；请追加消解决策')

    def as_envelope(self):
        return {
            'id': str(self.pk), 'row_no': self.row_no, 'field': self.field,
            'conflict_key': self.conflict_key, 'conflict_code': self.conflict_code,
            'severity': self.severity, 'source_ref': self.source_ref,
            'row_digest': self.row_digest, 'resolution': self.resolution,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }


class ImportSubmissionReceipt(models.Model):
    """Internal append-only receipt for one committed import batch (contract §5.2)."""

    STATUS_COMMITTED = 'committed'
    STATUS_FAILED = 'failed'
    STATUS_DRY_RUN = 'dry_run'
    STATUS_CHOICES = [
        (STATUS_COMMITTED, '已提交'),
        (STATUS_FAILED, '失败'),
        (STATUS_DRY_RUN, '预演'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('core.Organization', on_delete=models.PROTECT,
                                     related_name='+')
    request_token = models.UUIDField()
    action = models.CharField(max_length=40, default='import.commit')
    input_digest = models.CharField(max_length=64)
    import_key = models.CharField(max_length=64)
    preview_ref = models.JSONField(default=dict)
    period = models.CharField(max_length=7)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=STATUS_COMMITTED)
    precheck_digest = models.CharField(max_length=64, blank=True, default='')
    precheck_as_of_date = models.DateField(null=True, blank=True)
    result_refs = models.JSONField(default=list)
    conflicts_resolved = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    missing = models.JSONField(default=list)
    created_by = models.ForeignKey('core.Membership', on_delete=models.PROTECT,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'request_token'],
                                    name='onboarding_import_receipt_token_unique'),
            models.UniqueConstraint(fields=['organization', 'import_key'],
                                    condition=Q(status='committed'),
                                    name='onboarding_import_receipt_key_unique'),
            models.CheckConstraint(
                condition=Q(status__in=['committed', 'failed', 'dry_run']),
                name='onboarding_import_receipt_status'),
        ]
        indexes = [models.Index(fields=['organization', 'created_at'],
                                name='onboarding_receipt_org_created')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('导入提交收据不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('导入提交收据不可删除')

    def as_result(self, *, replayed=False):
        return {
            'status': self.status,
            'receipt_id': str(self.pk),
            'request_token': str(self.request_token),
            'import_key': self.import_key,
            'preview_ref': self.preview_ref,
            'period': self.period,
            'precheck_digest': self.precheck_digest,
            'precheck_as_of_date': (self.precheck_as_of_date.isoformat()
                                    if self.precheck_as_of_date else None),
            'result_refs': self.result_refs,
            'row_count': len(self.result_refs or ()),
            'conflicts_resolved': self.conflicts_resolved,
            'warnings': self.warnings,
            'missing': self.missing,
            'created_at': self.created_at.isoformat(),
            'replayed': replayed,
            'dry_run': False,
        }
