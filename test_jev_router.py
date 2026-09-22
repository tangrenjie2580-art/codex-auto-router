#!/usr/bin/env python3
"""Unit tests for the Jev router's schema handling and local guardrails."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from jev_router import (  # noqa: E402
    build_questions,
    build_state,
    fallback_decision,
    normalise_jev_response,
    route_decision,
)


def answer(choice, probabilities, confidence=0.90):
    return {
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def analysis(
    model="luna",
    model_probs=None,
    complexity="simple",
    risk="low",
    task_type="routine_operation",
    can_luna="yes",
    confidence=0.90,
    reasoning="low",
):
    model_probs = model_probs or {"luna": 0.85, "sol": 0.10, "astra": 0.05}
    return {
        "task_complexity": answer(
            complexity, {"simple": 0.90, "medium": 0.05, "complex": 0.03, "extreme": 0.02}
        ),
        "reasoning_required": answer(
            reasoning, {"low": 0.90, "medium": 0.05, "high": 0.03, "very_high": 0.02}
        ),
        "execution_risk": answer(risk, {"low": 0.90, "medium": 0.08, "high": 0.02}),
        "task_type": answer(
            task_type,
            {
                "routine_operation": 0.90,
                "troubleshooting": 0.02,
                "coding": 0.02,
                "architecture": 0.02,
                "research": 0.01,
                "planning": 0.01,
                "file_editing": 0.01,
                "other": 0.01,
            },
        ),
        "recommended_model": answer(model, model_probs, confidence),
        "can_luna_reliably_complete": answer(
            can_luna, {"yes": 0.90 if can_luna == "yes" else 0.10, "no": 0.10 if can_luna == "yes" else 0.90}
        ),
    }


ROUTE_CASES = [
    ("更新 TTD", analysis(), "luna"),
    ("查看日志", analysis(task_type="routine_operation"), "luna"),
    ("修改一个配置项", analysis(task_type="file_editing"), "luna"),
    ("简单 HTML/CSS 修改", analysis(task_type="coding"), "luna"),
    (
        "分析网络异常根因",
        analysis(
            model="sol",
            model_probs={"luna": 0.20, "sol": 0.68, "astra": 0.12},
            complexity="complex",
            reasoning="high",
            task_type="troubleshooting",
            can_luna="no",
        ),
        "sol",
    ),
    (
        "重构多个网页模块",
        analysis(
            model="sol",
            model_probs={"luna": 0.18, "sol": 0.65, "astra": 0.17},
            complexity="complex",
            reasoning="high",
            task_type="coding",
            can_luna="no",
        ),
        "sol",
    ),
    (
        "设计 APK 修改方案",
        analysis(
            model="sol",
            model_probs={"luna": 0.15, "sol": 0.70, "astra": 0.15},
            complexity="complex",
            reasoning="high",
            task_type="planning",
            can_luna="no",
        ),
        "sol",
    ),
    (
        "提出系统架构优化方案",
        analysis(
            model="sol",
            model_probs={"luna": 0.12, "sol": 0.65, "astra": 0.23},
            complexity="complex",
            reasoning="very_high",
            task_type="architecture",
            can_luna="no",
        ),
        "sol",
    ),
    (
        "删除生产数据库中的全部数据",
        analysis(
            model="luna",
            model_probs={"luna": 0.90, "sol": 0.07, "astra": 0.03},
            can_luna="yes",
        ),
        "sol",
    ),
    (
        "设计跨系统迁移架构并制定回滚方案",
        analysis(
            model="astra",
            model_probs={"luna": 0.05, "sol": 0.25, "astra": 0.70},
            complexity="extreme",
            risk="medium",
            reasoning="very_high",
            task_type="architecture",
            can_luna="no",
        ),
        "astra",
    ),
]


class JevRouterTests(unittest.TestCase):
    def test_questions_are_one_call_and_typed_choices(self):
        questions = build_questions()
        self.assertEqual(
            set(questions),
            {
                "task_complexity",
                "reasoning_required",
                "execution_risk",
                "task_type",
                "recommended_model",
                "can_luna_reliably_complete",
            },
        )
        self.assertTrue(all(item["type"] == "choice" for item in questions.values()))

    def test_response_normalisation(self):
        response = {"answers": analysis()}
        parsed = normalise_jev_response(response)
        self.assertEqual(parsed["recommended_model"]["choice"], "luna")
        self.assertIn("sol", parsed["recommended_model"]["probabilities"])

    def test_ten_representative_routes(self):
        print("\nROUTE TESTS")
        print("TASK | EXPECTED | ACTUAL | REASON")
        for task, jev_analysis, expected in ROUTE_CASES:
            state = build_state(task)
            decision = route_decision(jev_analysis, state)
            print("{} | {} | {} | {}".format(task, expected, decision["model"], decision["reason_code"]))
            self.assertEqual(decision["model"], expected, task)

    def test_low_confidence_defaults_to_sol(self):
        result = route_decision(
            analysis(
                model="luna",
                model_probs={"luna": 0.80, "sol": 0.15, "astra": 0.05},
                confidence=0.40,
            ),
            build_state("不确定的任务"),
        )
        self.assertEqual(result["model"], "sol")
        self.assertEqual(result["reason_code"], "low_jev_confidence")

    def test_extreme_high_risk_can_escalate_to_astra(self):
        result = route_decision(
            analysis(
                model="astra",
                model_probs={"luna": 0.03, "sol": 0.22, "astra": 0.75},
                complexity="extreme",
                risk="high",
                reasoning="very_high",
                task_type="architecture",
                can_luna="no",
            ),
            build_state("高风险极端架构迁移"),
        )
        self.assertEqual(result["model"], "astra")

    def test_fallback_never_returns_luna(self):
        result = fallback_decision(build_state("Jev 不可用"), "missing_api_key")
        self.assertEqual(result["model"], "sol")
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(result["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
