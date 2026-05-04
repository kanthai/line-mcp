#!/usr/bin/env bash
set -euo pipefail

PACKAGE="jp.naver.line.android"
ACTIVITY="${PACKAGE}/.activity.SplashActivity"
LOG_TAG="[line-watchdog]"

status_out="$(waydroid status 2>/dev/null || true)"

for _ in $(seq 1 10); do
    if grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
        break
    fi
    sleep 3
    status_out="$(waydroid status 2>/dev/null || true)"
done

if ! grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session is not running after wait; skipping"
    exit 0
fi

if sudo waydroid shell -- ps -A | grep -q "$PACKAGE"; then
    echo "$LOG_TAG LINE already running"
    exit 0
fi

echo "$LOG_TAG Launching LINE"
sudo waydroid shell -- am start -n "$ACTIVITY" >/dev/null

sleep 3

if sudo waydroid shell -- ps -A | grep -q "$PACKAGE"; then
    echo "$LOG_TAG LINE launched successfully"
    exit 0
fi

echo "$LOG_TAG Failed to confirm LINE process after launch"
exit 1
