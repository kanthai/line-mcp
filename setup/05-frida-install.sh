#!/usr/bin/env bash
# Phase 3: Install frida-server in the Waydroid container.
WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
#
# Downloads frida-server for android-arm64, pushes it into the container,
# and installs a systemd service (line-mcp-frida.service) that starts it
# after waydroid-container is up.
#
# Run as: sudo bash 05-frida-install.sh
#
# After running, test with:
#   waydroid shell -- ps -A | grep frida
#   python3 -c "import frida; d=frida.get_remote_device('$(cat /tmp/waydroid-ip 2>/dev/null || echo <waydroid-ip>):27042'); print(d)"
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

# ---- config ----

FRIDA_VERSION="${FRIDA_VERSION:-17.9.1}"
case "$(uname -m)" in
    aarch64) FRIDA_ARCH="${FRIDA_ARCH:-android-arm64}" ;;
    x86_64)  FRIDA_ARCH="${FRIDA_ARCH:-android-x86_64}" ;;
    *)       FRIDA_ARCH="${FRIDA_ARCH:-android-arm64}" ;;
esac
FRIDA_BINARY="frida-server-${FRIDA_VERSION}-${FRIDA_ARCH}"
FRIDA_URL="https://github.com/frida/frida/releases/download/${FRIDA_VERSION}/${FRIDA_BINARY}.xz"
FRIDA_DEST="/data/local/tmp/frida-server"
FRIDA_PORT="${FRIDA_PORT:-27042}"

USER_UID=$(id -u "${WAYDROID_USER}")
USER_XDG="/run/user/${USER_UID}"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# ---- helpers ----

wsh() {
    XDG_RUNTIME_DIR="$USER_XDG" waydroid shell -- sh -c "$*"
}

waydroid_ip() {
    XDG_RUNTIME_DIR="$USER_XDG" waydroid status 2>/dev/null \
        | grep -oP 'IP address:\s*\K[\d.]+' \
        || echo ""
}

# ---- preflight ----

echo "==> Checking Waydroid is running"
STATUS=$(XDG_RUNTIME_DIR="$USER_XDG" waydroid status 2>/dev/null \
    | grep -oP 'Session:\s*\K\S+' || echo "STOPPED")
if [[ "$STATUS" != "RUNNING" ]]; then
    echo "ERROR: Waydroid session is $STATUS. Start it first."
    exit 1
fi

# ---- download ----

FRIDA_XZ="$TMP_DIR/${FRIDA_BINARY}.xz"
FRIDA_BIN="$TMP_DIR/frida-server"

echo "==> Downloading frida-server ${FRIDA_VERSION} (${FRIDA_ARCH})"
curl -L --fail --progress-bar -o "$FRIDA_XZ" "$FRIDA_URL"

echo "==> Decompressing"
xz -dk "$FRIDA_XZ" -c > "$FRIDA_BIN"
chmod +x "$FRIDA_BIN"

# ---- push into container ----

echo "==> Pushing frida-server into container"
# /data is inside the LXC container — not bind-mounted to the host.
# Strategy: try ADB push first (fastest), fall back to base64 pipe via waydroid shell.

wsh "mkdir -p /data/local/tmp"

_push_via_adb() {
    local ip
    ip=$(waydroid_ip)
    [[ -z "$ip" ]] && return 1
    XDG_RUNTIME_DIR="$USER_XDG" waydroid prop set persist.waydroid.adb true 2>/dev/null || true
    sleep 2
    adb connect "${ip}:5555" > /dev/null 2>&1 || return 1
    sleep 1
    if ! adb -s "${ip}:5555" devices 2>/dev/null | grep -q "${ip}:5555.*device$"; then
        return 1
    fi
    echo "  Using ADB push (${ip}:5555)"
    adb -s "${ip}:5555" push "$FRIDA_BIN" "$FRIDA_DEST"
    adb -s "${ip}:5555" shell "chmod 755 $FRIDA_DEST"
}

_push_via_base64() {
    echo "  Using base64 pipe via waydroid shell (~60 MB, takes ~30s)"
    base64 "$FRIDA_BIN" | \
        XDG_RUNTIME_DIR="$USER_XDG" waydroid shell -- \
            sh -c "base64 -d > ${FRIDA_DEST} && chmod 755 ${FRIDA_DEST}"
}

if ! _push_via_adb; then
    _push_via_base64
fi

echo "  Pushed to ${FRIDA_DEST}"

# ---- verify ----

echo "==> Verifying binary inside container"
wsh "${FRIDA_DEST} --version" 2>/dev/null | tr -d '\r' \
    || { echo "ERROR: frida-server --version failed"; exit 1; }

# ---- install Python frida module ----

echo "==> Installing frida Python module (version ${FRIDA_VERSION})"
PYTHON3="$(which python3 2>/dev/null || true)"
if [[ -n "$PYTHON3" ]]; then
    sudo -u "${WAYDROID_USER}" "$PYTHON3" -m pip install -q --user "frida==${FRIDA_VERSION}" frida-tools 2>/dev/null \
        || sudo -u "${WAYDROID_USER}" "$PYTHON3" -m pip install -q "frida==${FRIDA_VERSION}" frida-tools
    sudo -u "${WAYDROID_USER}" "$PYTHON3" -c "import frida; print('  frida Python OK:', frida.__version__)"
else
    echo "  WARNING: python3 not found — install frida manually:"
    echo "    pip install frida==${FRIDA_VERSION} frida-tools"
fi

# ---- systemd service ----

echo "==> Installing line-mcp-frida.service"
IP=$(waydroid_ip)
echo "$IP" > /tmp/waydroid-ip

cat > /etc/systemd/system/line-mcp-frida.service <<EOF
[Unit]
Description=frida-server for LINE MCP (Waydroid container)
After=waydroid-container.service
Requires=waydroid-container.service

[Service]
Type=oneshot
User=root
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/waydroid shell -- sh -c "nohup ${FRIDA_DEST} -l 0.0.0.0:${FRIDA_PORT} > /tmp/frida-server.log 2>&1 &"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable line-mcp-frida.service

echo ""
echo "==> Starting frida-server now"
# Kill any existing instance first.
wsh "pkill -f frida-server 2>/dev/null || true"
sleep 1
wsh "nohup ${FRIDA_DEST} -l 0.0.0.0:${FRIDA_PORT} > /tmp/frida-server.log 2>&1 &"
sleep 2

echo "==> Verifying frida-server is listening"
if wsh "ss -tlnp 2>/dev/null | grep :${FRIDA_PORT}" 2>/dev/null | grep -q "${FRIDA_PORT}"; then
    echo "  frida-server listening on :${FRIDA_PORT}"
else
    echo "  WARNING: port ${FRIDA_PORT} not visible via ss — frida may still be starting"
fi

echo ""
echo "Done."
echo ""
echo "  frida-server: container port ${FRIDA_PORT}"
echo "  Waydroid IP:  ${IP:-<check: waydroid status | grep IP>}"
echo "  Connect with: frida.get_remote_device('${IP:-<ip>}:${FRIDA_PORT}')"
echo ""
echo "  Test:"
echo "    python3 -c \"import frida; d=frida.get_remote_device('${IP:-<ip>}:${FRIDA_PORT}'); print(d)\""
echo ""
echo "  Next: python3 tools/refresh_token.py"
echo "  Then trigger any LINE network activity to capture the CDN auth token."
