#!/usr/bin/env bash
# Capture a fresh X-Line-Access CDN session token from LINE's SSL traffic.
#
# Usage:
#   ./refresh-cdn-token.sh [--no-clear-cache] [--timeout 120]
#
# The script:
#   1. Optionally clears LINE's image caches to force a CDN re-download.
#   2. Attaches frida_sniff_download.py to the running LINE process.
#   3. Sweeps the entire chat list with taps and scrolls to trigger image loads.
#   4. Exits as soon as frida reports a CDN token saved (works even when the
#      session token hasn't changed — detection is log-based, not diff-based).
#
# Requires: LINE running in Waydroid, frida-server running at 27042.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/../../vllm_env/bin/python3"
AUTH_JSON="$HOME/.config/line-mcp/auth.json"
FRIDA_LOG="/tmp/cdn_sniff_$(date +%s).log"
MINT_CHAT="u3afadd8153d65b14766bfc9b8da27cfe"

CLEAR_CACHE=1
TIMEOUT=120
SPAWN=1       # restart LINE to clear Glide memory cache; use --no-spawn to attach only
FRIDA_PID=

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-clear-cache) CLEAR_CACHE=0 ;;
        --no-spawn)       SPAWN=0 ;;
        --timeout) TIMEOUT="$2"; shift ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
    shift
done

# ── Helpers ───────────────────────────────────────────────────────────────────
wsh()  { sudo waydroid shell -- sh -c "$*"; }
tap()  { sudo waydroid shell -- input tap "$1" "$2" 2>/dev/null || true; }
swipe(){ sudo waydroid shell -- input swipe "$1" "$2" "$3" "$4" 400 2>/dev/null || true; }

die() { printf '[!] %b\n' "$*" >&2; cleanup; exit 1; }

check_log() {
    # Success if frida wrote "CDN token captured" to the log
    grep -q "CDN token captured" "$FRIDA_LOG" 2>/dev/null
}

cleanup() {
    if [[ -n "$FRIDA_PID" ]] && kill -0 "$FRIDA_PID" 2>/dev/null; then
        kill "$FRIDA_PID" 2>/dev/null || true
        wait "$FRIDA_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── 1. Preflight checks ───────────────────────────────────────────────────────
echo "[*] Checking LINE and frida-server..."

LINE_PID=$(wsh "ps -A | grep '[l]ine.android' | awk '{print \$2}'" 2>/dev/null | tr -d '[:space:]')
[[ -n "$LINE_PID" ]] || die "LINE is not running. Start it first."

FRIDA_RUNNING=$(wsh "ps -A | grep -c '[f]rida-server'" 2>/dev/null | tr -d '[:space:]' || true)
if [[ "${FRIDA_RUNNING:-0}" -lt 1 ]]; then
    echo "[*] Starting frida-server..."
    wsh "setsid /data/local/tmp/frida-server -l 0.0.0.0:27042 </dev/null >/data/local/tmp/frida.log 2>&1 &"
    sleep 4
    FRIDA_RUNNING=$(wsh "ps -A | grep -c '[f]rida-server'" 2>/dev/null | tr -d '[:space:]' || true)
    [[ "${FRIDA_RUNNING:-0}" -ge 1 ]] || die "frida-server failed to start."
fi

echo "[*] LINE pid=$LINE_PID, frida-server running."

# ── 2. Clear image caches (attach-mode only; spawn mode handles its own clear) ─
if [[ "$CLEAR_CACHE" -eq 1 && "$SPAWN" -eq 0 ]]; then
    echo "[*] Clearing LINE image caches (disk only — use spawn to also clear memory)..."
    wsh "rm -rf /data/data/jp.naver.line.android/cache/image_manager_disk_cache/* 2>/dev/null; true"
    wsh "rm -rf /data/data/jp.naver.line.android/cache/coil3_disk_cache/* 2>/dev/null; true"
    wsh "rm -rf /sdcard/Android/data/jp.naver.line.android/files/chats/*/messages/* 2>/dev/null; true"
    echo "[*] Caches cleared."
fi

# ── 3. Start frida hook ───────────────────────────────────────────────────────
SPAWN_FLAG=""
[[ "$SPAWN" -eq 1 ]] && SPAWN_FLAG="--spawn"

if [[ "$SPAWN" -eq 1 ]]; then
    echo "[*] Spawn mode: force-stopping LINE, clearing all caches, and respawning..."
    echo "[*]   (Glide memory cache is also cleared on restart — ensures CDN re-fetch)"
fi

echo "[*] Starting frida hook (log: $FRIDA_LOG)..."
"$PYTHON" -u "$SCRIPT_DIR/frida_sniff_download.py" $SPAWN_FLAG > "$FRIDA_LOG" 2>&1 &
FRIDA_PID=$!

# In spawn mode the script kills LINE, clears caches, and spawns — wait longer
HOOK_WAIT=15
[[ "$SPAWN" -eq 1 ]] && HOOK_WAIT=35

for i in $(seq 1 $HOOK_WAIT); do
    sleep 1
    if grep -q "SSL_write" "$FRIDA_LOG" 2>/dev/null; then
        echo "[*] Hooks installed (${i}s)."
        break
    fi
    kill -0 "$FRIDA_PID" 2>/dev/null || die "frida script exited early.\n$(tail -20 "$FRIDA_LOG")"
done
grep -q "SSL_write" "$FRIDA_LOG" 2>/dev/null || die "SSL_write hook not installed after ${HOOK_WAIT}s."

# In spawn mode frida_sniff_download.py already sends the deep link and taps
# after startup — check immediately, then fall through to our own sweeps.
if [[ "$SPAWN" -eq 1 ]]; then
    echo "[*] Waiting for frida spawn-mode navigation to complete (~30s)..."
    for i in $(seq 1 35); do
        sleep 1
        check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }
        kill -0 "$FRIDA_PID" 2>/dev/null || {
            check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }
            die "frida exited without capturing token.\n$(tail -20 "$FRIDA_LOG")"
        }
    done
