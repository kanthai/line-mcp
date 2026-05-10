#!/bin/bash
set -e
VENV_DIR="/home/kanthai/.hermes/hermes-agent/venv"
HERMES_SRC="/home/kanthai/.hermes/hermes-agent"

if ! "$VENV_DIR/bin/python3" -c "import sys" 2>/dev/null; then
    echo "[hermes-entrypoint] Rebuilding venv (first run or wrong arch)..."
    python3 -m venv --clear "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet -e "$HERMES_SRC[messaging,mcp]"
    echo "[hermes-entrypoint] Done."
fi

exec "$VENV_DIR/bin/python" -m hermes_cli.main gateway run --replace "$@"
