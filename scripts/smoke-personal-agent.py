"""End-to-end MCP -> HTTP -> domain dry runs against the LOCAL DEMO only. No model calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
APP_PYTHON = str(ROOT / ".venv/bin/python")

def run_app(code):
    environment = dict(os.environ, DJANGO_SETTINGS_MODULE="pmo.settings")
    # A demo check must never inherit a production database selection from the shell.
    for name in ("POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_PORT"):
        environment.pop(name, None)
    environment["PMO_ENV"] = "development"
    environment["PMO_DB"] = str(ROOT / "runtime/pmo.sqlite3")
    result = subprocess.run([APP_PYTHON, "-c", "import django; django.setup(); " + code], cwd=ROOT,
                            env=environment, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

async def main():
    fixture = run_app('''from core.models import Membership, Task; from core.agent_auth import issue_token; from django.utils import timezone; import json; m=Membership.objects.select_related("organization","user").get(user__username="member", organization__is_demo=True); t=Task.objects.filter(project__organization=m.organization, owner=m, status="todo").first(); assert t is not None; token,raw=issue_token(m,"本地MCP连通性验证",["projects:read","tasks:read","time:write_self","expense:write_self","tasks:write_self"],[t.project_id],7); print(json.dumps({"token":raw,"token_id":token.pk,"member_id":m.pk,"project_id":t.project_id,"task_id":t.pk,"version":t.version,"day":str(timezone.localdate())}))''')
    key = str(uuid.uuid4())
    def check(result):
        if result.isError: raise RuntimeError("MCP tool call failed")
        data = result.structuredContent
        if data is None:
            data = json.loads(next(item.text for item in result.content if item.type == "text"))
        return data
    try:
        params = StdioServerParameters(command=str(ROOT / ".agent-venv/bin/python"), args=[str(ROOT / "integrations/mcp/server.py")],
            env={"PMO_BASE_URL": "http://127.0.0.1:8765", "PMO_AGENT_TOKEN": fixture["token"], "PMO_ALLOW_LOCAL_HTTP": "1"})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 7
                identity = check(await session.call_tool("who_am_i", {}))
                assert identity["role"] == "member"
                projects = check(await session.call_tool("list_projects", {}))
                assert [p["id"] for p in projects["projects"]] == [fixture["project_id"]]
                detail = check(await session.call_tool("get_project", {"project_id": fixture["project_id"]}))
                assert "finance" not in detail["project"]
                check(await session.call_tool("list_tasks", {"project_id": fixture["project_id"]}))
                time = check(await session.call_tool("submit_my_timesheet", {"project_id": fixture["project_id"], "work_date": fixture["day"],
                    "minutes": 30, "note": "MCP预校验-" + key, "idempotency_key": key, "dry_run": True}))
                assert time["dry_run"] and time["valid"]
                expense = check(await session.call_tool("submit_my_expense", {"project_id": fixture["project_id"], "title": "MCP费用预校验",
                    "amount_cents": 1234, "occurred_on": fixture["day"], "counterparty": "演练交易方", "reference": "MCP-" + key,
                    "note": "预校验不入账", "idempotency_key": key + "-expense", "dry_run": True}))
                assert expense["dry_run"] and expense["valid"]
                task = check(await session.call_tool("update_my_task_status", {"task_id": fixture["task_id"], "status": "doing",
                    "expected_version": fixture["version"], "completion_note": "预校验不更改任务", "idempotency_key": key + "-task", "dry_run": True}))
                assert task["dry_run"] and task["valid"]
        verification = run_app('from core.models import Timesheet,Document,Task; import json; print(json.dumps({"times":Timesheet.objects.filter(note="MCP预校验-' + key + '").count(),"expenses":Document.objects.filter(reference="MCP-' + key + '").count(),"version":Task.objects.get(pk=' + str(fixture["task_id"]) + ').version}))')
        assert verification == {"times": 0, "expenses": 0, "version": fixture["version"]}
        print(json.dumps({"ok": True, "tools": 7, "live_read_calls": 4, "dry_run_calls": 3, "business_writes": 0}, ensure_ascii=False))
    finally:
        run_app('from core.models import Membership; from core.agent_auth import revoke_token; import json; revoke_token(Membership.objects.get(pk=' + str(fixture["member_id"]) + '),' + str(fixture["token_id"]) + '); print(json.dumps({"revoked":True}))')

if __name__ == "__main__": asyncio.run(main())
