#!/usr/bin/env bash
# 00 — Proxmox HOST: binder module + redroid-binder service + LXC features.
# Run as root ON THE PROXMOX HOST from a checkout of this repo, after the LXC exists.
# Create it with the PVE UI / pct create as a **privileged** CT (uncheck "Unprivileged
# container" / `--unprivileged 0`), Debian 12, ≥4 GB RAM, ≥40 GB disk — that is what CT103 is;
# Redroid needs --privileged Docker + binderfs, which an unprivileged CT cannot provide.
#
#   CT_ID=103 CONTAINER=redroid bash setup/00-proxmox-host.sh
set -euo pipefail
CT_ID="${CT_ID:-103}"
CONTAINER="${CONTAINER:-redroid}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run as root on the Proxmox host" >&2; exit 1; }
command -v pct >/dev/null || { echo "pct not found — this must run on the Proxmox host" >&2; exit 1; }
pct status "$CT_ID" >/dev/null 2>&1 || { echo "CT $CT_ID does not exist yet — create it first" >&2; exit 1; }
if pct config "$CT_ID" | grep -q '^unprivileged: 1'; then
  echo "CT $CT_ID is unprivileged — Redroid needs a privileged CT (recreate with --unprivileged 0)" >&2; exit 1
fi

echo "== binder kernel module"
install -m 644 "$HERE/proxmox/modules-load.d-binder.conf" /etc/modules-load.d/binder.conf
modprobe binder_linux
grep -q binder /proc/devices || { echo "binder major not registered — kernel lacks binder_linux?" >&2; exit 1; }
echo "   binder major now: $(awk '$2=="binder"{print $1}' /proc/devices) (dynamic — changes each boot)"

echo "== LXC features (nesting for Docker, keyctl; GPU render node is optional but used on CT103)"
pct set "$CT_ID" -features nesting=1,keyctl=1
if [ -e /dev/dri/renderD128 ] && ! grep -q '^dev0:' "/etc/pve/lxc/$CT_ID.conf"; then
  pct set "$CT_ID" -dev0 /dev/dri/renderD128 || echo "   (dev0 passthrough skipped)"
fi

echo "== binder_alloc helper"
if [ ! -x /usr/local/bin/binder_alloc ]; then
  if command -v gcc >/dev/null && [ -e /usr/include/linux/android/binderfs.h ]; then
    gcc -O2 -o /usr/local/bin/binder_alloc "$HERE/proxmox/binder_alloc.c"
  else
    echo "   gcc / linux headers missing on the host. Build it elsewhere (any x86_64 Linux with" >&2
    echo "   build-essential + linux-libc-dev — e.g. inside the LXC: apt install gcc linux-libc-dev;" >&2
    echo "   gcc -O2 -o binder_alloc proxmox/binder_alloc.c) and copy the binary to /usr/local/bin/binder_alloc." >&2
    exit 1
  fi
fi
install -m 755 "$HERE/proxmox/redroid-binder-alloc" /usr/local/bin/redroid-binder-alloc

echo "== redroid-binder.service"
sed -e "s/--start-container redroid 103/--start-container $CONTAINER $CT_ID/" \
    -e "s/in CT103/in CT$CT_ID/" \
    "$HERE/proxmox/redroid-binder.service" > /etc/systemd/system/redroid-binder.service
systemctl daemon-reload
systemctl enable redroid-binder.service
echo "   enabled: $(systemctl is-enabled redroid-binder.service)"
cat <<MSG

NOTE: feature changes (nesting/keyctl) apply on the next CT start — if CT$CT_ID was already
running: pct reboot $CT_ID

Host side done. Next, inside the LXC:
   pct exec $CT_ID -- bash       # then run setup/01-lxc-base.sh and setup/02-redroid.sh
When 02-redroid.sh has CREATED the '$CONTAINER' container, come back here and run:
   systemctl start redroid-binder.service && journalctl -u redroid-binder.service -n 20
MSG
