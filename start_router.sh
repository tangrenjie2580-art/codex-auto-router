#!/bin/sh
set -eu

ROUTER_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$ROUTER_DIR/.venv"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$ROUTER_DIR/requirements.txt"
fi

exec "$VENV_DIR/bin/python" -m uvicorn responses_router:app \
  --app-dir "$ROUTER_DIR" \
  --host 127.0.0.1 \
  --port 8787 \
  --no-access-log

