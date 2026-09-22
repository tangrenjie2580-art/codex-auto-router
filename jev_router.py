#!/usr/bin/env python3
"""Route a task to Luna, Sol, or Astra using Jev plus local hard rules.

This is intentionally a router only. It never starts Codex or executes a task.
The implementation uses the standard library so it works with the host's
Python 3.9 without installing the TypeSafe SDK.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional, Tuple


DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_TIMEOUT_SECONDS = 20.0

MODEL_OPTIONS = ("luna", "sol", "astra")
COMPLEXITY_OPTIONS = ("simple", "medium", "complex", "extreme")
REASONING_OPTIONS = ("low", "medium", "high", "very_high")
RISK_OPTIONS = ("low", "medium", "high")
TASK_TYPE_OPTIONS = (
    "routine_operation",
    "troubleshooting",
    "coding",
    "architecture",
    "research",
    "planning",
    "file_editing",
    "other",
)
YES_NO_OPTIONS = ("yes", "no")


class RouterError(Exception):
    """An expected Jev transport or response-shape failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _choice_question(instructions: str, options: Tuple[str, ...]) -> Dict[str, Any]:
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": {option: option.replace("_", " ") for option in options},
    }


def build_questions() -> Dict[str, Dict[str, Any]]:
    """Return the complete, single-call Jev question set."""

    return {
        "task_complexity": _choice_question(
            "Classify the user's task complexity based only on the state. "
            "simple means a clear, small, recoverable task; medium means "
            "several routine steps; complex means substantial analysis or "
            "interdependencies; extreme means a long, high-impact chain or "
            "major architecture change.",
            COMPLEXITY_OPTIONS,
        ),
        "reasoning_required": _choice_question(
            "What reasoning depth is required to complete the task reliably? "
            "Judge the task, not the user's writing quality.",
            REASONING_OPTIONS,
        ),
        "execution_risk": _choice_question(
            "What is the execution risk if the task is handled incorrectly? "
            "high means difficult to recover, destructive, system-wide, or "
            "otherwise high impact.",
            RISK_OPTIONS,
        ),
        "task_type": _choice_question(
            "What is the primary task type? Select the closest option; use "
            "other when none fits.",
            TASK_TYPE_OPTIONS,
        ),
        "recommended_model": _choice_question(
            "Which execution model is the best initial fit for this task? "
            "Luna is the default for clear routine work, Sol is for real "
            "analysis and cross-file troubleshooting, and Astra is reserved "
            "for extreme or unusually high-cost reasoning tasks.",
            MODEL_OPTIONS,
        ),
        "can_luna_reliably_complete": _choice_question(
            "Can Luna reliably complete the task within the stated scope "
            "without needing to redesign the approach?",
            YES_NO_OPTIONS,
        ),
    }


