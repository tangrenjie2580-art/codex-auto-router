#!/usr/bin/env python3
"""Shared routing contracts and model-escalation guardrails.

The decision engine is replaceable.  Today it is a deterministic rule engine;
later it can be a Jev-backed engine without changing the escalation policy or
the caller-facing result shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol


MODEL_IDS = {
    "luna": "gpt-6-luna",
    "sol": "gpt-6-sol",
    "astra": "gpt-6-astra",
}

MODEL_REASONING_EFFORTS = {
    "luna": "xhigh",
    "sol": "medium",
    "astra": "low",
}


@dataclass(frozen=True)
class RoutingRequest:
    task: str
    current_model: Optional[str] = None
    current_effort: Optional[str] = None
    failure_count: int = 0
    repeated_error: bool = False
    scope_expanded: bool = False
    progress: str = ""
    failure_summary: str = ""


@dataclass
class RoutingDecision:
    model: str
    matched_rules: List[str]
    reason: str
    source: str = "rules"
    confidence: float = 1.0
    scores: Dict[str, int] = field(default_factory=dict)
    effort_override: Optional[str] = None

    @property
    def model_id(self) -> str:
        return MODEL_IDS[self.model]

    @property
    def reasoning_effort(self) -> str:
        return self.effort_override or MODEL_REASONING_EFFORTS[self.model]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["model_id"] = self.model_id
        result["reasoning_effort"] = self.reasoning_effort
        return result


class DecisionEngine(Protocol):
    """Interface implemented by rule-based and future Jev classifiers."""

    def classify(self, request: RoutingRequest) -> RoutingDecision:
        ...


class ModelRouter:
    """Own escalation state so an executing model never self-promotes."""

    def __init__(self, engine: DecisionEngine) -> None:
        self.engine = engine

    def route(self, request: RoutingRequest) -> RoutingDecision:
        current = (request.current_model or "").lower()
        if current and current not in MODEL_IDS:
            return RoutingDecision(
                model="sol",
                matched_rules=["unknown_current_model"],
                reason="当前模型状态无法识别，安全默认交给 Sol。",
                source="guardrail",
                confidence=1.0,
            )

        if current == "astra":
            return RoutingDecision(
                model="astra",
                matched_rules=["no_automatic_downgrade"],
                reason="Astra 执行阶段不自动降级。",
                source="guardrail",
                confidence=1.0,
            )

        if current == "sol":
            if request.failure_count >= 2 or request.repeated_error:
                rules = ["sol_multiple_failures"]
                if request.repeated_error:
                    rules.append("same_error_repeated")
                if request.current_effort in {"high", "xhigh", "max"}:
                    return RoutingDecision(
                        model="astra",
                        matched_rules=rules + ["sol_high_exhausted"],
                        reason="Sol High 仍未解决或重复同一错误，升级 Astra 定向重诊断。",
                        source="guardrail",
                        confidence=1.0,
                    )
                next_effort = "medium" if request.current_effort == "low" else "high"
                return RoutingDecision(
                    model="sol",
                    effort_override=next_effort,
                    matched_rules=rules + ["raise_sol_effort"],
                    reason="Sol 当前推理强度未形成结论，先提高到 {}。".format(next_effort),
                    source="guardrail",
                    confidence=1.0,
                )
            return RoutingDecision(
                model="sol",
                effort_override=request.current_effort,
                matched_rules=["no_automatic_downgrade"],
                reason="任务仍在 Sol 分析阶段，保持当前推理强度。",
                source="guardrail",
                confidence=1.0,
            )

        if current == "luna":
            escalation_rules: List[str] = []
            if request.failure_count >= 2:
                escalation_rules.append("luna_two_failures")
            if request.repeated_error:
                escalation_rules.append("same_error_repeated")
            if request.scope_expanded:
                escalation_rules.append("scope_expanded")
            if escalation_rules:
                return RoutingDecision(
                    model="sol",
                    matched_rules=escalation_rules,
                    reason="Luna 命中强制升级条件，由 Router 升级 Sol。",
                    source="guardrail",
                    confidence=1.0,
                )
            return RoutingDecision(
                model="luna",
                matched_rules=["luna_retry_budget_available"],
                reason="Luna 尚未达到两次失败或范围扩大阈值，可继续当前明确执行。",
                source="guardrail",
                confidence=1.0,
            )

        return self.engine.classify(request)
