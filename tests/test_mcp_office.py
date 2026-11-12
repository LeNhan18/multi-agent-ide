import asyncio
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from backend.app.api.mcp import router, provider_call


class OfficeMcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MCP_BRIDGE_TOKEN": "b" * 40, "MCP_WORD_WORKSPACE": self.tmp.name})
        self.env.start()
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.headers = {"Authorization": "Bearer " + "b" * 40, "MCP-Protocol-Version": "2025-06-18"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env.stop()
        self.tmp.cleanup()

    async def rpc(self, method, params=None, service="word", headers=None):
        return await self.client.post("/mcp/" + service, headers=headers if headers is not None else self.headers,
                                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    async def test_auth_origin_and_schema(self):
        self.assertEqual((await self.rpc("tools/list", headers={})).status_code, 401)
        self.assertEqual((await self.rpc("tools/list", headers={**self.headers, "Origin": "https://evil.test"})).status_code, 403)
        self.assertEqual((await self.rpc("initialize")).json()["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len((await self.rpc("tools/list")).json()["result"]["tools"]), 3)
        res = await self.rpc("tools/call", {"name": "word_read", "arguments": {"path": 123}})
        self.assertEqual(res.json()["error"]["code"], -32602)
        res = await self.rpc("tools/call", {"name": "word_create_draft", "arguments": {"title": "t", "text": "x"}})
        self.assertIn("confirmation", res.json()["error"]["message"])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_invalid_header_and_json_do_not_raise_server_errors(self):
        response = await self.client.post(
            "/mcp/word", headers=[(b"authorization", b"Bearer \xff")], content=b"{}",
        )
        self.assertEqual(response.status_code, 401)
        invalid_json = b'{"jsonrpc":"2.0","id":NaN,"method":"initialize"}'
        response = await self.client.post("/mcp/word", headers=self.headers, content=invalid_json)
        self.assertEqual(response.status_code, 400)
        response = await self.client.post(
            "/mcp/word", headers=self.headers,
            json={"jsonrpc": "2.0", "id": [], "method": "initialize"},
        )
        self.assertEqual(response.json()["error"]["code"], -32600)
        self.assertIsNone(response.json()["id"])

    async def test_corrupt_docx_returns_tool_error(self):
        malformed = Path(self.tmp.name) / "invalid.docx"
        with zipfile.ZipFile(malformed, "w") as archive:
            archive.writestr("unrelated.txt", "Not a Word document")
        response = await self.rpc(
            "tools/call", {"name": "word_read", "arguments": {"path": malformed.name}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["isError"])

    async def test_word_roundtrip_and_isolation(self):
        args = {"title": "Báo cáo AI Office", "text": "Nội dung kiểm chứng"}
        response = await self.rpc("tools/call", {"name": "word_create_draft", "arguments": args, "_meta": {"ai-office/confirmed": True}})
        self.assertFalse(response.json()["result"]["isError"])
        path = json.loads(response.json()["result"]["content"][0]["text"])["path"]
        self.assertTrue(path.startswith(".agent-drafts/"))
        res = await self.rpc("tools/call", {"name": "word_read", "arguments": {"path": path}})
        self.assertIn("Nội dung kiểm chứng", res.json()["result"]["content"][0]["text"])
        res = await self.rpc("tools/call", {"name": "word_read", "arguments": {"path": "../outside.docx"}})
        self.assertTrue(res.json()["result"]["isError"])
        second = await self.rpc("tools/call", {"name": "word_create_draft", "arguments": args, "_meta": {"ai-office/confirmed": True}})
        self.assertNotEqual(path, json.loads(second.json()["result"]["content"][0]["text"])["path"])

    async def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as other:
            (Path(self.tmp.name) / ".agent-drafts").symlink_to(other, target_is_directory=True)
            res = await self.rpc("tools/call", {"name": "word_create_draft", "arguments": {"title": "t", "text": "x"}, "_meta": {"ai-office/confirmed": True}})
            self.assertTrue(res.json()["result"]["isError"])
            self.assertEqual(list(Path(other).iterdir()), [])

    async def test_provider_requests_are_bounded_and_draft_only(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"id": "example", "isDraft": True})
        original = httpx.AsyncClient
        with patch.dict(os.environ, {"NOTION_API_KEY": "notion-secret", "OUTLOOK_ACCESS_TOKEN": "graph-secret"}), patch("backend.app.api.mcp.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
            await provider_call("outlook", "outlook_create_draft", {"subject": "Test", "text": "Draft", "to": "person@example.com"})
            self.assertEqual(str(calls[-1].url), "https://graph.microsoft.com/v1.0/me/messages")
            self.assertEqual(calls[-1].method, "POST")
            self.assertEqual(json.loads(calls[-1].content)["body"]["contentType"], "Text")
            await provider_call("notion", "notion_search", {"query": "Roadmap"})
            self.assertEqual(json.loads(calls[-1].content)["page_size"], 20)
            self.assertEqual(calls[-1].headers["Notion-Version"], "2025-09-03")
            with self.assertRaises(ValueError):
                await provider_call("notion", "notion_read", {"page_id": "../escape"})
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
