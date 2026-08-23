#!/usr/bin/env bash
# 05 — inside the LXC: end-to-end health check of the stack. Safe to run anytime (read-only).
set -uo pipefail
ENV_FILE=/etc/line-mcp/line-mcp.env
ADB="adb -s 127.0.0.1:5555"
ok(){ printf '  \e[32mOK\e[0m   %s\n' "$*"; }
bad(){ printf '  \e[31mFAIL\e[0m %s\n' "$*"; RC=1; }
RC=0
# shellcheck disable=SC1090
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
[ "$(id -u)" -eq 0 ] || echo "(run as root for the full check — $ENV_FILE is 0600, so key-based tests will fail otherwise)"

echo "Redroid"
docker inspect --format '{{.State.Running}}' redroid 2>/dev/null | grep -q true && ok "container running" || bad "container not running (host: systemctl start redroid-binder.service)"
docker exec redroid getprop sys.boot_completed 2>/dev/null | grep -q 1 && ok "sys.boot_completed=1" || bad "Android not booted"
docker exec redroid ls /dev/binderfs/binder >/dev/null 2>&1 && ok "binder device present" || bad "no /dev/binderfs/binder"
$ADB get-state 2>/dev/null | grep -q device && ok "adb connected" || bad "adb not connected (adb connect 127.0.0.1:5555)"
$ADB shell ps -A 2>/dev/null | grep -q jp.naver.line.android && ok "LINE process running" || bad "LINE not running"
DB="${LINE_MCP_HOST_DB:-/var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases/naver_line}"
[ -e "$DB" ] && ok "LINE DB exists" || bad "LINE DB missing at $DB (not logged in?)"
if [ "$(id -u)" -eq 0 ] && [ -e "$DB" ]; then
  N=$(sqlite3 "file:$DB?mode=ro" 'select count(*) from chat' 2>/dev/null) && ok "LINE DB readable as root: $N chats" || bad "cannot read LINE DB as root"
  if [ "${LINE_MCP_DB_MODE:-auto}" != postgres ]; then
    N=$(sudo -u line sqlite3 "file:$DB?mode=ro" 'select count(*) from chat' 2>/dev/null) && ok "LINE DB readable as line (direct mode): $N chats" || bad "line user cannot read LINE DB — rerun setup/04 (docker drop-in + app gid), or use postgres mode"
  fi
fi

echo "line-mcp"
for u in line-mcp.service line-token-refresh.timer line-watchdog.timer line-restart.timer redroid-lmkd-watchdog.timer; do
  [ "$(systemctl is-enabled "$u" 2>/dev/null)" = enabled ] && [ "$(systemctl is-active "$u")" = active ] && ok "$u enabled+active" || bad "$u: $(systemctl is-enabled "$u" 2>/dev/null)/$(systemctl is-active "$u")"
done
if systemctl is-enabled line-sync-postgres.service >/dev/null 2>&1; then
  [ "$(systemctl is-active line-sync-postgres.service)" = active ] && ok "line-sync-postgres active" || bad "line-sync-postgres not active"
fi
curl -fs --max-time 5 http://127.0.0.1:8765/health | grep -q ok && ok "/health" || bad "/health not answering"
C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8765/mcp); [ "$C" = 401 ] && ok "/mcp without key → 401" || bad "/mcp without key → $C (expected 401)"
C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Authorization: Bearer ${LINE_MCP_API_KEY:-}" http://127.0.0.1:8765/mcp); { [ "$C" = 406 ] || [ "$C" = 200 ]; } && ok "/mcp with key → $C" || bad "/mcp with key → $C"
[ -s /home/line/.config/line-mcp/auth.json ] && ok "CDN token cached ($(stat -c %y /home/line/.config/line-mcp/auth.json | cut -d. -f1))" || bad "no auth.json yet (journalctl -u line-token-refresh)"

# live tool call through the MCP protocol
if [ -n "${LINE_MCP_API_KEY:-}" ] && command -v python3 >/dev/null; then
  python3 - "$LINE_MCP_API_KEY" <<'PY' && ok "MCP list_chats round-trip" || bad "MCP tool call failed"
import json, sys, urllib.request
key = sys.argv[1]; url = "http://127.0.0.1:8765/mcp"
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
def post(body, sid=None):
    h = dict(H); 
    if sid: h["mcp-session-id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), h), timeout=20)
    return r.headers.get("mcp-session-id"), r.read().decode()
sid, _ = post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}})
post({"jsonrpc":"2.0","method":"notifications/initialized"}, sid)
_, out = post({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_chats","arguments":{"limit":1}}}, sid)
assert '"result"' in out and '"isError": true' not in out, out[:300]
PY
fi
exit $RC
