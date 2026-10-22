"""Bounded personal-agent API. Browser forms and API call the same domain services."""
import hashlib
import json
import re
import secrets
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError as DomainValidation, PermissionDenied as DomainDenied
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler
from .access import visible_projects, can_read_finance
from .agent_auth import token_digest, allowed_scopes
from .models import AgentToken, AgentOperation, Employee, Task, Membership
from .services import submit_time, submit_document, update_task, lock_company, audit, MAX_MONEY
from .reporting import project_summary

class Conflict(exceptions.APIException):
    status_code = 409
    default_detail = "相同来源标识的内容已改变，请核对后使用更正流程"
    default_code = "idempotency_conflict"

class PersonalAgentAuthentication(BaseAuthentication):
    def authenticate_header(self, request): return 'Bearer realm="project-hub"'
    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header: return None
        if len(header) != 2 or header[0].lower() != b"bearer": raise exceptions.AuthenticationFailed("令牌无效或已失效")
        try: raw = header[1].decode("ascii")
        except UnicodeDecodeError: raise exceptions.AuthenticationFailed("令牌无效或已失效")
        if not re.fullmatch(r"ph_[A-Za-z0-9_-]{48}", raw): raise exceptions.AuthenticationFailed("令牌无效或已失效")
        now = timezone.now()
        with transaction.atomic():
            token = AgentToken.objects.select_for_update().filter(token_hash=token_digest(raw), revoked_at__isnull=True, expires_at__gt=now).first()
            if not token: raise exceptions.AuthenticationFailed("令牌无效或已失效")
            member = Membership.objects.select_related("user", "organization").get(pk=token.member_id)
            if not member.active or not member.user.is_active or not secrets.compare_digest(token.auth_snapshot, member.user.get_session_auth_hash()):
                raise exceptions.AuthenticationFailed("令牌无效或已失效")
            if now - token.window_start >= timedelta(minutes=1): token.window_start, token.window_count = now, 0
            if token.window_count >= 60: raise exceptions.Throttled(wait=60)
            token.window_count += 1
            token.last_used_at = now
            token.save(update_fields=["window_start", "window_count", "last_used_at"])
            token.member = member
        return member.user, token

class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict): raise serializers.ValidationError("请求须为JSON对象")
        extra = set(data) - set(self.fields)
        if extra: raise serializers.ValidationError({"unknown_fields": sorted(extra)})
        return super().to_internal_value(data)

class StrictInteger(serializers.IntegerField):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_value", 2**63 - 1)
        super().__init__(**kwargs)
    def to_internal_value(self, data):
        if type(data) is not int: raise serializers.ValidationError("须为整数，不能使用字符串、小数或布尔值")
        return super().to_internal_value(data)

