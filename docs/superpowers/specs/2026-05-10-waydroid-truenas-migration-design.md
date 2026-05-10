# TrueNAS Migration Design: Waydroid + OpenClaw + Hermes

**Date:** 2026-05-10
**Goal:** Move Waydroid (LINE automation), OpenClaw, and Hermes off the DGX Spark entirely, freeing all 121 GB unified memory for vLLM inference. Services land on TrueNAS Scale 25.10.

---

## Context

- **DGX Spark (claw-brain):** GB10 Superchip, 121 GB unified memory, Nvidia GPU. After migration: runs vLLM slots only (+ vllm-think-proxy, which stays — it's tiny and tightly coupled to SlotE at 8007).
- **TrueNAS Scale 25.10 (Fangtooth):** Intel iGPU, Docker Compose support, KVM virtualization. Receives all migrated services.
- **line-mcp** reads LINE messages via `sudo waydroid shell -- sqlite3` — must co-locate with Waydroid.
- TrueNAS kernel lacks `binder`/`binderfs` — LXC jails and Docker cannot run Waydroid. KVM VM is required for Waydroid only.

---

## Target Architecture

```
DGX Spark (stays)
└── vLLM slots: 8000, 8004, 8007, 8009
└── vllm-think-proxy: 8017 → 8007 (expose to LAN: 0.0.0.0:8017)

TrueNAS Scale 25.10
├── KVM VM: waydroid-vm
│   ├── Xvfb :0  (1920×1080 landscape)
│   ├── Weston  (wayland-0 socket)
│   ├── Waydroid  (LINE app)
│   ├── line-mcp SSE server  (port 8765, LAN only)
│   ├── x11vnc  (port 5900, LAN only)
│   └── watchdogs: waydroid-watchdog, line-watchdog, line-foreground-pulse
│
└── Docker Compose: agent-stack
    ├── openclaw  (Telegram bot + agent platform)
    └── hermes    (NousResearch agent + cron jobs)
```

---

## Section 1 — Waydroid VM

| Parameter | Value |
|---|---|
| Hypervisor | TrueNAS Scale 25.10 KVM |
| Guest OS | Ubuntu 24.04 LTS minimal, user: `kanthai` |
| vCPU | 4 cores |
| RAM | 6 GB |
| Disk | 64 GB zvol on TrueNAS pool |
| Network | VirtIO NIC on bridge `br0` — own LAN IP |
| GPU | None (SwiftShader software rendering) |

**Display stack (systemd chain):**
```
xvfb.service → weston.service → waydroid-session.service → x11vnc.service
```

**line-mcp transport change:** FastMCP changes from stdio to SSE (one-line change in `mcp/server.py`):
```python
server.run(transport="sse", host="0.0.0.0", port=8765)
```

Run as `line-mcp.service` systemd unit inside the VM. Port 8765 open to LAN subnet only via `ufw`.

---

## Section 2 — OpenClaw + Hermes (Docker Compose)

Two separate containers in one `docker-compose.yml` on TrueNAS. Separate containers because they have different binaries and independent restart lifecycles, but they share the same compose stack for easy management.

**Volumes mounted into both containers:**
- `~/.config/openclaw.env` → `/home/kanthai/.config/openclaw.env` (API keys)

**OpenClaw container:**
- Mounts `~/.openclaw/` as persistent volume
- Connects to vLLM at `http://<spark-ip>:8xxx/v1` (all localhost URLs updated)
- line-mcp MCP entry changes from stdio to SSE: `http://<waydroid-vm-ip>:8765/sse`
- obsidian MCP: stays stdio, vault dir mounted into container at `~/vault`

**Hermes container:**
- Mounts `~/.hermes/` as persistent volume
- `config.yaml`: all `localhost:8xxx` → `<spark-ip>:8xxx`
- Cron jobs run inside the container (Hermes manages its own cron via `jobs.json`)

**vllm-think-proxy stays on DGX Spark** — it's ~5 MB of Python, inseparable from SlotE latency. Change: bind it to `0.0.0.0:8017` instead of `127.0.0.1:8017` so OpenClaw on TrueNAS can reach it.

---

## Section 3 — Data Migration

### Waydroid VM

| Source (DGX Spark) | Destination (VM) | Notes |
|---|---|---|
| `~/waydroid-a11-images/system.img` | `/var/lib/waydroid/images/system.img` | LineageOS 18.1 ARM64 |
| `~/waydroid-a11-images/vendor.img` | `/var/lib/waydroid/images/vendor.img` | |
| `/var/lib/waydroid/` | `/var/lib/waydroid/` | Config + overlay (A13 storage fix included) |
| `~/.local/share/waydroid/` | `~/.local/share/waydroid/` | LINE app data, DB, E2EE keys |
| `~/line-mcp/` | `~/line-mcp/` | Full repo |
| `/etc/sudoers.d/waydroid` | recreate via `setup-waydroid-sudo.sh` | |

Post-copy: `waydroid init --images-path /var/lib/waydroid/images` (registers existing images, preserves LINE login).

### Docker (OpenClaw + Hermes)

| Source (DGX Spark) | Destination (TrueNAS volume) |
|---|---|
| `~/.openclaw/` | openclaw data volume |
| `~/.hermes/` | hermes data volume |
| `~/.config/openclaw.env` | shared secrets volume |

---

## Section 4 — Config Changes Required

| Component | Change |
|---|---|
| `~/.hermes/config.yaml` | All `localhost:8xxx` → `<spark-ip>:8xxx` |
| `~/.openclaw/openclaw.json` | All `127.0.0.1:8xxx` → `<spark-ip>:8xxx` |
| `~/.openclaw/openclaw.json` | `line-mcp` MCP: stdio → `http://<vm-ip>:8765/sse` |
| `vllm-think-proxy.service` | Bind `0.0.0.0:8017` instead of `127.0.0.1:8017` |
| `line-mcp/mcp/server.py` | `server.run(transport="sse", host="0.0.0.0", port=8765)` |

---

## Out of Scope

- iGPU passthrough to Waydroid VM
- Write path / sending LINE messages
- Magisk / root hiding
- Moving vllm-think-proxy off DGX Spark
