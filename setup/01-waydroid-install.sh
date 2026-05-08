#!/usr/bin/env bash
# Phase 1a: Install Waydroid + ADB on Ubuntu 24.04 (aarch64)
WAYDROID_USER="${WAYDROID_USER:-${SUDO_USER:-$(logname)}}"
# Run as: sudo bash 01-waydroid-install.sh
set -euo pipefail

ensure_binderfs_mount() {
    mkdir -p /dev/binderfs
    if ! mountpoint -q /dev/binderfs; then
        mount -t binder binder /dev/binderfs
    fi
}

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" && "$ARCH" != "x86_64" ]]; then
    echo "WARNING: Untested architecture $ARCH — continuing anyway"
fi

echo "==> Enabling linger for ${WAYDROID_USER} (required for headless Waydroid session)"
loginctl enable-linger "${WAYDROID_USER}"
# XDG_RUNTIME_DIR=/run/user/$(id -u "${WAYDROID_USER}") is only valid while the user session
# is active; linger keeps the session alive permanently even without a login.

echo "==> Installing dependencies"
apt-get update -q
apt-get install -y curl ca-certificates android-tools-adb sqlite3 weston \
    unzip xz-utils e2fsprogs python3 python3-pip

echo "==> Adding Waydroid PPA"
# repo.waydro.id installs a signed apt source; review before running in production
curl -fsSL https://repo.waydro.id | bash

echo "==> Installing Waydroid"
apt-get install -y waydroid

if apt-cache show waydroid-dkms > /dev/null 2>&1; then
    echo "==> Installing waydroid-dkms"
    # waydroid-dkms builds binder_linux.ko against the running kernel headers.
    if [[ ! -d /lib/modules/$(uname -r)/build ]]; then
        echo "ERROR: Kernel headers not found at /lib/modules/$(uname -r)/build"
        echo "Install with: sudo apt install linux-headers-$(uname -r)"
        exit 1
    fi
    apt-get install -y waydroid-dkms
else
    echo "==> waydroid-dkms not available in apt; using kernel-provided binder module"
fi

echo "==> Loading binder kernel module"
# Try both names; some kernels ship binder_linux directly.
modprobe binder_linux devices=binder,hwbinder,vndbinder 2>/dev/null || \
modprobe binder 2>/dev/null || {
    echo "ERROR: Could not load binder module."
    echo "Check: dmesg | tail -30"
    echo "DKMS build may have failed: dkms status"
    exit 1
}

# Persist across reboots (use whichever name succeeded)
if lsmod | grep -q binder_linux; then
    echo "binder_linux" > /etc/modules-load.d/waydroid.conf
else
    echo "binder" > /etc/modules-load.d/waydroid.conf
fi

echo "==> Installing binderfs systemd mount unit"
# Persistent binderfs mount via systemd (fstab is unreliable — /dev is tmpfs and
# the mount point directory doesn't exist yet when fstab is processed at boot).
REPO_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp "${REPO_DIR_EARLY}/conf/waydroid-binderfs.tmpfiles" /etc/tmpfiles.d/waydroid-binderfs.conf
cp "${REPO_DIR_EARLY}/systemd/dev-binderfs.mount" /etc/systemd/system/dev-binderfs.mount
systemctl daemon-reload
systemctl enable --now dev-binderfs.mount

echo "==> Mounting binderfs (current session)"
ensure_binderfs_mount

echo "==> Verifying binder devices"
sleep 1
if [[ ! -e /dev/binderfs/binder-control ]]; then
    echo "ERROR: binderfs not mounted correctly."
    echo "  dmesg output:"
    dmesg | grep -i binder | tail -10 || true
    exit 1
fi
ls -la /dev/binderfs/ 2>/dev/null || true
echo "binder OK"

echo "==> Enabling Waydroid container service"
systemctl enable --now waydroid-container

echo "==> Installing sudoers rules for waydroid-watchdog"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDOERS_FILE=/etc/sudoers.d/waydroid-watchdog
cat > "$SUDOERS_FILE" <<SUDOERS
${WAYDROID_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart waydroid-container
${WAYDROID_USER} ALL=(root) NOPASSWD: /usr/bin/waydroid container unfreeze
${WAYDROID_USER} ALL=(root) NOPASSWD: /usr/bin/waydroid container freeze
${WAYDROID_USER} ALL=(root) NOPASSWD: /usr/bin/waydroid shell -- *
${WAYDROID_USER} ALL=(root) NOPASSWD: ${REPO_DIR}/setup/02-waydroid-init.sh
${WAYDROID_USER} ALL=(root) NOPASSWD: /sbin/modprobe binder_linux
${WAYDROID_USER} ALL=(root) NOPASSWD: /bin/mkdir -p /dev/binderfs
${WAYDROID_USER} ALL=(root) NOPASSWD: /bin/mount -t binder binder /dev/binderfs
SUDOERS
chmod 440 "$SUDOERS_FILE"
echo "   Written: $SUDOERS_FILE"

echo ""
echo "Done. Next: sudo bash 02-waydroid-init.sh"
