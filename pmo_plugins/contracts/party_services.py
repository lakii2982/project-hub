import hashlib
import json
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.access import require_member, require_write, visible_projects
from core.models import Membership
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company

from .models import Contact, Contract, Counterparty, normalize_display, normalized_key, normalized_roles

READ_ROLES = {"owner", "finance", "auditor", "pm"}
WRITE_ROLES = {"owner", "finance"}


def _fresh_member(member):
    try:
        fresh = Membership.objects.select_related("organization", "user").get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied("无效公司成员上下文") from error
    require_member(fresh)
    return fresh


def _require_enabled(member):
    if not is_enabled(member.organization, "contracts"):
        raise PermissionDenied("客户供应商与合同模块已停用")


def _reader(member):
    fresh = _fresh_member(member)
    _require_enabled(fresh)
    if fresh.role not in READ_ROLES:
        raise PermissionDenied("无权查看客户供应商与合同")
    return fresh


def _writer(member):
    fresh = _fresh_member(member)
    require_write(fresh)
    _require_enabled(fresh)
    if fresh.role not in WRITE_ROLES:
        raise PermissionDenied("只有公司负责人或项目财务可以维护客户供应商与合同")
    return fresh


def _digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _request_token(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("请求标识无效，请刷新表单后重试") from error


def _name(value, maximum):
    value = normalize_display(value)
    if not value or len(value) > maximum or len(normalized_key(value)) > 320:
        raise ValidationError(f"名称须为1至{maximum}个字符")
    return value


def _version(value):
    if type(value) is not int or value < 1:
        raise ValidationError("记录版本无效，请刷新后重试")
    return value


def _business_date(value, label, required=True):
    if value is None and not required:
        return None
    if type(value) is not date:
        raise ValidationError(f"{label}无效")
    return value


def _party_values(display_name, roles, active_from, inactive_on):
    display_name = _name(display_name, 160)
    if not isinstance(roles, (list, tuple)) or any(not isinstance(role, str) for role in roles):
        raise ValidationError("业务角色无效")
    roles = normalized_roles(roles)
    if not roles or any(role not in {"customer", "supplier"} for role in roles):
        raise ValidationError("至少选择客户或供应商角色，且角色不可重复")
    active_from = _business_date(active_from, "有效起始日")
    inactive_on = _business_date(inactive_on, "失效日期", required=False)
    if inactive_on and (inactive_on < active_from or inactive_on < timezone.localdate()):
        raise ValidationError("有效记录不能使用过去的失效日期")
    return {
        "display_name": display_name,
        "roles": roles,
        "active_from": active_from,
        "inactive_on": inactive_on,
    }


def _party_payload(values):
    return {
        "display_name": normalized_key(values["display_name"]),
        "roles": values["roles"],
        "active_from": values["active_from"].isoformat(),
        "inactive_on": values["inactive_on"].isoformat() if values["inactive_on"] else None,
    }


def _idempotent_row(model, member, token, digest):
    row = model.objects.filter(organization=member.organization, create_token=token).first()
    if row is None:
        return None
    if row.create_digest != digest:
        raise ValidationError("此请求标识已用于不同输入，请刷新后重新提交")
    return row


def _active_party(member, party_id, *, lock=False):
    queryset = Counterparty.objects.filter(organization=member.organization)
    if lock:
        queryset = queryset.select_for_update()
    try:
        party = queryset.get(pk=party_id)
    except (Counterparty.DoesNotExist, ValueError, ValidationError) as error:
        raise ValidationError("客户／供应商不存在或不可用") from error
    today = timezone.localdate()
    if party.status != Counterparty.Status.ACTIVE or party.active_from > today or (party.inactive_on and party.inactive_on < today):
        raise ValidationError("客户／供应商当前不可用")
    return party


def _party_for_read(member, party_id):
    try:
        party = Counterparty.objects.select_related("organization").get(organization=member.organization, pk=party_id)
    except (Counterparty.DoesNotExist, ValueError, ValidationError) as error:
        raise PermissionDenied("记录不存在或无权访问") from error
    if member.role == "pm":
        allowed = Contract.objects.filter(
            party=party,
            project__in=visible_projects(member),
        ).exists()
        if not allowed:
            raise PermissionDenied("记录不存在或无权访问")
    return party


def list_parties(member, *, query="", role=""):
    member = _reader(member)
    rows = Counterparty.objects.filter(organization=member.organization).prefetch_related("contacts")
    if member.role == "pm":
        rows = rows.filter(contracts__project__in=visible_projects(member)).distinct()
    query = normalize_display(query)
    if query:
        rows = rows.filter(display_name__icontains=query)
    if role in {"customer", "supplier"}:
        rows = rows.filter(pk__in=[row.pk for row in rows if role in row.roles])
    elif role:
        rows = rows.none()
    return rows


def get_party(member, party_id):
    return _party_for_read(_reader(member), party_id)


@transaction.atomic
def create_party(member, *, display_name, roles, active_from, inactive_on=None, request_token):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    values = _party_values(display_name, roles, active_from, inactive_on)
    token = _request_token(request_token)
    payload = _party_payload(values)
    digest = _digest(payload)
    existing = _idempotent_row(Counterparty, member, token, digest)
    if existing:
        return existing
    if Counterparty.objects.filter(organization=member.organization, normalized_name=payload["display_name"]).exists():
        raise ValidationError("同名客户／供应商已存在，请选择原记录；系统不会自动合并")
    party = Counterparty(
        organization=member.organization,
        **values,
        create_token=token,
        create_digest=digest,
    )
    try:
        with transaction.atomic():
            party.full_clean()
            party.save()
            audit(member, "contracts.counterparty.created", party, payload)
    except IntegrityError as error:
        raise ValidationError("同名客户／供应商已存在，请选择原记录") from error
    return party


@transaction.atomic
def update_party(member, party_id, *, display_name, roles, active_from, inactive_on=None, expected_version):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    expected_version = _version(expected_version)
    party = _party_for_write(member, party_id)
    if party.status != Counterparty.Status.ACTIVE:
        raise ValidationError("只能编辑有效客户／供应商")
    if party.version != expected_version:
        raise ValidationError("记录已被其他人更新，请刷新后重试")
    values = _party_values(display_name, roles, active_from, inactive_on)
    if "customer" not in values["roles"] and party.contracts.exists():
        raise ValidationError("该记录已被合同引用，不能移除客户角色")
    normalized = normalized_key(values["display_name"])
    if Counterparty.objects.filter(organization=member.organization, normalized_name=normalized).exclude(pk=party.pk).exists():
        raise ValidationError("同名客户／供应商已存在，请选择原记录；系统不会自动合并")
    for key, value in values.items():
        setattr(party, key, value)
    party.version += 1
    party.full_clean()
    party.save(update_fields=["display_name", "normalized_name", "roles", "active_from", "inactive_on", "version"])
    audit(member, "contracts.counterparty.updated", party, {"version": party.version, **_party_payload(values)})
    return party


def _party_for_write(member, party_id):
    try:
        return Counterparty.objects.select_for_update().get(organization=member.organization, pk=party_id)
    except (Counterparty.DoesNotExist, ValueError, ValidationError) as error:
        raise PermissionDenied("记录不存在或无权访问") from error


def _contact_values(name, email, phone):
    name = _name(name, 100)
    email = str(email or "").strip()
    phone = str(phone or "").strip()
    if len(phone) > 40:
        raise ValidationError("联系电话最多40个字符")
    return {"name": name, "email": email, "phone": phone}


def _contact_payload(party, values):
    return {
        "party_id": str(party.pk),
        "name": normalized_key(values["name"]),
        "email": values["email"].casefold(),
        "phone": values["phone"],
    }


@transaction.atomic
def create_contact(member, *, party_id, name, email="", phone="", request_token):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    values = _contact_values(name, email, phone)
    token = _request_token(request_token)
    party = _active_party(member, party_id, lock=True)
    payload = _contact_payload(party, values)
    digest = _digest(payload)
    existing = _idempotent_row(Contact, member, token, digest)
    if existing:
        return existing
    party = _active_party(member, party_id, lock=True)
    payload = _contact_payload(party, values)
    contact = Contact(
        organization=member.organization,
        party=party,
        **values,
        create_token=token,
        create_digest=digest,
    )
    try:
        with transaction.atomic():
            contact.full_clean()
            contact.save()
            audit(member, "contracts.contact.created", contact, payload)
    except IntegrityError as error:
        raise ValidationError("同名联系人已存在，请选择原记录") from error
    return contact


@transaction.atomic
def update_contact(member, contact_id, *, name, email="", phone="", expected_version):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    expected_version = _version(expected_version)
    try:
        contact = Contact.objects.select_for_update().select_related("party").get(
            organization=member.organization,
            pk=contact_id,
        )
    except (Contact.DoesNotExist, ValueError, ValidationError) as error:
        raise PermissionDenied("记录不存在或无权访问") from error
    if contact.status != Contact.Status.ACTIVE:
        raise ValidationError("只能编辑有效联系人")
    if contact.version != expected_version:
        raise ValidationError("记录已被其他人更新，请刷新后重试")
    _active_party(member, contact.party_id)
    values = _contact_values(name, email, phone)
    for key, value in values.items():
        setattr(contact, key, value)
    contact.version += 1
    contact.full_clean()
    contact.save(update_fields=["name", "normalized_name", "email", "phone", "version"])
    audit(member, "contracts.contact.updated", contact, {"version": contact.version, **_contact_payload(contact.party, values)})
    return contact
