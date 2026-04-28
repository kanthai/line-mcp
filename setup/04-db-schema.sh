#!/usr/bin/env bash
# Phase 1d: Dump LINE SQLite schema and sample rows
# Run as: bash 04-db-schema.sh  (passwordless sudo via /etc/sudoers.d/waydroid-watchdog)
set -euo pipefail

DB="/data/data/jp.naver.line.android/databases/naver_line"
WS="sudo waydroid shell --"

echo "==> Stopping LINE for a clean read"
$WS am force-stop jp.naver.line.android 2>/dev/null || true
sleep 2

echo ""
echo "==> SQLCipher check"
if $WS sqlite3 "$DB" "SELECT count(*) FROM sqlite_master;" > /dev/null 2>&1; then
    echo "  PLAIN SQLite — proceeding."
else
    echo "  *** SQLCIPHER ENCRYPTED *** — need Frida key extraction first."
    exit 1
fi

echo ""
echo "=== ALL TABLES ==="
$WS sqlite3 "$DB" ".tables"

echo ""
echo "=== Row counts ==="
$WS sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" \
    | while IFS= read -r t; do
        count=$($WS sqlite3 "$DB" "SELECT COUNT(*) FROM \"$t\";" 2>/dev/null || echo "err")
        printf "  %-45s %s rows\n" "$t" "$count"
    done

echo ""
echo "=== Full schema ==="
$WS sqlite3 "$DB" ".schema" | tee /tmp/naver_line_schema.sql
echo ""
echo "  Saved to /tmp/naver_line_schema.sql"

echo ""
echo "=== chat_history sample (5 rows) ==="
$WS sqlite3 -header "$DB" "SELECT id,type,chat_id,from_mid,content,created_time FROM chat_history ORDER BY id DESC LIMIT 5;" 2>/dev/null || true

echo ""
echo "=== chat sample (5 rows) ==="
$WS sqlite3 -header "$DB" "SELECT chat_id,chat_name,last_message,last_created_time,message_count,unread_type_and_count FROM chat ORDER BY last_created_time DESC LIMIT 5;" 2>/dev/null || true

echo ""
echo "Next: sudo bash 05-frida-install.sh"
