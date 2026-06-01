#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="[line-watchdog]"
PACKAGE="jp.naver.line.android"
ACTIVITY="${PACKAGE}/.activity.SplashActivity"
WAYDROID_IP="192.168.240.112"
WAYDROID_GW="192.168.240.1"
WAYDROID_NET="192.168.240.0/24"
PULSE_COUNTER="/run/line-watchdog-pulse-count"
PULSE_EVERY=5  # every 5 × 2 min = 10 min

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0

_ensure_network_routes() {
    sudo waydroid shell -- sh -c "
        ip route add ${WAYDROID_NET} dev eth0 src ${WAYDROID_IP} table eth0 2>/dev/null || true
        ip route add default via ${WAYDROID_GW} dev eth0 table eth0 2>/dev/null || true
    " 2>/dev/null || true
    echo "$LOG_TAG network routes ensured"
}

_wait_for_running() {
    local timeout="${1:-15}" elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        local s
        s="$(waydroid status 2>/dev/null || true)"
        if grep -q "Session:[[:space:]]*RUNNING" <<<"$s" && \
           ! grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$s"; then
            return 0
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    return 1
}

# ── 1. Waydroid health ──────────────────────────────────────────────────────
status_out="$(waydroid status 2>/dev/null || true)"
waydroid_ok=false

if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG container FROZEN — unfreezing"
    sudo /usr/bin/waydroid container unfreeze
    if _wait_for_running 15; then
        echo "$LOG_TAG unfrozen"
        _ensure_network_routes
        waydroid_ok=true
    else
        echo "$LOG_TAG unfreeze failed — restarting waydroid-session-xvfb"
        sudo systemctl restart waydroid-session-xvfb.service
        _wait_for_running 30 || true
        _ensure_network_routes
    fi
elif grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    _ensure_network_routes
    waydroid_ok=true
else
    echo "$LOG_TAG session not running — restarting waydroid-session-xvfb"
    sudo systemctl restart waydroid-session-xvfb.service
    if _wait_for_running 30; then
        echo "$LOG_TAG session recovered"
        _ensure_network_routes
        waydroid_ok=true
    else
        echo "$LOG_TAG failed to recover Waydroid session"
    fi
fi

# ── 2. SSE health ───────────────────────────────────────────────────────────
HEALTH_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${LINE_MCP_API_KEY}" \
    --max-time 5 http://localhost:8765/health 2>/dev/null) || true
HEALTH_CODE="${HEALTH_CODE:-000}"

if [ "$HEALTH_CODE" != "200" ]; then
    echo "$LOG_TAG health returned ${HEALTH_CODE} — restarting line-mcp"
    sudo systemctl restart line-mcp.service
else
    echo "$LOG_TAG health ok (${HEALTH_CODE})"
fi

# ── 3. LINE foreground pulse (every PULSE_EVERY ticks) ──────────────────────
if $waydroid_ok; then
    count=$(cat "$PULSE_COUNTER" 2>/dev/null || echo 0)
    count=$((count + 1))
    if [ "$count" -ge "$PULSE_EVERY" ]; then
        echo "$LOG_TAG foreground pulse"
        sudo waydroid shell -- am start -n "$ACTIVITY" >/dev/null 2>&1 || true
        count=0
    fi
    echo "$count" > "$PULSE_COUNTER"
fi

echo "$LOG_TAG done"
