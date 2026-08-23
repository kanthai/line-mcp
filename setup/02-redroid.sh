#!/usr/bin/env bash
# 02 — inside the LXC (root): create the Redroid container and (after the host granted binder)
# wait for Android, connect ADB and apply the keep-awake settings.
#
# Two-phase because binder must be granted by the Proxmox host BEFORE Android boots:
#   phase 1 (this script, first run):   docker create redroid  → go run redroid-binder.service on the host
#   phase 2 (this script, second run):  wait for boot, adb connect, settings
set -euo pipefail
IMAGE="${REDROID_IMAGE:-redroid/redroid:12.0.0_64only-latest}"
NAME="${CONTAINER:-redroid}"
ADB="adb -s 127.0.0.1:5555"
[ "$(id -u)" -eq 0 ] || { echo "run as root inside the LXC" >&2; exit 1; }

if ! docker inspect "$NAME" >/dev/null 2>&1; then
  echo "== creating $NAME ($IMAGE)"
  docker pull "$IMAGE"
  # --restart no on purpose: redroid-binder.service on the HOST starts the container after
  # granting the binder major. use_memfd avoids the ashmem module the PVE kernel lacks.
  docker create --name "$NAME" --privileged --restart no \
    -p 5555:5555 -p 5900:5900 \
    -v redroid-data:/data \
    "$IMAGE" \
    androidboot.use_memfd=true androidboot.redroid_gpu_mode=guest
  cat <<MSG

Container created (not started). Now ON THE PROXMOX HOST run:
    systemctl start redroid-binder.service && journalctl -u redroid-binder.service -n 20 --no-pager
then re-run this script to wait for Android and apply settings.
MSG
  exit 0
fi

if ! docker inspect --format '{{.State.Running}}' "$NAME" | grep -q true; then
  echo "$NAME exists but is not running. Start it from the HOST with: systemctl start redroid-binder.service" >&2
  exit 1
fi

echo "== waiting for Android boot (cold start takes 4–10 min)"
for i in $(seq 1 120); do
  if docker exec "$NAME" getprop sys.boot_completed 2>/dev/null | grep -q 1; then break; fi
  if docker logs --tail 50 "$NAME" 2>&1 | grep -q "Binder driver '/dev/binder' could not be opened"; then
    echo "binder not available inside the container — rerun redroid-binder.service on the host" >&2; exit 1
  fi
  sleep 5
done
docker exec "$NAME" getprop sys.boot_completed | grep -q 1 || { echo "boot_completed never became 1" >&2; exit 1; }
echo "   boot_completed=1"

echo "== adb"
adb start-server >/dev/null
adb connect 127.0.0.1:5555 >/dev/null
$ADB wait-for-device
# After boot `adb devices` often shows BOTH 127.0.0.1:5555 and a ghost emulator-5554 —
# always address the device with -s 127.0.0.1:5555.
$ADB shell getprop ro.build.version.release

echo "== keep-awake / no-doze settings (persist in the redroid-data volume)"
$ADB shell settings put system screen_off_timeout 2147483647
$ADB shell settings put global stay_on_while_plugged_in 7
$ADB shell settings put global wifi_sleep_policy 2
$ADB shell settings put global adaptive_battery_management_enabled 0
$ADB shell settings put global device_idle_enabled 0
$ADB shell settings put global app_standby_enabled 0
$ADB shell settings put global low_power 0
# Cosmetic: Redroid polls a WiFi HAL forever; short-circuit it.
$ADB shell setprop wlan.driver.status failed || true

echo "done. Next: install + log in to LINE — see setup/03-line-login.md"
