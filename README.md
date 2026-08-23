# line-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents
read access to your LINE chats — including headless decryption of E2EE (Letter Sealing)
photos — by running the **official LINE Android app inside [Redroid](https://github.com/remote-android/redroid-doc)**
(Android 12 in Docker) and reading its SQLite database.

No LINE API key, no cloud relay, no patched APK. 24 MCP tools over streamable HTTP with
Bearer auth. This is the stack that runs 24/7 on the author's Proxmox LXC **CT103**; the repo
is laid out so the same stack can be rebuilt on another machine from scratch.

---

## How it works

```
 MCP clients (Hermes, Claude Code, OpenClaw …)
        │  POST http://<lxc>:8765/mcp   Authorization: Bearer <LINE_MCP_API_KEY>
        ▼
 ┌─ LXC (Debian 12, nesting=1) ─────────────────────────────────────────────────────────┐
 │  line-mcp.service      mcp/server.py  (FastMCP, uvicorn :8765, user `line`)           │
 │        │ read path                                                                    │
 │        ├── postgres ──► PostgreSQL line_raw/cdb  ◄── line-sync-postgres.service (opt) │
 │        └── direct ────► SQLite files in the redroid-data Docker volume               │
 │        │ media path ──► LINE OBS CDN + AES-256-CTR/HMAC decrypt (tools/line_db.py)    │
 │        │ auth path ───► ~/.config/line-mcp/auth.json  ◄── line-token-refresh.timer    │
 │                                                         (reads LINE's own SQLite)     │
 │  ┌─ Docker ─────────────────────────────────────────────────────────────────────┐    │
 │  │ redroid  (redroid/redroid:12.0.0_64only-latest, --privileged, ADB :5555)     │    │
 │  │   └─ LINE 26.7.1 (jp.naver.line.android), logged in as a secondary device   │    │
 │  └──────────────────────────────────────────────────────────────────────────────┘    │
 │  timers: line-watchdog (2 min)  line-restart (nightly)  redroid-lmkd-watchdog (2 min)│
 └──────────────────────────────────────────────────────────────────────────────────────┘
 Proxmox host: binder_linux module + redroid-binder.service (grants the dynamic binder
               major to the LXC and allocates /dev/binderfs/* inside the container)
```

LINE keeps `naver_line` / `contact` as plain SQLite under `/data/data/jp.naver.line.android/databases/`.
For E2EE photos LINE caches the already-decrypted key material (`ENC_KM`) in
`chat_history.parameter`; the pipeline (from the LY Corp *LINE Encryption Overview v2.2*) is

```
KM (32 B, base64 in parameter["ENC_KM"])
HKDF-SHA256(KM, salt=None, info="FileEncryption", L=76) → Kenc[32] ‖ Kmac[32] ‖ IV[12]
blob = GET https://obs-th.line-apps.com/r/talk/{sid}/{oid}   (X-Line-Access token)
verify HMAC-SHA256(Kmac, C) == blob[-32:] ;  AES-256-CTR(Kenc, IV‖0x00000000, C) → JPEG
```

The CDN token (`X-Line-Access`) is LINE's own `setting.OBS_ENCRYPTED_ACCESS_TOKEN`; LINE
rotates it reactively roughly every 4 days, so `line-token-refresh.timer` copies it out every
30 min and, when LINE has nulled it, opens a chat headlessly to make LINE mint a new one.

---

## Repository layout

| Path | What |
|---|---|
| `mcp/server.py` | MCP server — tool registry, Bearer middleware, `/health`, `/files` |
| `tools/line_db.py` | data layer: SQLite/Postgres queries, CDN download, E2EE decrypt, media enrichment |
| `tools/refresh_token.py` | CDN token refresh (SQLite read; ADB-driven chat open when the token is null) |
| `tools/line_sync_postgres.py` | SQLite → PostgreSQL mirror (for `LINE_MCP_DB_MODE=postgres`) |
| `tools/decrypt_media.py` | offline CLI: decrypt a raw CDN blob with a known KM |
| `tools/redroid-screen.py` | stdlib web viewer/controller for the headless Android screen (no VNC/scrcpy on this image) — used for the LINE QR login |
| `config/line-mcp.env.example` | all environment variables, documented → `/etc/line-mcp/line-mcp.env` |
| `systemd/` | LXC units + timers exactly as deployed on CT103 |
| `scripts/` | helper scripts the units call (`/usr/local/bin`) |
| `proxmox/` | **host-side** binder bring-up: module, `binder_alloc.c`, allocator, service, LXC conf |
| `setup/00…05` | step-by-step install scripts + LINE login guide |
| `RUNBOOK.md` | day-2 operations: status, recovery, known failure modes |
| `docs/history.md` | Waydroid/DGX-Spark → TrueNAS VM → Redroid/CT103 lineage, and where the old scripts went |
| `docs/dry-run-2026-08-23.md` | full transcript of a from-scratch rebuild on a throwaway CT (both read paths) |
| `tests/` | unit tests (`pytest`) |

---

## Requirements

- **Proxmox VE** host (tested: PVE 9, kernel 7.0.2-6-pve, x86_64) with the `binder_linux`
  module available. Any Linux host with binderfs can work, but the host-side helpers in
  `proxmox/` assume `pct`/`lxc-cgroup`.
- An LXC: **privileged** (Redroid needs `--privileged` Docker + binderfs), Debian 12,
  `features: nesting=1,keyctl=1`, ≥4 GB RAM (CT103: 4 cores / 6 GB / 60 GB, swap 0, optional
  `/dev/dri/renderD128` passthrough), static IP.
- Inside the LXC: Docker CE, `adb`, `sqlite3`, Python 3.11 (`setup/01-lxc-base.sh` installs them).
- **LINE APK** — CT103 runs 26.7.1, the **arm64-v8a** build (Redroid `_64only` runs it through its
  native bridge); pull the splits from an existing install or source it yourself — not distributed here.
- A phone with the LINE account, to scan the login QR once.
- Optional: a PostgreSQL 14+ server for the mirror read path (`pg_trgm` extension).

---

## Install (fresh machine)

Each script prints what to do next. All of them are idempotent.

> Dry-run 2026-08-23 (full log: `docs/dry-run-2026-08-23.md`): every step 0 → 5 was executed
> on a throwaway privileged CT and then destroyed — binder allocated on a brand-new container,
> Android 12 booted, LINE (arm64 splits) installed, the QR login screen driven via
> `tools/redroid-screen.py`, and **both read paths verified with real data** (a read-only
> snapshot of CT103's DB stood in for a live scan): `direct` SQLite and `postgres` (mirror +
> live `line-sync-postgres.service`) each returned real `list_chats`/`search_messages` results
> through MCP. Only the CDN-token step needs a real QR scan.

```bash
# 0. Proxmox HOST — binder module, binder_alloc, redroid-binder.service, LXC features
git clone https://github.com/kanthai/line-mcp.git && cd line-mcp
CT_ID=103 bash setup/00-proxmox-host.sh

# 1. inside the LXC — Docker, adb, python, `line` user
pct exec 103 -- bash -lc 'git clone https://github.com/kanthai/line-mcp.git /root/line-mcp && bash /root/line-mcp/setup/01-lxc-base.sh'

# 2. inside the LXC — create the Redroid container …
pct exec 103 -- bash /root/line-mcp/setup/02-redroid.sh
#    … HOST grants binder + starts it …
systemctl start redroid-binder.service && journalctl -u redroid-binder.service -n 20 --no-pager
#    … inside the LXC — wait for boot, adb, keep-awake settings
pct exec 103 -- bash /root/line-mcp/setup/02-redroid.sh

# 3. install LINE + QR login (manual, ~5 min) — follow setup/03-line-login.md
#    (python3 tools/redroid-screen.py → http://<lxc-ip>:6080/ shows the screen, click = tap)

# 4. inside the LXC — line-mcp venv, env file, units, timers
pct exec 103 -- bash /root/line-mcp/setup/04-line-mcp-install.sh          # direct SQLite mode
#   or, with a PostgreSQL mirror (set DATABASE_URL in /etc/line-mcp/line-mcp.env first):
pct exec 103 -- env WITH_POSTGRES=1 bash /root/line-mcp/setup/04-line-mcp-install.sh

# 5. verify end-to-end (includes a real MCP list_chats round-trip)
pct exec 103 -- bash /home/line/line-mcp/setup/05-verify.sh
```

Then point your MCP client at `http://<lxc-ip>:8765/mcp` with header
`Authorization: Bearer <LINE_MCP_API_KEY>` (from `/etc/line-mcp/line-mcp.env`).
Hermes example (`config.yaml`):

```yaml
mcp_servers:
  line-mcp:
    url: http://11.0.0.103:8765/mcp
    headers: { Authorization: "Bearer ${MCP_LINE_MCP_API_KEY}" }
```

### Read path: `direct` vs `postgres`

| | `direct` (default in the env example) | `postgres` (what CT103 runs) |
|---|---|---|
| data source | live SQLite files in the `redroid-data` volume | `line_raw`/`cdb` schemas mirrored by `line-sync-postgres.service` (≤30 s lag) |
| needs | the server process to open `/var/lib/docker/volumes/...` | a PostgreSQL server + `DATABASE_URL` |
| why | zero extra infra | unprivileged `line` service user, many concurrent readers, trigram search index, agents on other hosts can query the mirror |

> **Live-DB caveat for `direct` mode:** LINE keeps `naver_line`/`contact` in SQLite **WAL**
> mode with a `0600` `-shm` sidecar, so an unprivileged read-only open of the *live, actively
> written* DB can fail (`attempt to write a readonly database`). Direct mode is reliable
> against a static snapshot or a single low-traffic reader; for the live multi-reader case use
> `postgres` mode — which is why CT103 runs it.

Docker resets `/var/lib/docker` to `0710 root:root` on every daemon start, so a non-root
server cannot traverse to the SQLite files. For `direct` mode `setup/04-line-mcp-install.sh`
therefore installs a `docker.service` drop-in (`ExecStartPost=/bin/chmod o+x /var/lib/docker`,
`systemd/docker.service.d-line-mcp-db-access.conf`) and adds `line` to the LINE app's
Android gid that owns the database files — verified on CT103 to give the `line` user
read-only access. `line-token-refresh.service` and `line-sync-postgres.service` run as root
regardless (CT103 predates the drop-in and runs postgres mode).

---

## MCP tools (24)

| Group | Tools |
|---|---|
| Chats & messages | `list_chats` `list_unread_chats` `get_messages` (keyset `before_id`, `since_ms`/`until_ms`) `get_latest_inbound_message` `list_latest_inbound_messages` `list_reply_candidates` `get_message_context` `get_message_raw` (Flex/Markup cards decoded) `search_messages` `get_chat_summary` `summarize_recent_activity` (auto-compacted to ≤60 k chars) `get_chat_stats` |
| People | `find_person` `list_contacts` `list_group_members` |
| Media | `list_media` `get_media_info` `pull_message_image` (**preferred** — CDN + E2EE decrypt, returns base64 `data` + `mime_type`) `pull_chat_images` `download_media` |
| Media, UI-cache fallbacks | `extract_cached_media` `open_chat_and_cache` — legacy Waydroid-era tools that drive the Android UI through `waydroid shell`; **not functional on Redroid**, kept registered for API compatibility (CDN path covers all current use) |
| Auth | `refresh_cdn_token` `set_auth_token` |

Downloaded images are returned inline (`data`, base64) so remote agents need no filesystem
access; PDFs/DOCX get `text_content` if `pymupdf`/`python-docx` are installed. Files are also
served at `LINE_MCP_MEDIA_URL` (`/files/<name>?key=<api key>`).

Known limits: video E2EE (chunked) not implemented; CDN blobs older than a few weeks may be
gone; CDN host is the Thai region (`LINE_CDN_BASE` to change).

---

## Configuration

Everything is environment-driven; see **`config/line-mcp.env.example`** for the full,
commented list. The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `LINE_MCP_API_KEY` | — (required) | Bearer key clients must send |
| `LINE_MCP_DB_MODE` | `auto` | `direct` · `postgres` · `auto` (postgres if `DATABASE_URL`) · `waydroid` (legacy) |
| `DATABASE_URL` | — | Postgres DSN for `postgres` mode and the sync service |
| `LINE_MCP_HOST_DB` / `LINE_MCP_HOST_CONTACT_DB` | redroid-data volume paths | live SQLite files |
| `LINE_MCP_MEDIA_URL` | `http://127.0.0.1:8765/files` | public base for media links |
| `LINE_ADB_DEVICE` | `127.0.0.1:5555` | Redroid ADB serial (always address it explicitly — a ghost `emulator-5554` also appears) |
| `LINE_TOKEN_REFRESH_*` | see example | token-refresh candidate/wait knobs |

Secrets live only in `/etc/line-mcp/line-mcp.env` (0600). On CT103 they are rendered from
an Infisical vault; never commit them.

---

## Development

```bash
python3 -m venv venv && venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q            # 13 tests, no LINE needed
venv/bin/python mcp/server.py           # needs LINE_MCP_API_KEY (+ DB env) — serves :8765
```

History, the Waydroid-era setup scripts and the TrueNAS migration plan are reachable in git
history (`git log --all`, tag `waydroid-era`) — see `docs/history.md`.

## License

MIT — see `LICENSE`. LINE is a trademark of LY Corporation; this project is not affiliated
with or endorsed by LY Corporation and does not distribute the LINE application.
