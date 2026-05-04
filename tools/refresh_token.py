#!/usr/bin/env python3
"""
Read the X-Line-Access token directly from LINE's SQLite database
(naver_line setting table, key=OBS_ENCRYPTED_ACCESS_TOKEN) and save it
to ~/.config/line-mcp/auth.json for use by the CDN download path.

Usage:
    python3 tools/refresh_token.py

No Frida, no network interception needed — LINE stores the token in its
SQLite database which is readable as root inside the Waydroid container.
"""
from __future__ import annotations
import subprocess, sys, re
from pathlib import Path

DB_PATH = "/data/data/jp.naver.line.android/databases/naver_line"
SETTING_KEY = "OBS_ENCRYPTED_ACCESS_TOKEN"


def read_token_from_db() -> str:
    result = subprocess.run(
        ["sudo", "waydroid", "shell", "--", "sqlite3", DB_PATH,
         f"SELECT value FROM setting WHERE key = '{SETTING_KEY}';"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"sqlite3 query failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    # The stored value is base64 token followed by metadata (non-base64 digits).
    # Strip everything after the last '=' padding character.
    idx = len(raw)
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] == "=":
            idx = i + 1
            break
    token = raw[:idx]
    if not re.match(r"^[A-Za-z0-9+/]+=*$", token):
        raise RuntimeError(f"Extracted value doesn't look like base64: {token[:40]}...")
    return token


def main():
    print("[*] Reading token from LINE SQLite database...", file=sys.stderr)
    token = read_token_from_db()
    print(f"[+] Token read ({len(token)} chars)", file=sys.stderr)

    sys.path.insert(0, str(Path(__file__).parent))
    from line_db import save_auth_token
    save_auth_token(token)
    print(f"[+] Saved to ~/.config/line-mcp/auth.json", file=sys.stderr)


if __name__ == "__main__":
    main()