def build_state(
    task: str,
    progress: Optional[str] = None,
    failure: Optional[str] = None,
    phase: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the state sent to Jev, including reroute context when supplied."""

    state: Dict[str, Any] = {"task": task}
    if progress:
        state["current_progress"] = progress
    if failure:
        state["failure_reason"] = failure
    if phase:
        state["phase"] = phase
    return state


def call_jev(state: Any) -> Dict[str, Any]:
    """Call Jev and return its JSON response without exposing credentials."""

    api_key = os.environ.get("JEV_API_KEY")
    if not api_key:
        raise RouterError("missing_api_key")

    endpoint = os.environ.get("JEV_API_URL", DEFAULT_API_URL)
    model = os.environ.get("JEV_MODEL", DEFAULT_JEV_MODEL)
    try:
        timeout = float(os.environ.get("JEV_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    payload = {
        "model": model,
        "state": state,
        "questions": build_questions(),
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            raise RouterError("http_4xx")
        raise RouterError("http_5xx")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RouterError("network_error")

    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RouterError("invalid_json")
    if not isinstance(decoded, dict):
        raise RouterError("invalid_response")
    return decoded


def _probabilities(answer: Mapping[str, Any]) -> Dict[str, float]:
    raw = answer.get("probabilities")
    if not isinstance(raw, Mapping):
        raise RouterError("missing_probabilities")
    probabilities: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise RouterError("invalid_probability")
        if number < 0 or number > 1:
            raise RouterError("invalid_probability")
        probabilities[str(key)] = number
    if not probabilities:
        raise RouterError("empty_probabilities")
    return probabilities


def _normalise_choice(answer: Any) -> Dict[str, Any]:
    if not isinstance(answer, Mapping):
        raise RouterError("invalid_answer")
    probabilities = _probabilities(answer)
    choice = answer.get("choice")
    if choice is None:
        choice = max(probabilities, key=probabilities.get)
    confidence = answer.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise RouterError("invalid_confidence")
        if confidence < 0 or confidence > 1:
            raise RouterError("invalid_confidence")
    return {
        "choice": str(choice),
        "probabilities": probabilities,
        "confidence": confidence,
    }


def normalise_jev_response(response: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and reduce Jev's response to the fields the router needs."""

    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise RouterError("missing_answers")
    result: Dict[str, Any] = {}
    for name in (
        "task_complexity",
        "reasoning_required",
        "execution_risk",
        "task_type",
        "recommended_model",
        "can_luna_reliably_complete",
    ):
        if name not in answers:
            raise RouterError("missing_answer_" + name)
        result[name] = _normalise_choice(answers[name])
    return result


_HIGH_IMPACT_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+--force\b",
    r"\bdrop\s+(database|table|schema)\b",
    r"\btruncate\s+(database|table)\b",
    r"\bdelete\s+(all|everything|the\s+database|production)\b",
    r"\b清空\b",
    r"删除.*(全部|数据库|生产|系统)",
    r"\b不可逆\b",
    r"\b永久替换\b",
    r"\b系统级\b",
)


def has_high_impact_operation(state: Any) -> bool:
    text = json.dumps(state, ensure_ascii=False, sort_keys=True)
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _HIGH_IMPACT_PATTERNS)


