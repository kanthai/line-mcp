#!/usr/bin/env bash
# Install extracted LINE split APKs headlessly through waydroid shell.
# Run as: sudo bash 03-install-line-headless-apkm.sh [/tmp/line_apkm_extract]
set -euo pipefail

APK_DIR="${1:-/tmp/line_apkm_extract}"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0 [apk_dir]"
    exit 1
fi

if [[ ! -d "$APK_DIR" ]]; then
    echo "ERROR: APK directory not found: $APK_DIR"
    exit 1
fi

BASE_APK="$APK_DIR/base.apk"
ABI_APK="$APK_DIR/split_config.arm64_v8a.apk"

if [[ ! -f "$BASE_APK" || ! -f "$ABI_APK" ]]; then
    echo "ERROR: Missing required APKs in $APK_DIR"
    echo "Required: base.apk and split_config.arm64_v8a.apk"
    exit 1
fi

DENSITY_RAW="$(waydroid shell -- wm density | grep -oE '[0-9]+' | tail -1 || true)"
if [[ -z "$DENSITY_RAW" ]]; then
    echo "ERROR: Could not determine Waydroid density"
    exit 1
fi

case "$DENSITY_RAW" in
    120) DENSITY_SPLIT="split_config.ldpi.apk" ;;
    160|180) DENSITY_SPLIT="split_config.mdpi.apk" ;;
    213) DENSITY_SPLIT="split_config.tvdpi.apk" ;;
    240) DENSITY_SPLIT="split_config.hdpi.apk" ;;
    320) DENSITY_SPLIT="split_config.xhdpi.apk" ;;
    480) DENSITY_SPLIT="split_config.xxhdpi.apk" ;;
    640) DENSITY_SPLIT="split_config.xxxhdpi.apk" ;;
    *)
        echo "ERROR: Unsupported density '$DENSITY_RAW'"
        exit 1
        ;;
esac

DENSITY_APK="$APK_DIR/$DENSITY_SPLIT"
if [[ ! -f "$DENSITY_APK" ]]; then
    echo "ERROR: Density split not found: $DENSITY_APK"
    exit 1
fi

echo "Using density $DENSITY_RAW -> $DENSITY_SPLIT"

copy_into_waydroid() {
    local src="$1"
    local dst="$2"
    waydroid shell -- sh -c "cat > '$dst'" < "$src"
}

echo "==> Copying APKs into Waydroid"
copy_into_waydroid "$BASE_APK" /data/local/tmp/base.apk
copy_into_waydroid "$ABI_APK" /data/local/tmp/split_config.arm64_v8a.apk
copy_into_waydroid "$DENSITY_APK" "/data/local/tmp/$DENSITY_SPLIT"

echo "==> Installing LINE"
waydroid shell -- pm install -g \
    /data/local/tmp/base.apk \
    /data/local/tmp/split_config.arm64_v8a.apk \
    "/data/local/tmp/$DENSITY_SPLIT"

echo "==> Verifying install"
waydroid shell -- pm path jp.naver.line.android

echo ""
echo "Next: bash 03b-install-line.sh  (log in via GUI), then sudo bash 05-frida-install.sh"
