#!/usr/bin/env bash
set -euo pipefail

PACKAGE="jp.naver.line.android"
ACTIVITY="${PACKAGE}/.activity.SplashActivity"
LOG_TAG="[line-watchdog]"

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0

# Wait for waydroid session to be running (up to 30s)
status_out="$(waydroid status 2>/dev/null || true)"
for _ in $(seq 1 10); do
    if grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
        break
    fi
    sleep 3
    status_out="$(waydroid status 2>/dev/null || true)"
done

if ! grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session is not running; skipping"
    exit 0
fi

# Ensure LINE process is running inside Android
if sudo waydroid shell -- ps -A 2>/dev/null | grep -q "$PACKAGE"; then
    echo "$LOG_TAG LINE already running"
else
    echo "$LOG_TAG Launching LINE"
    sudo waydroid shell -- am start -n "$ACTIVITY" >/dev/null 2>&1 || true
    sleep 3
    if sudo waydroid shell -- ps -A 2>/dev/null | grep -q "$PACKAGE"; then
        echo "$LOG_TAG LINE launched successfully"
    else
        echo "$LOG_TAG Failed to confirm LINE process after launch"
    fi
fi

# Check SSE endpoint health
SSE_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${LINE_MCP_API_KEY}" \
    --max-time 5 http://localhost:8765/sse 2>/dev/null) || true
SSE_CODE="${SSE_CODE:-000}"

if [ "$SSE_CODE" != "200" ]; then
    echo "$LOG_TAG SSE endpoint returned ${SSE_CODE}, restarting line-mcp-sse"
    sudo systemctl restart line-mcp-sse.service
else
    echo "$LOG_TAG SSE endpoint ok (${SSE_CODE})"
fi
