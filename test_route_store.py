#!/usr/bin/env python3
"""Tests for privacy-preserving response-chain routing."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from route_store import RouteStore  # noqa: E402
from responses_router import ContinuationRouteError, route_payload  # noqa: E402


class RouteStoreTests(unittest.TestCase):
    def test_stores_only_hmac_not_raw_response_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RouteStore(Path(temp_dir))
            store.remember("resp_sensitive", "luna", "gpt-6-luna", "xhigh")
            raw = store.data_path.read_text(encoding="utf-8")
            self.assertNotIn("resp_sensitive", raw)
            self.assertEqual(store.lookup("resp_sensitive")["model"], "luna")
            self.assertEqual(store.key_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.data_path.stat().st_mode & 0o777, 0o600)

    def test_continuation_inherits_model_without_classifying_tool_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RouteStore(Path(temp_dir))
            store.remember("resp_previous", "luna", "gpt-6-luna", "xhigh")
            routed, record = route_payload(
                {
                    "input": [{"type": "function_call_output", "output": "failed"}],
                    "previous_response_id": "resp_previous",
                    "model": "ignored",
                },
                store,
            )
            self.assertEqual(routed["model"], "gpt-6-luna")
            self.assertEqual(routed["reasoning"]["effort"], "xhigh")
            self.assertEqual(record["source"], "response_chain")

    def test_unknown_continuation_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RouteStore(Path(temp_dir))
            with self.assertRaises(ContinuationRouteError):
                route_payload(
                    {
                        "input": [{"type": "function_call_output", "output": "ok"}],
                        "previous_response_id": "resp_unknown",
                    },
                    store,
                )

    def test_new_user_execution_turn_reclassifies_instead_of_inheriting_sol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RouteStore(Path(temp_dir))
            store.remember("resp_plan", "sol", "gpt-6-sol", "medium")
            routed, record = route_payload(
                {
                    "input": [
                        {
                            "role": "user",
                            "content": "按刚才已经确认的方案开始执行修改并验证结果",
                        }
                    ],
                    "previous_response_id": "resp_plan",
                    "model": "gpt-6-sol",
                },
                store,
            )
            self.assertEqual(routed["model"], "gpt-6-luna")
            self.assertEqual(routed["reasoning"]["effort"], "xhigh")
            self.assertEqual(record["source"], "rules")
            self.assertIn("approved_plan_execution", record["matched_rules"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
