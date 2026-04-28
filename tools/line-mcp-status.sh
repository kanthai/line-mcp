#!/usr/bin/env bash
set -euo pipefail

echo "==> Waydroid status"
waydroid status 2>/dev/null || echo "waydroid status unavailable"

echo ""
echo "==> LINE process"
if sudo waydroid shell -- ps -A | grep -q "jp.naver.line.android"; then
    sudo waydroid shell -- ps -A | grep "jp.naver.line.android"
else
    echo "LINE not running"
fi

echo ""
echo "==> User timers"
systemctl --user --no-pager --plain --type=timer --all | grep -E 'waydroid-watchdog|line-watchdog' || true

echo ""
echo "==> Recent waydroid-watchdog logs"
journalctl --user -u waydroid-watchdog.service -n 10 --no-pager || true

echo ""
echo "==> Recent line-watchdog logs"
journalctl --user -u line-watchdog.service -n 10 --no-pager || true
