# line-mcp RUNBOOK (Redroid / LXC)

Day-2 operations for the stack described in `README.md`. Paths assume the CT103 layout
(`/home/line/line-mcp`, LXC reached with `pct exec 103 -- …` from the Proxmox host; replace
`103` with your CT id). ADB is always `adb -s 127.0.0.1:5555`.

## 1. Status at a glance

```bash
bash /home/line/line-mcp/setup/05-verify.sh                     # everything, incl. a live MCP call
systemctl list-timers 'line-*' 'redroid-*' --no-pager            # timers + last/next run
journalctl -u line-mcp -n 50 --no-pager
docker exec redroid getprop sys.boot_completed                   # 1 = Android up
adb -s 127.0.0.1:5555 shell ps -A | grep jp.naver.line.android   # LINE running?
```

| Unit | Cadence | Runs as | Purpose |
|---|---|---|---|
| `line-mcp.service` | always | `line` | MCP server :8765 |
| `line-token-refresh.timer` | every 30 min (`*:0/30`, Persistent) | root | copy `OBS_ENCRYPTED_ACCESS_TOKEN` → `~line/.config/line-mcp/auth.json`; if LINE nulled it, open a chat to make LINE mint a new one |
| `line-watchdog.timer` | every 2 min | `line` | relaunch LINE if dead; restart `line-mcp` if `/mcp` unhealthy |
| `line-restart.timer` | 21:00 UTC daily | root | `am force-stop` + relaunch LINE (native-heap leak, ~3 GB/2 days) |
| `redroid-lmkd-watchdog.timer` | every 2 min | `line` | restart Android `lmkd` when it busy-spins on `epoll_wait EINVAL` |
| `line-sync-postgres.service` | always (optional) | root | SQLite → Postgres mirror |
| host `redroid-binder.service` | at host boot (+ manual reruns) | root | grant binder major to LXC, `docker start redroid`, allocate `/dev/binderfs/*` |

## 2. Recovery playbook

### Android not booting / `Binder driver '/dev/binder' could not be opened`
The host did not grant the current binder major or did not allocate the devices.
```bash
# Proxmox host
systemctl is-enabled redroid-binder.service     # must be enabled (is-active lies — see proxmox/README.md)
systemctl start redroid-binder.service && journalctl -u redroid-binder.service -n 20 --no-pager
grep binder /proc/devices; grep devices.allow /etc/pve/lxc/103.conf
```
After `pct reboot 103` or `docker restart redroid` this **must be rerun by hand** (boot-scoped).

### ADB shows two devices / "more than one device"
Normal after boot (`127.0.0.1:5555` + ghost `emulator-5554`). Always pass `-s 127.0.0.1:5555`.
If offline: `adb disconnect; adb connect 127.0.0.1:5555`.

### CDN downloads fail / `pull_message_image` → `cdn_failed`
LINE rotates the OBS token reactively (~every 4 days, TTL field says 7): it lets it expire,
nulls it, and only then mints a new one on its own network activity. Between those points
downloads fail for minutes–hours. The timer polls every 30 min; to force it now:
```bash
systemctl start line-token-refresh.service; journalctl -u line-token-refresh -n 30 --no-pager
ls -la /home/line/.config/line-mcp/auth.json           # mtime = last successful copy
```
You cannot force LINE to re-auth early — opening chats with cached images is a no-op; the
script's chat-open trick only helps once the value is already null. Consumers should treat
`cdn_failed` as transient and retry later rather than giving up.

### LINE app dead or eating memory
```bash
/usr/local/bin/line-nightly-restart.sh          # restart only the app (what the nightly timer does)
docker exec redroid sh -c "dumpsys meminfo jp.naver.line.android | grep 'TOTAL PSS'"
```
`am`/`monkey` are shell wrappers inside Android — always `docker exec redroid sh -c "am …"`
(raw exec gives `exec format error`); `monkey -c LAUNCHER` is flaky, use
`am start -n jp.naver.line.android/jp.naver.line.android.activity.SplashActivity`.

