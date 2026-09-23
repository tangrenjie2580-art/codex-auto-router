#!/usr/bin/env python3
"""Tests and an inspectable routing table for the rule-only router."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from real_task_cases import ESCALATION_CASES, INITIAL_CASES  # noqa: E402
from routing_core import (  # noqa: E402
    MODEL_IDS,
    MODEL_REASONING_EFFORTS,
    ModelRouter,
    RoutingRequest,
)
from rule_router import RuleDecisionEngine  # noqa: E402


class RuleRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = ModelRouter(RuleDecisionEngine())

    def test_at_least_twenty_real_initial_tasks(self):
        self.assertGreaterEqual(len(INITIAL_CASES), 20)
        print("\n任务 → 选择模型 → 命中规则 → 理由")
        for task, expected in INITIAL_CASES:
            decision = self.router.route(RoutingRequest(task=task))
            print(
                "{} → {} → {} → {}".format(
                    task,
                    decision.model,
                    ",".join(decision.matched_rules),
                    decision.reason,
                )
            )
            self.assertEqual(decision.model, expected, task)

    def test_escalation_is_owned_by_router(self):
        for task, current, failures, repeated, expanded, expected in ESCALATION_CASES:
            decision = self.router.route(
                RoutingRequest(
                    task=task,
                    current_model=current,
                    failure_count=failures,
                    repeated_error=repeated,
                    scope_expanded=expanded,
                )
            )
            self.assertEqual(decision.model, expected, task)
            self.assertEqual(decision.source, "guardrail")
            if task == "Sol 多次失败仍未解决":
                self.assertEqual(decision.reasoning_effort, "high")

    def test_unclear_defaults_to_sol(self):
        decision = self.router.route(RoutingRequest(task="帮我看看"))
        self.assertEqual(decision.model, "sol")
        self.assertEqual(decision.matched_rules, ["unclear_default_sol"])

    def test_analysis_overrides_routine_word(self):
        decision = self.router.route(
            RoutingRequest(task="更新 TTD 前先分析认证失败的根因并制定修复方案")
        )
        self.assertEqual(decision.model, "sol")

    def test_routine_conversation_uses_luna(self):
        tasks = (
            "这个规则现在可以使用了吗？",
            "我现在会话设定的是 Luna xhigh，请求会先经过 Router 判断，是吗？",
            "你说的原生 Codex TUI 是什么意思？",
            "Jev 和当前规则版有什么区别？",
        )
        for task in tasks:
            with self.subTest(task=task):
                decision = self.router.route(RoutingRequest(task=task))
                self.assertEqual(decision.model, "luna")
                self.assertIn("routine_conversation", decision.matched_rules)

    def test_complex_question_overrides_conversation_shape(self):
        tasks = (
            "为什么 Codex Router 最近频繁崩溃？请排查根因",
            "判断这个 Router 架构是否能支持多系统联动，并评估风险",
            "这个认证问题是什么意思？请分析失败根因",
        )
        for task in tasks:
            with self.subTest(task=task):
                decision = self.router.route(RoutingRequest(task=task))
                self.assertEqual(decision.model, "sol")

    def test_approved_plan_execution_returns_to_luna(self):
        tasks = (
            "按刚才已经确认的方案开始执行修改并验证结果",
            "方案已经定稿，开始实施并修改多个文件",
            "按上述方案修复认证问题，不需要重新分析",
        )
        for task in tasks:
            with self.subTest(task=task):
                decision = self.router.route(RoutingRequest(task=task))
                self.assertEqual(decision.model, "luna")
                self.assertIn("approved_plan_execution", decision.matched_rules)

    def test_execution_phase_keeps_analysis_and_risk_floors(self):
        cases = (
            "按上述方案执行，同时重新评估可行性",
            "按上述方案执行，但先重新评估认证风险并排查失败原因",
            "按已确认方案删除生产数据库中的全部数据",
        )
        for task in cases:
            with self.subTest(task=task):
                decision = self.router.route(RoutingRequest(task=task))
                self.assertEqual(decision.model, "sol")

        extreme = self.router.route(RoutingRequest(
            task="按既定大型跨系统迁移方案开始执行零停机切换"
        ))
        self.assertEqual(extreme.model, "sol")

    def test_model_ids_are_current_alias_mapping(self):
        self.assertEqual(MODEL_IDS["luna"], "gpt-6-luna")
        self.assertEqual(MODEL_IDS["sol"], "gpt-6-sol")
        self.assertEqual(MODEL_IDS["astra"], "gpt-6-astra")
        self.assertEqual(MODEL_REASONING_EFFORTS["luna"], "xhigh")
        self.assertEqual(MODEL_REASONING_EFFORTS["sol"], "medium")
        self.assertEqual(MODEL_REASONING_EFFORTS["astra"], "low")

    def test_engine_can_be_replaced(self):
        class FakeFutureJevEngine:
            def classify(self, request):
                return RuleDecisionEngine().classify(
                    RoutingRequest(task="查看日志")
                )

        decision = ModelRouter(FakeFutureJevEngine()).route(
            RoutingRequest(task="任意任务")
        )
        self.assertEqual(decision.model, "luna")


if __name__ == "__main__":
    unittest.main(verbosity=2)
