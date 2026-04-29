#!/usr/bin/env bash
# Start Waydroid + LINE + frida-server + capture CDN token after a reboot.
# Run as: sudo bash tools/start-after-reboot.sh
set -euo pipefail

WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
FRIDA_BIN="/data/local/tmp/frida-server"
FRIDA_PORT="${FRIDA_PORT:-27042}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

USER_UID=$(id -u "${WAYDROID_USER}")
USER_XDG="/run/user/${USER_UID}"
USER_HOME=$(getent passwd "${WAYDROID_USER}" | cut -d: -f6)

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

wsh() { XDG_RUNTIME_DIR="$USER_XDG" waydroid shell -- sh -c "$*"; }

# ── 1. Wayland compositor ────────────────────────────────────────────────────

echo "==> Wayland compositor"
WAYLAND_DISPLAY_NAME="${WAYLAND_DISPLAY:-}"
if [[ -z "$WAYLAND_DISPLAY_NAME" ]]; then
    [[ -S "${USER_XDG}/wayland-rdp" ]] && WAYLAND_DISPLAY_NAME="wayland-rdp" || WAYLAND_DISPLAY_NAME="wayland-0"
fi

if [[ ! -S "${USER_XDG}/${WAYLAND_DISPLAY_NAME}" ]]; then
    echo "  Starting headless Weston"
    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" XDG_RUNTIME_DIR="$USER_XDG" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
        nohup weston --backend=headless --socket="$WAYLAND_DISPLAY_NAME" \
            --idle-time=0 --width=1280 --height=720 \
            > /tmp/weston-headless.log 2>&1 &
    for i in $(seq 1 20); do
        [[ -S "${USER_XDG}/${WAYLAND_DISPLAY_NAME}" ]] && break
        sleep 1
    done
    [[ -S "${USER_XDG}/${WAYLAND_DISPLAY_NAME}" ]] || { echo "ERROR: Weston failed"; exit 1; }
    echo "  Weston ready"
else
    echo "  Already running"
fi

# ── 2. Waydroid container ────────────────────────────────────────────────────

echo "==> Waydroid container"
systemctl is-active --quiet waydroid-container || systemctl start waydroid-container
sleep 2

# ── 3. Waydroid session ──────────────────────────────────────────────────────

echo "==> Waydroid session"
STATUS=$(sudo -E -u "${WAYDROID_USER}" XDG_RUNTIME_DIR="$USER_XDG" WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
    waydroid status 2>/dev/null | grep -oP 'Session:\s*\K\S+' || true)

if [[ "$STATUS" != "RUNNING" ]]; then
    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" XDG_RUNTIME_DIR="$USER_XDG" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
        WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
        nohup waydroid session start > /tmp/waydroid-session.log 2>&1 &

    echo "  Waiting for RUNNING..."
    for i in $(seq 1 40); do
        STATUS=$(sudo -E -u "${WAYDROID_USER}" XDG_RUNTIME_DIR="$USER_XDG" WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
            waydroid status 2>/dev/null | grep -oP 'Session:\s*\K\S+' || true)
        [[ "$STATUS" == "RUNNING" ]] && break
        [[ $i -eq 40 ]] && { echo "ERROR: Timed out"; cat /tmp/waydroid-session.log; exit 1; }
        echo "  [$i/40] ${STATUS:-unknown}..."
        sleep 3
    done
fi
echo "  Session RUNNING"

# ── 4. Wait for Android boot ─────────────────────────────────────────────────

echo "==> Waiting for Android boot"
for i in $(seq 1 60); do
    boot=$(wsh "/system/bin/getprop sys.boot_completed 2>/dev/null" | tr -d '\r' || true)
    [[ "$boot" == "1" ]] && break
    [[ $i -eq 60 ]] && { echo "ERROR: Android did not boot"; exit 1; }
    echo "  [$i/60] waiting..."
    sleep 3
done
echo "  Android booted"

# ── 5. frida-server ──────────────────────────────────────────────────────────

echo "==> frida-server"
if wsh "ps -A | grep -q '[f]rida-server'" 2>/dev/null; then
    echo "  Already running"
else
    wsh "setsid ${FRIDA_BIN} -l 0.0.0.0:${FRIDA_PORT} </dev/null >/data/local/tmp/frida.log 2>&1 &"
    sleep 3
    wsh "ps -A | grep -q '[f]rida-server'" 2>/dev/null && echo "  Started" || echo "  WARNING: frida-server did not start"
fi

# ── 6. Launch LINE ───────────────────────────────────────────────────────────

echo "==> Launching LINE"
wsh "am start -n jp.naver.line.android/.activity.SplashActivity" > /dev/null 2>&1 || true
sleep 5

# ── 7. Capture CDN token ─────────────────────────────────────────────────────

echo "==> Capturing CDN token"
PYTHON3=$(sudo -u "${WAYDROID_USER}" which python3 2>/dev/null || which python3 2>/dev/null || true)
if [[ -z "$PYTHON3" ]]; then
    echo "  WARNING: python3 not found — run tools/refresh_token.py manually"
else
    REFRESH_SCRIPT="${SCRIPT_DIR}/refresh_token.py"
    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" \
        nohup "$PYTHON3" -u "$REFRESH_SCRIPT" > /tmp/refresh-token.log 2>&1 &
    echo "  Running in background — waiting up to 30s for token..."
    for i in $(seq 1 15); do
        sleep 2
        if [[ -f "${USER_HOME}/.config/line-mcp/auth.json" ]]; then
            TOKEN_LEN=$(sudo -u "${WAYDROID_USER}" python3 -c "import json,pathlib; d=json.loads(pathlib.Path('${USER_HOME}/.config/line-mcp/auth.json').read_text()); print(len(d.get('x_line_access','')))" 2>/dev/null || echo 0)
            [[ "$TOKEN_LEN" -gt 100 ]] && { echo "  Token captured (${TOKEN_LEN} chars)"; break; }
        fi
        [[ $i -eq 15 ]] && echo "  Token not yet captured — LINE may need more time. Check /tmp/refresh-token.log"
    done
fi

# ── Done ─────────────────────────────────────────────────────────────────────

IP=$(sudo -E -u "${WAYDROID_USER}" XDG_RUNTIME_DIR="$USER_XDG" \
    waydroid status 2>/dev/null | grep -oP 'IP address:\s*\K[\d.]+' || echo "unknown")

echo ""
echo "Done."
echo "  Waydroid IP:  $IP"
echo "  frida-server: ${IP}:${FRIDA_PORT}"
echo "  MCP server:   python3 mcp/server.py"
