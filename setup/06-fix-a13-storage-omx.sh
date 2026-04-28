#!/usr/bin/env bash
# Fix Android 13 Waydroid external storage mount on some aarch64 systems.
WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
#
# The 20260403 arm64_only MAINLINE vendor image declares
# android.hardware.media.omx@1.0::IOmxStore/default in VINTF, but does not ship
# a matching service. MediaProvider asks mediaserver for codec/HDR info during
# ExternalStorageService startup; mediaserver then blocks forever waiting for
# the missing OMXStore HAL, so /sdcard FUSE never mounts.
#
# This installs a vendor manifest overlay that removes only the invalid OMX HAL
# declaration, then restarts Waydroid.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PATCH_MANIFEST="$SCRIPT_DIR/patches/vendor-manifest-no-omx.xml"
OVERLAY_MANIFEST="/var/lib/waydroid/overlay/vendor/etc/vintf/manifest.xml"
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

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Run as root: sudo bash $0"
        exit 1
    fi
}

verify_a13_arm64_only() {
    local system_img="/var/lib/waydroid/images/system.img"
    local release device

    if [[ ! -f "$system_img" ]]; then
        echo "ERROR: missing $system_img"
        exit 1
    fi

    release=$(debugfs -R 'cat /system/build.prop' "$system_img" 2>/dev/null \
        | awk -F= '$1 == "ro.build.version.release" {print $2; exit}' | tr -d '\r')
    device=$(debugfs -R 'cat /system/build.prop' "$system_img" 2>/dev/null \
        | awk -F= '$1 == "ro.product.system.device" {print $2; exit}' | tr -d '\r')

    if [[ "$release" != "13" ]]; then
        echo "WARNING: expected Android 13, got release=$release device=$device — proceeding anyway"
    elif [[ "$device" != "waydroid_arm64_only" ]]; then
        echo "WARNING: expected waydroid_arm64_only, got device=$device — proceeding anyway"
    else
        echo "  Verified: Android $release $device"
    fi
}

install_overlay() {
    if [[ ! -f "$PATCH_MANIFEST" ]]; then
        echo "ERROR: missing patch manifest: $PATCH_MANIFEST"
        exit 1
    fi

    mkdir -p "$(dirname "$OVERLAY_MANIFEST")"
    cp "$PATCH_MANIFEST" "$OVERLAY_MANIFEST"
    chmod 0644 "$OVERLAY_MANIFEST"
    echo "Installed overlay: $OVERLAY_MANIFEST"
}

restart_waydroid() {
    echo "==> Restarting Waydroid"
    waydroid session stop 2>/dev/null || true
    systemctl restart waydroid-container

    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" \
        XDG_RUNTIME_DIR="$USER_XDG" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
        WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
        nohup waydroid session start > /tmp/waydroid-session-storage-fix.log 2>&1 &
}

wait_for_boot() {
    echo "==> Waiting for Waydroid boot"
    for i in $(seq 1 90); do
        local status session container ip boot=""
        status=$(sudo -E -u ${WAYDROID_USER} \
            HOME="$USER_HOME" \
            XDG_RUNTIME_DIR="$USER_XDG" \
            WAYLAND_DISPLAY="$WAYLAND_DISPLAY_NAME" \
            waydroid status 2>/dev/null || true)
        session=$(awk -F: '$1 == "Session" {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' <<< "$status")
        container=$(awk -F: '$1 == "Container" {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' <<< "$status")
        ip=$(awk -F: '$1 == "IP address" {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' <<< "$status")

        if [[ "$session" == "RUNNING" && "$container" == "RUNNING" && -n "$ip" && "$ip" != "UNKNOWN" ]]; then
            boot=$(waydroid shell -- /system/bin/getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
            if [[ "$boot" == "1" ]]; then
                echo "Waydroid booted at $ip"
                return 0
            fi
        fi

        echo "  [$i/90] session=${session:-unknown} container=${container:-unknown} ip=${ip:-unknown} boot=${boot:-0}"
        sleep 2
    done

    echo "ERROR: timed out waiting for Waydroid boot"
    echo "Session log: /tmp/waydroid-session-storage-fix.log"
    exit 1
}

verify_storage() {
    echo "==> Verifying /sdcard"
    local mount_line
    mount_line=$(waydroid shell -- /system/bin/mount 2>/dev/null | grep '/storage/emulated type fuse' || true)
    if [[ -z "$mount_line" ]]; then
        echo "ERROR: /storage/emulated FUSE mount is missing"
        waydroid shell -- /system/bin/logcat -d -t 200 \
            | grep -Ei 'StorageManagerService|ExternalStorageService|MediaProvider|IOmxStore|omx|FuseDaemon' || true
        exit 1
    fi

    if ! waydroid shell -u 2000 -- /system/bin/ls -la /storage/self/primary/ >/tmp/waydroid-sdcard-check.log 2>&1; then
        echo "ERROR: shell user cannot access /storage/self/primary"
        cat /tmp/waydroid-sdcard-check.log
        exit 1
    fi

    echo "/sdcard is accessible"
}

if ! command -v debugfs > /dev/null 2>&1; then
    echo "ERROR: debugfs not found — install e2fsprogs: sudo apt-get install -y e2fsprogs"
    exit 1
fi

require_root
verify_a13_arm64_only
install_overlay
restart_waydroid
wait_for_boot
verify_storage

echo ""
echo "A13 storage fix complete."
