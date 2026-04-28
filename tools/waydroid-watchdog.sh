#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="[waydroid-watchdog]"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_SCRIPT="${SCRIPT_DIR}/../setup/02-waydroid-init.sh"
FRIDA_BIN="/data/local/tmp/frida-server"
FRIDA_PORT=27042
# Standard Waydroid container IP — adjust if your subnet differs
WAYDROID_IP="192.168.240.112"
WAYDROID_GW="192.168.240.1"
WAYDROID_NET="192.168.240.0/24"

_ensure_network_routes() {
    # Android policy routing (rule 29998 → table eth0) has no IPv4 routes after container restart.
    # Without routes in the eth0 table, frida-server's TCP replies are dropped by the unreachable catchall.
    # Gratuitous ARP populates the host ARP cache (arp_ignore=1 blocks unsolicited responses).
    sudo waydroid shell -- sh -c "
        ip route add ${WAYDROID_NET} dev eth0 src ${WAYDROID_IP} table eth0 2>/dev/null || true
        ip route add default via ${WAYDROID_GW} dev eth0 table eth0 2>/dev/null || true
        ip route show table eth0 2>/dev/null | grep -q '^default via ${WAYDROID_GW} dev eth0' || exit 1
        arping -I eth0 -A -c 2 ${WAYDROID_IP} >/dev/null 2>&1 || true
    " 2>/dev/null || true
    echo "$LOG_TAG Container eth0 routes ensured"
}

_ensure_emulated_storage() {
    # MediaProvider's ExternalStorageService can ANR in this Waydroid image, leaving
    # /storage/emulated/0 unmounted. LINE then shows blank media and may stall sync.
    sudo waydroid shell -- sh -c "
        if ! mount | grep -q ' on /storage/emulated/0 '; then
            mount -o bind /data/media/0 /storage/emulated/0 2>/dev/null || true
        fi
        mkdir -p /storage/emulated/0/Android/data/jp.naver.line.android/files 2>/dev/null || true
        test -d /storage/emulated/0/Android/data/jp.naver.line.android/files
    " 2>/dev/null || true
    echo "$LOG_TAG Emulated storage path ensured"
}

_ensure_frida_server() {
    if sudo waydroid shell -- sh -c "ps -A | grep -q '[f]rida-server'" 2>/dev/null; then
        echo "$LOG_TAG frida-server already running"
        return 0
    fi
    echo "$LOG_TAG Starting frida-server"
    sudo waydroid shell -- sh -c \
        "setsid ${FRIDA_BIN} -l 0.0.0.0:${FRIDA_PORT} </dev/null >/data/local/tmp/frida.log 2>&1 &" \
        2>/dev/null
    sleep 3
    if sudo waydroid shell -- sh -c "ps -A | grep -q '[f]rida-server'" 2>/dev/null; then
        echo "$LOG_TAG frida-server started"
    else
        echo "$LOG_TAG WARNING: frida-server failed to start"
    fi
}

status_out="$(waydroid status 2>/dev/null || true)"

if grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Container is FREEZING/FROZEN — unfreezing"
    sudo /usr/bin/waydroid container unfreeze
    sleep 3
    status_out="$(waydroid status 2>/dev/null || true)"
fi

if grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out" && \
   ! grep -qE "Container:[[:space:]]*(FREEZING|FROZEN)" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session already running"
    _ensure_network_routes
    _ensure_emulated_storage
    _ensure_frida_server
    exit 0
fi

echo "$LOG_TAG Restarting waydroid-container"
sudo systemctl restart waydroid-container
sleep 5

echo "$LOG_TAG Reinitializing Waydroid session"
sudo "$INIT_SCRIPT" >/dev/null

sleep 5
status_out="$(waydroid status 2>/dev/null || true)"
if grep -q "Session:[[:space:]]*RUNNING" <<<"$status_out"; then
    echo "$LOG_TAG Waydroid session recovered"
    _ensure_network_routes
    _ensure_emulated_storage
    _ensure_frida_server
    exit 0
fi

echo "$LOG_TAG Failed to recover Waydroid session"
exit 1