def _dimension(analysis: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = analysis.get(name)
    if not isinstance(value, Mapping):
        return {}
    return value


def _probability(analysis: Mapping[str, Any], name: str, option: str) -> float:
    probs = _dimension(analysis, name).get("probabilities", {})
    if not isinstance(probs, Mapping):
        return 0.0
    try:
        return float(probs.get(option, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _choice(analysis: Mapping[str, Any], name: str) -> str:
    value = _dimension(analysis, name).get("choice")
    return str(value) if value is not None else ""


def _confidence(analysis: Mapping[str, Any], name: str) -> float:
    value = _dimension(analysis, name).get("confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def route_decision(
    analysis: Mapping[str, Any],
    state: Any,
    source: str = "jev",
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply Jev output and the non-negotiable local guardrails."""

    model_probs = {
        model: _probability(analysis, "recommended_model", model)
        for model in MODEL_OPTIONS
    }
    model_confidence = _confidence(analysis, "recommended_model")
    complexity = _choice(analysis, "task_complexity")
    reasoning = _choice(analysis, "reasoning_required")
    risk = _choice(analysis, "execution_risk")
    task_type = _choice(analysis, "task_type")
    luna_yes_probability = _probability(
        analysis, "can_luna_reliably_complete", "yes"
    )
    luna_reliable = _choice(analysis, "can_luna_reliably_complete") == "yes"

    model = "sol"
    reason_code = "safe_default_sol"

    if source != "jev":
        reason_code = "jev_unavailable_" + (error_code or "unknown")
    elif has_high_impact_operation(state):
        model = "sol"
        reason_code = "hard_rule_high_impact"
    elif risk == "high" and not (
        complexity == "extreme"
        and model_probs["astra"] >= 0.65
        and model_probs["astra"] - model_probs["sol"] >= 0.10
    ):
        model = "sol"
        reason_code = "hard_rule_high_risk"
    elif model_confidence < 0.65:
        model = "sol"
        reason_code = "low_jev_confidence"
    elif (
        complexity == "extreme"
        and model_probs["astra"] >= 0.65
        and model_probs["astra"] - model_probs["sol"] >= 0.10
    ):
        model = "astra"
        reason_code = "extreme_astra_threshold"
    elif model_probs["sol"] >= 0.60:
        model = "sol"
        reason_code = "sol_threshold"
    elif abs(model_probs["luna"] - model_probs["sol"]) < 0.10:
        model = "sol"
        reason_code = "luna_sol_tie_break"
    elif (
        max(model_probs["sol"], model_probs["astra"]) >= 0.25
        and abs(model_probs["sol"] - model_probs["astra"]) < 0.10
    ):
        model = "sol"
        reason_code = "sol_astra_tie_break"
    elif (
        model_probs["luna"] >= 0.75
        and risk != "high"
        and luna_reliable
        and luna_yes_probability >= 0.50
    ):
        model = "luna"
        reason_code = "luna_threshold"
    elif model_probs["astra"] >= 0.65 and complexity == "extreme":
        model = "astra"
        reason_code = "extreme_astra_threshold"
    else:
        model = max(model_probs, key=model_probs.get)
        if model not in MODEL_OPTIONS or (model == "luna" and not luna_reliable):
            model = "sol"
            reason_code = "safe_default_sol"
        else:
            reason_code = "jev_recommendation"

    decision: Dict[str, Any] = {
        "model": model,
        "complexity": complexity or None,
        "reasoning_required": reasoning or None,
        "risk": risk or None,
        "task_type": task_type or None,
        "can_luna_reliably_complete": "yes" if luna_reliable else "no",
        "confidence": round(model_confidence, 4),
        "recommended_model_probabilities": model_probs,
        "reason_code": reason_code,
        "source": source,
    }
    if error_code:
        decision["fallback_reason"] = error_code
    return decision


def fallback_decision(state: Any, error_code: str) -> Dict[str, Any]:
    empty_analysis = {
        "recommended_model": {
            "choice": "sol",
            "probabilities": {"luna": 0.0, "sol": 1.0, "astra": 0.0},
            "confidence": 0.0,
        },
        "task_complexity": {"choice": "unknown", "probabilities": {}, "confidence": 0.0},
        "reasoning_required": {"choice": "unknown", "probabilities": {}, "confidence": 0.0},
        "execution_risk": {"choice": "unknown", "probabilities": {}, "confidence": 0.0},
        "task_type": {"choice": "unknown", "probabilities": {}, "confidence": 0.0},
        "can_luna_reliably_complete": {
            "choice": "no",
            "probabilities": {"yes": 0.0, "no": 1.0},
            "confidence": 0.0,
        },
    }
    return route_decision(empty_analysis, state, source="fallback", error_code=error_code)


def _state_from_args(args: argparse.Namespace) -> Any:
    if args.state_json:
        try:
            return json.loads(args.state_json)
        except json.JSONDecodeError as exc:
            raise SystemExit("--state-json 不是有效 JSON") from exc
    task = " ".join(args.task).strip()
    if not task:
        raise SystemExit("请提供任务文本，或使用 --state-json")
    return build_state(task, args.progress, args.failure, args.phase)


def _print_text(decision: Mapping[str, Any]) -> None:
    print("MODEL=" + str(decision["model"]))
    print("CONFIDENCE=" + format(float(decision["confidence"]), ".2f"))
    print("COMPLEXITY=" + str(decision.get("complexity")))
    print("RISK=" + str(decision.get("risk")))
    print("TASK_TYPE=" + str(decision.get("task_type")))
    print("SOURCE=" + str(decision.get("source")))
    print("REASON=" + str(decision.get("reason_code")))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="*", help="完整用户任务")
    parser.add_argument("--state-json", help="用于重路由的完整 JSON state")
    parser.add_argument("--progress", help="当前执行进展")
    parser.add_argument("--failure", help="最近失败原因")
    parser.add_argument("--phase", help="当前阶段，例如 initial 或 reroute")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 决策")
    args = parser.parse_args(argv)
    state = _state_from_args(args)

    try:
        response = call_jev(state)
        analysis = normalise_jev_response(response)
        decision = route_decision(analysis, state)
    except RouterError as exc:
        decision = fallback_decision(state, exc.code)

    if args.json:
        print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    else:
        _print_text(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
