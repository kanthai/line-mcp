# line-mcp

A [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI assistants read access to your LINE chats — including full E2EE image decryption — directly from the LINE SQLite database running inside a [Waydroid](https://waydroid.io/) Android container.

No LINE API key required. No cloud relay. Everything runs locally on your own machine. Uses the official LINE Android app — no patched APK, no custom client.

---

## What it does

- **Read LINE chats and messages** — list, search, triage, summarize
- **Decrypt E2EE photos headlessly** — no UI tap, no Waydroid window needed
- **Read Flex and Markup card data** — extract structured content (account balances, action buttons) from bank/service bot messages that have no plain text
- **Token management** — read and rotate the CDN auth token directly from LINE's SQLite database; no network interception needed

The MCP server exposes 20 tools to any MCP-compatible AI orchestrator (Claude Desktop, Claude Code, OpenClaw, etc.).

---

## How it works

LINE stores its chat database as a plain SQLite file inside the Waydroid container at:

```
/data/data/jp.naver.line.android/databases/naver_line
```

No encryption, no password. We read it directly via `sudo waydroid shell -- sqlite3`.

For E2EE media (photos sent with Letter Sealing), LINE caches the decrypted key material (`KM`) in plaintext in `chat_history.parameter` after receiving the message. The decryption pipeline is derived from the [LINE Encryption Overview ver 2.2](https://www.lycorp.co.jp/ja/privacy-security/line-encryption-whitepaper-ver2.2.pdf) (publicly available from LY Corporation):

```
1. KM (32 bytes, base64) from chat_history.parameter["ENC_KM"]
2. HKDF-SHA256(KM, salt=None, info=b"FileEncryption", L=76)
   → Kenc[32] + Kmac[32] + IV[12]
3. Blob from obs-th.line-apps.com CDN: C[file_size] || HMAC-SHA256[32]
4. Verify: HMAC-SHA256(Kmac, C) == blob[-32:]
5. Decrypt: AES-256-CTR(Kenc, IV + b"\x00"*4, C) → plaintext JPEG
```

The CDN auth token (`X-Line-Access`) is a session-scoped bearer token (~24–48h). LINE stores it in plaintext in the `naver_line` SQLite database under `setting.OBS_ENCRYPTED_ACCESS_TOKEN`. We read it with a single `sqlite3` query and persist it to `~/.config/line-mcp/auth.json`. It needs refreshing only after the token expires (~daily).

---

## Verified

Tested and working on **NVIDIA DGX Spark** running **Ubuntu 24.04.4 LTS** (kernel 6.17.0-1014-nvidia, aarch64).

---

## Requirements

- Linux host (tested on Ubuntu 22.04 / 24.04)
- [Waydroid](https://waydroid.io/) with Android 13 and LINE installed
- **LINE APK** — not included; you must source this yourself. LINE is available on the Google Play Store or as an `.apkm` from [APKMirror](https://www.apkmirror.com/apk/line-corporation/line/). This tool does not distribute LINE in any form.
- Python 3.10+ with `cryptography`, `requests`, `mcp` packages
- `sudo` access to run `waydroid shell` commands

---

## Setup

### 1. Install Waydroid and LINE

```bash
sudo bash setup/01-waydroid-install.sh   # install Waydroid
sudo bash setup/02-waydroid-init.sh      # init Android 13 image (arch auto-detected)
```

**Install LINE APK** — two options depending on what you have:

```bash
# Option A (preferred): headless install from a split APK set (.apkm extract)
sudo bash setup/03-install-line-headless-apkm.sh /path/to/line_apkm_extract/

# Option B: single APK or XAPK with UI session (if Option A isn't available)
bash setup/03b-install-line.sh /path/to/LINE.apk
```

After installing, log in to LINE interactively once — bring up the Waydroid UI, sign in, and verify your number. After that, everything runs headless.

Optionally fix Android 13 storage and OMX issues on some hosts (DGX Spark, some ARM boards):

```bash
sudo bash setup/06-fix-a13-storage-omx.sh
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Capture the CDN auth token

```bash
python3 tools/refresh_token.py
# reads OBS_ENCRYPTED_ACCESS_TOKEN from LINE's naver_line SQLite DB
# saves token to ~/.config/line-mcp/auth.json
```

The token is valid for ~24–48 hours. The watchdog timer re-runs this automatically every 6 hours. You only need to run it manually after a fresh LINE login or device re-registration.

### 4. Wire into your MCP client

Add to your MCP config:

```json
{
  "mcpServers": {
    "line-mcp": {
      "command": "python3",
      "args": ["/path/to/line-mcp/mcp/server.py"]
    }
  }
}
```

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_chats(limit)` | Recent chats ordered by last activity |
| `list_unread_chats(limit)` | Chats with unread messages |
| `get_messages(chat_id, limit)` | Messages in one chat |
| `get_latest_inbound_message()` | Single newest received message |
| `list_latest_inbound_messages(limit)` | Newest received messages across all chats |
| `list_reply_candidates(limit)` | Chats awaiting your reply |
| `get_message_context(message_id, before, after)` | Messages around a target |
| `find_person(query, limit, message_limit)` | Search by name across chats and group senders |
| `summarize_recent_activity(hours, chat_limit, messages_per_chat)` | Structured activity window |
| `get_chat_summary(chat_id, message_limit, media_limit)` | Chat + messages + media in one call |
| `search_messages(query, limit)` | Full-text search |
| `list_media(chat_id, limit)` | Media attachments |
| `get_media_info(message_id)` | Metadata for one media message |
| `pull_message_image(message_id, destination_dir)` | **Decrypt and save one E2EE image** |
| `pull_chat_images(chat_id, destination_dir, limit)` | Batch decrypt images from a chat |
| `download_media(message_id, destination_dir)` | Direct download (errors if token stale) |
| `set_auth_token(x_line_access)` | Update the cached CDN token |
| `open_chat_and_cache(chat_id, wait_seconds)` | Last resort: open LINE UI and render |
| `extract_cached_media(destination_dir, min_bytes, limit)` | Export already-rendered cache files |
| `get_message_raw(message_id)` | Raw parameter blob with `*_JSON` decoded — for Flex/Markup card messages |

### Reading `pull_message_image` results

```python
result["mode"]             # "decrypt" = CDN path; "cache" = UI cache fallback
result["count"]            # number of files saved
result["downloaded_files"] # list: {downloaded, decrypted, path, bytes, mime_type}
```

If `mode == "cache"` and `count == 0`, the CDN token is stale — run `python3 tools/refresh_token.py`.

### Flex and Markup messages

Banks and services (e.g. KBank, SCB) send rich card messages with no plain text. Use `get_message_raw(message_id)` to get the decoded structure:

- Type 22 (Flex): balance, account info, actions in `params["FLEX_JSON"]`  
- Type 17 (Markup): image URL in `params["DOWNLOAD_URL"]`, interactive layout in `params["MARKUP_JSON"]`

---

## Watchdog

Keep LINE, Waydroid, and the CDN token alive automatically. The sudoers rules are written by `01-waydroid-install.sh`. To enable the timers:

```bash
cp systemd/line-watchdog.{service,timer} \
   systemd/waydroid-watchdog.{service,timer} \
   systemd/line-foreground-pulse.{service,timer} \
   systemd/line-token-refresh.{service,timer} \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now \
    waydroid-watchdog.timer \
    line-watchdog.timer \
    line-foreground-pulse.timer \
    line-token-refresh.timer
```

| Timer | Interval | Purpose |
|-------|----------|---------|
| `waydroid-watchdog` | every 3 min | Restart Waydroid session if down; unfreeze container if frozen (waits up to 15s before escalating to full restart) |
| `line-watchdog` | every 5 min | Restart LINE process if not running |
| `line-foreground-pulse` | every 10 min | Bring LINE to foreground to encourage message sync |
| `line-token-refresh` | every 6 h | Re-read CDN token from SQLite and update auth.json |

---

## Waydroid display

For normal operation (read + E2EE decrypt), Waydroid runs fully headless:

```bash
WAYLAND_DISPLAY=wayland-0 waydroid session start
```

The only time a display is needed is `open_chat_and_cache` (last-resort fallback for old/expired blobs). A headless Weston instance is sufficient even then if you pass `--backend=headless`.

---

## Known limitations

- **Write path (send messages)**: not implemented
- **Video**: chunked SHA256 decryption not implemented — falls back to UI cache  
- **Old CDN blobs**: URLs for messages weeks+ old may have expired  
- **Thailand CDN only**: defaults to `obs-th.line-apps.com`; override with `LINE_CDN_BASE=https://obs-sg.line-apps.com/r/talk` (or your region's endpoint) before starting the server
- **One account per container**: designed for personal use on your own account

---

## Project layout

```
mcp/server.py                   — FastMCP stdio server (20 tools)
tools/line_db.py                — SQLite read layer + E2EE decrypt pipeline
tools/refresh_token.py          — Read CDN token from LINE's naver_line SQLite DB
tools/decrypt_media.py          — CLI tool: decrypt a blob file given a KM hex string
tools/start-after-reboot.sh     — Full post-reboot startup: Weston + Waydroid + LINE + token
tools/line-watchdog.sh          — Keep LINE process running inside Waydroid
tools/waydroid-watchdog.sh      — Keep Waydroid session alive; auto-unfreeze frozen container
tools/line-token-refresh.sh     — Refresh CDN token (called by line-token-refresh.timer)
tools/waydroid-storage-fix.sh   — Fix Waydroid FUSE emulated storage (run once if needed)
systemd/                        — systemd user service + timer units
setup/                          — One-time setup scripts (01–06)
```

---

## Cryptography reference

The E2EE decryption pipeline is derived entirely from LINE Corporation's publicly available **LINE Security Whitepaper v2.2**, which documents the Letter Sealing protocol including key derivation, AES-256-CTR encryption, and HMAC-SHA256 integrity verification for media attachments. No proprietary knowledge was used.

> [LINE Encryption Overview ver 2.2 (PDF)](https://www.lycorp.co.jp/ja/privacy-security/line-encryption-whitepaper-ver2.2.pdf) — LY Corporation.

---

## Disclaimer

- **Personal use only.** This tool is designed to access your own LINE messages on your own device. Do not use it to access accounts or data you do not own.
- **Not affiliated with LINE Corporation.** LINE, LINE Creators Market, and related marks are trademarks of LY Corporation.
- **No warranty.** This software is provided as-is. Use at your own risk.
- **Your responsibility.** You are responsible for ensuring your use complies with LINE's Terms of Service and applicable laws in your jurisdiction. The authors accept no liability for misuse.

---

## License

MIT
