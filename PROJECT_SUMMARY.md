# Codex Auto Router 项目总结

## 一句话介绍

Codex Auto Router 是一个运行在本机的 OpenAI Responses API 兼容代理。它在 Codex Desktop、Codex CLI 发出请求后，先判断任务应该交给 Luna、Sol 还是 Astra，再保持 Codex 原生的流式响应、工具调用和上下文链路完成执行。

## 为什么提出这个需求

原来的工作方式主要依赖默认 Luna 自行完成任务，再由模型判断是否需要升级。实际使用中存在三个问题：

1. Luna 对“自己是否应该升级”的判断并不稳定，可能在复杂问题上持续尝试。
2. 用户必须手动选择模型，Desktop、CLI 等入口容易出现不同规则。
3. Sol 和 Astra 额度更宝贵，不希望简单任务无差别使用强模型。

最初设想是让 TypeSafe Jev 只做分类：根据复杂度、推理需求和风险选择执行模型；Jev 不参与最终回答。由于 Jev 暂时无法注册使用，项目先落地了完全本地、无需外部 API 的规则判断版本，以验证整体代理架构。

## 提出的核心需求

- Luna：日常问答、明确且低风险的操作、固定 SOP、小范围修改、状态和日志查询。
- Sol：根因分析、故障排查、方案规划、需求存在隐含条件、跨模块影响判断。
- Astra：极复杂架构、高失败成本、多系统联动，以及 Sol 仍无法解决的问题。
- 判断不明确时安全默认 Sol。
- 模型和推理强度固定映射为 Luna/xhigh、Sol/medium、Astra/low。
- Router 负责初始判断，执行模型不能自行决定升级。
- Sol 完成方案后，新的用户消息明确要求按方案执行时，重新交给 Luna。
- 高风险或要求重新分析的执行请求不降级。
- 不修改 Codex 安装文件；配置可备份、启用、关闭和恢复。
- 不记录完整用户内容、Authorization 或登录凭据。
- 兼容 Responses API 的 streaming、tool calls、reasoning 和 `previous_response_id`。
- 后续可将规则判断层替换成“硬规则 + Jev”，而不重写代理和执行层。

## 实现架构

```text
Codex Desktop / CLI
        |
        v
Local Responses Router (127.0.0.1:8787)
        |
        +-- 新用户任务 --> RuleDecisionEngine --> Luna / Sol / Astra
        |
        +-- 工具续接 ----> response-chain inheritance
        |
        v
OpenAI / ChatGPT Codex upstream
```

判断层通过 `DecisionEngine` 接口与代理层分离。当前入口使用 `RuleDecisionEngine`；未来的 `JevDecisionEngine` 可以实现同一接口。升级、不自动降级和高风险下限仍由本地代码控制。

## 当前达到的效果

- Codex Desktop 和 CLI 共用同一个本地 Router，不需要输入特殊前缀。
- 用户在界面中选择的初始模型可以被 Router 按任务覆盖。
- 普通确认、使用说明和机制解释会进入 Luna。
- 根因分析、架构评估和方案设计进入 Sol。
- 严格的极复杂任务门槛才进入 Astra。
- Sol 规划完成后，用户说“按刚才已经确认的方案开始执行修改并验证结果”，新一轮会切回 Luna。
- 同一轮中的工具续接继承原模型，避免分析过程中意外切换。
- 路由记录只保存任务哈希、模型、规则和状态。
- macOS 可通过 LaunchAgent 登录后自动启动，并有启用/关闭脚本。
- 自动测试覆盖规则、阶段交接、SSE、WebSocket、工具调用、配置保护和响应链继承。

## 与 ops-router 的关系

两者现在有明确分工：

- 本地 Responses Router：负责每个新用户任务的初始模型选择和明确的阶段切换。
- ops-router：负责运行过程中出现失败、范围扩大或方向错误时的升级与协作交接。

初始请求已经由本地 Router 选择 Sol 时，ops-router 不应为同一个判断再次创建 Sol。Router 自动选择模型不等于用户显式指定模型。

## 已知限制

- 规则判断是确定性的，但不能理解所有自然语言；真实误判需要持续加入回归样本。
- 同一轮内没有新的用户阶段信号时，不会根据模型输出猜测“方案已经完成”并自动切换模型。
- 仅靠 HTTP 状态无法可靠判断业务失败，因此“Luna 连续两次业务失败后自动升级”的完整运行时闭环尚未实现。
- 尚未完成对子智能体显式模型选择的端到端保护验证。
- macOS LaunchAgent 已实现；Windows 自动后台服务尚未实现。
- `jev_router.py` 是未来接入参考，当前正式入口不调用 Jev。

## 后续路线

1. 收集真实任务误判样本并持续扩充回归测试。
2. 增加明确、可验证的执行结果信号，完成失败计数和自动升级。
3. 验证子智能体显式模型请求不会被代理错误覆盖。
4. 获得 Jev API 后先运行影子模式，对比规则判断、Jev 判断和实际任务结果。
5. 稳定后启用“硬安全规则 + Jev 语义判断 + Sol 兜底”。

