#!/usr/bin/env bash
set -euo pipefail

PACKAGE="jp.naver.line.android"
ACTIVITY="${PACKAGE}/.activity.SplashActivity"
LOG_TAG="[line-foreground-pulse]"

status_out="$(waydroid status 2>/dev/null || true)"

if ! grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session is not running; skipping"
    exit 0
fi

if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid container is freezing/frozen; skipping"
    exit 0
fi

if sudo waydroid shell -- ps -A 2>/dev/null | grep -q "$PACKAGE"; then
    echo "$LOG_TAG Bringing LINE to foreground"
else
    echo "$LOG_TAG LINE not running; launching"
fi

sudo waydroid shell -- am start -n "$ACTIVITY" >/dev/null
exit 0
