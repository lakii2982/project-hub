"""Personal, expiring, project-bound capabilities. Tokens never inherit administrative actions."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .access import visible_projects, require_member, can_read_finance
from .models import AgentToken, Membership
from .services import lock_company, audit

SCOPE_CHOICES = [
    ("projects:read", "查询授权项目"), ("tasks:read", "查询项目任务"),
    ("time:write_self", "填报本人工时（提交待审批）"),
    ("expense:write_self", "提交本人项目费用（提交待审批）"),
    ("tasks:write_self", "更新本人负责的任务状态"),
    ("finance:read_summary", "查询授权项目的财务汇总"),
]

def token_digest(raw):
    return hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()

def allowed_scopes(member):
    result = {key for key, unused in SCOPE_CHOICES}
    if member.role == "auditor": result -= {"time:write_self", "expense:write_self", "tasks:write_self"}
    if not can_read_finance(member): result.discard("finance:read_summary")
    return result

@transaction.atomic
def issue_token(member, name, scopes, project_ids, days=30):
    lock_company(member)
    require_member(member)
    from .plugin_runtime import is_enabled
    if not is_enabled(member.organization, "agent_gateway"):
        raise ValidationError("个人 Agent 接入插件已停用，无法建立新接入")
    if not name.strip() or len(name) > 80: raise ValidationError("请填写80字以内的接入名称")
    if not scopes or not set(scopes) <= allowed_scopes(member): raise ValidationError("授权范围超出当前身份权限")
    if type(days) is not int or days not in {7, 30, 90}: raise ValidationError("有效期只能为7、30或90天")
    ids = sorted(set(project_ids))
    permitted = set(visible_projects(member).values_list("id", flat=True))
    if not ids or not set(ids) <= permitted: raise ValidationError("只能授权当前可访问的项目，至少选择一个")
    if AgentToken.objects.filter(member=member, revoked_at__isnull=True, expires_at__gt=timezone.now()).count() >= 10:
        raise ValidationError("最多保留10个有效接入，请先撤销不用的令牌")
    raw = "ph_" + secrets.token_urlsafe(36)
    token = AgentToken.objects.create(member=member, name=name, prefix=raw[:12], token_hash=token_digest(raw),
        auth_snapshot=member.user.get_session_auth_hash(), scopes=sorted(scopes), project_ids=ids,
        expires_at=timezone.now() + timedelta(days=days))
    audit(member, "agent_token.created", token, {"name": name, "scopes": token.scopes, "project_ids": ids, "expires_at": token.expires_at})
    return token, raw

@transaction.atomic
def revoke_token(member, pk):
    lock_company(member)
    token = AgentToken.objects.get(pk=pk, member=member)
    if token.revoked_at: return token
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    audit(member, "agent_token.revoked", token, {"name": token.name})
    return token
