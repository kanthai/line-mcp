#!/usr/bin/env python3
"""
Read the X-Line-Access token directly from LINE's SQLite database
(naver_line setting table, key=OBS_ENCRYPTED_ACCESS_TOKEN) and save it
to ~/.config/line-mcp/auth.json for use by the CDN download path.

Usage:
    python3 tools/refresh_token.py
"""
from __future__ import annotations
import re, sqlite3, sys
from pathlib import Path

SETTING_KEY = "OBS_ENCRYPTED_ACCESS_TOKEN"


def read_token_from_db() -> str:
    sys.path.insert(0, str(Path(__file__).parent))
    from line_db import HOST_DB

    conn = sqlite3.connect(f"file:{HOST_DB}?mode=ro", uri=True, timeout=10)
    row = conn.execute(
        "SELECT value FROM setting WHERE key = ?", (SETTING_KEY,)
    ).fetchone()
    conn.close()

    if not row or not row[0]:
        raise RuntimeError("OBS_ENCRYPTED_ACCESS_TOKEN not found in naver_line DB")

    raw = row[0].strip()
    # Stored value is base64 token followed by metadata digits after the last '='.
    idx = len(raw)
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] == "=":
            idx = i + 1
            break
    token = raw[:idx]
    if not re.match(r"^[A-Za-z0-9+/]+=*$", token):
        raise RuntimeError(f"Value doesn't look like base64: {token[:40]}...")
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
