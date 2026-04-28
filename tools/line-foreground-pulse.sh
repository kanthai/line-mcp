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

echo "$LOG_TAG Bringing LINE to foreground"
sudo waydroid shell -- am start -n "$ACTIVITY" >/dev/null
echo "$LOG_TAG Pulse sent"
