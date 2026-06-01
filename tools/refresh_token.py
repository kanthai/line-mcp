#!/usr/bin/env python3
"""
Read the X-Line-Access token directly from LINE's SQLite database
(naver_line setting table, key=OBS_ENCRYPTED_ACCESS_TOKEN) and save it
to ~/.config/line-mcp/auth.json for use by the CDN download path.

If LINE has cleared OBS_ENCRYPTED_ACCESS_TOKEN, first open a small set of
private 1:1 chats that have unread image messages, because live testing showed
this is the reliable trigger for LINE to repopulate the OBS token. If no such
chat exists, fall back to LINE's current Android dynamic chat shortcuts through
DirectShareToChatActivity. If LINE still does not repopulate the token, this
script fails without saving stale auth.

Usage:
    python3 tools/refresh_token.py

Optional env:
    LINE_TOKEN_REFRESH_CHAT_ID          force a specific chat id
    LINE_TOKEN_REFRESH_MAX_CANDIDATES   max automatic chats to try, default 5
    LINE_TOKEN_REFRESH_WAIT_SECONDS     wait per launched chat, default 15
    LINE_TOKEN_REFRESH_DB_MODE          override LINE_MCP_DB_MODE for this script only (e.g. direct, postgres)
"""
from __future__ import annotations
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Apply per-script DB mode override before line_db is imported
_db_mode_override = os.environ.get("LINE_TOKEN_REFRESH_DB_MODE")
if _db_mode_override:
    os.environ["LINE_MCP_DB_MODE"] = _db_mode_override

SETTING_KEY = "OBS_ENCRYPTED_ACCESS_TOKEN"
DEFAULT_WAIT_SECONDS = 15.0
DEFAULT_MAX_CANDIDATES = 5


class TokenUnavailable(RuntimeError):
    pass


def _host_db() -> Path:
    sys.path.insert(0, str(Path(__file__).parent))
    from line_db import HOST_DB
    return Path(HOST_DB)


def _read_raw_value() -> str | None:
    conn = sqlite3.connect(f"file:{_host_db()}?mode=ro", uri=True, timeout=10)
    try:
        row = conn.execute("SELECT value FROM setting WHERE key = ?", (SETTING_KEY,)).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return str(row[0]).strip()


def _extract_token(raw: str) -> str:
    idx = len(raw)
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] == "=":
            idx = i + 1
            break
    token = raw[:idx]
    if not re.match(r"^[A-Za-z0-9+/]+=*$", token):
        raise TokenUnavailable("OBS_ENCRYPTED_ACCESS_TOKEN value is not valid base64")
    return token


_ADB_DEVICE = os.environ.get("LINE_ADB_DEVICE", "127.0.0.1:5555")


