#!/usr/bin/env python3
"""Independent Luna/Sol/Astra router backed only by local rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Pattern, Sequence, Tuple

from routing_core import ModelRouter, RoutingDecision, RoutingRequest


@dataclass(frozen=True)
class TextRule:
    code: str
    model: str
    weight: int
    explanation: str
    patterns: Tuple[Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


def _compile(*patterns: str) -> Tuple[Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


RULES: Tuple[TextRule, ...] = (
    TextRule(
        "routine_update",
        "luna",
        4,
        "明确的常规更新或既有维护流程",
        _compile(
            r"更新\s*(ttd|ttt|tgtodrive)",
            r"(docker|容器).*(常规)?更新",
            r"docker\s+compose\s+(pull|up)",
            r"按.*(sop|流程).*(执行|处理|更新)",
        ),
    ),
    TextRule(
        "simple_query",
        "luna",
        3,
        "简单查询、状态或日志读取",
        _compile(
            r"^(查看|查询|检查|读取).*(状态|日志|版本|端口|文件|配置)",
            r"^(看下|看看).*(状态|日志|版本)",
            r"\b(status|logs?|version)\b",
        ),
    ),
    TextRule(
        "small_clear_edit",
        "luna",
        3,
        "目标明确的小范围文件或界面修改",
        _compile(
            r"(修改|调整|替换|新增).*(一个|单个|这一个).*(配置|字段|文件|按钮|颜色|文案)",
            r"简单.*(html|css|文案|样式).*(修改|调整)",
            r"(重命名|整理|移动).*(文件|目录)",
        ),
    ),
    TextRule(
        "existing_procedure",
        "luna",
        3,
        "已有脚本、明确步骤或固定 SOP",
        _compile(
            r"(执行|运行).*(已有|现有|这个).*(脚本|命令)",
            r"按照.*(步骤|sop|runbook|手册)",
            r"固定.*(流程|规则|sop)",
        ),
    ),
    TextRule(
        "approved_plan_execution",
        "luna",
        7,
        "方案已经明确或确认，当前只需执行和验证",
        _compile(
            r"^(?:请)?按(?:照)?(?:刚才|上述|上面)?(?:已确认|已经确认|既定|现有)?(?:的)?(?:方案|计划|步骤|修改清单).*(执行|实施|修改|修复|落地|开始|继续)",
            r"^(方案|计划|修改清单).*(已经|已)(确认|确定|定稿).*(执行|实施|修改|开始|继续)",
            r"^(开始|继续|直接|现在)(按|照)?.{0,20}(方案|计划|步骤).*(执行|实施|修改|落地)",
        ),
    ),
    TextRule(
        "routine_conversation",
        "luna",
        3,
        "普通确认、使用说明或当前机制解释",
        _compile(
            r"(是这样|理解.*(?:对|正确)|说得对|对不对|是吗|对吗)[？?。！! ]*$",
            r"^(这个|这条|刚才|现在|目前|我).{0,80}(可以用|能用|生效|使用|运行|模型|路由|规则).{0,40}(吗|么|？|\?)$",
            r"(怎么用|如何使用|是什么意思|什么意思|有什么区别|有何区别)[？?。！! ]*$",
            r"^(请)?(简单)?(回答|解释|说明|确认).{0,100}$",
        ),
    ),
    TextRule(
        "root_cause_analysis",
        "sol",
        6,
        "需要原因分析或故障根因判断",
        _compile(
            r"(分析|查明|定位|排查).*(原因|根因|故障|异常|崩溃|失败|问题)",
            r"为什么.*(失败|异常|崩溃|无法|不工作)",
            r"(故障排查|根因分析|troubleshoot|root cause)",
        ),
    ),
    TextRule(
        "planning_or_design",
        "sol",
        5,
        "需要方案规划、设计或技术取舍",
        _compile(
            r"(制定|设计|规划|提出).*(方案|流程|规则|架构|改造|优化)",
            r"(方案设计|技术选型|架构优化|稳定性改造)",
            r"比较.*(方案|路线|技术)",
            r"(判断|评估|分析).*(方案|架构|设计).*(可行|风险|取舍|影响)",
            r"(方案|架构|设计).*(能不能|是否).*(支撑|支持|承载|扩展)",
        ),
    ),
    TextRule(
        "multi_file_or_complex_code",
        "sol",
        5,
        "多文件、重构或复杂代码任务",
        _compile(
            r"(多个|多处|跨).*(文件|模块|组件|代码)",
            r"(重构|复杂代码|调用链|跨层)",
            r"(前端|后端|数据库).*(联动|一起修改)",
        ),
    ),
    TextRule(
        "network_auth_runtime",
        "sol",
        5,
        "涉及网络、认证或多层运行态诊断",
        _compile(
            r"(网络|代理|tun|tailscale|mihomo|clash|websocket).*(异常|故障|无法|未|没有|排查|修复|原因|为什么)",
            r"(认证|cookie|token|oauth|权限).*(失败|异常|问题|排查)",
            r"(容器|服务).*(连接|通信).*(失败|异常|问题)",
        ),
    ),
    TextRule(
        "multi_layer_media_diagnosis",
        "sol",
        5,
        "媒体播放或数据链路需要跨层定位",
        _compile(
            r"(emby|infuse|forward|strm|播放|黑屏|缓冲).*(哪一层|链路|排查|原因|失败)",
            r"(ttd|tgtodrive).*(strm|emby|播放器).*(问题|异常|哪一层|链路)",
        ),
    ),
    TextRule(
        "ambiguous_scope",
        "sol",
        4,
        "目标或范围不明确，存在隐含条件",
        _compile(
            r"^(处理|修复|优化|改进|弄|搞)(一下)?(这个|当前)?(问题|系统|项目)?[。！! ]*$",
            r"需求.*(不明确|模糊|隐含)",
            r"先看看.*怎么(办|处理|解决)",
        ),
    ),
    TextRule(
        "high_impact_operation",
        "sol",
        9,
        "重大删除、不可逆或生产级变更至少使用 Sol",
        _compile(
            r"(删除|清空|销毁).*(全部|数据库|生产|系统|容器|数据)",
            r"(不可逆|永久替换|强制推送|reset\s+--hard|rm\s+-rf)",
            r"(生产|核心).*(迁移|切换|升级|停机)",
        ),
    ),
    TextRule(
        "extreme_cross_system_architecture",
        "astra",
        8,
        "大型跨系统架构、迁移或高强度长链路规划",
        _compile(
            r"(大型|整体|全量|端到端).*(架构|重构|迁移|重新设计)",
            r"(多个|多套|跨).*(系统|平台|服务).*(联动|迁移|重构|架构)",
            r"(零停机|无损).*(迁移|切换).*(回滚|容灾)",
            r"(长链路|高失败成本|极复杂).*(规划|架构|重构|任务)",
        ),
    ),
    TextRule(
        "extreme_breadth",
        "astra",
        5,
        "任务同时覆盖多类关键基础设施",
        _compile(
            r"(ttd|tgtodrive).*(mihomo|clash).*(emby|caddy|tailscale)",
            r"(desktop|桌面端).*(cli).*(ide).*(router|路由|代理)",
        ),
    ),
)


class RuleDecisionEngine:
    """Deterministic classifier; safe-defaults to Sol when unclear."""

    source = "rules"

    def classify(self, request: RoutingRequest) -> RoutingDecision:
        text = " ".join(
            value for value in (request.task, request.progress, request.failure_summary) if value
        ).strip()
        if not text:
            return RoutingDecision(
                model="sol",
                matched_rules=["empty_or_unclear_task"],
                reason="任务内容为空或无法判断，默认 Sol。",
                source=self.source,
                confidence=1.0,
                scores={"luna": 0, "sol": 0, "astra": 0},
            )

        matched = [rule for rule in RULES if rule.matches(text)]
        scores = {"luna": 0, "sol": 0, "astra": 0}
        for rule in matched:
            scores[rule.model] += rule.weight

        matched_codes = [rule.code for rule in matched]
        explanations = [rule.explanation for rule in matched]

        # A new user turn that explicitly approves an existing plan is a phase
        # transition, not another planning request.  Keep high-impact work and
        # requests that ask for fresh analysis above Luna.
        if "approved_plan_execution" in matched_codes:
            analysis_text = re.sub(
                r"不(?:需要|用|必)(?:再|重新)?(?:进行)?"
                r"(?:分析|评估|设计|规划|比较|排查|定位)",
                "",
                text,
            )
            asks_for_new_analysis = bool(
                re.search(
                    r"(?:先|同时|还要|并且|并|再次|重新|需要).{0,12}"
                    r"(?:分析|评估|设计|规划|比较|排查|定位)",
                    analysis_text,
                )
            )
            safety_floor_codes = {
                "high_impact_operation",
                "extreme_cross_system_architecture",
                "extreme_breadth",
            }
            if asks_for_new_analysis:
                return RoutingDecision(
                    model="sol",
                    matched_rules=matched_codes + ["execution_requires_fresh_analysis"],
                    reason="执行请求仍要求重新分析或评估，保留 Sol。",
                    source=self.source,
                    confidence=0.88,
                    scores=scores,
                )
            if safety_floor_codes.intersection(matched_codes):
                return RoutingDecision(
                    model="sol",
                    matched_rules=matched_codes,
                    reason="方案虽已明确，但执行涉及重大或跨系统风险，至少由 Sol 把关。",
                    source=self.source,
                    confidence=0.94,
                    scores=scores,
                )
            if not asks_for_new_analysis and "root_cause_analysis" not in matched_codes:
                return RoutingDecision(
                    model="luna",
                    matched_rules=matched_codes,
                    reason="方案已经明确，当前进入机械执行与验证阶段，交回 Luna。",
                    source=self.source,
                    confidence=0.94,
                    scores=scores,
                )

        # Initial routing always stops at Sol.  Astra requires evidence from
        # an actual Sol analysis rather than keywords in the first request.
        astra_codes = {rule.code for rule in matched if rule.model == "astra"}
        explicit_extreme = bool(re.search(r"极复杂|高失败成本|长链路|零停机|大型|全量", text))
        if scores["astra"] >= 10 or (
            "extreme_cross_system_architecture" in astra_codes and explicit_extreme
        ):
            return RoutingDecision(
                model="sol",
                matched_rules=matched_codes,
                reason="；".join(explanations) + "，先由 Sol 分析，再决定是否需要 Astra。",
                source=self.source,
                confidence=0.92,
                scores=scores,
            )

        # Any real analysis/risk signal overrides routine keywords.
        if scores["sol"] > 0 or scores["astra"] > 0:
            return RoutingDecision(
                model="sol",
                matched_rules=matched_codes,
                reason=("；".join(explanations) or "存在复杂信号") + "，优先 Sol。",
                source=self.source,
                confidence=0.88,
                scores=scores,
            )

        if scores["luna"] >= 3:
            return RoutingDecision(
                model="luna",
                matched_rules=matched_codes,
                reason="；".join(explanations) + "，适合 Luna 直接执行。",
                source=self.source,
                confidence=0.90,
                scores=scores,
            )

        return RoutingDecision(
            model="sol",
            matched_rules=["unclear_default_sol"],
            reason="没有命中足够明确的低风险执行规则，默认 Sol。",
            source=self.source,
            confidence=0.70,
            scores=scores,
        )


def build_router() -> ModelRouter:
    return ModelRouter(RuleDecisionEngine())


def _print_text(decision: RoutingDecision) -> None:
    print("MODEL=" + decision.model)
    print("MODEL_ID=" + decision.model_id)
    print("REASONING_EFFORT=" + decision.reasoning_effort)
    print("SOURCE=" + decision.source)
    print("MATCHED_RULES=" + ",".join(decision.matched_rules))
    print("REASON=" + decision.reason)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="*", help="用户任务")
    parser.add_argument("--current-model", choices=("luna", "sol", "astra"))
    parser.add_argument("--current-effort", choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--failure-count", type=int, default=0)
    parser.add_argument("--repeated-error", action="store_true")
    parser.add_argument("--scope-expanded", action="store_true")
    parser.add_argument("--progress", default="")
    parser.add_argument("--failure-summary", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    task = " ".join(args.task).strip()
    request = RoutingRequest(
        task=task,
        current_model=args.current_model,
        current_effort=args.current_effort,
        failure_count=max(args.failure_count, 0),
        repeated_error=args.repeated_error,
        scope_expanded=args.scope_expanded,
        progress=args.progress,
        failure_summary=args.failure_summary,
    )
    decision = build_router().route(request)
    if args.json:
        print(json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        _print_text(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
