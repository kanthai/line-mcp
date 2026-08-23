#!/bin/sh
# Nightly restart of the LINE Android app inside the redroid container to
# reclaim its leaking native heap (grows ~3GB+ over a couple of days' uptime).
# Restarts ONLY the app, not the container or the line-mcp/line-assistant agents.
set -u
PKG=jp.naver.line.android
log() { echo "[line-nightly-restart] $*"; }

before=$(docker exec redroid sh -c "dumpsys meminfo $PKG 2>/dev/null | grep 'TOTAL PSS' | awk '{print \$3}'")
log "PSS before: ${before:-unknown} KB"

# am is an Android shell-script wrapper — must go through sh -c, not raw exec.
# Use explicit `am start -n` (deterministic); monkey is flaky and sometimes no-ops.
ACT="$PKG/jp.naver.line.android.activity.SplashActivity"
docker exec redroid sh -c "am force-stop $PKG"
sleep 3

i=0
while [ "$i" -lt 5 ]; do
  docker exec redroid sh -c "am start -n $ACT" >/dev/null 2>&1
  sleep 6
  if docker exec redroid sh -c "ps -A | grep -q $PKG"; then
    after=$(docker exec redroid sh -c "dumpsys meminfo $PKG 2>/dev/null | grep 'TOTAL PSS' | awk '{print \$3}'")
    log "relaunched OK on attempt $((i+1)), PSS after: ${after:-unknown} KB"
    exit 0
  fi
  i=$((i+1))
  log "attempt $i: not up yet, retrying"
done

log "ERROR: $PKG did not come back after 5 relaunch attempts"
exit 1
