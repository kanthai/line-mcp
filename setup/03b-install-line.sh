#!/usr/bin/env bash
# Phase 1c: Install LINE APK/XAPK into Waydroid + verify launch
WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
# Run as: bash 03b-install-line.sh /path/to/LINE.apk  (or .xapk / .apkm)
#
# Handles both single APK and split APK bundles (.xapk/.apkm).
# Split bundles are extracted and installed via adb install-multiple.
set -euo pipefail

APK="${1:-}"
USER_XDG="/run/user/$(id -u "${WAYDROID_USER}" 2>/dev/null || echo 1000)"
USER_HOME=$(getent passwd "${WAYDROID_USER}" | cut -d: -f6)
WAYDROID_SESSION_LOG="/tmp/waydroid-session-${WAYDROID_USER}.log"

if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    WAYDROID_DISPLAY="$WAYLAND_DISPLAY"
elif [[ -S "${USER_XDG}/wayland-rdp" ]]; then
    WAYDROID_DISPLAY="wayland-rdp"
else
    WAYDROID_DISPLAY="wayland-0"
fi

# ---- helpers ----

get_waydroid_status() {
    HOME="$USER_HOME" \
    XDG_RUNTIME_DIR="$USER_XDG" \
    WAYLAND_DISPLAY="$WAYDROID_DISPLAY" \
    waydroid status 2>/dev/null \
        | grep -oP 'Session:\s*\K\S+' || true
}

get_waydroid_ip() {
    HOME="$USER_HOME" \
    XDG_RUNTIME_DIR="$USER_XDG" \
    WAYLAND_DISPLAY="$WAYDROID_DISPLAY" \
    waydroid status 2>/dev/null \
        | grep -oP 'IP address:\s*\K[\d.]+' \
        || true
}

connect_adb() {
    local ip
    ip=$(get_waydroid_ip)
    if [[ -z "$ip" ]]; then
        echo "  (could not determine Waydroid IP for ADB)"
        return 1
    fi
    adb connect "${ip}:5555" > /dev/null 2>&1 || true
    # Give it a moment to register
    sleep 2
    adb -s "${ip}:5555" devices 2>/dev/null | grep -q "device$" && {
        echo "$ip"
        return 0
    }
    return 1
}

restart_waydroid_session() {
    sudo -E -u "${WAYDROID_USER}" \
        HOME="$USER_HOME" \
        XDG_RUNTIME_DIR="$USER_XDG" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_XDG}/bus" \
        WAYLAND_DISPLAY="$WAYDROID_DISPLAY" \
        nohup waydroid session start > "$WAYDROID_SESSION_LOG" 2>&1 &
}

# ---- usage ----

if [[ -z "$APK" ]]; then
    echo "Usage: bash $0 /path/to/LINE.apk  (or .xapk / .apkm)"
    echo ""
    echo "Get the LINE APK (arm64-v8a) from:"
    echo "  - Pull from existing Android device:"
    echo "      adb shell pm path jp.naver.line.android"
    echo "      adb pull <path>/base.apk"
    echo "  - APKMirror: search 'LINE: Calls & Messages' — select arm64-v8a variant"
    echo "  - APKPure: download LINE, choose arm64-v8a"
    echo ""
    echo "IMPORTANT: arm64-v8a only — x86/x86_64 APKs will not run on this system."
    exit 1
fi

if [[ ! -f "$APK" ]]; then
    echo "ERROR: File not found: $APK"
    exit 1
fi

EXT="${APK##*.}"

# ---- enable ADB BEFORE starting session ----
# persist.waydroid.adb is read by waydroid-container at session start;
# setting it on an already-running session has no effect until the container
# restarts. Set it now and restart the container before continuing.

echo "==> Enabling ADB inside Waydroid (requires container restart to take effect)"
XDG_RUNTIME_DIR="$USER_XDG" waydroid prop set persist.waydroid.adb true 2>/dev/null || true

# Restart container so the new prop is picked up. Requires sudo.
if sudo -n true 2>/dev/null; then
    echo "==> Restarting waydroid-container so persist.waydroid.adb takes effect"
    sudo systemctl restart waydroid-container
    sleep 5
    # Re-start the user session after the container bounce
    restart_waydroid_session
    echo "Waiting for RUNNING..."
    for i in $(seq 1 60); do
        STATUS=$(get_waydroid_status)
        [[ "$STATUS" == "RUNNING" ]] && { echo "Waydroid RUNNING"; break; }
        [[ $i -eq 60 ]] && { echo "ERROR: Timed out after restart"; exit 1; }
        sleep 3
    done
else
    echo "  (no passwordless sudo — assuming container was restarted manually;"
    echo "   if ADB does not connect below, run: sudo systemctl restart waydroid-container)"
