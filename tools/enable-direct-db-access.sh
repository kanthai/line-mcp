#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${1:-${SUDO_USER:-}}"
if [ -z "$TARGET_USER" ]; then
    echo "Usage: sudo $0 [target-user]" >&2
    exit 1
fi

USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
DB_DIR="${LINE_MCP_DB_DIR:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINE_MCP_DIR="${LINE_MCP_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
PYTHON="${LINE_MCP_PYTHON:-${USER_HOME}/vllm_env/bin/python}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo $0 [user]" >&2
    exit 1
fi

if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
    echo "Could not resolve home directory for user: $TARGET_USER" >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "Python interpreter not found or not executable: $PYTHON" >&2
    echo "Set LINE_MCP_PYTHON=/path/to/python and rerun." >&2
    exit 1
fi

if ! command -v setfacl >/dev/null 2>&1; then
    echo "setfacl is required. Install the acl package first." >&2
    exit 1
fi

if [ -z "$DB_DIR" ]; then
    for candidate in \
        "${USER_HOME}/.local/share/waydroid/data/data/jp.naver.line.android/databases" \
        "/var/lib/waydroid/data/data/jp.naver.line.android/databases"
    do
        if [ -d "$candidate" ]; then
            DB_DIR="$candidate"
            break
        fi
    done
fi

if [ -z "$DB_DIR" ] || [ ! -d "$DB_DIR" ]; then
    echo "LINE database directory not found. Set LINE_MCP_DB_DIR=/path/to/databases and rerun." >&2
    exit 1
fi

parent_dirs=()
dir="$DB_DIR"
while [ "$dir" != "/" ]; do
    dir="$(dirname "$dir")"
    parent_dirs=("$dir" "${parent_dirs[@]}")
    case "$dir" in
        "$USER_HOME"|/var/lib/waydroid) break ;;
    esac
done

for dir in "${parent_dirs[@]}"; do
    setfacl -m "u:${TARGET_USER}:--x" "$dir"
done

setfacl -m "u:${TARGET_USER}:rx" "$DB_DIR"
setfacl -d -m "u:${TARGET_USER}:rX" "$DB_DIR"

for file in naver_line naver_line-wal naver_line-shm contact contact-wal contact-shm; do
    if [ -e "${DB_DIR}/${file}" ]; then
        setfacl -m "u:${TARGET_USER}:r" "${DB_DIR}/${file}"
    fi
done

sudo -u "$TARGET_USER" env \
    "PYTHONPATH=${LINE_MCP_DIR}/tools" \
    "LINE_MCP_DB_MODE=direct" \
    "LINE_MCP_HOST_DB=${DB_DIR}/naver_line" \
    "LINE_MCP_HOST_CONTACT_DB=${DB_DIR}/contact" \
    "$PYTHON" - <<'PY'
import line_db

rows = line_db._q("SELECT chat_id FROM chat ORDER BY last_created_time DESC LIMIT 1")
if not rows:
    raise SystemExit("direct DB read returned no rows")
print("direct DB read ok:", rows[0]["chat_id"])
PY

uid="$(id -u "$TARGET_USER")"
if [ -d "/run/user/${uid}" ]; then
    sudo -u "$TARGET_USER" env "XDG_RUNTIME_DIR=/run/user/${uid}" \
        systemctl --user restart hermes-gateway.service
fi

if systemctl list-unit-files line-accounting-agent.service >/dev/null 2>&1; then
    systemctl restart line-accounting-agent.service
fi

echo "direct LINE DB access enabled for ${TARGET_USER}"
