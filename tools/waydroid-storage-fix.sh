#!/bin/bash
# Fix Waydroid's broken FUSE emulated storage by bind-mounting the real data
# directory under /mnt (shared peer-group propagates to all app namespaces).
# Run as root after `waydroid session start`, or install as LXC post-start hook.

LXC_PATH="/var/lib/waydroid/lxc"

INIT_PID=$(lxc-info -P "$LXC_PATH" -n waydroid -sH 2>/dev/null | awk '{print $2}')

if [ -z "$INIT_PID" ] || [ ! -d "/proc/$INIT_PID" ]; then
    echo "waydroid-storage-fix: container not running" >&2
    exit 1
fi

# Wait up to 45s for vold to create /mnt/user/0
for i in $(seq 1 45); do
    if nsenter --mount=/proc/"$INIT_PID"/ns/mnt -- test -d /mnt/user/0 2>/dev/null; then
        break
    fi
    sleep 1
done

nsenter --mount=/proc/"$INIT_PID"/ns/mnt -- sh -c '
    mkdir -p /mnt/user/0/emulated/0
    if mountpoint -q /mnt/user/0/emulated/0 2>/dev/null; then
        echo "already mounted"
        exit 0
    fi
    mount --bind /data/media/0 /mnt/user/0/emulated/0
    echo "bind mount OK: /storage/emulated/0 -> /data/media/0"
' 2>&1
