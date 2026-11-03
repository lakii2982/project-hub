"""Q-34 append-only contract amount changes (1.0 simplified contract).

The original ``Project.contract_cents`` stays a historical initiation estimate.  A contract
amount change appends a new record with the *new total*; the project page reads the latest
record and shows the full history.  Nothing here rewrites past amounts, ledger facts or
billing/expense rows; it only appends an audited commercial fact.
"""
import uuid
from datetime import date, datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .access import require_project, require_write
from .models import Project, ProjectContractChange
from .services import audit, check_open, check_period, lock_company

MAX_AMOUNT_CENTS = 10 ** 12  # 100亿元, same bound as the rest of the money rules.
NOTE_MIN = 4
NOTE_MAX = 240


def contract_amount_cents(project):
    """Current contract amount: latest appended change, else the initiation estimate."""
    latest = project.contract_changes.order_by("-created_at", "-id").first()
    return latest.amount_cents if latest is not None else project.contract_cents


def contract_history(project, limit=200):
    """Newest-first append-only history; each row stores its own previous amount."""
    rows = list(project.contract_changes.select_related("created_by")
                .order_by("-created_at", "-id")[:limit])
    return [{"change": row, "previous_cents": row.previous_amount_cents,
             "amount_cents": row.amount_cents,
             "delta_cents": row.amount_cents - row.previous_amount_cents} for row in rows]


@transaction.atomic
def record_contract_change(member, *, project, amount_cents, note, effective_on, token):
    """Append one contract amount change; token/idempotency and append-only audit included."""
    org = lock_company(member)
    require_write(member)
    project = Project.objects.select_for_update().get(pk=project.pk)
    if project.organization_id != org.pk:
        raise PermissionDenied("项目不属于当前公司")
    require_project(member, project, manage=True)
    check_open(project)
    if not isinstance(token, uuid.UUID):
        raise ValidationError("提交编号无效，请刷新后重试")
    if type(amount_cents) is not int or not 0 <= amount_cents <= MAX_AMOUNT_CENTS:
        raise ValidationError("合同额须为0至100亿元的整数分")
    note = (note or "").strip()
    if not NOTE_MIN <= len(note) <= NOTE_MAX:
        raise ValidationError(f"请填写{NOTE_MIN}至{NOTE_MAX}字的合同额变更说明")
    if not isinstance(effective_on, date) or isinstance(effective_on, datetime):
        raise ValidationError("请填写有效的变更生效日期")
    check_period(org, effective_on)
    existing = ProjectContractChange.objects.filter(token=token).first()
    if existing is not None:
        if (existing.project_id != project.pk or existing.created_by_id != member.user_id
                or existing.amount_cents != amount_cents or existing.note != note
                or existing.effective_on != effective_on):
            raise ValidationError("提交编号已被使用，请刷新后重试")
        return existing
    current = contract_amount_cents(project)
    if amount_cents == current:
        raise ValidationError("合同额未变化，无需追加变更")
    if amount_cents == 0:
        raise ValidationError("合同额变更须大于0；如需终止项目请使用项目阶段/归档流程")
    previous = current
    row = ProjectContractChange.objects.create(
        organization=org, project=project, previous_amount_cents=previous,
        amount_cents=amount_cents, note=note, effective_on=effective_on, token=token,
        created_by=member.user)
    audit(member, "project.contract_changed", project, {
        "change_id": str(row.pk), "previous_amount_cents": previous,
        "amount_cents": amount_cents, "effective_on": str(effective_on), "note": note})
    return row