class StrictBoolean(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool: raise serializers.ValidationError("须为JSON布尔值")
        return data

class TimeInput(StrictSerializer):
    project_id = StrictInteger(min_value=1)
    task_id = StrictInteger(min_value=1, required=False, allow_null=True)
    work_date = serializers.DateField(input_formats=["%Y-%m-%d"])
    minutes = StrictInteger(min_value=1, max_value=1440)
    note = serializers.CharField(min_length=1, max_length=300)
    dry_run = StrictBoolean(default=True)

class ExpenseInput(StrictSerializer):
    project_id = StrictInteger(min_value=1)
    title = serializers.CharField(min_length=1, max_length=160)
    amount_cents = StrictInteger(min_value=1, max_value=MAX_MONEY)
    tax_cents = StrictInteger(min_value=0, max_value=MAX_MONEY, default=0)
    recoverable_tax = StrictBoolean(default=False)
    occurred_on = serializers.DateField(input_formats=["%Y-%m-%d"])
    counterparty = serializers.CharField(max_length=120, allow_blank=True, default="")
    reference = serializers.CharField(min_length=1, max_length=240)
    note = serializers.CharField(max_length=8000, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=["vendor", "travel", "software", "equipment", "overhead", "other"], default="other")
    dry_run = StrictBoolean(default=True)
    def validate(self, data):
        if data["tax_cents"] > data["amount_cents"]: raise serializers.ValidationError("税额不能超过含税金额")
        return data

class TaskStatusInput(StrictSerializer):
    status = serializers.ChoiceField(choices=Task.Status.values)
    expected_version = StrictInteger(min_value=1)
    completion_note = serializers.CharField(max_length=2000, allow_blank=True, default="")
    dry_run = StrictBoolean(default=True)

class AgentAPIView(APIView):
    authentication_classes = [PersonalAgentAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    required_scope = None
    http_method_names = ["get", "post", "head", "options"]
    def initial(self, request, *args, **kwargs):
        self.request_id = uuid.uuid4().hex
        super().initial(request, *args, **kwargs)
        from .plugin_runtime import is_enabled
        if not is_enabled(request.auth.member.organization, "agent_gateway"):
            raise exceptions.NotFound("个人 Agent 接入插件已停用")
        if self.required_scope and (self.required_scope not in request.auth.scopes or self.required_scope not in allowed_scopes(request.auth.member)):
            raise exceptions.PermissionDenied("个人接入未获此操作范围，或当前身份权限已改变")
    def handle_exception(self, error):
        if isinstance(error, DomainValidation): error = exceptions.ValidationError(error.messages)
        elif isinstance(error, DomainDenied): error = exceptions.PermissionDenied(str(error))
        return super().handle_exception(error)
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["X-Request-ID"] = getattr(self, "request_id", "")
        return response
    def projects(self):
        return visible_projects(self.request.auth.member).filter(pk__in=self.request.auth.project_ids)
    def project(self, pk): return get_object_or_404(self.projects(), pk=pk)
    def read_audit(self, obj, action):
        audit(self.request.auth.member, action, obj, {"token_id": self.request.auth.pk, "request_id": self.request_id})

class MeView(AgentAPIView):
    def get(self, request):
        token = request.auth
        self.read_audit(token, "agent.identity_read")
        return Response({"schema_version": "api/v1", "user": request.user.get_full_name() or request.user.username,
            "role": token.member.role, "organization": token.member.organization.name,
            "scopes": sorted(set(token.scopes) & allowed_scopes(token.member)), "project_ids": list(self.projects().values_list("id", flat=True)),
            "expires_at": token.expires_at.isoformat()})

class ProjectsView(AgentAPIView):
    required_scope = "projects:read"
    def get(self, request):
        qs = self.projects().order_by("id")
        query = request.query_params.get("q", "").strip()[:100]
        if query:
            from django.db.models import Q
            qs = qs.filter(Q(code__icontains=query) | Q(name__icontains=query))
        self.read_audit(request.auth, "agent.projects_read")
        return Response({"schema_version": "api/v1", "projects": [{"id": p.pk, "code": p.code, "name": p.name,
            "stage": p.stage, "start_date": str(p.start_date), "end_date": str(p.end_date)} for p in qs[:100]]})

class ProjectView(AgentAPIView):
    required_scope = "projects:read"
    def get(self, request, pk):
        project = self.project(pk)
        result = {"id": project.pk, "code": project.code, "name": project.name, "stage": project.stage,
            "scope": project.scope, "acceptance_criteria": project.acceptance_criteria, "start_date": str(project.start_date), "end_date": str(project.end_date),
            "milestones": [{"id": m.id, "name": m.name, "due_date": str(m.due_date), "accepted": bool(m.accepted_at)} for m in project.milestones.all()[:100]]}
        if "finance:read_summary" in request.auth.scopes and can_read_finance(request.auth.member, project):
            summary = project_summary(project)
            result["finance"] = {k: v for k, v in summary.items() if k.endswith("_cents")}
            result["finance"]["currency"] = "CNY"
        self.read_audit(project, "agent.project_read")
        return Response({"schema_version": "api/v1", "project": result})

class TasksView(AgentAPIView):
    required_scope = "tasks:read"
    def get(self, request, pk):
        project = self.project(pk)
        rows = project.tasks.select_related("owner__user")
        self.read_audit(project, "agent.tasks_read")
        return Response({"schema_version": "api/v1", "tasks": [{"id": t.pk, "name": t.name, "status": t.status,
            "owner": str(t.owner), "owned_by_me": t.owner_id == request.auth.member_id, "due_date": str(t.due_date),
            "version": t.version, "dependency_id": t.dependency_id} for t in rows[:200]]})


def execute_command(request, operation, data, function):
    token = request.auth
    member = token.member
    dry_run = data.pop("dry_run")
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,100}", key): raise exceptions.ValidationError("请提供8至100字符的Idempotency-Key；同一业务重试须复用同一标识")
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    post_token = uuid.uuid5(uuid.NAMESPACE_URL, f"agent:{member.organization_id}:{member.user_id}:{operation}:{key}")
    with transaction.atomic():
        lock_company(member)
        current = AgentToken.objects.select_related("member__user").get(pk=token.pk)
        if (current.revoked_at or current.expires_at <= timezone.now() or not current.member.active or not current.member.user.is_active
            or not secrets.compare_digest(current.auth_snapshot, current.member.user.get_session_auth_hash())):
            raise exceptions.AuthenticationFailed("授权已经改变，请重新连接")
        request.auth.member = current.member
        required = {"time_submitted": "time:write_self", "expense_submitted": "expense:write_self", "task_status_updated": "tasks:write_self"}[operation]
        if required not in current.scopes or required not in allowed_scopes(current.member):
            raise exceptions.PermissionDenied("当前身份权限已改变")
        project_id = data.get("project_id")
        if project_id and (project_id not in current.project_ids or not visible_projects(current.member).filter(pk=project_id).exists()):
            raise exceptions.PermissionDenied("项目授权已经改变")
        existing = AgentOperation.objects.filter(member=member, operation=operation, key_hash=key_hash).first()
        if existing:
            if existing.payload_hash != digest: raise Conflict()
            if dry_run:
                return {"schema_version": "api/v1", "dry_run": True, "valid": True, "already_submitted": True, "preview": existing.result}, True
            return existing.result, False
        row, result = function(data, post_token)
        if dry_run:
            transaction.set_rollback(True)
            return {"schema_version": "api/v1", "dry_run": True, "valid": True, "preview": result}, True
        result = {"schema_version": "api/v1", "dry_run": False, **result}
        AgentOperation.objects.create(member=member, operation=operation, key_hash=key_hash, payload_hash=digest, result=result)
        audit(member, f"agent.{operation}", row, {"token_id": token.pk, "request_id": getattr(request, "pmo_request_id", ""), "key_hash": key_hash})
        return result, False

class TimesheetView(AgentAPIView):
    required_scope = "time:write_self"
    def post(self, request):
        serializer = TimeInput(data=request.data); serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        project = self.project(data["project_id"])
        employee = get_object_or_404(Employee, member=request.auth.member, active=True)
        task = get_object_or_404(Task, pk=data["task_id"], project=project) if data.get("task_id") else None
        request.pmo_request_id = self.request_id
        def apply(values, key):
            row = submit_time(request.auth.member, {"project": project, "employee": employee, "task": task,
                "work_date": values["work_date"], "minutes": values["minutes"], "note": values["note"]}, key)
            return row, {"id": row.pk, "status": row.status, "project_id": project.pk, "work_date": str(row.work_date), "minutes": row.minutes}
        result, dry = execute_command(request, "time_submitted", data, apply)
        return Response(result, status=200 if dry else 202)

class ExpensesView(AgentAPIView):
    required_scope = "expense:write_self"
    def post(self, request):
        serializer = ExpenseInput(data=request.data); serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        project = self.project(data["project_id"])
        request.pmo_request_id = self.request_id
        def apply(values, key):
            payload = {k: v for k, v in values.items() if k != "project_id"}
            row = submit_document(request.auth.member, {**payload, "project": project, "kind": "expense"}, key)
            return row, {"id": row.pk, "status": row.status, "project_id": project.pk, "title": row.title, "amount_cents": row.amount_cents, "currency": "CNY"}
        result, dry = execute_command(request, "expense_submitted", data, apply)
        return Response(result, status=200 if dry else 202)

class TaskStatusView(AgentAPIView):
    required_scope = "tasks:write_self"
    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk, project__in=self.projects(), owner=request.auth.member)
        serializer = TaskStatusInput(data=request.data); serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        data["task_id"] = pk
        data["project_id"] = task.project_id
        request.pmo_request_id = self.request_id
        def apply(values, key):
            current_task = get_object_or_404(Task, pk=task.pk, owner=request.auth.member)
            row = update_task(request.auth.member, current_task.pk, values["status"], values["expected_version"], values["completion_note"])
            return row, {"id": row.pk, "status": row.status, "version": row.version, "project_id": row.project_id}
        result, dry = execute_command(request, "task_status_updated", data, apply)
        return Response(result)
