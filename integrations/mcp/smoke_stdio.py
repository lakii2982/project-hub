#!/usr/bin/env python3
"""Smoke-test the adapter with the official MCP stdio client."""

import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED_TOOLS = {
    "get_project",
    "list_projects",
    "list_tasks",
    "submit_my_expense",
    "submit_my_timesheet",
    "update_my_task_status",
    "who_am_i",
}


async def main() -> None:
    folder = Path(__file__).resolve().parent
    project_root = folder.parents[1]
    parameters = StdioServerParameters(
        command=str(project_root / ".agent-venv/bin/python"),
        args=[str(folder / "server.py")],
        env={
            "PMO_BASE_URL": "https://pmo.example.com",
            "PMO_AGENT_TOKEN": "ph_" + "0" * 48,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        cwd=project_root,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {tool.name for tool in result.tools}
            if names != EXPECTED_TOOLS:
                raise AssertionError(f"unexpected tools: {sorted(names)}")
            print("7 tools:", ",".join(sorted(names)))


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=10))