### lmkd pinning a core at 100 %
Signature: `logcat -s lowmemorykiller:E` floods `epoll_wait failed (errno=22)`. The watchdog
restarts `lmkd` in place (`stop lmkd; start lmkd`). Do **not** set `ro.lmk.use_psi=false` —
it crash-loops lmkd on this cgroup-v2 image.

### line-mcp returns 401 / Hermes says "not responding"
Key mismatch: `/etc/line-mcp/line-mcp.env` `LINE_MCP_API_KEY` must equal what the client
sends. Always use the LXC's LAN IP (not a Docker bridge IP) in client configs.

### Token refresh fails with `unable to open database file`
It ran as the `line` user. Docker resets `/var/lib/docker` to 0710 on daemon start, so the
unit runs as root (`User=root`, `HOME=/home/line`) — check for a stray drop-in overriding it.

### Postgres mode: `relation does not exist` / stale data
```bash
systemctl status line-sync-postgres; journalctl -u line-sync-postgres -n 30 --no-pager
DATABASE_URL=… /home/line/line-mcp/venv/bin/python3 tools/line_sync_postgres.py --once
```
First run creates `line_raw.*` + `cdb.contacts`, the `(chat_id, created_time)` index and the
`pg_trgm` GIN index on `chat_history.content`. Give the DB role `CREATE` on the database
(extension needs superuser or `pg_trgm` pre-installed).

### Start over on Redroid (keeps the LINE login)
```bash
docker stop redroid && docker rm redroid          # redroid-data volume (LINE account) survives
bash setup/02-redroid.sh                          # recreate → host: systemctl start redroid-binder.service → rerun
```

## 3. Manual operations

```bash
# screenshot (no VNC/scrcpy on the _64only image)
adb -s 127.0.0.1:5555 exec-out screencap -p > /tmp/screen.png
# UI hierarchy
adb -s 127.0.0.1:5555 shell uiautomator dump /sdcard/ui.xml && adb -s 127.0.0.1:5555 shell cat /sdcard/ui.xml
# open a chat by id (needs root inside Android — plain `am start` fails from uid 2000)
adb -s 127.0.0.1:5555 shell su root am start -a android.intent.action.VIEW -f 0x8000 \
  -n jp.naver.line.android/.service.share.DirectShareToChatActivity --es chatId '<chat_id>'
# read the live DB as root
sqlite3 "file:/var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases/naver_line?mode=ro" \
  'select chat_id,chat_name,last_created_time from chat order by last_created_time desc limit 5'
# smoke-test a tool from the shell (Bearer via query string also works on /files)
curl -s -H "Authorization: Bearer $LINE_MCP_API_KEY" http://127.0.0.1:8765/health
```

## 4. Upgrading

```bash
cd /home/line/line-mcp && sudo -u line git pull
bash setup/04-line-mcp-install.sh          # re-syncs venv deps, scripts, units; restarts nothing it doesn't need to
sudo systemctl restart line-mcp.service    # pick up code changes
```

## 5. Things that bit us (keep)

- `redroid-binder.service` existed for 7 weeks without ever being **enabled**; every host boot
  lost Android. Audit with `is-enabled`, not `is-active`.
- Monotonic timers (`OnBootSec`/`OnUnitActiveSec`) ended up `elapsed / Trigger: n/a` after a
  host migration and never fired again → token went stale. Use `OnCalendar` + `Persistent`.
- A 0-byte JSON state file crash-looped a consumer for 5 weeks: write state atomically
  (`.tmp` + `os.replace`) and tolerate empty files.
- Docker bridge IPs drift across restarts; cross-host consumers must use the LXC's LAN IP, and
  a host-net service consumed cross-host must not bind 127.0.0.1.
- `is_processed()`-style consumers that treat `retry_count>=3` as done silently lose messages
  during a token outage — make token/CDN failures non-counting retries.
- Android power settings (`screen_off_timeout`, doze off, …) persist in the `redroid-data`
  volume; no reapply service needed.