def _run_adb(args: list[str], timeout: int = 20) -> str:
    return subprocess.run(
        ["adb", "-s", _ADB_DEVICE, "shell", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    ).stdout


def list_direct_share_chat_ids() -> list[tuple[str, str]]:
    out = _run_adb(["su", "root", "cmd", "shortcut", "get-shortcuts", "jp.naver.line.android"])
    shortcuts: list[tuple[str, str]] = []
    current_label = ""
    for line in out.splitlines():
        label = re.search(r"shortLabel=(.*?), resId=", line)
        if label:
            current_label = label.group(1)
            continue
        if "DirectShareToChatActivity" not in line:
            continue
        chat = re.search(r"chatId=([^}\]]+)", line)
        if chat:
            shortcuts.append((current_label, chat.group(1)))
    return shortcuts


def _max_candidates() -> int:
    raw = os.environ.get("LINE_TOKEN_REFRESH_MAX_CANDIDATES", str(DEFAULT_MAX_CANDIDATES))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_CANDIDATES


def _dedupe_chats(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for label, chat_id in candidates:
        if chat_id in seen:
            continue
        seen.add(chat_id)
        result.append((label, chat_id))
    return result


def list_private_unread_image_chat_ids(limit: int | None = None) -> list[tuple[str, str]]:
    if limit is None:
        limit = _max_candidates()
    limit = max(1, int(limit))

    sys.path.insert(0, str(Path(__file__).parent))
    import line_db

    rows = line_db._q(f"""
        SELECT chat_id, name, image_count, unread_images, latest_img_at FROM (
          SELECT
            c.chat_id AS chat_id,
            COALESCE(con.overridden_name, con.profile_name, c.chat_name, c.chat_id) AS name,
            SUM(CASE WHEN COALESCE(h.attachement_image, 0) != 0 THEN 1 ELSE 0 END) AS image_count,
            SUM(
              CASE
                WHEN COALESCE(h.attachement_image, 0) != 0
                 AND CAST(h.created_time AS INTEGER) > COALESCE(CAST(c.read_up AS INTEGER), 0)
                THEN 1 ELSE 0
              END
            ) AS unread_images,
            MAX(
              CASE
                WHEN COALESCE(h.attachement_image, 0) != 0
                THEN CAST(h.created_time AS INTEGER) ELSE 0
              END
            ) AS latest_img_at
          FROM chat c
          JOIN cdb.contacts con ON con.mid = c.chat_id
          LEFT JOIN chat_history h ON h.chat_id = c.chat_id
          WHERE c.type = 1
            AND con.contact_type = 1
            AND con.bot_category IS NULL
          GROUP BY c.chat_id, c.chat_name, con.overridden_name, con.profile_name
        ) sub
        WHERE unread_images > 0
        ORDER BY unread_images DESC, latest_img_at DESC
        LIMIT {limit}
    """, attach_contact=True)

    candidates: list[tuple[str, str]] = []
    for row in rows:
        chat_id = str(row.get("chat_id") or "").strip()
        if not chat_id:
            continue
        name = str(row.get("name") or chat_id).strip()
        unread_images = int(row.get("unread_images") or 0)
        candidates.append((f"private-unread-image:{name}:{unread_images}", chat_id))
    return candidates


def trigger_token_regeneration(chat_id: str, wait_seconds: float) -> None:
    # Must run as root to launch unexported DirectShareToChatActivity
    _run_adb([
        "su", "root", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-f", "0x8000",
        "-n", "jp.naver.line.android/.service.share.DirectShareToChatActivity",
        "--es", "chatId", chat_id,
    ])
    time.sleep(wait_seconds)


def _candidate_chats() -> list[tuple[str, str]]:
    forced = os.environ.get("LINE_TOKEN_REFRESH_CHAT_ID")
    if forced:
        return [("env", forced)]

    limit = _max_candidates()
    private_unread_images = list_private_unread_image_chat_ids(limit=limit)
    if len(private_unread_images) >= limit:
        return private_unread_images[:limit]

    return _dedupe_chats(private_unread_images + list_direct_share_chat_ids())[:limit]


def read_token_from_db(chat_id: str | None = None, wait_seconds: float | None = None) -> str:
    raw = _read_raw_value()
    if raw is None:
        if wait_seconds is None:
            wait_seconds = float(os.environ.get("LINE_TOKEN_REFRESH_WAIT_SECONDS", DEFAULT_WAIT_SECONDS))
        candidates = [("argument", chat_id)] if chat_id else _candidate_chats()
        if not candidates:
            raise TokenUnavailable(f"{SETTING_KEY} is null and no LINE chat refresh candidates are available")
        print(f"[!] {SETTING_KEY} is null; trying {len(candidates)} LINE chat refresh candidate(s)", file=sys.stderr)
        for label, candidate_chat_id in candidates:
            print(f"[*] Opening LINE chat refresh candidate {label!r} ({candidate_chat_id})", file=sys.stderr)
            trigger_token_regeneration(candidate_chat_id, wait_seconds)
            raw = _read_raw_value()
            if raw is not None:
                break

    if raw is None:
        raise TokenUnavailable(f"{SETTING_KEY} is still missing after LINE chat refresh")
    return _extract_token(raw)


def main():
    print("[*] Reading token from LINE SQLite database...", file=sys.stderr)
    token = read_token_from_db()
    print(f"[+] Token read ({len(token)} chars)", file=sys.stderr)

    sys.path.insert(0, str(Path(__file__).parent))
    from line_db import save_auth_token
    save_auth_token(token)
    print("[+] Saved to ~/.config/line-mcp/auth.json", file=sys.stderr)


if __name__ == "__main__":
    main()
