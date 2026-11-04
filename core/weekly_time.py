"""Q-35 weekly time entry and simplified automatic labor cost.

One weekly submission creates or corrects rows in the core ``Timesheet`` source fact.  The
member's internal cost rate is snapshotted by business date; a missing rate never becomes a
fake zero cost -- the row stays unpriced and appears in the gap list until a rate exists.
The ``simplified`` mode is separate from the legacy approval/ledger mode, so historical
timesheets, their ledger postings and the monthly labor pool are never rewritten.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .access import require_write, visible_projects
from .models import CostRate, Employee, NonProjectDestination, Timesheet
from .services import audit, check_period, lock_company, time_cost

MODE = "simplified"
STATUS = "approved"
MAX_MINUTES = 1440
NOTE_MAX = 300


class WeekTimeError(ValidationError):
    """Row-keyed validation failure; the view re-renders the posted values with inline errors."""

    def __init__(self, row_errors, general=()):
        self.row_errors = row_errors
        self.general = list(general)
        messages = list(self.general)
        for messages_for_row in row_errors.values():
            messages.extend(messages_for_row)
        super().__init__(messages)


# Business weeks outside this range are clamped (never a 500 for crafted ?week values).
EARLIEST_WEEK = date(1970, 1, 5)
LATEST_WEEK = date(2100, 12, 27)


def week_start_for(day):
    if day < EARLIEST_WEEK:
        day = EARLIEST_WEEK
    elif day > LATEST_WEEK:
        day = LATEST_WEEK
    return day - timedelta(days=day.weekday())


def week_bounds(week_start):
    return week_start, week_start + timedelta(days=6)


def week_label(week_start):
    end = week_start + timedelta(days=6)
    return f"{week_start:%Y年%m月%d日} — {end:%m月%d日}"


def latest_rate(employee, work_date):
    return CostRate.objects.filter(employee=employee, effective_from__lte=work_date).first()


def _parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _parse_hours(value):
    """Parse an hours input (``8``, ``7.5``) into integer minutes for storage.

    Hours are a display/input unit only: the source fact remains integer minutes, so
    conversions round half up and reject non-positive or over-24h values.
    """
    if isinstance(value, bool):
        return None
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        hours = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not hours.is_finite() or hours <= 0 or hours > Decimal(MAX_MINUTES) / Decimal(60):
        return None
    minutes = int((hours * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minutes if 1 <= minutes <= MAX_MINUTES else None


def _parse_minutes(value):
    if isinstance(value, bool):
        return None
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return minutes if 1 <= minutes <= MAX_MINUTES else None


def _parse_target(value):
    raw = (value or "").strip()
    if raw.startswith("project:") and raw[8:].isdigit():
        return "project", int(raw[8:])
    if raw.startswith("nonproject:"):
        # NonProjectDestination uses a UUID primary key, not an integer id.
        try:
            uuid.UUID(raw[11:].strip())
        except (ValueError, AttributeError):
            return None, None
        return "nonproject", raw[11:].strip()
    return None, None


def authorized_employee(member, employee):
    """Every role records its own time only; 1.0 simplified scope has no proxy entry (代录)."""
    if employee.organization_id != member.organization_id or not employee.active:
        raise ValidationError("员工不属于当前公司或已停用，请刷新后重试")
    if employee.member_id != member.pk:
        raise PermissionDenied("成员只能填报本人的工时")
    return employee


def _project(member, project_id):
    project = visible_projects(member).filter(pk=project_id).first()
    if project is None:
        raise ValidationError("项目不存在或无权填报")
    if project.stage in {"closed", "cancelled", "on_hold"}:
        raise ValidationError("项目已结束或暂停；新增投入前请由负责人恢复项目")
    return project


def _destination(organization, destination_id):
    destination = NonProjectDestination.objects.filter(
        pk=destination_id, organization=organization, active=True).first()
    if destination is None:
        raise ValidationError("非项目去向不存在或已停用，请刷新后重试")
    return destination


def _hours_text(minutes):
    """Render integer minutes as an editable hour value (8, 7.5, 0.1)."""
    value = (Decimal(int(minutes)) / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(value.normalize(), "f")
    return text


def _row_payload(row, key):
    return {
        "key": key, "id": row.pk, "token": str(row.token), "version": row.version,
        "work_date": row.work_date.isoformat(),
        "target": f"project:{row.project_id}" if row.project_id else f"nonproject:{row.non_project_destination_id}",
        "minutes": row.minutes, "hours": _hours_text(row.minutes), "note": row.note,
        "nonproject": row.project_id is None,
        "unpriced": row.simplified_unpriced,
        "corrected": row.version > 1,
        "cost_cents": row.cost_cents,
        "errors": [],
    }


def weekly_rows(member, employee, week_start):
    """Existing simplified rows for one employee/week, newest day first, in editable payloads."""
    start, end = week_bounds(week_start)
    rows = Timesheet.objects.filter(
        organization=member.organization, employee=employee, accounting_mode=MODE,
        status=STATUS, work_date__range=(start, end)).select_related("project").order_by("work_date", "id")
    return [_row_payload(row, f"row-{index}") for index, row in enumerate(rows)]


def blank_rows(week_start, start_index):
    rows = []
    for offset in range(7):
        rows.append({
            # Token is generated at render time so a double-click / back-resubmit of the same
            # form reuses the same idempotency key instead of creating a second row.
            "key": f"row-{start_index + offset}", "id": "", "token": str(uuid.uuid4()), "version": "",
            "work_date": (week_start + timedelta(days=offset)).isoformat(), "target": "",
            "minutes": "", "hours": "", "note": "", "nonproject": False, "unpriced": False,
            "corrected": False, "cost_cents": None, "errors": [],
            "new_group": offset == 0 and start_index > 0,
        })
    return rows


def _snapshot(row):
    return {
        "project_id": row.project_id, "non_project_destination_id": row.non_project_destination_id,
        "work_date": row.work_date.isoformat(), "minutes": row.minutes, "note": row.note,
        "hourly_rate_cents": row.hourly_rate_cents, "cost_cents": row.cost_cents,
        "cost_state": row.cost_state, "version": row.version,
    }


def _rate_snapshot(employee, project, work_date, minutes):
    if project is None:
        return None, None, "", "nonproject"
    rate = latest_rate(employee, work_date)
    if rate is None:
        return None, None, "", "missing_rate"
    return rate.hourly_cents, time_cost(rate.hourly_cents, minutes), rate.basis, "priced"


@transaction.atomic
def save_week(member, employee, week_start, rows):
    """Validate and persist one week of entries. Raises WeekTimeError with row-keyed messages.

    Unchanged rows are no-ops (repeated submissions are idempotent); corrections update the
    same source row, bump ``version`` and append an audit record with before/after values.
    """
    org = lock_company(member)
    require_write(member)
    employee = Employee.objects.select_related("member").get(pk=employee.pk, organization=org)
    authorized_employee(member, employee)
    start, end = week_bounds(week_start)
    today = timezone.localdate()
    errors, parsed = {}, []
    seen_ids, seen_tokens = set(), set()
    for row in rows:
        key = row.get("key") or "row-?"
        messages = []
        work_date = _parse_date(row.get("work_date"))
        if work_date is None:
            messages.append("请填写工作日期")
        elif work_date < start or work_date > end:
            messages.append("工作日期必须在本周内")
        elif work_date > today:
            messages.append("实际业务日期不能在未来")
        else:
            try:
                check_period(org, work_date)
            except ValidationError as error:
                messages.extend(error.messages)
        project = destination = None
        kind, target_id = _parse_target(row.get("target"))
        if target_id is None:
            messages.append("请选择项目或非项目投入")
        else:
            try:
                target = _project(member, target_id) if kind == "project" else _destination(org, target_id)
                project, destination = (target, None) if kind == "project" else (None, target)
            except (ValidationError, PermissionDenied) as error:
                messages.extend([str(message) for message in error.messages])
        raw_minutes = str(row.get("minutes") or "").strip()
        minutes = _parse_minutes(raw_minutes) if raw_minutes else _parse_hours(row.get("hours"))
        if minutes is None:
            messages.append("工时须为0.1至24小时（例如 8 或 7.5）")
        note = (row.get("note") or "").strip()
        if not note:
            messages.append("请填写工作说明")
        elif len(note) > NOTE_MAX:
            messages.append(f"工作说明不能超过{NOTE_MAX}字")
        existing, token, idempotent = None, None, False
        if row.get("id"):
            try:
                row_id = int(row["id"])
            except (TypeError, ValueError):
                row_id = None
                messages.append("工时记录编号无效，请刷新后重试")
            if row_id is not None and row_id in seen_ids:
                messages.append("同一工时记录在本次提交中重复出现，请刷新后重试")
            elif row_id is not None:
                seen_ids.add(row_id)
            try:
                existing = Timesheet.objects.select_related("project").get(
                    pk=row_id, organization=org, employee=employee,
                    accounting_mode=MODE, status=STATUS) if row_id is not None else None
            except (Timesheet.DoesNotExist, TypeError, ValueError):
                messages.append("该工时记录已变更或不属于本人，请刷新后重试")
                existing = None
            if existing is not None:
                if str(row.get("version") or "") != str(existing.version):
                    messages.append("记录已被其他人更正，请刷新后重试")
                try:
                    check_period(org, existing.work_date)
                except ValidationError as error:
                    messages.extend(error.messages)
        else:
            raw_token = (row.get("token") or "").strip()
            token = None
            if raw_token:
                try:
                    token = uuid.UUID(raw_token)
                except (ValueError, AttributeError):
                    messages.append("提交编号无效，请刷新后重试")
            if token is None:
                # No client token: derive a stable key from the submitted fact. Identical
                # tokenless posts therefore cannot double-post the same投入 (double-click,
                # cached page, or a retry without the hidden field).
                target_key = (f"project:{project.pk}" if project else
                              f"nonproject:{destination.pk}" if destination else "")
                token = uuid.uuid5(uuid.NAMESPACE_URL, "|".join([
                    "q35-weekly", str(org.pk), str(employee.pk), work_date.isoformat() if work_date else "",
                    target_key, str(minutes if minutes is not None else ""),
                    (note or "")[:NOTE_MAX]]))
            if token in seen_tokens:
                messages.append("同一提交编号在本次提交中重复出现，请刷新后重试")
            seen_tokens.add(token)
            found = Timesheet.objects.filter(token=token).first() if token else None
            if found is not None:
                if (found.organization_id != org.pk or found.employee_id != employee.pk
                        or found.submitted_by_id != member.user_id or found.accounting_mode != MODE
                        or found.status != STATUS):
                    messages.append("提交编号已被使用，请刷新后重试")
                elif not messages:
                    if (found.project_id != (project.pk if project else None)
                            or found.non_project_destination_id != (destination.pk if destination else None)
                            or found.work_date != work_date or found.minutes != minutes
                            or found.note != note):
                        messages.append("同一提交编号的工时内容已改变，请使用更正或刷新后重试")
                    else:
                        existing, idempotent = found, True
        parsed.append({
            "key": key, "existing": existing, "idempotent": idempotent, "token": token,
            "work_date": work_date, "project": project, "destination": destination,
            "minutes": minutes, "note": note,
        })
        if messages:
            errors[key] = messages
    if not errors:
        # Daily cross-project total (legacy submitted/approved rows included): one person can
        # never record more than 24h on a calendar day, whichever entry path created the row.
        corrected_ids = [item["existing"].pk for item in parsed if item["existing"] is not None]
        others = Timesheet.objects.filter(
            employee=employee, work_date__range=(start, end),
            status__in=["submitted", STATUS]).exclude(pk__in=corrected_ids)
        per_day = {}
        for work_date, minutes in others.values_list("work_date", "minutes"):
            per_day[work_date] = per_day.get(work_date, 0) + minutes
        for item in parsed:
            per_day[item["work_date"]] = per_day.get(item["work_date"], 0) + item["minutes"]
        for item in parsed:
            if per_day[item["work_date"]] > MAX_MINUTES:
                errors.setdefault(item["key"], []).append(
                    f"同一员工当日累计工时不能超过24小时（当前 {_hours_text(per_day[item['work_date']])} 小时）")
    if errors:
        raise WeekTimeError(errors)
    created = corrected = unpriced = 0
    for item in parsed:
        existing = item["existing"]
        if item.get("idempotent"):
            continue
        hourly, cost, basis, state = _rate_snapshot(
            employee, item["project"], item["work_date"], item["minutes"])
        if existing is not None:
            before = _snapshot(existing)
            changed = (existing.project_id != (item["project"].pk if item["project"] else None)
                       or existing.non_project_destination_id != (item["destination"].pk if item["destination"] else None)
                       or existing.work_date != item["work_date"] or existing.minutes != item["minutes"]
                       or existing.note != item["note"] or existing.cost_cents != cost
                       or existing.hourly_rate_cents != hourly)
            if not changed:
                continue
            existing.project = item["project"]
            existing.non_project_destination = item["destination"]
            existing.work_date, existing.minutes, existing.note = item["work_date"], item["minutes"], item["note"]
            existing.hourly_rate_cents, existing.cost_cents = hourly, cost
            existing.rate_basis, existing.cost_state = basis, state
            existing.version += 1
            existing.save(update_fields=[
                "project", "non_project_destination", "work_date", "minutes", "note",
                "hourly_rate_cents", "cost_cents", "rate_basis", "cost_state", "version"])
            audit(member, "time.corrected", existing,
                  {"before": before, "after": _snapshot(existing)})
            corrected += 1
        else:
            row = Timesheet.objects.create(
                organization=org, accounting_mode=MODE, cost_state=state,
                project=item["project"], non_project_destination=item["destination"],
                employee=employee, work_date=item["work_date"], minutes=item["minutes"],
                note=item["note"], hourly_rate_cents=hourly, cost_cents=cost,
                rate_basis=basis, status=STATUS, submitted_by=member.user, token=item["token"])
            audit(member, "time.recorded" if cost is not None else "time.recorded_unpriced", row,
                  {"minutes": item["minutes"], "project_id": item["project"].pk if item["project"] else None,
                   "destination_id": item["destination"].pk if item["destination"] else None,
                   "cost_cents": cost})
            created += 1
        if cost is None and item["project"] is not None:
            unpriced += 1
    return {"created": created, "corrected": corrected, "unpriced": unpriced}


def price_pending_for_rate(member, rate):
    """Price unpriced project rows covered by a newly created rate; returns priced row count."""
    organization = rate.employee.organization
    rows = Timesheet.objects.filter(
        organization=organization, employee=rate.employee, accounting_mode=MODE,
        status=STATUS, project__isnull=False, cost_cents__isnull=True,
        work_date__gte=rate.effective_from)
    if organization.locked_through is not None:
        # A locked period is never restated by a later rate backfill.
        rows = rows.filter(work_date__gt=organization.locked_through)
    rows = rows.order_by("work_date", "id")
    priced = 0
    for row in rows:
        applicable = latest_rate(row.employee, row.work_date)
        if applicable is None:
            continue
        row.hourly_rate_cents = applicable.hourly_cents
        row.cost_cents = time_cost(applicable.hourly_cents, row.minutes)
        row.rate_basis = applicable.basis
        row.cost_state = "priced"
        row.version += 1
        row.save(update_fields=["hourly_rate_cents", "cost_cents", "rate_basis", "cost_state", "version"])
        audit(member, "time.priced", row,
              {"rate_id": applicable.pk, "cost_cents": row.cost_cents, "minutes": row.minutes})
        priced += 1
    return priced


def simplified_labor_cost(project):
    """Project labor cost from priced weekly rows; unpriced rows stay gaps, never zero."""
    return Timesheet.objects.filter(
        project=project, accounting_mode=MODE, status=STATUS,
        cost_cents__isnull=False).aggregate(total=Sum("cost_cents"))["total"] or 0


def simplified_labor_cost_days(project):
    return Timesheet.objects.filter(
        project=project, accounting_mode=MODE, status=STATUS,
        cost_cents__isnull=False).values_list("work_date", "cost_cents")


def simplified_gap_count(project):
    return Timesheet.objects.filter(
        project=project, accounting_mode=MODE, status=STATUS, cost_cents__isnull=True).count()


def missing_rate_gaps(member, limit=20):
    """Gap list: unpriced weekly rows visible to the caller; never renders a fake zero cost."""
    rows = Timesheet.objects.filter(
        organization=member.organization, accounting_mode=MODE, status=STATUS,
        project__isnull=False, cost_cents__isnull=True)
    if member.role == "member":
        rows = rows.filter(employee__member=member)
    elif member.role == "pm":
        rows = rows.filter(project__in=visible_projects(member).filter(manager=member))
    rows = rows.select_related("employee", "project").order_by("-work_date", "-id")
    return {
        "count": rows.count(),
        "minutes": rows.aggregate(total=Sum("minutes"))["total"] or 0,
        "employees": rows.values("employee_id").distinct().count(),
        "rows": list(rows[:limit]),
    }


def week_project_totals(member, week_start):
    """Project-level weekly totals for owners/PMs; aggregate only, never per-person rates."""
    start, end = week_bounds(week_start)
    projects = visible_projects(member)
    if member.role == "pm":
        projects = projects.filter(manager=member)
    rows = Timesheet.objects.filter(
        project__in=projects, accounting_mode=MODE, status=STATUS,
        work_date__range=(start, end)).values("project_id", "project__code", "project__name").annotate(
        # Aliases must not shadow model fields: an aggregate alias named like the field
        # it sums nests SUM(SUM(...)) on SQLite.
        minutes_total=Sum("minutes"), cost_total=Sum("cost_cents"),
        people=Count("employee_id", distinct=True),
        gaps=Count("id", filter=Q(cost_cents__isnull=True))).order_by("project__code")
    return [{
        "project_id": row["project_id"], "code": row["project__code"], "name": row["project__name"],
        "minutes": row["minutes_total"] or 0, "cost_cents": row["cost_total"] or 0,
        "people": row["people"], "gaps": row["gaps"] or 0,
    } for row in rows]


def own_week_totals(employee, week_start):
    start, end = week_bounds(week_start)
    rows = Timesheet.objects.filter(
        employee=employee, accounting_mode=MODE, status=STATUS, work_date__range=(start, end))
    priced = rows.filter(cost_cents__isnull=False).aggregate(
        minutes_total=Sum("minutes"), cost_total=Sum("cost_cents"))
    return {
        "minutes": rows.aggregate(total=Sum("minutes"))["total"] or 0,
        "priced_minutes": priced["minutes_total"] or 0,
        "cost_cents": priced["cost_total"] or 0,
        "gaps": rows.filter(project__isnull=False, cost_cents__isnull=True).count(),
    }
