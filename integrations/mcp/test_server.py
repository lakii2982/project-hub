import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp.server.fastmcp.exceptions import ToolError

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from server import ApiConfig, ConfigurationError, create_server, validate_base_url


TOKEN = "ph_" + "a" * 48


class ConfigurationTests(unittest.TestCase):
    def test_http_is_limited_to_explicit_local_opt_in(self):
        with patch.dict(
            os.environ,
            {
                "PMO_BASE_URL": "http://127.0.0.1:8765",
                "PMO_AGENT_TOKEN": TOKEN,
            },
            clear=True,
        ):
            with self.assertRaises(ConfigurationError):
                ApiConfig.from_env()

        with patch.dict(
            os.environ,
            {
                "PMO_BASE_URL": "http://localhost:8765",
                "PMO_AGENT_TOKEN": TOKEN,
                "PMO_ALLOW_LOCAL_HTTP": "1",
            },
            clear=True,
        ):
            config = ApiConfig.from_env()

        self.assertEqual(config.base_url, "http://localhost:8765")
        self.assertEqual(config.token, TOKEN)

        with self.assertRaises(ConfigurationError):
            validate_base_url(
                "http://example.com", allow_local_http=True
            )

    def test_base_url_rejects_credentials_query_fragment_and_other_schemes(self):
        invalid_urls = [
            "file:///tmp/pmo",
            "http://example.com",
            "https://user:password@pmo.example.com",
            "https://pmo.example.com?token=unsafe",
            "https://pmo.example.com#fragment",
        ]

        for invalid_url in invalid_urls:
            with self.subTest(url=invalid_url):
                with self.assertRaises(ConfigurationError):
                    validate_base_url(invalid_url)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def make_server(self, handler):
        transport = httpx.MockTransport(handler)
        server = create_server(
            config=ApiConfig("https://pmo.example.com", TOKEN),
            transport=transport,
        )
        self.addCleanup(server.pmo_client.close)
        return server

    async def test_lists_exactly_seven_typed_tools_with_annotations(self):
        def handler(request):
            return httpx.Response(200, json={"schema_version": "api/v1"})

        server = self.make_server(handler)
        tools = {tool.name: tool for tool in await server.list_tools()}
        expected = {
            "who_am_i",
            "list_projects",
            "get_project",
            "list_tasks",
            "submit_my_timesheet",
            "submit_my_expense",
            "update_my_task_status",
        }

        self.assertEqual(set(tools), expected)
        self.assertEqual(
            tools["submit_my_timesheet"].inputSchema["properties"]["dry_run"][
                "default"
            ],
            True,
        )
        self.assertEqual(
            tools["submit_my_expense"].inputSchema["properties"]["dry_run"][
                "default"
            ],
            True,
        )
        self.assertEqual(
            tools["update_my_task_status"].inputSchema["properties"]["dry_run"][
                "default"
            ],
            True,
        )
        for tool in tools.values():
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertTrue(tool.annotations.idempotentHint)
            self.assertFalse(tool.annotations.openWorldHint)
            self.assertNotIn(TOKEN, json.dumps(tool.inputSchema))

        for name in {"who_am_i", "list_projects", "get_project", "list_tasks"}:
            self.assertTrue(tools[name].annotations.readOnlyHint)
        for name in {
            "submit_my_timesheet",
            "submit_my_expense",
            "update_my_task_status",
        }:
            self.assertFalse(tools[name].annotations.readOnlyHint)

    async def test_write_tool_defaults_to_dry_run_and_uses_private_env_token(self):
        captured = {}

        def handler(request):
            captured["request"] = request
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "schema_version": "api/v1",
                    "dry_run": True,
                    "valid": True,
                    "preview": {"project_id": 7, "amount_cents": 12345},
                },
            )

        server = self.make_server(handler)
        result = await server.call_tool(
            "submit_my_expense",
            {
                "project_id": 7,
                "title": "测试费用",
                "amount_cents": 12345,
                "occurred_on": "2025-01-10",
                "counterparty": "测试供应商",
                "reference": "EXP-1",
                "note": "dry-run边界测试",
                "idempotency_key": "expense-dry-run-001",
            },
        )

        request = captured["request"]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/api/v1/expenses")
        self.assertEqual(captured["body"]["dry_run"], True)
        self.assertEqual(
            request.headers["Authorization"], f"Bearer {TOKEN}"
        )
        self.assertEqual(
            request.headers["Idempotency-Key"], "expense-dry-run-001"
        )
        self.assertNotIn(TOKEN, repr(result))
        self.assertFalse(server.pmo_client.client.follow_redirects)

    async def test_redirect_is_rejected_without_a_second_request(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1/redirected"},
            )

        server = self.make_server(handler)
        with self.assertRaises(ToolError) as error:
            await server.call_tool("who_am_i", {})

        message = str(error.exception)
        self.assertIn("API 302", message)
        self.assertIn("不支持重定向", message)
        self.assertNotIn(TOKEN, message)
        self.assertEqual(calls, 1)

    async def test_api_error_redacts_token_and_authorization(self):
        def handler(request):
            return httpx.Response(
                401,
                json={
                    "detail": f"令牌 {TOKEN} 无效",
                    "authorization": f"Bearer {TOKEN}",
                },
            )

        server = self.make_server(handler)
        with self.assertRaises(ToolError) as error:
            await server.call_tool("who_am_i", {})

        message = str(error.exception)
        self.assertIn("API 401", message)
        self.assertIn("[REDACTED]", message)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn("Bearer " + TOKEN, message)

    async def test_success_payload_removes_personal_rate_fields(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "schema_version": "api/v1",
                    "hourly_rate_cents": 98765,
                    "nested": {
                        "rate_basis": "PRIVATE_RATE_BASIS_TEST",
                        "cost_cents": 100,
                    },
                },
            )

        server = self.make_server(handler)
        result = await server.call_tool("who_am_i", {})
        structured = result[1]

        self.assertNotIn("hourly_rate_cents", structured)
        self.assertEqual(structured["nested"], {"cost_cents": 100})
        self.assertNotIn("PRIVATE_RATE_BASIS_TEST", repr(result))

    async def test_strict_integer_input_is_rejected_before_network(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"schema_version": "api/v1"})

        server = self.make_server(handler)
        with self.assertRaises(ToolError):
            await server.call_tool("list_tasks", {"project_id": "1"})

        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