fi

# ---- check waydroid ----

STATUS=$(get_waydroid_status)
if [[ "$STATUS" != "RUNNING" ]]; then
    echo "ERROR: Waydroid not running (status: ${STATUS:-unknown})"
    echo "Run: sudo bash 02-waydroid-init.sh"
    exit 1
fi

WAYDROID_IP=$(connect_adb || true)
if [[ -z "$WAYDROID_IP" ]]; then
    if [[ "$EXT" == "xapk" || "$EXT" == "apkm" ]]; then
        echo "ERROR: Split APK bundles require ADB, but Waydroid ADB is not reachable."
        echo "Run these commands, then retry this script:"
        echo "  sudo systemctl restart waydroid-container"
        echo "  sudo bash $(dirname "$0")/02-waydroid-init.sh"
        exit 1
    fi
    echo "WARNING: ADB not available — falling back to waydroid app install (single APK only)"
fi

# ---- install ----

TMPDIR_SPLIT="/tmp/line_apk_split"

if [[ "$EXT" == "xapk" || "$EXT" == "apkm" ]]; then
    echo "==> Detected split APK bundle (.${EXT})"
    rm -rf "$TMPDIR_SPLIT"
    mkdir -p "$TMPDIR_SPLIT"
    echo "  Extracting..."
    unzip -q "$APK" "*.apk" -d "$TMPDIR_SPLIT"
    APKS=("$TMPDIR_SPLIT"/*.apk)
    echo "  Found ${#APKS[@]} split APK(s): $(basename -a "${APKS[@]}" | tr '\n' ' ')"
    echo "  Installing via adb install-multiple..."
    adb -s "${WAYDROID_IP}:5555" install-multiple -r "${APKS[@]}"
    rm -rf "$TMPDIR_SPLIT"
else
    echo "==> Installing single APK: $APK"
    if [[ -n "$WAYDROID_IP" ]]; then
        adb -s "${WAYDROID_IP}:5555" install -r "$APK"
    else
        XDG_RUNTIME_DIR="$USER_XDG" waydroid app install "$APK"
    fi
fi

echo ""
echo "==> Verifying LINE is installed"
sleep 3
INSTALLED=$(XDG_RUNTIME_DIR="$USER_XDG" waydroid app list 2>/dev/null \
    | grep -i "jp.naver.line.android" || true)
if [[ -z "$INSTALLED" ]]; then
    echo "WARNING: jp.naver.line.android not found in app list"
    echo "  (May still appear after a session restart)"
else
    echo "  $INSTALLED"
fi

echo ""
echo "==> Launching LINE for initial DB creation"
if [[ -n "$WAYDROID_IP" ]]; then
    adb -s "${WAYDROID_IP}:5555" shell \
        "am start -n jp.naver.line.android/.activity.SplashActivity" 2>/dev/null || true
else
    XDG_RUNTIME_DIR="$USER_XDG" waydroid app launch jp.naver.line.android 2>/dev/null || true
fi

echo "Waiting 30s for LINE to initialize..."
sleep 30

# ---- check DB ----

DB_PATH="/var/lib/waydroid/data/data/jp.naver.line.android/databases/naver_line"
echo ""
if [[ $EUID -eq 0 ]] || sudo -n ls "$DB_PATH" > /dev/null 2>&1; then
    if sudo ls "$DB_PATH" > /dev/null 2>&1; then
        echo "SUCCESS: naver_line DB found"
        sudo ls -lh "$DB_PATH"
        echo ""
        echo "==> Checking if naver_line is plain SQLite or SQLCipher-encrypted..."
        COPY="/tmp/naver_line_check.db"
        sudo cp "$DB_PATH" "$COPY"
        sudo chmod 644 "$COPY"
        if sqlite3 "$COPY" "SELECT count(*) FROM sqlite_master;" > /dev/null 2>&1; then
            echo "  PLAIN SQLite — read path will work directly."
        else
            echo "  *** SQLCIPHER ENCRYPTED *** — sqlite3 cannot open this DB directly."
            echo "  Re-install LINE and log in again; the DB should be unencrypted on Android 13."
        fi
        rm -f "$COPY"
    else
        echo "DB not found yet. LINE needs to be logged in first."
        echo "  Log in via: waydroid show-full-ui  (requires a display)"
        echo "  After login the DB will appear at: $DB_PATH"
    fi
else
    echo "  (Need root to check DB path — run as sudo or check manually)"
    echo "  sudo ls $DB_PATH"
fi

echo ""
echo "Next:"
echo "  - Log in to LINE via GUI (waydroid show-full-ui)"
echo "  - Then run: bash 04-db-schema.sh"
