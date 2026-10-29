from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.access import require_project, visible_projects
from core.models import Project
from core.services import audit, lock_company

from .models import Contract, Counterparty, normalize_display
from .party_services import (
    _active_party,
    _business_date,
    _digest,
    _idempotent_row,
    _reader,
    _request_token,
    _version,
    _writer,
)


def _contract_code(value):
    value = normalize_display(value)
    if not value or len(value) > 80:
        raise ValidationError("合同编号须为1至80个字符")
    return value


def _signed_on(value):
    value = _business_date(value, "签署日期", required=False)
    if value and value > timezone.localdate():
        raise ValidationError("签署日期不能在未来")
    return value


def _payload(project_id, party_id, contract_code, signed_on):
    return {
        "project_id": str(project_id),
        "party_id": str(party_id),
        "contract_code": contract_code,
        "signed_on": signed_on.isoformat() if signed_on else None,
    }


def _project(member, project_id, *, lock=False):
    queryset = Project.objects.filter(organization=member.organization)
    if lock:
        queryset = queryset.select_for_update()
    try:
        project = queryset.get(pk=project_id)
    except (Project.DoesNotExist, ValueError, ValidationError) as error:
        raise ValidationError("项目不存在或不可用") from error
    require_project(member, project)
    return project


def _contract_for_read(member, contract_id):
    try:
        contract = Contract.objects.select_related("project", "party", "organization").get(
            organization=member.organization,
            pk=contract_id,
        )
    except (Contract.DoesNotExist, ValueError, ValidationError) as error:
        raise PermissionDenied("记录不存在或无权访问") from error
    if member.role == "pm":
        require_project(member, contract.project)
    return contract


def list_contracts(member, *, query=""):
    member = _reader(member)
    rows = Contract.objects.filter(organization=member.organization).select_related("project", "party")
    if member.role == "pm":
        rows = rows.filter(project__in=visible_projects(member))
    query = normalize_display(query)
    if query:
        from django.db.models import Q
        rows = rows.filter(
            Q(contract_code__icontains=query)
            | Q(party__display_name__icontains=query)
            | Q(project__name__icontains=query)
            | Q(project__code__icontains=query)
        )
    return rows


def get_contract(member, contract_id):
    return _contract_for_read(_reader(member), contract_id)


@transaction.atomic
def create_contract_draft(member, *, project_id, party_id, contract_code, signed_on=None, request_token):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    contract_code = _contract_code(contract_code)
    signed_on = _signed_on(signed_on)
    token = _request_token(request_token)
    project = _project(member, project_id, lock=True)
    party = _active_party(member, party_id, lock=True)
    if "customer" not in (party.roles or []):
        raise ValidationError("合同往来方必须具有客户角色")
    payload = _payload(project.pk, party.pk, contract_code, signed_on)
    digest = _digest(payload)
    existing = _idempotent_row(Contract, member, token, digest)
    if existing:
        return existing
    project = _project(member, project_id, lock=True)
    party = _active_party(member, party_id, lock=True)
    if "customer" not in (party.roles or []):
        raise ValidationError("合同往来方必须具有客户角色")
    if Contract.objects.filter(organization=member.organization, contract_code=contract_code).exists():
        raise ValidationError("此合同编号已存在，请打开原合同或更换编号")
    contract = Contract(
        organization=member.organization,
        project=project,
        party=party,
        contract_code=contract_code,
        signed_on=signed_on,
        status=Contract.Status.DRAFT,
        parent_ref=None,
        create_token=token,
        create_digest=digest,
    )
    try:
        with transaction.atomic():
            contract.full_clean()
            contract.save()
            audit(member, "contracts.contract_draft.created", contract, payload)
    except IntegrityError as error:
        raise ValidationError("此合同编号已存在，请打开原合同或更换编号") from error
    return contract


@transaction.atomic
def update_contract_draft(member, contract_id, *, project_id, party_id, contract_code, signed_on=None, expected_version):
    member = _writer(member)
    lock_company(member)
    member = _writer(member)
    expected_version = _version(expected_version)
    try:
        contract = Contract.objects.select_for_update().get(organization=member.organization, pk=contract_id)
    except (Contract.DoesNotExist, ValueError, ValidationError) as error:
        raise PermissionDenied("记录不存在或无权访问") from error
    if contract.versions.filter(status="submitted").exists():
        raise ValidationError("合同已提交审批，不能修改合同头")
    if contract.status != Contract.Status.DRAFT:
        raise ValidationError("只能编辑草稿合同；生效、终止或替代合同不可通过此服务修改")
    if contract.version != expected_version:
        raise ValidationError("记录已被其他人更新，请刷新后重试")
    contract_code = _contract_code(contract_code)
    signed_on = _signed_on(signed_on)
    project = _project(member, project_id, lock=True)
    party = _active_party(member, party_id, lock=True)
    if "customer" not in (party.roles or []):
        raise ValidationError("合同往来方必须具有客户角色")
    if Contract.objects.filter(organization=member.organization, contract_code=contract_code).exclude(pk=contract.pk).exists():
        raise ValidationError("此合同编号已存在，请打开原合同或更换编号")
    contract.project = project
    contract.party = party
    contract.contract_code = contract_code
    contract.signed_on = signed_on
    contract.version += 1
    contract.full_clean()
    contract.save(update_fields=["project", "party", "contract_code", "signed_on", "version"])
    audit(
        member,
        "contracts.contract_draft.updated",
        contract,
        {"version": contract.version, **_payload(project.pk, party.pk, contract_code, signed_on)},
    )
    return contract
