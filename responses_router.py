#!/usr/bin/env python3
"""Loopback-only OpenAI Responses API router for Codex.

The proxy changes only ``model`` and ``reasoning.effort``. All other request
fields and upstream response bytes are forwarded unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import gzip
import io
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse

import httpx
import zstandard
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from routing_core import RoutingRequest
from route_store import RouteStore
from rule_router import build_router


DEFAULT_UPSTREAM = "https://api.openai.com/v1"
CHATGPT_UPSTREAM = "https://chatgpt.com/backend-api/codex"
DEFAULT_LOG_PATH = Path.home() / ".codex-router" / "proxy-routes.jsonl"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

app = FastAPI(title="Codex Rule Router", docs_url=None, redoc_url=None)
_last_route: Optional[Dict[str, Any]] = None
_route_store = RouteStore()


class ContinuationRouteError(ValueError):
    pass


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            value = part.get("text")
            if isinstance(value, str) and part.get("type") in {
                None,
                "input_text",
                "text",
            }:
                parts.append(value)
    return "\n".join(parts).strip()


def extract_latest_user_task(payload: Mapping[str, Any]) -> str:
    """Extract only the newest user turn; never combine the full conversation."""

    input_value = payload.get("input")
    if isinstance(input_value, str):
        return input_value.strip()
    if not isinstance(input_value, list):
        return ""
    for item in reversed(input_value):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = _text_from_content(item.get("content"))
        if text:
            return text
    return ""


def route_payload(
    payload: Mapping[str, Any], store: Optional[RouteStore] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task = extract_latest_user_task(payload)
    routed = dict(payload)
    previous_response_id = payload.get("previous_response_id")
    inherited = None
    if not task and isinstance(previous_response_id, str):
        inherited = store.lookup(previous_response_id) if store else None
        if inherited is None:
            raise ContinuationRouteError(
                "Cannot safely route a continuation without its response-chain mapping."
            )

    if inherited:
        model = str(inherited["model"])
        model_id = str(inherited["model_id"])
        effort = str(inherited["reasoning_effort"])
        source = "response_chain"
        matched_rules = ["previous_response_model_inheritance"]
    else:
        decision = build_router().route(RoutingRequest(task=task))
        model = decision.model
        model_id = decision.model_id
        effort = decision.reasoning_effort
        source = decision.source
        matched_rules = decision.matched_rules

    routed["model"] = model_id
    reasoning = routed.get("reasoning")
    reasoning_copy = dict(reasoning) if isinstance(reasoning, dict) else {}
    reasoning_copy["effort"] = effort
    routed["reasoning"] = reasoning_copy
    route_id = "P-" + secrets.token_hex(4).upper()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "route_id": route_id,
        "source": source,
        "model": model,
        "model_id": model_id,
        "reasoning_effort": effort,
        "matched_rules": matched_rules,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest()[:16],
        "status": "routed",
    }
    return routed, record


def _safe_log(record: Mapping[str, Any]) -> None:
    path = Path(os.environ.get("CODEX_ROUTER_LOG_PATH", str(DEFAULT_LOG_PATH)))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _upstream_base(headers: Optional[Mapping[str, str]] = None) -> str:
    configured = os.environ.get("CODEX_ROUTER_UPSTREAM_BASE_URL")
    if configured:
        base = configured.rstrip("/")
    elif headers and any(key.lower() == "chatgpt-account-id" for key in headers):
        base = CHATGPT_UPSTREAM
    else:
        base = DEFAULT_UPSTREAM
    parsed = urlparse(base)
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 8787:
        raise RuntimeError("upstream points back to the local router")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("invalid CODEX_ROUTER_UPSTREAM_BASE_URL")
    return base


def _request_headers(headers: Mapping[str, str], body_reencoded: bool = False) -> Dict[str, str]:
    blocked = HOP_BY_HOP_HEADERS | {"host", "content-length"}
    if body_reencoded:
        blocked.add("content-encoding")
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


async def _request_json(request: Request) -> Any:
    body = await request.body()
    encoding = request.headers.get("content-encoding", "").lower().strip()
    if encoding in {"", "identity"}:
        decoded = body
    elif encoding == "gzip":
        decoded = gzip.decompress(body)
    elif encoding in {"zstd", "zstandard"}:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body)) as reader:
            decoded = reader.read()
    else:
        raise ValueError("unsupported request content-encoding: " + encoding)
    return json.loads(decoded)


def _response_headers(headers: Mapping[str, str], streaming: bool) -> Dict[str, str]:
    blocked = set(HOP_BY_HOP_HEADERS)
    if streaming:
        blocked.add("content-length")
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def _route_headers(record: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "X-Codex-Router-Id": str(record["route_id"]),
        "X-Codex-Router-Model": str(record["model"]),
        "X-Codex-Router-Effort": str(record["reasoning_effort"]),
        "X-Codex-Router-Rules": ",".join(record["matched_rules"]),
    }


def _websocket_upstream(headers: Mapping[str, str]) -> str:
    base = _upstream_base(headers)
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + "/responses"


def _websocket_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    blocked = HOP_BY_HOP_HEADERS | {
        "host",
        "content-length",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def _route_websocket_message(message: str) -> Tuple[str, Dict[str, Any]]:
    payload = json.loads(message)
    if not isinstance(payload, dict) or payload.get("type") != "response.create":
        raise ValueError("unsupported Responses WebSocket message")
    routed, record = route_payload(payload, _route_store)
    return json.dumps(routed, ensure_ascii=False, separators=(",", ":")), record


def _remember_websocket_event(message: str, record: Optional[Mapping[str, Any]]) -> None:
    if record is None:
        return
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return
    if payload.get("type") == "response.created":
        response_value = payload.get("response")
        if isinstance(response_value, dict):
            _remember_response_id(response_value.get("id"), record)


def _remember_response_id(response_id: Any, record: Mapping[str, Any]) -> None:
    if isinstance(response_id, str) and response_id:
        _route_store.remember(
            response_id,
            str(record["model"]),
            str(record["model_id"]),
            str(record["reasoning_effort"]),
        )


async def _stream_upstream(
    response: httpx.Response,
    client: httpx.AsyncClient,
    record: Mapping[str, Any],
) -> AsyncIterator[bytes]:
    pending = b""
    try:
        async for chunk in response.aiter_raw():
            pending += chunk
            while b"\n\n" in pending:
                event, pending = pending.split(b"\n\n", 1)
                for line in event.splitlines():
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if payload.get("type") == "response.created":
                        response_value = payload.get("response")
                        if isinstance(response_value, dict):
                            _remember_response_id(response_value.get("id"), record)
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    configured = os.environ.get("CODEX_ROUTER_UPSTREAM_BASE_URL")
    return {
        "status": "ok",
        "router": "rules",
        "upstream_mode": "explicit" if configured else "auth_header_auto",
    }


@app.get("/v1/router/last")
async def last_route() -> Response:
    if _last_route is None:
        return JSONResponse({"status": "empty"}, status_code=404)
    return JSONResponse(_last_route)


@app.websocket("/v1/responses")
async def responses_websocket(websocket: WebSocket) -> None:
    global _last_route
    upstream = None
    try:
        upstream = await websockets.connect(
            _websocket_upstream(websocket.headers),
            additional_headers=_websocket_headers(websocket.headers),
            max_size=None,
            open_timeout=20,
        )
        accept_headers = []
        for name in ("openai-model", "x-reasoning-included", "x-codex-turn-state"):
            value = upstream.response.headers.get(name)
            if value is not None:
                accept_headers.append((name.encode("ascii"), value.encode("latin-1")))
        await websocket.accept(headers=accept_headers)
        state: Dict[str, Optional[Dict[str, Any]]] = {"record": None}

        async def client_to_upstream() -> None:
            global _last_route
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                text_value = message.get("text")
                if text_value is None:
                    raise ValueError("binary Responses WebSocket requests are unsupported")
                routed, record = _route_websocket_message(text_value)
                state["record"] = record
                _last_route = record
                _safe_log(record)
                await upstream.send(routed)

        async def upstream_to_client() -> None:
            async for message in upstream:
                if isinstance(message, str):
                    _remember_websocket_event(message, state["record"])
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(message)

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="local router websocket error")
        except RuntimeError:
            pass
    finally:
        if upstream is not None:
            await upstream.close()


@app.post("/v1/responses")
async def responses(request: Request) -> Response:
    global _last_route
    try:
        payload = await _request_json(request)
        if not isinstance(payload, dict):
            raise ValueError("request JSON must be an object")
        routed, record = route_payload(payload, _route_store)
        _last_route = record
        _safe_log(record)
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0))
        upstream_request = client.build_request(
            "POST",
            _upstream_base(request.headers) + "/responses",
            headers=_request_headers(request.headers, body_reencoded=True),
            json=routed,
        )
        upstream = await client.send(upstream_request, stream=bool(routed.get("stream")))
        headers = _response_headers(upstream.headers, bool(routed.get("stream")))
        headers.update(_route_headers(record))
        if routed.get("stream"):
            return StreamingResponse(
                _stream_upstream(upstream, client, record),
                status_code=upstream.status_code,
                headers=headers,
                media_type=None,
            )
        content = await upstream.aread()
        try:
            response_payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response_payload = {}
        if isinstance(response_payload, dict):
            _remember_response_id(response_payload.get("id"), record)
        await upstream.aclose()
        await client.aclose()
        return Response(content, status_code=upstream.status_code, headers=headers)
    except (ValueError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "invalid_request_error"}}, status_code=400)
    except (httpx.HTTPError, RuntimeError) as exc:
        return JSONResponse(
            {"error": {"message": "Local router could not reach its configured upstream.", "type": "router_upstream_error"}},
            status_code=502,
            headers={"X-Codex-Router-Error": type(exc).__name__},
        )


@app.get("/v1/models")
async def models(request: Request) -> Response:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
            upstream = await client.get(
                _upstream_base(request.headers) + "/models",
                headers=_request_headers(request.headers),
                params=request.query_params,
            )
        return Response(
            upstream.content,
            status_code=upstream.status_code,
            headers=_response_headers(upstream.headers, streaming=False),
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        return JSONResponse(
            {"error": {"message": "Local router could not reach its configured upstream.", "type": "router_upstream_error"}},
            status_code=502,
            headers={"X-Codex-Router-Error": type(exc).__name__},
        )
