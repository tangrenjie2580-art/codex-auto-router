#!/usr/bin/env python3
"""Manage the macOS user LaunchAgent for the local Codex Router."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence


LABEL = "com.tars.codex-router"


def _paths() -> tuple[Path, Path, Path]:
    router_dir = Path(__file__).resolve().parent
    plist_path = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    state_dir = Path.home() / ".codex-router"
    return router_dir, plist_path, state_dir


def _domain() -> str:
    return "gui/{}".format(os.getuid())


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def install() -> str:
    if sys.platform != "darwin":
        raise RuntimeError("automatic service installation is currently implemented for macOS")
    router_dir, plist_path, state_dir = _paths()
    python = router_dir / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError("router .venv is missing; run start_router.sh once first")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "uvicorn",
            "responses_router:app",
            "--app-dir",
            str(router_dir),
            "--host",
            "127.0.0.1",
            "--port",
            "8787",
            "--no-access-log",
        ],
        "WorkingDirectory": str(router_dir.parent),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(state_dir / "server.stdout.log"),
        "StandardErrorPath": str(state_dir / "server.stderr.log"),
    }
    fd, temporary = tempfile.mkstemp(prefix=LABEL + ".", dir=str(plist_path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)
        os.replace(temporary, plist_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    _run("launchctl", "bootout", _domain(), str(plist_path), check=False)
    _run("launchctl", "bootstrap", _domain(), str(plist_path))
    _run("launchctl", "kickstart", "-k", _domain() + "/" + LABEL)
    return "installed_and_started " + str(plist_path)


def start() -> str:
    _, plist_path, _ = _paths()
    if not plist_path.exists():
        raise RuntimeError("router LaunchAgent is not installed")
    loaded = _run("launchctl", "print", _domain() + "/" + LABEL, check=False)
    if loaded.returncode != 0:
        _run("launchctl", "bootstrap", _domain(), str(plist_path))
    _run("launchctl", "kickstart", "-k", _domain() + "/" + LABEL)
    return "started"


def stop() -> str:
    _, plist_path, _ = _paths()
    _run("launchctl", "bootout", _domain(), str(plist_path), check=False)
    return "stopped"


def status() -> str:
    result = _run("launchctl", "print", _domain() + "/" + LABEL, check=False)
    return "loaded" if result.returncode == 0 else "not_loaded"


def uninstall() -> str:
    _, plist_path, _ = _paths()
    _run("launchctl", "bootout", _domain(), str(plist_path), check=False)
    if plist_path.exists():
        plist_path.unlink()
    return "uninstalled"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "start", "stop", "status", "uninstall"))
    args = parser.parse_args(argv)
    print(globals()[args.action]())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
