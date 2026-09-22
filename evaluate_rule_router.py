#!/usr/bin/env python3
"""Print the real-task routing matrix and compare it with an all-Luna baseline."""

from __future__ import annotations

from collections import Counter

from real_task_cases import INITIAL_CASES
from routing_core import RoutingRequest
from rule_router import build_router


def main() -> int:
    router = build_router()
    correct = 0
    baseline_correct = 0
    selected = Counter()

    print("| 任务 | 选择模型 | 命中规则 | 理由 |")
    print("|---|---|---|---|")
    for task, expected in INITIAL_CASES:
        decision = router.route(RoutingRequest(task=task))
        selected[decision.model] += 1
        correct += int(decision.model == expected)
        baseline_correct += int(expected == "luna")
        print(
            "| {} | {} | {} | {} |".format(
                task.replace("|", "\\|"),
                decision.model,
                ", ".join(decision.matched_rules),
                decision.reason.replace("|", "\\|"),
            )
        )

    total = len(INITIAL_CASES)
    print()
    print("样本数: {}".format(total))
    print("规则 Router: {}/{} ({:.1f}%)".format(correct, total, correct / total * 100))
    print(
        "全部默认 Luna 基线: {}/{} ({:.1f}%)".format(
            baseline_correct, total, baseline_correct / total * 100
        )
    )
    print(
        "模型分布: luna={}, sol={}, astra={}".format(
            selected["luna"], selected["sol"], selected["astra"]
        )
    )
    print("说明: 这是按既定规则标注的架构验证集，不是独立盲测准确率。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
