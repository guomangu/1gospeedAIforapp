#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$ROOT_DIR/.venv/bin:$PATH"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export STRUCTURED_AI_ENV="${STRUCTURED_AI_ENV:-$ROOT_DIR/.env}"
PY_BIN="$ROOT_DIR/.venv/bin/python"
[ ! -x "$PY_BIN" ] && PY_BIN="python3"
exec "$PY_BIN" -m structuredai.zed_acp
