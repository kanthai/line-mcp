#!/usr/bin/env bash
set -euo pipefail
PACKAGE="jp.naver.line.android"
ACTIVITY="${PACKAGE}/.activity.SplashActivity"
LOG_TAG="[line-watchdog]"
ADB="adb -s 127.0.0.1:5555"

if ! docker inspect --format "{{.State.Running}}" redroid 2>/dev/null | grep -q true; then
    echo "$LOG_TAG Redroid not running; skipping"
    exit 0
fi

$ADB get-state >/dev/null 2>&1 || adb connect 127.0.0.1:5555 >/dev/null 2>&1

if $ADB shell ps -A 2>/dev/null | grep -q "$PACKAGE"; then
    echo "$LOG_TAG LINE already running"
else
    echo "$LOG_TAG Launching LINE"
    $ADB shell am start -n "$ACTIVITY" >/dev/null 2>&1 || true
    sleep 3
    $ADB shell ps -A 2>/dev/null | grep -q "$PACKAGE" && echo "$LOG_TAG LINE launched" || echo "$LOG_TAG LINE launch failed"
fi

MCP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${LINE_MCP_API_KEY}" \
    --max-time 5 http://localhost:8765/mcp 2>/dev/null) || MCP_CODE="000"

if [ "$MCP_CODE" = "406" ] || [ "$MCP_CODE" = "200" ]; then
    echo "$LOG_TAG line-mcp ok"
else
    echo "$LOG_TAG line-mcp returned ${MCP_CODE}, restarting"
    sudo systemctl restart line-mcp.service
fi
