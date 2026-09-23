#!/usr/bin/env python3
"""Protocol and safety tests for the local Responses API router."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import httpx
import zstandard

sys.path.insert(0, os.path.dirname(__file__))

import responses_router  # noqa: E402
import router_config  # noqa: E402
from route_store import RouteStore  # noqa: E402


class PayloadTests(unittest.TestCase):
    def test_extracts_only_latest_user_turn(self):
        payload = {
            "input": [
                {"role": "user", "content": "旧任务"},
                {"role": "assistant", "content": "旧回复"},
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "分析网络异常根因"}],
                },
            ]
        }
        self.assertEqual(
            responses_router.extract_latest_user_task(payload), "分析网络异常根因"
        )

    def test_rewrites_only_model_and_effort(self):
        payload = {
            "model": "gpt-6-luna",
            "input": "分析网络异常根因",
            "instructions": "keep me",
            "previous_response_id": "resp_previous",
            "tools": [
                {"type": "shell"},
                {"type": "apply_patch"},
                {"type": "mcp", "server_label": "example"},
            ],
            "reasoning": {"effort": "low", "summary": "auto"},
            "future_field": {"preserve": True},
        }
        routed, record = responses_router.route_payload(payload)
        self.assertEqual(routed["model"], "gpt-6-sol")
        self.assertEqual(routed["reasoning"], {"effort": "medium", "summary": "auto"})
        for key in (
            "instructions",
            "previous_response_id",
            "tools",
            "future_field",
            "input",
        ):
            self.assertEqual(routed[key], payload[key])
        self.assertEqual(record["model"], "sol")
        self.assertNotIn("task", record)

    def test_model_effort_matrix(self):
        cases = (
            ("更新 TTD", "gpt-6-luna", "xhigh"),
            ("分析网络异常根因", "gpt-6-sol", "medium"),
            (
                "设计大型跨系统迁移架构，要求零停机、容灾和完整回滚",
                "gpt-6-sol",
                "medium",
            ),
        )
        for task, model, effort in cases:
            routed, _ = responses_router.route_payload({"input": task})
            self.assertEqual((routed["model"], routed["reasoning"]["effort"]), (model, effort))

    def test_chatgpt_header_selects_codex_backend_without_reading_credential(self):
        self.assertEqual(
            responses_router._upstream_base({"ChatGPT-Account-ID": "opaque"}),
            "https://chatgpt.com/backend-api/codex",
        )
        self.assertEqual(
            responses_router._upstream_base({"Authorization": "Bearer opaque"}),
            "https://api.openai.com/v1",
        )

    def test_websocket_response_create_is_routed(self):
        original = {
            "type": "response.create",
            "model": "gpt-6-luna",
            "input": [{"role": "user", "content": "分析网络异常根因"}],
            "tools": [{"type": "shell"}],
            "stream": True,
        }
        routed_text, record = responses_router._route_websocket_message(
            json.dumps(original)
        )
        routed = json.loads(routed_text)
        self.assertEqual(routed["type"], "response.create")
        self.assertEqual(routed["model"], "gpt-6-sol")
        self.assertEqual(routed["reasoning"]["effort"], "medium")
        self.assertEqual(routed["tools"], original["tools"])
        self.assertEqual(record["model"], "sol")


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        responses_router._last_route = None
        self.real_async_client = httpx.AsyncClient

    async def _run_request(self, stream: bool):
        captured = {}

        class SSEStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.created"}\n\n'
                yield b'data: [DONE]\n\n'

        def upstream(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content)
            if stream:
                return httpx.Response(
                    200,
                    stream=SSEStream(),
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(200, json={"id": "resp_test", "object": "response"})

        transport = httpx.MockTransport(upstream)

        def client_factory(**kwargs):
            return self.real_async_client(transport=transport, **kwargs)

        asgi = httpx.ASGITransport(app=responses_router.app)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            responses_router.httpx, "AsyncClient", side_effect=client_factory
        ), mock.patch.object(
            responses_router, "_route_store", RouteStore(Path(temp_dir) / "state")
        ), mock.patch.dict(
            os.environ,
            {
                "CODEX_ROUTER_UPSTREAM_BASE_URL": "https://upstream.invalid/v1",
                "CODEX_ROUTER_LOG_PATH": str(Path(temp_dir) / "routes.jsonl"),
            },
            clear=False,
        ):
            async with self.real_async_client(
                transport=asgi, base_url="http://127.0.0.1:8787"
            ) as client:
                response = await client.post(
                    "/v1/responses",
                    headers={"Authorization": "Bearer secret-token", "X-Custom": "keep"},
                    json={
                        "model": "gpt-6-luna",
                        "input": "分析网络异常根因",
                        "stream": stream,
                        "tools": [{"type": "shell"}],
                        "previous_response_id": "resp_previous",
                    },
                )
                log_text = (Path(temp_dir) / "routes.jsonl").read_text(encoding="utf-8")
        return response, captured, log_text

    async def test_non_stream_forwards_auth_and_preserves_fields(self):
        response, captured, log_text = await self._run_request(False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "resp_test")
        self.assertEqual(captured["headers"]["authorization"], "Bearer secret-token")
        self.assertEqual(captured["payload"]["tools"], [{"type": "shell"}])
        self.assertEqual(captured["payload"]["previous_response_id"], "resp_previous")
        self.assertEqual(captured["payload"]["model"], "gpt-6-sol")
        self.assertEqual(response.headers["x-codex-router-model"], "sol")
        self.assertNotIn("secret-token", log_text)
        self.assertNotIn("分析网络异常根因", log_text)

    async def test_streaming_sse_body_is_unchanged(self):
        response, captured, _ = await self._run_request(True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/event-stream")
        self.assertEqual(
            response.content,
            b'data: {"type":"response.created"}\n\ndata: [DONE]\n\n',
        )
        self.assertTrue(captured["payload"]["stream"])

    async def test_zstd_request_body_is_decoded_and_reencoded_without_header(self):
        captured = {}

        def upstream(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "resp_zstd"})

        transport = httpx.MockTransport(upstream)

        def client_factory(**kwargs):
            return self.real_async_client(transport=transport, **kwargs)

        body = json.dumps({"input": "更新 TTD", "stream": False}).encode("utf-8")
        compressed = zstandard.ZstdCompressor().compress(body)
        asgi = httpx.ASGITransport(app=responses_router.app)
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            responses_router.httpx, "AsyncClient", side_effect=client_factory
        ), mock.patch.object(
            responses_router, "_route_store", RouteStore(Path(temp_dir) / "state")
        ), mock.patch.dict(
            os.environ,
            {
                "CODEX_ROUTER_UPSTREAM_BASE_URL": "https://upstream.invalid/v1",
                "CODEX_ROUTER_LOG_PATH": str(Path(temp_dir) / "routes.jsonl"),
            },
            clear=False,
        ):
            async with self.real_async_client(transport=asgi, base_url="http://router") as client:
                response = await client.post(
                    "/v1/responses",
                    content=compressed,
                    headers={"content-type": "application/json", "content-encoding": "zstd"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["payload"]["model"], "gpt-6-luna")
        self.assertNotIn("content-encoding", captured["headers"])


class ConfigTests(unittest.TestCase):
    def test_enable_disable_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            router_config.Path, "home", return_value=Path(temp_dir)
        ):
            config = Path(temp_dir) / "config.toml"
            original = 'model = "gpt-6-luna"\n\n[desktop]\npreventSleep = true\n'
            config.write_text(original, encoding="utf-8")
            self.assertTrue(router_config.enable(config).startswith("enabled backup="))
            enabled = config.read_text(encoding="utf-8")
            self.assertIn('openai_base_url = "http://127.0.0.1:8787/v1"', enabled)
            self.assertIn('[desktop]\npreventSleep = true', enabled)
            self.assertTrue(router_config.disable(config).startswith("disabled backup="))
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_enable_refuses_to_replace_another_proxy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.toml"
            config.write_text('openai_base_url = "https://other.example/v1"\n', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                router_config.enable(config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