fi

# ── 4. Sweep function — open every visible chat in the left pane ──────────────
# Screen: 1280×688. Two-pane layout. Left pane: x≈0–350.
# Tabs (~55px) + search bar (~55px) at top → chats start at y≈110. Item height ~78px.
sweep_chats() {
    local label="${1:-}"
    [[ -n "$label" ]] && echo "[*] $label"
    for y in 148 226 304 382 460 538 616 680; do
        check_log && return 0
        tap 175 "$y"
        sleep 1.2
    done
    sleep 3   # let last-opened chat finish loading images
    return 1
}

# ── 5. Navigate to chat tab and run sweeps ────────────────────────────────────
navigate() {
    sudo waydroid shell -- am start -a android.intent.action.VIEW \
        -d "line://nv/chat/${MINT_CHAT}" 2>/dev/null || true
    sleep 2
}

navigate
sweep_chats "Sweep 1 — top of chat list" || true
check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }

echo "[*] Scrolling chat list down..."
swipe 175 580 175 200   # swipe up = scroll list down
sleep 1.5
sweep_chats "Sweep 2 — after scroll" || true
check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }

swipe 175 580 175 200
sleep 1.5
sweep_chats "Sweep 3 — after second scroll" || true
check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }

# ── 6. Poll with periodic re-navigation until timeout ─────────────────────────
echo "[*] Waiting up to ${TIMEOUT}s for CDN token (continuing taps)..."
DEADLINE=$(( $(date +%s) + TIMEOUT ))
ROUND=0

while [[ $(date +%s) -lt $DEADLINE ]]; do
    sleep 2
    check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }

    kill -0 "$FRIDA_PID" 2>/dev/null || {
        check_log && { echo "[+] Token captured — saved to $AUTH_JSON"; exit 0; }
        die "frida exited without capturing token.\n$(tail -20 "$FRIDA_LOG")"
    }

    ELAPSED=$(( $(date +%s) - (DEADLINE - TIMEOUT) ))
    if (( ELAPSED % 20 == 0 && ELAPSED > 0 )); then
        ROUND=$(( ROUND + 1 ))
        echo "[*] Still waiting (${ELAPSED}s) — re-navigating (round $ROUND)..."
        navigate
        swipe 175 580 175 200
        sleep 1
        sweep_chats "Re-sweep round $ROUND" || true
    fi
done

die "Timed out after ${TIMEOUT}s. No CDN token captured.\nFrida log: $FRIDA_LOG"
