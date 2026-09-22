# Codex Auto Router

一个本地 OpenAI Responses API 兼容模型 Router，让 Codex Desktop 和 Codex CLI 根据任务自动选择 Luna、Sol 或 Astra。

当前正式入口使用纯本地规则，不依赖 Jev。判断层已经抽象为可替换接口，后续可升级为“硬规则 + Jev 语义判断”。完整的需求背景、实现结果和限制见 [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)。

## 当前路由策略

| 模型 | 推理强度 | 任务类型 |
|---|---|---|
| Luna | xhigh | 日常问答、明确执行、固定 SOP、小范围修改、查询和日志 |
| Sol | medium | 根因分析、故障排查、方案设计、模糊需求、跨模块影响 |
| Astra | low | 极复杂架构、高失败成本、多系统联动、Sol 仍无法解决 |

判断不明确时默认 Sol。明确方案后的新用户执行请求会回到 Luna；重新分析和高风险任务不会因此降级。

## 架构

```text
ModelRouter                         升级和安全规则
└── DecisionEngine                 可替换的判断接口
    ├── RuleDecisionEngine         当前正式实现
    └── Future JevDecisionEngine   后续 Jev 实现

Codex Desktop / CLI
└── http://127.0.0.1:8787/v1
    └── Responses Router
        └── OpenAI / ChatGPT Codex upstream
```

代理支持：

- `POST /v1/responses`
- Responses WebSocket
- SSE streaming
- tool calls、reasoning、instructions、tools 等字段透明转发
- `previous_response_id` 响应链模型继承
- `GET /healthz`
- `GET /v1/router/last`

代理只重写 `model` 与 `reasoning.effort`。日志不保存任务原文、Authorization 或 Codex 登录凭据。

## 安装与启用（macOS）

要求 Python 3.9+，以及已经能正常使用的 Codex Desktop 或 CLI。

```sh
git clone https://github.com/tangrenjie2580-art/codex-auto-router.git
cd codex-auto-router
./start_router.sh
```

首次启动会创建项目内 `.venv` 并安装依赖。确认健康后按 `Ctrl-C` 停止前台进程，再安装后台服务：

```sh
./router-service install
./router-service status
curl -fsS http://127.0.0.1:8787/healthz
```

启用 Codex 用户级入口：

```sh
./enable-router
```

脚本会先备份 `~/.codex/config.toml`，然后设置：

```toml
openai_base_url = "http://127.0.0.1:8787/v1"
```

如果配置中已有其他 `openai_base_url`，脚本会拒绝覆盖。建议完全退出并重新打开 Codex Desktop，使新配置加载。

## 关闭和恢复

停止让 Codex 使用 Router，但保留服务和代码：

```sh
./disable-router
```

停止后台服务：

```sh
./router-service stop
```

彻底移除 LaunchAgent：

```sh
./router-service uninstall
```

`disable-router` 只删除本项目写入的 base URL，不会用旧备份覆盖其间产生的其他配置变化。

## 独立测试规则

```sh
python3 rule_router.py "更新 TTD"
python3 rule_router.py --json "分析网络异常根因"
python3 rule_router.py --json "按刚才已经确认的方案开始执行修改并验证结果"
```

完整测试：

```sh
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
python3 evaluate_rule_router.py
```

测试集属于按既定策略标注的架构回归测试，不是独立盲测准确率。

## Codex Auto CLI（可选）

`codex_auto.py` 是独立命令入口，会先显示判断结果，再启动 `codex exec`：

```sh
python3 codex_auto.py --dry-run "分析网络异常根因"
python3 codex_auto.py --skip-git-repo-check "更新 TTD"
```

正式启用 Responses Router 后，Desktop 和 CLI 无需使用此前缀。

## Jev 状态

`jev_router.py` 保留了 Jev 请求结构、概率解析和本地安全阈值，供未来适配参考；它尚未接入当前运行中的 Responses Router。接入时应保留：

- 高风险硬规则优先；
- Jev 超时、异常或低置信度时回退 Sol；
- 只发送精简决策状态，不发送完整会话和凭据；
- 先影子运行，再决定是否正式接管。

## 平台状态

- macOS：LaunchAgent 安装、启用和关闭已经实现。
- Windows：规则和 FastAPI 代码可运行，但自动后台服务安装脚本尚未实现。
- Codex IDE Extension：架构上可使用同一用户级 endpoint，但当前仓库尚未给出完整端到端验收结论。

## License

MIT
