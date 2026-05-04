#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="[line-token-refresh]"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

status_out="$(waydroid status 2>/dev/null || true)"
if ! grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session not running; skipping"
    exit 0
fi

if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Container frozen — unfreezing before token refresh"
    sudo /usr/bin/waydroid container unfreeze
    sleep 5
    status_out="$(waydroid status 2>/dev/null || true)"
    if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
        echo "$LOG_TAG Container still frozen after unfreeze attempt; skipping"
        exit 1
    fi
fi

echo "$LOG_TAG Refreshing CDN token from LINE SQLite DB"
python3 "${SCRIPT_DIR}/refresh_token.py" 2>&1 | sed "s/^/$LOG_TAG /"
