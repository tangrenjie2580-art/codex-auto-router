#!/usr/bin/env python3
"""Route one task, show a receipt, then run it with ``codex exec``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from routing_core import RoutingDecision, RoutingRequest
from rule_router import build_router


DEFAULT_LOG_PATH = Path.home() / ".codex-router" / "routes.jsonl"


def route_task(task: str) -> RoutingDecision:
    return build_router().route(RoutingRequest(task=task))


def build_codex_command(
    codex_bin: str,
    decision: RoutingDecision,
    task: str,
    workdir: Optional[str] = None,
    sandbox: Optional[str] = None,
    images: Optional[Sequence[str]] = None,
    skip_git_repo_check: bool = False,
) -> List[str]:
    command = [
        codex_bin,
        "exec",
        "--model",
        decision.model_id,
        "--config",
        'model_reasoning_effort="{}"'.format(decision.reasoning_effort),
    ]
    if workdir:
        command.extend(["--cd", workdir])
    if sandbox:
        command.extend(["--sandbox", sandbox])
    for image in images or ():
        command.extend(["--image", image])
    if skip_git_repo_check:
        command.append("--skip-git-repo-check")
    command.append(task)
    return command


def make_route_record(
    route_id: str,
    task: str,
    decision: RoutingDecision,
    status: str,
    exit_code: Optional[int] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "route_id": route_id,
        "source": decision.source,
        "model": decision.model,
        "model_id": decision.model_id,
        "reasoning_effort": decision.reasoning_effort,
        "matched_rules": decision.matched_rules,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
        "status": status,
    }
    if exit_code is not None:
        record["exit_code"] = exit_code
    return record


def append_route_record(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_last_route(path: Path) -> Optional[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        if line.strip():
            return json.loads(line)
    return None


def print_receipt(route_id: str, decision: RoutingDecision) -> None:
    print(
        "[CODEX-AUTO] ID={} SOURCE={} MODEL={} MODEL_ID={} EFFORT={} RULE={}".format(
            route_id,
            decision.source,
            decision.model,
            decision.model_id,
            decision.reasoning_effort,
            ",".join(decision.matched_rules),
        ),
        file=sys.stderr,
        flush=True,
    )
    print("[CODEX-AUTO] REASON=" + decision.reason, file=sys.stderr, flush=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="*", help="交给 Codex 的完整任务")
    parser.add_argument("-C", "--cd", help="Codex 工作目录")
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        help="可选；不提供时沿用 Codex 当前配置",
    )
    parser.add_argument("-i", "--image", action="append", default=[])
    parser.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="允许在非 Git 目录运行；不会绕过审批或沙箱",
    )
    parser.add_argument("--dry-run", action="store_true", help="只判断并显示命令")
    parser.add_argument("--last-route", action="store_true", help="显示最近一次路由记录")
    parser.add_argument("--no-log", action="store_true", help="不写脱敏路由记录")
    args = parser.parse_args(argv)

    log_path = Path(os.environ.get("CODEX_AUTO_LOG_PATH", str(DEFAULT_LOG_PATH)))
    if args.last_route:
        record = read_last_route(log_path)
        if record is None:
            print("尚无 codex-auto 路由记录。")
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    task = " ".join(args.task).strip()
    if not task:
        parser.error("请提供任务，例如：codex-auto \"更新 TTD\"")

    decision = route_task(task)
    route_id = "R-" + secrets.token_hex(3).upper()
    print_receipt(route_id, decision)

    codex_bin = os.environ.get("CODEX_AUTO_CODEX_BIN") or shutil.which("codex")
    if not codex_bin:
        print("[CODEX-AUTO] ERROR=找不到 codex 命令", file=sys.stderr)
        return 127

    command = build_codex_command(
        codex_bin=codex_bin,
        decision=decision,
        task=task,
        workdir=args.cd,
        sandbox=args.sandbox,
        images=args.image,
        skip_git_repo_check=args.skip_git_repo_check,
    )

    if args.dry_run:
        print("[CODEX-AUTO] COMMAND=" + shlex.join(command), file=sys.stderr)
        return 0

    if not args.no_log:
        append_route_record(
            log_path,
            make_route_record(route_id, task, decision, status="dispatched"),
        )

    try:
        completed = subprocess.run(command, check=False)
        exit_code = int(completed.returncode)
    except OSError as exc:
        print("[CODEX-AUTO] ERROR=无法启动 Codex: {}".format(exc), file=sys.stderr)
        exit_code = 127

    if not args.no_log:
        append_route_record(
            log_path,
            make_route_record(
                route_id, task, decision, status="completed", exit_code=exit_code
            ),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
