#!/bin/bash
# Watchdog: checks waydroid container state + line-mcp health endpoint.
# Restarts waydroid-session-xvfb (and dependents) if container is frozen,
# or just line-mcp if the health endpoint is unresponsive.
set -euo pipefail

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0

CONTAINER_STATE=
HEALTH_CODE=000 || true
HEALTH_CODE="${HEALTH_CODE:-000}"

if [ "$CONTAINER_STATE" != "RUNNING" ]; then
    echo "watchdog: waydroid container is '${CONTAINER_STATE:-unknown}', restarting waydroid-session-xvfb"
    systemctl restart waydroid-session-xvfb.service
    exit 0
fi

if [ "$HEALTH_CODE" != "200" ]; then
    echo "watchdog: health endpoint returned ${HEALTH_CODE}, restarting line-mcp"
    systemctl restart line-mcp.service
    exit 0
fi

echo "watchdog: ok (container=RUNNING, health=${HEALTH_CODE})"
