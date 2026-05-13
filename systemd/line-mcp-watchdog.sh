#!/bin/bash
# Watchdog: checks waydroid container state + SSE endpoint health.
# Restarts waydroid-session-xvfb (and dependents) if container is frozen,
# or just line-mcp-sse if the SSE endpoint is unresponsive.
set -euo pipefail

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0

CONTAINER_STATE=$(waydroid status 2>/dev/null | awk '/^Container:/{print $2}')
SSE_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${LINE_MCP_API_KEY}" \
    --max-time 5 http://localhost:8765/sse 2>/dev/null) || true
SSE_CODE="${SSE_CODE:-000}"

if [ "$CONTAINER_STATE" != "RUNNING" ]; then
    echo "watchdog: waydroid container is '${CONTAINER_STATE:-unknown}', restarting waydroid-session-xvfb"
    systemctl restart waydroid-session-xvfb.service
    exit 0
fi

if [ "$SSE_CODE" != "200" ]; then
    echo "watchdog: SSE endpoint returned ${SSE_CODE}, restarting line-mcp-sse"
    systemctl restart line-mcp-sse.service
    exit 0
fi

echo "watchdog: ok (container=RUNNING, sse=${SSE_CODE})"
