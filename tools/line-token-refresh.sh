#!/usr/bin/env bash
set -euo pipefail

LOG_TAG="[line-token-refresh]"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "$LOG_TAG Refreshing CDN token from LINE SQLite DB"
python3 "${SCRIPT_DIR}/refresh_token.py" 2>&1 | sed "s/^/$LOG_TAG /"
