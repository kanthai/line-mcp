# Proxmox host side — Redroid binder bring-up

Everything in this directory lives on the **Proxmox host**, not in the LXC.
CT103 is a plain Debian 12 LXC with `nesting=1,keyctl=1`; Docker runs inside it and
Redroid (Android 12 x86_64) runs inside Docker. Android needs binder, and on the PVE
kernel that is harder than it sounds:

| Fact (verified on pve-a2, kernel 7.0.2-6-pve) | Consequence |
|---|---|
| `binder_linux` is built as a module and only offers **binderfs** — no classic `/dev/binder` nodes | nothing to bind-mount into the LXC |
| the module's char-device **major is dynamic and changes every host boot** (seen 509, 511, 235, 236, 237) | the LXC's cgroup device allow-list must be refreshed each boot |
| binderfs devices are created **inside the Redroid container** at `/dev/binderfs/{binder,hwbinder,vndbinder}` | but Android init's seccomp filter blocks the `BINDER_CTL_ADD` ioctl, so Redroid's own allocator silently fails |
| Docker `--privileged` inside an LXC cannot grant a device major the LXC's parent cgroup does not allow | so the host must `lxc-cgroup devices.allow c <major>:* rwm` before the container starts |

Symptom when any of this is missing: Android boots to `init`/`adbd`/`logd` only, no zygote or
system_server, and logcat aborts with `Binder driver '/dev/binder' could not be opened. Terminating.`

## What the unit does

`redroid-binder.service` → `redroid-binder-alloc --start-container redroid 103`:

1. waits for CT103 and its Docker daemon;
2. reads the live binder major from `/proc/devices`, grants it to the LXC with `lxc-cgroup`
   **and** persists exactly that one line into `/etc/pve/lxc/103.conf` (pruning majors it
   wrote on earlier boots — state in `/var/lib/redroid-binder/majors-103`, config backups
   next to it);
3. `docker start redroid` inside the LXC (the container therefore has restart policy `no`);
4. finds the container's host PID via its cgroup scope, runs `binder_alloc` against
   `/proc/<pid>/root/dev/binderfs/binder-control`, waits for the three devices, re-checks the
   allocated major, and `chmod 666`s them.

It is idempotent — rerun it for recovery. It is **boot-scoped**: a `pct reboot 103` or a
manual `docker restart redroid` recreates binderfs, so rerun `systemctl start redroid-binder.service`
on the host afterwards.

## Install

```bash
# on the Proxmox host, from a checkout of this repo
install -m 644 proxmox/modules-load.d-binder.conf /etc/modules-load.d/binder.conf
modprobe binder_linux
gcc -O2 -o /usr/local/bin/binder_alloc proxmox/binder_alloc.c       # needs gcc + linux headers (apt install build-essential pve-headers)
install -m 755 proxmox/redroid-binder-alloc /usr/local/bin/redroid-binder-alloc
install -m 644 proxmox/redroid-binder.service /etc/systemd/system/redroid-binder.service
systemctl daemon-reload
systemctl enable --now redroid-binder.service      # ENABLE, not just start — see below
```

Edit the CT id / container name in `redroid-binder.service`'s `ExecStart` if yours differ
(`setup/00-proxmox-host.sh` does all of the above with `CT_ID` / `CONTAINER` variables).

## Verify

```bash
systemctl is-enabled redroid-binder.service          # must print: enabled
journalctl -u redroid-binder.service -n 20 --no-pager
grep binder /proc/devices; grep devices.allow /etc/pve/lxc/103.conf   # majors must match
pct exec 103 -- docker exec redroid ls -l /dev/binderfs/              # binder, hwbinder, vndbinder, 666
pct exec 103 -- docker exec redroid getprop sys.boot_completed         # 1 (5–10 min after cold start)
```

## Lessons baked into this directory

- **`is-active` lies, `is-enabled` tells the truth.** The unit existed for seven weeks, was
  started by hand three times, and was never enabled — every host reboot lost Android until
  someone reran it. Always check `systemctl is-enabled`.
- **Stale majors are not harmless.** A dead `c 511:* rwm` line grants the LXC rwm on whatever
  driver gets major 511 next boot. The allocator now prunes what it wrote.
- `options binder_linux num_binders=255` is a no-op on this kernel (`unknown parameter 'num_binders' ignored`).
- There is no `/dev/binder` on the host, no udev rule, and nothing to `mknod`. Don't.
- The `_64only` Redroid image has no H.264/VP8 encoder → scrcpy and VNC do not work. Use
  `adb exec-out screencap -p` for screenshots (see `setup/03-line-login.md`).
