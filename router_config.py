#!/usr/bin/env python3
"""Safely enable or disable the local Router in the user Codex config."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence


ROUTER_URL = "http://127.0.0.1:8787/v1"
BASE_URL_RE = re.compile(r'^\s*openai_base_url\s*=\s*"([^"]*)"\s*(?:#.*)?$', re.MULTILINE)


def _backup(path: Path) -> Path:
    backup_dir = Path.home() / ".codex-router" / "backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / (path.name + "." + stamp)
    shutil.copy2(path, destination)
    return destination


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def enable(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = BASE_URL_RE.search(text)
    if match:
        if match.group(1) == ROUTER_URL:
            return "already_enabled"
        raise RuntimeError("openai_base_url already points somewhere else; refusing to overwrite it")
    backup = _backup(path)
    first_table = re.search(r"^\s*\[", text, re.MULTILINE)
    position = first_table.start() if first_table else len(text)
    prefix = text[:position].rstrip() + "\nopenai_base_url = \"" + ROUTER_URL + "\"\n\n"
    suffix = text[position:].lstrip("\n")
    _atomic_write(path, prefix + suffix)
    return "enabled backup=" + str(backup)


def require_healthy_router() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/healthz", timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("local router health check returned HTTP {}".format(response.status))
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("local router is not healthy; config was not changed") from exc


def disable(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = BASE_URL_RE.search(text)
    if not match:
        return "already_disabled"
    if match.group(1) != ROUTER_URL:
        raise RuntimeError("openai_base_url is not the local router; refusing to remove it")
    backup = _backup(path)
    updated = text[: match.start()] + text[match.end() :]
    updated = re.sub(r"\n{3,}", "\n\n", updated, count=1)
    _atomic_write(path, updated)
    return "disabled backup=" + str(backup)


def status(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = BASE_URL_RE.search(text)
    if not match:
        return "disabled"
    if match.group(1) == ROUTER_URL:
        return "enabled"
    return "other=" + match.group(1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "disable", "status"))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".codex" / "config.toml",
        help="Codex user config path",
    )
    args = parser.parse_args(argv)
    if args.action == "enable":
        require_healthy_router()
        print(enable(args.config))
    elif args.action == "disable":
        print(disable(args.config))
    else:
        print(status(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
