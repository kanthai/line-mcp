# History and lineage

`line-mcp` has lived on three hosts. The current tree only describes the third; the earlier
setups are preserved in git history for reference.

| Period | Host | Android runtime | Notes |
|---|---|---|---|
| 2026-04 → 2026-05-10 | NVIDIA DGX Spark (aarch64, Ubuntu 24.04) | **Waydroid**, Android 13 arm64 | Where the read path, Flex/Markup decoding and the E2EE pipeline were worked out. Magisk/Frida were explored and then dropped — the CDN+AES-CTR path needs neither. Setup scripts `setup/01-…09-*.sh`, X11/Weston/xvfb units, waydroid watchdogs. |
| 2026-05-10 → 2026-05-28 | TrueNAS Scale, KVM VM "waydroidvm" (11.0.0.13) | Waydroid | Moved off the Spark to free GPU memory. SSE → streamable-HTTP transport, Bearer auth, Docker compose for OpenClaw/Hermes alongside. Design/plan docs under `docs/superpowers/`. |
| 2026-05-28 → today | Proxmox LXC **CT103** "android" (11.0.0.103) on pve-a2 | **Redroid** 12 x86_64 in Docker | Everything in this repo. Binder via host-granted dynamic major + in-container binderfs; non-root `line` service user; Postgres mirror read path (2026-06-01); 24 tools; nightly LINE restart; lmkd watchdog. |

## Finding the old material

```bash
git tag                      # waydroid-era = last commit of the Waydroid/TrueNAS layout
git show waydroid-era:README.md
git show waydroid-era:setup/02-waydroid-init.sh
git show waydroid-era:docs/superpowers/plans/2026-05-10-truenas-migration.md
git log --all --oneline      # `postgres` branch history was merged into main (2026-08-23)
```

Removed from the working tree on 2026-08-23 because they describe hosts that no longer exist:
Waydroid install/init/storage-fix scripts and watchdogs, xvfb/weston/x11vnc/waydroid systemd
units, TrueNAS Docker compose + OpenClaw/Hermes Dockerfiles, the migration spec/plan, the
agent units that belong to the separate `line-assistant-agent` repo, and Frida research hooks.

## Why Redroid needed host help (short version)

The PVE kernel ships `binder_linux` as binderfs-only with a dynamic major; Android's init
cannot issue `BINDER_CTL_ADD` under its own seccomp filter, and a privileged Docker
container inside an LXC cannot use a device major the LXC cgroup does not allow. Hence
`proxmox/redroid-binder.service`: grant the live major to the LXC, start the container,
allocate `binder`/`hwbinder`/`vndbinder` from the host through `/proc/<pid>/root`, `chmod 666`.
Full write-up in `proxmox/README.md`.
