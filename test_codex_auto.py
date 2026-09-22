#!/usr/bin/env python3
"""Tests for the Codex Auto command wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import codex_auto  # noqa: E402


class CodexAutoTests(unittest.TestCase):
    def test_luna_command(self):
        decision = codex_auto.route_task("更新 TTD")
        command = codex_auto.build_codex_command("codex", decision, "更新 TTD")
        self.assertEqual(decision.model, "luna")
        self.assertEqual(
            command,
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-luna",
                "--config",
                'model_reasoning_effort="xhigh"',
                "更新 TTD",
            ],
        )

    def test_sol_command(self):
        decision = codex_auto.route_task("分析网络异常根因")
        command = codex_auto.build_codex_command("codex", decision, "分析网络异常根因")
        self.assertEqual(decision.model, "sol")
        self.assertIn("gpt-5.6-sol", command)
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_astra_command(self):
        decision = codex_auto.route_task(
            "设计大型跨系统迁移架构，要求零停机、容灾和完整回滚"
        )
        self.assertEqual(decision.model, "astra")
        self.assertEqual(decision.model_id, "gpt-6-astra")
        self.assertEqual(decision.reasoning_effort, "low")

    def test_optional_codex_arguments(self):
        decision = codex_auto.route_task("查看日志")
        command = codex_auto.build_codex_command(
            "codex",
            decision,
            "查看日志",
            workdir="/tmp/work",
            sandbox="workspace-write",
            images=["a.png"],
            skip_git_repo_check=True,
        )
        self.assertIn("--cd", command)
        self.assertIn("--sandbox", command)
        self.assertIn("--image", command)
        self.assertIn("--skip-git-repo-check", command)

    def test_log_contains_hash_not_task_text(self):
        decision = codex_auto.route_task("更新 TTD")
        record = codex_auto.make_route_record(
            "R-TEST", "更新 TTD", decision, "completed", 0
        )
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("更新 TTD", serialized)
        self.assertIn("task_sha256", record)
        self.assertEqual(record["reasoning_effort"], "xhigh")

    def test_dry_run_does_not_launch_codex(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "CODEX_AUTO_LOG_PATH": str(Path(temp_dir) / "routes.jsonl"),
                "CODEX_AUTO_CODEX_BIN": "/opt/homebrew/bin/codex",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "codex_auto.subprocess.run"
            ) as run:
                result = codex_auto.main(["--dry-run", "更新 TTD"])
            self.assertEqual(result, 0)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
