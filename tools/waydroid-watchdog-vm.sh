#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="[waydroid-watchdog]"
WAYDROID_IP="192.168.240.112"
WAYDROID_GW="192.168.240.1"
WAYDROID_NET="192.168.240.0/24"

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0

_ensure_network_routes() {
    sudo waydroid shell -- sh -c "
        ip route add ${WAYDROID_NET} dev eth0 src ${WAYDROID_IP} table eth0 2>/dev/null || true
        ip route add default via ${WAYDROID_GW} dev eth0 table eth0 2>/dev/null || true
        ip route show table eth0 2>/dev/null | grep -q '^default via ${WAYDROID_GW} dev eth0' || exit 1
    " 2>/dev/null || true
    echo "$LOG_TAG Container eth0 routes ensured"
}

_wait_for_running() {
    local timeout="${1:-15}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        local s
        s="$(waydroid status 2>/dev/null || true)"
        if grep -q "Session:[[:space:]]*RUNNING" <<<"$s" && \
           ! grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$s"; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

status_out="$(waydroid status 2>/dev/null || true)"

if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Container is FREEZING/FROZEN — unfreezing"
    sudo /usr/bin/waydroid container unfreeze
    if _wait_for_running 15; then
        echo "$LOG_TAG Container unfrozen and running"
        _ensure_network_routes
        exit 0
    fi
    echo "$LOG_TAG Container still not running after unfreeze; restarting service chain"
    sudo systemctl restart waydroid-session-xvfb.service
    _wait_for_running 30 || true
    exit 0
fi

if grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out" && \
   ! grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session already running"
    _ensure_network_routes
    exit 0
fi

echo "$LOG_TAG Session not running — restarting waydroid-session-xvfb"
sudo systemctl restart waydroid-session-xvfb.service

if _wait_for_running 30; then
    echo "$LOG_TAG Waydroid session recovered"
    _ensure_network_routes
    exit 0
fi

echo "$LOG_TAG Failed to recover Waydroid session"
exit 1
