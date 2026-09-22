#!/usr/bin/env python3
"""Privacy-preserving response-chain model inheritance."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


class RouteStore:
    def __init__(self, state_dir: Optional[Path] = None, max_entries: int = 1000) -> None:
        self.state_dir = state_dir or Path.home() / ".codex-router"
        self.key_path = self.state_dir / "route-store.key"
        self.data_path = self.state_dir / "response-routes.json"
        self.max_entries = max_entries

    def _ensure_dir(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass

    def _key(self) -> bytes:
        self._ensure_dir()
        try:
            return self.key_path.read_bytes()
        except FileNotFoundError:
            key = secrets.token_bytes(32)
            fd = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            return key

    def _digest(self, response_id: str) -> str:
        return hmac.new(self._key(), response_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def _read(self) -> Dict[str, Dict[str, Any]]:
        try:
            value = json.loads(self.data_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: Dict[str, Dict[str, Any]]) -> None:
        self._ensure_dir()
        fd, temp_name = tempfile.mkstemp(prefix="response-routes.", dir=str(self.state_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.data_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def remember(self, response_id: str, model: str, model_id: str, effort: str) -> None:
        if not response_id:
            return
        routes = self._read()
        routes[self._digest(response_id)] = {
            "model": model,
            "model_id": model_id,
            "reasoning_effort": effort,
        }
        while len(routes) > self.max_entries:
            routes.pop(next(iter(routes)))
        self._write(routes)

    def lookup(self, response_id: str) -> Optional[Dict[str, Any]]:
        if not response_id:
            return None
        return self._read().get(self._digest(response_id))

