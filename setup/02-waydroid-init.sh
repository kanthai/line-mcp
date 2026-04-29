#!/usr/bin/env bash
# Phase 1b: Initialize Waydroid with vanilla Android 13 (arm64)
WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
# Run as: sudo bash 02-waydroid-init.sh
#
# Safe to re-run: skips init if images already exist (won't wipe a patched image).
# Use --force to re-download images (destructive — wipes Magisk patches).
set -euo pipefail

FORCE="${1:-}"
SYSTEM_IMG="/var/lib/waydroid/images/system.img"
USER_UID=$(id -u "${WAYDROID_USER}")
USER_XDG="/run/user/${USER_UID}"
USER_HOME=$(getent passwd "${WAYDROID_USER}" | cut -d: -f6)
WAYLAND_DISPLAY_NAME="${WAYLAND_DISPLAY:-}"
if [[ -z "$WAYLAND_DISPLAY_NAME" ]]; then
    if [[ -S "${USER_XDG}/wayland-rdp" ]]; then
        WAYLAND_DISPLAY_NAME="wayland-rdp"
    else
        WAYLAND_DISPLAY_NAME="wayland-0"
    fi
fi
WAYLAND_SOCKET="${USER_XDG}/${WAYLAND_DISPLAY_NAME}"
WESTON_LOG="/tmp/weston-headless.log"

ensure_wayland_socket() {
    if [[ -S "$WAYLAND_SOCKET" ]]; then
        return 0
    fi

    if [[ "$WAYLAND_DISPLAY_NAME" != "wayland-0" ]]; then
        echo "ERROR: Wayland socket missing: $WAYLAND_SOCKET"
        exit 1
    fi

    if ! command -v weston > /dev/null 2>&1; then
        echo "ERROR: weston is not installed."
        echo "Install it with: sudo apt-get install -y weston"
        exit 1
    fi

    echo "==> Starting headless Weston compositor"
    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" \
        XDG_RUNTIME_DIR="$USER_XDG" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
        nohup weston \
            --backend=headless \
            --socket="$WAYLAND_DISPLAY_NAME" \
            --idle-time=0 \
            --width=1280 \
            --height=720 > "$WESTON_LOG" 2>&1 &

    for i in $(seq 1 20); do
        if [[ -S "$WAYLAND_SOCKET" ]]; then
            echo "Weston is ready"
            return 0
        fi
        sleep 1
    done

    echo "ERROR: Weston did not create $WAYLAND_SOCKET"
    echo "Check log: $WESTON_LOG"
    exit 1
}

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

if [[ ! -e /dev/binder && ! -e /dev/anbox-binder && ! -e /dev/binderfs/binder && ! -e /dev/binderfs/anbox-binder ]]; then
    echo "ERROR: binder device missing. Run 01-waydroid-install.sh first."
    exit 1
fi

if [[ -f "$SYSTEM_IMG" && -z "$FORCE" ]]; then
    echo "==> Images already exist at $SYSTEM_IMG — skipping init."
    echo "    Pass --force to wipe and re-download (destroys Magisk patches)."
else
    echo "==> Initializing Waydroid (vanilla Android 13, $(uname -m))"
    echo "    Downloads ~800MB from sourceforge.net..."
    # No -f flag here; the guard above handles re-runs safely.
    # If forcing: waydroid init -s VANILLA will re-download fresh images.
    waydroid init -s VANILLA
    echo "Init complete."
fi

echo ""
echo "==> Starting Waydroid session"
# XDG_RUNTIME_DIR must exist (it does if linger is enabled from script 01).
if [[ ! -d "$USER_XDG" ]]; then
    echo "ERROR: $USER_XDG does not exist."
    echo "linger not enabled? Run: loginctl enable-linger ${WAYDROID_USER}"
    exit 1
fi

ensure_wayland_socket

# waydroid session start must run as ${WAYDROID_USER} with the correct env.
# nohup keeps it alive after this script exits.
sudo -E -u "${WAYDROID_USER}" \
    HOME="$USER_HOME" \
    XDG_RUNTIME_DIR="$USER_XDG" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
    WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
    nohup waydroid session start > /tmp/waydroid-session.log 2>&1 &

echo "Session starting (log: /tmp/waydroid-session.log)"
echo "Waiting up to 3 minutes for RUNNING state..."

for i in $(seq 1 60); do
    STATUS=$(sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" \
        XDG_RUNTIME_DIR="$USER_XDG" \
        WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
        waydroid status 2>/dev/null | grep -oP 'Session:\s*\K\S+' || true)
    if [[ "$STATUS" == "RUNNING" ]]; then
        echo "Waydroid is RUNNING"
        break
    fi
    if [[ $i -eq 60 ]]; then
        echo "ERROR: Timed out waiting for RUNNING. Check /tmp/waydroid-session.log"
        exit 1
    fi
    echo "  [$i/60] ${STATUS:-unknown}..."
    sleep 3
done

echo ""
echo "Done. Next: bash 03-install-line-headless-apkm.sh  (or 03b-install-line.sh for single APK)"
