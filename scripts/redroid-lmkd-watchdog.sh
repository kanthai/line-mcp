#!/usr/bin/env bash
# redroid-lmkd-watchdog: detect the Redroid lmkd epoll_wait EINVAL busy-spin
# (lmkd pins a core at 100% logging "epoll_wait failed (errno=22)") and restart
# the lmkd service in-place to clear it. Live fix proven 2026-08-21.
# Boot-prop fix (ro.lmk.use_psi=false) was rejected: it crash-loops lmkd on this
# cgroup-v2 Redroid image. See vault _raw/2026-08-21-redroid-lmkd-epoll-einval-spin-ct103.md
set -uo pipefail
LOG_TAG="[lmkd-watchdog]"
CONTAINER="redroid"
THRESH="${LMKD_EPOLL_THRESH:-50}"

if ! docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    echo "$LOG_TAG $CONTAINER not running; skipping"
    exit 0
fi

# The busy-spin emits the signature line thousands of times per second. A healthy
# lmkd emits it zero times. Count occurrences in a recent lowmemorykiller error window.
CNT=$(docker exec "$CONTAINER" sh -c "logcat -d -t 400 -s lowmemorykiller:E 2>/dev/null | grep -c 'epoll_wait failed'" 2>/dev/null)
CNT=${CNT:-0}
[[ "$CNT" =~ ^[0-9]+$ ]] || CNT=0

if [ "$CNT" -ge "$THRESH" ]; then
    OLDPID=$(docker exec "$CONTAINER" getprop init.svc_debug_pid.lmkd 2>/dev/null)
    echo "$LOG_TAG lmkd spin detected ($CNT epoll errors in window, pid=$OLDPID); restarting lmkd"
    docker exec "$CONTAINER" sh -c 'stop lmkd; sleep 1; start lmkd' 2>/dev/null || true
    sleep 3
    NEWPID=$(docker exec "$CONTAINER" getprop init.svc_debug_pid.lmkd 2>/dev/null)
    SVC=$(docker exec "$CONTAINER" getprop init.svc.lmkd 2>/dev/null)
    echo "$LOG_TAG lmkd restarted: svc=$SVC oldpid=$OLDPID newpid=$NEWPID"
else
    echo "$LOG_TAG lmkd healthy ($CNT epoll errors in window)"
fi
