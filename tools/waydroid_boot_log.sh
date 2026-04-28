#!/bin/bash
# Restart waydroid, wait for container to come up, capture logcat before it crashes.

set -e

echo "=== Stopping everything ==="
sudo systemctl stop waydroid-container.service 2>/dev/null || true
pkill -f "waydroid session start" 2>/dev/null || true
sleep 2

echo "=== Starting container service ==="
sudo systemctl start waydroid-container.service
sleep 1

echo "=== Starting session ==="
waydroid session start &
SESSION_PID=$!

echo "=== Waiting for container to reach RUNNING ==="
for i in $(seq 1 30); do
    sleep 1
    STATUS=$(waydroid status 2>/dev/null | grep "^Container:" | awk '{print $2}')
    echo "  [$i] Container: $STATUS"
    if [ "$STATUS" = "RUNNING" ]; then
        echo "=== Container RUNNING — capturing logcat ==="
        sudo waydroid logcat -- -v time 2>&1 | tee /tmp/waydroid_boot.log &
        LOGCAT_PID=$!

        # Wait until container dies
        while [ "$(waydroid status 2>/dev/null | grep '^Container:' | awk '{print $2}')" = "RUNNING" ]; do
            sleep 2
        done
        echo "=== Container stopped ==="
        kill $LOGCAT_PID 2>/dev/null || true
        break
    fi
done

echo ""
echo "=== Last 100 lines of boot log ==="
tail -100 /tmp/waydroid_boot.log 2>/dev/null || echo "(no log captured)"
echo ""
echo "Full log saved to /tmp/waydroid_boot.log"
