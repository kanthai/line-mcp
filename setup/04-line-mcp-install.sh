#!/usr/bin/env bash
# 04 — inside the LXC (root): install line-mcp under /home/line/line-mcp, its venv, env file,
# helper scripts, systemd units + timers. Idempotent; re-run after `git pull`.
#
#   bash setup/04-line-mcp-install.sh            # direct SQLite read path (default)
#   WITH_POSTGRES=1 bash setup/04-line-mcp-install.sh   # also enable the Postgres mirror service
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root inside the LXC" >&2; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST=/home/line/line-mcp
ENV_FILE=/etc/line-mcp/line-mcp.env
WITH_POSTGRES="${WITH_POSTGRES:-0}"

echo "== code → $DEST"
if [ "$SRC" != "$DEST" ]; then
  install -d -o line -g line "$DEST"
  rsync -a --delete --exclude venv --exclude .git --exclude '__pycache__' --exclude .pytest_cache "$SRC/" "$DEST/"
  chown -R line:line "$DEST"
fi

echo "== venv"
if [ ! -x "$DEST/venv/bin/python3" ]; then
  sudo -u line python3 -m venv "$DEST/venv"
fi
sudo -u line "$DEST/venv/bin/python3" -m pip install -q --upgrade pip
sudo -u line "$DEST/venv/bin/python3" -m pip install -q -r "$DEST/requirements.txt"

echo "== env file"
install -d -m 755 /etc/line-mcp
if [ ! -f "$ENV_FILE" ]; then
  install -m 600 "$DEST/config/line-mcp.env.example" "$ENV_FILE"
  KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -i "s|^LINE_MCP_API_KEY=.*|LINE_MCP_API_KEY=$KEY|" "$ENV_FILE"
  IP=$(hostname -I | awk '{print $1}')
  sed -i "s|^#LINE_MCP_MEDIA_URL=.*|LINE_MCP_MEDIA_URL=http://$IP:8765/files|" "$ENV_FILE"
  echo "   wrote $ENV_FILE with a fresh LINE_MCP_API_KEY — edit DATABASE_URL/LINE_MCP_DB_MODE if you want postgres mode"
else
  echo "   $ENV_FILE exists — left untouched"
fi

echo "== direct-mode DB access for the line user"
# shellcheck disable=SC1090
DBFILE=$(set -a; . "$ENV_FILE"; set +a; echo "${LINE_MCP_HOST_DB:-}")
DBDIR=$(dirname "${DBFILE:-/var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases/naver_line}")
# 1) traverse /var/lib/docker (Docker resets it to 0710 each start → drop-in re-applies)
install -d -m 755 /etc/systemd/system/docker.service.d
install -m 644 "$DEST/systemd/docker.service.d-line-mcp-db-access.conf" /etc/systemd/system/docker.service.d/line-mcp-db-access.conf
systemctl daemon-reload
chmod o+x /var/lib/docker
# 2) membership in the LINE app's Android gid (files are rw-rw---- <appuid>:<appgid>)
if [ -d "$DBDIR" ]; then
  G=$(stat -c %g "$DBDIR")
  getent group "$G" >/dev/null || groupadd -g "$G" android_line
  usermod -aG "$G" line
  echo "   line added to gid $G ($(getent group "$G" | cut -d: -f1)); re-login not needed for systemd services"
else
  echo "   LINE DB dir not found yet ($DBDIR) — log in to LINE (setup/03) and re-run this script"
fi

echo "== helper scripts → /usr/local/bin"
install -m 755 "$DEST"/scripts/line-watchdog.sh "$DEST"/scripts/line-nightly-restart.sh "$DEST"/scripts/redroid-lmkd-watchdog.sh /usr/local/bin/

echo "== systemd units"
UNITS="line-mcp.service line-token-refresh.service line-token-refresh.timer line-watchdog.service line-watchdog.timer line-restart.service line-restart.timer redroid-lmkd-watchdog.service redroid-lmkd-watchdog.timer"
[ "$WITH_POSTGRES" = 1 ] && UNITS="$UNITS line-sync-postgres.service"
for u in $UNITS; do install -m 644 "$DEST/systemd/$u" "/etc/systemd/system/$u"; done
systemctl daemon-reload
systemctl enable --now line-mcp.service line-token-refresh.timer line-watchdog.timer line-restart.timer redroid-lmkd-watchdog.timer
if [ "$WITH_POSTGRES" = 1 ]; then
  grep -q '^DATABASE_URL=' "$ENV_FILE" || echo "   WARNING: set DATABASE_URL in $ENV_FILE before the sync can work"
  systemctl enable --now line-sync-postgres.service
fi
# first token pull now rather than at the next half hour
systemctl start line-token-refresh.service || echo "   token refresh failed (LINE not logged in yet?) — it retries every 30 min"

echo
systemctl --no-pager --no-legend list-units 'line-*' 'redroid-*'
echo "done. Verify with: bash $DEST/setup/05-verify.sh"
