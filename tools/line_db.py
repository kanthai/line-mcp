"""
LINE SQLite read path.

All queries run inside the Waydroid container via:
  sudo waydroid shell -- sqlite3 <db> -json "<sql>"

Confirmed column names from setup/04-db-schema.sh on 2026-04-24:
  chat:         chat_id, chat_name, last_message, last_created_time, unread_type_and_count
  chat_history: id, type, chat_id, from_mid, content, created_time
"""
import base64
import hashlib
import hmac as _hmac
import json
import mimetypes
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CONTAINER_DB = "/data/data/jp.naver.line.android/databases/naver_line"
CONTACT_DB  = "/data/data/jp.naver.line.android/databases/contact"

# ── E2EE CDN auth ─────────────────────────────────────────────────────────────
_AUTH_FILE = Path.home() / ".config" / "line-mcp" / "auth.json"
_CDN_BASE  = os.environ.get("LINE_CDN_BASE", "https://obs-th.line-apps.com/r/talk")
_LINE_APP  = "ANDROIDSECONDARY\t26.5.0\tAndroid OS\t13"
_UA        = "Dalvik/2.1.0 (Linux; U; Android 13; WayDroid arm64 only Device Build/TQ3A.230901.001)"


def _load_auth() -> dict:
    if _AUTH_FILE.exists():
        return json.loads(_AUTH_FILE.read_text())
    return {}


def save_auth_token(x_line_access: str) -> None:
    """Persist a fresh X-Line-Access token for CDN downloads."""
    _AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    auth = _load_auth()
    auth["x_line_access"] = x_line_access
    _AUTH_FILE.write_text(json.dumps(auth, indent=2))


def _x_line_access() -> str | None:
    return os.environ.get("LINE_ACCESS_TOKEN") or _load_auth().get("x_line_access")


def _make_x_talk_meta(server_id: str) -> str:
    """Build the X-Talk-Meta header value (base64 JSON wrapping Thrift binary)."""
    sid_b = server_id.encode()
    thrift = struct.pack(">BHI", 11, 4, len(sid_b)) + sid_b  # string field 4 = server_id
    thrift += struct.pack(">BHq", 10, 5, 0)                   # i64 field 5 = 0
    thrift += struct.pack(">BHq", 10, 6, 0)                   # i64 field 6 = 0
    thrift += struct.pack(">BHB", 2, 14, 0)                   # bool field 14 = false
    thrift += struct.pack(">BHB", 3, 19, 0)                   # byte field 19 = 0
    thrift += struct.pack(">BHBi", 14, 27, 12, 0)             # list<struct> field 27, empty
    thrift += b"\x00"                                          # stop
    inner = base64.b64encode(thrift).decode()
    return base64.b64encode(json.dumps({"message": inner}, separators=(",", ":")).encode()).decode()


def _download_blob(server_id: str, sid: str, oid: str) -> bytes | None:
    """Download an E2EE blob from the CDN. Returns None if auth token missing or request fails."""
    token = _x_line_access()
    if not token:
        return None
    url = f"{_CDN_BASE}/{sid}/{oid}"
    req = Request(url, headers={
        "X-Talk-Meta": _make_x_talk_meta(server_id),
        "X-Line-Access": token,
        "X-Line-Application": _LINE_APP,
        "User-Agent": _UA,
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


def _decrypt_blob(blob: bytes, km_b64: str) -> bytes:
    """
    Decrypt a LINE E2EE media blob.
    blob = AES-256-CTR(Kenc, IV||0x00^4, plaintext) || HMAC-SHA256(Kmac, ciphertext)
    where Kenc[32], Kmac[32], IV[12] = HKDF-SHA256(KM, info="FileEncryption", L=76)
    """
    km = base64.b64decode(km_b64)
    derived = HKDF(
        algorithm=hashes.SHA256(), length=76,
        salt=None, info=b"FileEncryption",
        backend=default_backend(),
    ).derive(km)
    kenc, kmac, iv = derived[:32], derived[32:64], derived[64:76]
    c, mac = blob[:-32], blob[-32:]
    if _hmac.new(kmac, c, hashlib.sha256).digest() != mac:
        raise ValueError("HMAC-SHA256 integrity check failed — wrong key or corrupt blob")
    cipher = Cipher(algorithms.AES(kenc), modes.CTR(iv + b"\x00" * 4), backend=default_backend())
    return cipher.decryptor().update(c)


def _q(sql: str, attach_contact: bool = False) -> list[dict]:
    prefix = f"ATTACH '{CONTACT_DB}' AS cdb; " if attach_contact else ""
    cmd = ["sudo", "waydroid", "shell", "--", "sqlite3", CONTAINER_DB, "-json", prefix + sql]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout) if r.stdout.strip() else []


def _s(val: str) -> str:
    return val.replace("'", "''")


def _unread_count_sql(alias: str = "c") -> str:
    typed = f"CAST(COALESCE(NULLIF({alias}.unread_type_and_count, ''), '0') AS INTEGER)"
    delta = (
        f"MAX(CAST(COALESCE({alias}.message_count, 0) AS INTEGER) - "
        f"CAST(COALESCE({alias}.read_message_count, 0) AS INTEGER), 0)"
    )
    return f"MAX({typed}, {delta})"


@dataclass
class Chat:
    chat_id: str
    name: str
    last_message: str
    last_message_at: int   # unix ms
    unread_count: int


@dataclass
class Message:
    message_id: int
    chat_id: str
    sender_id: str
    sender_name: str
    text: str
    created_at: int        # unix ms
    type: int


@dataclass
class InboundMessage(Message):
    chat_name: str


@dataclass
class ReplyCandidate:
    chat_id: str
    chat_name: str
    unread_count: int
    latest_inbound_message_id: int
    latest_inbound_at: int
    latest_inbound_sender_id: str
    latest_inbound_sender_name: str
    latest_inbound_text: str
    latest_inbound_type: int
    latest_outgoing_at: int
    inbound_count_since_reply: int


@dataclass
class PersonChatMatch:
    chat_id: str
    chat_name: str
    match_kind: str
    matched_id: str
    matched_name: str
    last_message_at: int
    unread_count: int


@dataclass
class RecentChatActivity:
    chat_id: str
    chat_name: str
    unread_count: int
    message_count: int
    inbound_count: int
    outgoing_count: int
    media_count: int
    first_message_at: int
    last_message_at: int
    last_inbound_at: int
    last_outgoing_at: int
    latest_message_id: int
    latest_sender_id: str
    latest_sender_name: str
    latest_text: str
    latest_type: int
    needs_reply: bool


@dataclass
class Media:
    message_id: int
    chat_id: str
    chat_name: str
    sender_id: str
    sender_name: str
    created_at: int
    media_type: int
    image_flag: int
    local_uri: str
    download_url: str
    preview_url: str
    is_public: bool
    width: int
    height: int
    size: int
    oid: str           # LINE OBS object ID (present for E2EE images)
    e2ee: bool         # True when content is E2EE encrypted (no plain download_url)
    server_id: str     # server-side message ID (needed for X-Talk-Meta CDN auth)
    km_b64: str        # base64 KM (plaintext, cached by LINE post-decrypt) — empty for non-E2EE
    sid: str           # CDN storage shard ID (e.g. "emi") — needed for CDN URL construction


def _parse_parameter_blob(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    parts = blob.split("\t")
    out: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        key = parts[i].strip()
        if key:
            out[key] = parts[i + 1]
    return out


def _guess_ext(url: str, media_type: int, image_flag: int) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    if image_flag:
        return ".jpg"
    return {
        1: ".bin",
        2: ".mp4",
        3: ".m4a",
        14: ".bin",
    }.get(media_type, ".bin")


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)
    return cleaned.strip("._") or "media"


def _media_ext_from_bytes(data: bytes, fallback: str = ".bin") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".mp4"
    return fallback


def _cache_dirs() -> list[str]:
    return [
        "/data/data/jp.naver.line.android/cache/image_manager_disk_cache",
        "/data/data/jp.naver.line.android/cache/coil3_disk_cache",
    ]


def _run_waydroid_shell(args: list[str], *, timeout: int = 30, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sudo", "waydroid", "shell", "--", *args],
        capture_output=True,
        text=text,
        timeout=timeout,
    )


def _list_cache_files(min_bytes: int = 50_000) -> list[dict[str, int | str]]:
    dirs = " ".join(_cache_dirs())
    script = (
        f"for d in {dirs}; do "
        "[ -d \"$d\" ] || continue; "
        "find \"$d\" -type f 2>/dev/null | while IFS= read -r f; do "
        "size=$(stat -c %s \"$f\" 2>/dev/null || echo 0); "
        "mtime=$(stat -c %Y \"$f\" 2>/dev/null || echo 0); "
        f"[ \"$size\" -ge {int(min_bytes)} ] && printf '%s\\t%s\\t%s\\n' \"$f\" \"$size\" \"$mtime\"; "
        "done; "
        "done"
    )
    result = _run_waydroid_shell(["sh", "-c", script])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    files: list[dict[str, int | str]] = []
    for line in result.stdout.splitlines():
        parts = line.rsplit("\t", 2)
        if len(parts) != 3:
            continue
        path, size, mtime = parts
        if path.startswith(tuple(_cache_dirs())) and size.isdigit() and mtime.isdigit():
            files.append({"file": path, "size": int(size), "mtime": int(mtime)})
    files.sort(key=lambda item: int(item["mtime"]), reverse=True)
    return files


def _read_container_file(path: str, *, timeout: int = 30) -> bytes:
    if not path.startswith(tuple(_cache_dirs())) or "/../" in path or path.endswith("/.."):
        raise ValueError(f"refusing to read non-LINE-cache path: {path}")
    result = _run_waydroid_shell(["cat", path], timeout=timeout, text=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
        raise RuntimeError(stderr.strip())
    return result.stdout


def _save_cache_files(
    cache_files: list[dict[str, int | str]],
    destination_dir: str,
    *,
    prefix: str = "line-cache",
    limit: int = 20,
) -> list[dict[str, int | str]]:
    dest_dir = Path(destination_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, int | str]] = []
    used_names: set[str] = set()

    for item in cache_files[: max(0, int(limit))]:
        container_path = str(item["file"])
        data = _read_container_file(container_path)
        ext = _media_ext_from_bytes(data)
        base = _safe_name(Path(container_path).name)[:80] or "cache"
        filename = f"{_safe_name(prefix)}-{int(item['mtime'])}-{base}{ext}"
        if filename in used_names:
            filename = f"{len(used_names)}-{filename}"
        used_names.add(filename)
        dest_path = dest_dir / filename
        with open(dest_path, "wb") as f:
            f.write(data)
        saved.append({
            "container_file": container_path,
            "path": str(dest_path),
            "bytes": dest_path.stat().st_size,
            "mtime": int(item["mtime"]),
            "mime_type": mimetypes.guess_type(dest_path.name)[0] or "application/octet-stream",
        })
    return saved


def _media_rows(where_sql: str, limit: int) -> list[dict]:
    return _q(f"""
        SELECT
            h.id                                         AS message_id,
            h.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            COALESCE(h.from_mid, '')                     AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            CAST(COALESCE(h.created_time, 0) AS INTEGER) AS created_at,
            CAST(COALESCE(attachement_type, 0) AS INTEGER) AS media_type,
            CAST(COALESCE(attachement_image, 0) AS INTEGER) AS image_flag,
            COALESCE(h.attachement_local_uri, '')        AS local_uri,
            CAST(COALESCE(h.attachement_image_width, 0) AS INTEGER)  AS width,
            CAST(COALESCE(h.attachement_image_height, 0) AS INTEGER) AS height,
            CAST(COALESCE(h.attachement_image_size, 0) AS INTEGER)   AS size,
            COALESCE(CAST(h.server_id AS TEXT), '')      AS server_id,
            COALESCE(h.parameter, '')                    AS parameter
        FROM chat_history h
        LEFT JOIN chat c          ON c.chat_id = h.chat_id
        LEFT JOIN groups g        ON g.id = h.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = h.chat_id
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE (
            COALESCE(h.attachement_type, 0) != 0
            OR COALESCE(h.attachement_local_uri, '') != ''
            OR COALESCE(h.attachement_image, 0) != 0
        )
        {where_sql}
        ORDER BY h.created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)


def _row_to_media(row: dict) -> Media:
    params = _parse_parameter_blob(row.pop("parameter", ""))
    oid = params.get("OID", "")
    download_url = params.get("DOWNLOAD_URL", "")
    preview_url = params.get("PREVIEW_URL", "")
    km_b64 = params.get("ENC_KM", "")
    e2ee = bool(km_b64 or params.get("e2eeVersion"))
    return Media(
        **row,
        download_url=download_url,
        preview_url=preview_url,
        is_public=params.get("PUBLIC", "").lower() == "true",
        oid=oid,
        e2ee=e2ee,
        km_b64=km_b64,
        sid=params.get("SID", "emi"),
    )


def list_chats(limit: int = 50) -> list[Chat]:
    unread_count = _unread_count_sql("c")
    rows = _q(f"""
        SELECT
            c.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS name,
            COALESCE(c.last_message, '')                                             AS last_message,
            CAST(COALESCE(c.last_created_time, 0) AS INTEGER)                       AS last_message_at,
            {unread_count}                                                           AS unread_count
        FROM chat c
        LEFT JOIN groups g        ON g.id    = c.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = c.chat_id
        ORDER BY c.last_created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [Chat(**r) for r in rows]


def list_unread_chats(limit: int = 50) -> list[Chat]:
    unread_count = _unread_count_sql("c")
    rows = _q(f"""
        SELECT
            c.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS name,
            COALESCE(c.last_message, '')                                             AS last_message,
            CAST(COALESCE(c.last_created_time, 0) AS INTEGER)                       AS last_message_at,
            {unread_count}                                                           AS unread_count
        FROM chat c
        LEFT JOIN groups g         ON g.id = c.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = c.chat_id
        WHERE {unread_count} > 0
        ORDER BY c.last_created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [Chat(**r) for r in rows]


def get_chat(chat_id: str) -> Chat | None:
    unread_count = _unread_count_sql("c")
    rows = _q(f"""
        SELECT
            c.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS name,
            COALESCE(c.last_message, '')                                             AS last_message,
            CAST(COALESCE(c.last_created_time, 0) AS INTEGER)                       AS last_message_at,
            {unread_count}                                                           AS unread_count
        FROM chat c
        LEFT JOIN groups g         ON g.id = c.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = c.chat_id
        WHERE c.chat_id = '{_s(chat_id)}'
        LIMIT 1
    """, attach_contact=True)
    return Chat(**rows[0]) if rows else None


def get_messages(chat_id: str, limit: int = 50) -> list[Message]:
    rows = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE h.chat_id = '{_s(chat_id)}'
        ORDER BY h.created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [Message(**r) for r in rows]


def list_latest_inbound_messages(limit: int = 10) -> list[InboundMessage]:
    """List newest messages received from other LINE users, excluding your outgoing messages."""
    rows = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN chat c          ON c.chat_id = h.chat_id
        LEFT JOIN groups g        ON g.id = h.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = h.chat_id
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE COALESCE(h.from_mid, '') != ''
        ORDER BY h.created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [InboundMessage(**r) for r in rows]


def list_reply_candidates(limit: int = 25) -> list[ReplyCandidate]:
    """List chats where the newest inbound message is newer than your newest outgoing reply."""
    unread_count = _unread_count_sql("c")
    rows = _q(f"""
        WITH latest_inbound AS (
            SELECT h.*
            FROM chat_history h
            JOIN (
                SELECT chat_id, MAX(CAST(COALESCE(created_time, 0) AS INTEGER)) AS latest_inbound_at
                FROM chat_history
                WHERE COALESCE(from_mid, '') != ''
                GROUP BY chat_id
            ) li ON li.chat_id = h.chat_id
                AND CAST(COALESCE(h.created_time, 0) AS INTEGER) = li.latest_inbound_at
            WHERE COALESCE(h.from_mid, '') != ''
            GROUP BY h.chat_id
        ),
        latest_outgoing AS (
            SELECT
                chat_id,
                MAX(CAST(COALESCE(created_time, 0) AS INTEGER)) AS latest_outgoing_at
            FROM chat_history
            WHERE COALESCE(from_mid, '') = ''
            GROUP BY chat_id
        )
        SELECT
            li.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            {unread_count} AS unread_count,
            li.id AS latest_inbound_message_id,
            CAST(COALESCE(li.created_time, 0) AS INTEGER) AS latest_inbound_at,
            COALESCE(li.from_mid, '') AS latest_inbound_sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, li.from_mid, '') AS latest_inbound_sender_name,
            COALESCE(li.content, '') AS latest_inbound_text,
            li.type AS latest_inbound_type,
            CAST(COALESCE(lo.latest_outgoing_at, 0) AS INTEGER) AS latest_outgoing_at,
            (
                SELECT COUNT(*)
                FROM chat_history h2
                WHERE h2.chat_id = li.chat_id
                  AND COALESCE(h2.from_mid, '') != ''
                  AND CAST(COALESCE(h2.created_time, 0) AS INTEGER) > CAST(COALESCE(lo.latest_outgoing_at, 0) AS INTEGER)
            ) AS inbound_count_since_reply
        FROM latest_inbound li
        LEFT JOIN latest_outgoing lo ON lo.chat_id = li.chat_id
        LEFT JOIN chat c             ON c.chat_id = li.chat_id
        LEFT JOIN groups g           ON g.id = li.chat_id
        LEFT JOIN cdb.contacts con   ON con.mid = li.chat_id
        LEFT JOIN cdb.contacts scon  ON scon.mid = li.from_mid
        WHERE CAST(COALESCE(li.created_time, 0) AS INTEGER) > CAST(COALESCE(lo.latest_outgoing_at, 0) AS INTEGER)
        ORDER BY li.created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [ReplyCandidate(**r) for r in rows]


def get_message_context(message_id: int, before: int = 10, after: int = 5) -> dict[str, object]:
    target_rows = _q(f"""
        SELECT
            h.chat_id,
            CAST(COALESCE(h.created_time, 0) AS INTEGER) AS created_at
        FROM chat_history h
        WHERE h.id = {int(message_id)}
        LIMIT 1
    """)
    if not target_rows:
        raise ValueError(f"message_id not found: {message_id}")

    target = target_rows[0]
    chat_id = target["chat_id"]
    created_at = int(target["created_at"])
    before_limit = max(0, int(before))
    after_limit = max(0, int(after))

    chat = get_chat(chat_id)
    prior_rows = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE h.chat_id = '{_s(chat_id)}'
          AND CAST(COALESCE(h.created_time, 0) AS INTEGER) < {created_at}
        ORDER BY h.created_time DESC
        LIMIT {before_limit}
    """, attach_contact=True)
    target_message = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE h.id = {int(message_id)}
        LIMIT 1
    """, attach_contact=True)[0]
    after_rows = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE h.chat_id = '{_s(chat_id)}'
          AND CAST(COALESCE(h.created_time, 0) AS INTEGER) > {created_at}
        ORDER BY h.created_time ASC
        LIMIT {after_limit}
    """, attach_contact=True)

    messages = [Message(**r) for r in reversed(prior_rows)]
    messages.append(Message(**target_message))
    messages.extend(Message(**r) for r in after_rows)
    return {
        "chat": chat,
        "target_message_id": int(message_id),
        "messages": messages,
    }


def find_person(query: str, limit: int = 20, message_limit: int = 20) -> dict[str, object]:
    """
    Search LINE contacts, chat names, group names, and sender names.

    This is for requests like "find messages from Tew", where the person may
    appear as a group participant rather than as a direct chat title.
    """
    q = _s(query.strip().lower())
    if not q:
        return {"query": query, "chat_matches": [], "recent_messages": []}
    unread_count = _unread_count_sql("c")

    chat_rows = _q(f"""
        SELECT
            c.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            CASE
                WHEN lower(COALESCE(g.name, '')) LIKE '%{q}%' THEN 'group_name'
                WHEN lower(COALESCE(con.overridden_name, '')) LIKE '%{q}%'
                  OR lower(COALESCE(con.profile_name, '')) LIKE '%{q}%'
                  OR lower(COALESCE(c.chat_name, '')) LIKE '%{q}%' THEN 'chat_name'
                ELSE 'unknown'
            END AS match_kind,
            c.chat_id AS matched_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS matched_name,
            CAST(COALESCE(c.last_created_time, 0) AS INTEGER) AS last_message_at,
            {unread_count} AS unread_count
        FROM chat c
        LEFT JOIN groups g         ON g.id = c.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = c.chat_id
        WHERE lower(COALESCE(g.name, '') || ' ' || COALESCE(con.overridden_name, '') || ' ' ||
                    COALESCE(con.profile_name, '') || ' ' || COALESCE(c.chat_name, '')) LIKE '%{q}%'
        ORDER BY c.last_created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)

    sender_rows = _q(f"""
        SELECT
            h.id AS message_id,
            h.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            COALESCE(h.from_mid, '') AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '') AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER) AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN chat c            ON c.chat_id = h.chat_id
        LEFT JOIN groups g          ON g.id = h.chat_id
        LEFT JOIN cdb.contacts con  ON con.mid = h.chat_id
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE COALESCE(h.from_mid, '') != ''
          AND lower(COALESCE(scon.overridden_name, '') || ' ' ||
                    COALESCE(scon.profile_name, '') || ' ' || COALESCE(h.from_mid, '')) LIKE '%{q}%'
        ORDER BY h.created_time DESC
        LIMIT {int(message_limit)}
    """, attach_contact=True)

    sender_chat_rows = _q(f"""
        SELECT
            h.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            'sender_name' AS match_kind,
            COALESCE(h.from_mid, '') AS matched_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS matched_name,
            MAX(CAST(COALESCE(h.created_time, 0) AS INTEGER)) AS last_message_at,
            {unread_count} AS unread_count
        FROM chat_history h
        LEFT JOIN chat c            ON c.chat_id = h.chat_id
        LEFT JOIN groups g          ON g.id = h.chat_id
        LEFT JOIN cdb.contacts con  ON con.mid = h.chat_id
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE COALESCE(h.from_mid, '') != ''
          AND lower(COALESCE(scon.overridden_name, '') || ' ' ||
                    COALESCE(scon.profile_name, '') || ' ' || COALESCE(h.from_mid, '')) LIKE '%{q}%'
        GROUP BY h.chat_id, h.from_mid
        ORDER BY last_message_at DESC
        LIMIT {int(limit)}
    """, attach_contact=True)

    seen = set()
    chat_matches = []
    for row in chat_rows + sender_chat_rows:
        key = (row["chat_id"], row["match_kind"], row["matched_id"])
        if key in seen:
            continue
        seen.add(key)
        chat_matches.append(PersonChatMatch(**row))

    return {
        "query": query,
        "chat_matches": chat_matches,
        "recent_messages": [InboundMessage(**row) for row in sender_rows],
    }


def summarize_recent_activity(
    hours: int = 24,
    chat_limit: int = 30,
    messages_per_chat: int = 12,
) -> dict[str, object]:
    """Return structured LINE activity for a recent time window."""
    safe_hours = max(1, min(int(hours), 168))
    since_ms = int((time.time() - safe_hours * 3600) * 1000)
    chat_limit = max(1, min(int(chat_limit), 100))
    messages_per_chat = max(1, min(int(messages_per_chat), 50))
    unread_count = _unread_count_sql("c")

    activity_rows = _q(f"""
        WITH recent AS (
            SELECT *
            FROM chat_history
            WHERE CAST(COALESCE(created_time, 0) AS INTEGER) >= {since_ms}
        ),
        latest AS (
            SELECT r.*
            FROM recent r
            JOIN (
                SELECT chat_id, MAX(CAST(COALESCE(created_time, 0) AS INTEGER)) AS latest_at
                FROM recent
                GROUP BY chat_id
            ) lm ON lm.chat_id = r.chat_id
                AND CAST(COALESCE(r.created_time, 0) AS INTEGER) = lm.latest_at
            GROUP BY r.chat_id
        ),
        latest_inbound AS (
            SELECT
                chat_id,
                MAX(CAST(COALESCE(created_time, 0) AS INTEGER)) AS last_inbound_at
            FROM chat_history
            WHERE COALESCE(from_mid, '') != ''
            GROUP BY chat_id
        ),
        latest_outgoing AS (
            SELECT
                chat_id,
                MAX(CAST(COALESCE(created_time, 0) AS INTEGER)) AS last_outgoing_at
            FROM chat_history
            WHERE COALESCE(from_mid, '') = ''
            GROUP BY chat_id
        )
        SELECT
            r.chat_id,
            COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS chat_name,
            {unread_count} AS unread_count,
            COUNT(*) AS message_count,
            SUM(CASE WHEN COALESCE(r.from_mid, '') != '' THEN 1 ELSE 0 END) AS inbound_count,
            SUM(CASE WHEN COALESCE(r.from_mid, '') = '' THEN 1 ELSE 0 END) AS outgoing_count,
            SUM(CASE WHEN COALESCE(r.attachement_type, 0) != 0
                       OR COALESCE(r.attachement_local_uri, '') != ''
                       OR COALESCE(r.attachement_image, 0) != 0 THEN 1 ELSE 0 END) AS media_count,
            MIN(CAST(COALESCE(r.created_time, 0) AS INTEGER)) AS first_message_at,
            MAX(CAST(COALESCE(r.created_time, 0) AS INTEGER)) AS last_message_at,
            CAST(COALESCE(li.last_inbound_at, 0) AS INTEGER) AS last_inbound_at,
            CAST(COALESCE(lo.last_outgoing_at, 0) AS INTEGER) AS last_outgoing_at,
            l.id AS latest_message_id,
            COALESCE(l.from_mid, '') AS latest_sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, l.from_mid, '') AS latest_sender_name,
            COALESCE(l.content, '') AS latest_text,
            l.type AS latest_type,
            CASE
                WHEN CAST(COALESCE(li.last_inbound_at, 0) AS INTEGER) > CAST(COALESCE(lo.last_outgoing_at, 0) AS INTEGER)
                THEN 1 ELSE 0
            END AS needs_reply
        FROM recent r
        JOIN latest l             ON l.chat_id = r.chat_id
        LEFT JOIN latest_inbound li ON li.chat_id = r.chat_id
        LEFT JOIN latest_outgoing lo ON lo.chat_id = r.chat_id
        LEFT JOIN chat c          ON c.chat_id = r.chat_id
        LEFT JOIN groups g        ON g.id = r.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = r.chat_id
        LEFT JOIN cdb.contacts scon ON scon.mid = l.from_mid
        GROUP BY r.chat_id
        ORDER BY last_message_at DESC
        LIMIT {chat_limit}
    """, attach_contact=True)

    activities = [
        RecentChatActivity(
            **{
                **row,
                "needs_reply": bool(row["needs_reply"]),
            }
        )
        for row in activity_rows
    ]

    messages_by_chat: dict[str, list[Message]] = {}
    for activity in activities:
        rows = _q(f"""
            SELECT
                h.id                                              AS message_id,
                h.chat_id,
                COALESCE(h.from_mid, '')                          AS sender_id,
                COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
                COALESCE(h.content, '')                           AS text,
                CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
                h.type
            FROM chat_history h
            LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
            WHERE h.chat_id = '{_s(activity.chat_id)}'
              AND CAST(COALESCE(h.created_time, 0) AS INTEGER) >= {since_ms}
            ORDER BY h.created_time DESC
            LIMIT {messages_per_chat}
        """, attach_contact=True)
        messages_by_chat[activity.chat_id] = [Message(**row) for row in reversed(rows)]

    return {
        "since_ms": since_ms,
        "hours": safe_hours,
        "chat_count": len(activities),
        "activities": activities,
        "messages_by_chat": messages_by_chat,
    }


def search_messages(query: str, limit: int = 20) -> list[Message]:
    rows = _q(f"""
        SELECT
            h.id                                              AS message_id,
            h.chat_id,
            COALESCE(h.from_mid, '')                          AS sender_id,
            COALESCE(scon.overridden_name, scon.profile_name, h.from_mid, '') AS sender_name,
            COALESCE(h.content, '')                           AS text,
            CAST(COALESCE(h.created_time, 0) AS INTEGER)      AS created_at,
            h.type
        FROM chat_history h
        LEFT JOIN cdb.contacts scon ON scon.mid = h.from_mid
        WHERE h.content LIKE '%{_s(query)}%'
        ORDER BY h.created_time DESC
        LIMIT {int(limit)}
    """, attach_contact=True)
    return [Message(**r) for r in rows]


def list_media(chat_id: str | None = None, limit: int = 20) -> list[Media]:
    where = ""
    if chat_id:
        where = f"AND h.chat_id = '{_s(chat_id)}'"
    rows = _media_rows(where, limit)
    return [_row_to_media(row) for row in rows]


def get_media_info(message_id: int) -> Media:
    rows = _media_rows(f"AND h.id = {int(message_id)}", 1)
    if not rows:
        raise ValueError(f"message_id not found: {message_id}")
    return _row_to_media(rows[0])


def get_message_raw(message_id: int) -> dict:
    """
    Return the full parameter dict for any message, plus core message fields.

    Useful for Flex messages (type 22) and markup messages (type 17) where
    the content — account balance, transaction data, rich cards — lives in
    FLEX_JSON or MARKUP_JSON inside the parameter blob, not in any download URL.

    The returned dict always contains:
      message_id, chat_id, sender_id, type, created_at  — core fields
      params  — full key/value dict parsed from the tab-delimited parameter blob
                (FLEX_JSON and MARKUP_JSON values are decoded to nested dicts)
    """
    rows = _q(
        f"SELECT id, chat_id, COALESCE(from_mid,'') AS sender_id, "
        f"CAST(COALESCE(type,0) AS INTEGER) AS type, "
        f"CAST(COALESCE(created_time,0) AS INTEGER) AS created_at, "
        f"COALESCE(parameter,'') AS parameter "
        f"FROM chat_history WHERE id={int(message_id)} LIMIT 1"
    )
    if not rows:
        raise ValueError(f"message_id not found: {message_id}")
    row = rows[0]
    params = _parse_parameter_blob(row["parameter"])
    # Decode any JSON-valued fields so callers get structured data, not raw strings
    for key in list(params.keys()):
        if key.upper().endswith("_JSON"):
            try:
                params[key] = json.loads(params[key])
            except Exception:
                pass
    return {
        "message_id": row["id"],
        "chat_id": row["chat_id"],
        "sender_id": row["sender_id"],
        "type": row["type"],
        "created_at": row["created_at"],
        "params": params,
    }


def download_media(
    message_id: int,
    destination_dir: str = ".",
    prefer_preview: bool = False,
) -> dict[str, str | int | bool]:
    media = get_media_info(message_id)
    if media.e2ee:
        if not media.oid or not media.km_b64 or not media.server_id:
            raise ValueError(
                f"message_id {message_id}: E2EE but missing OID/KM/server_id in DB"
            )
        blob = _download_blob(media.server_id, media.sid, media.oid)
        if blob is None:
            raise ValueError(
                f"message_id {message_id}: E2EE CDN download failed — "
                "refresh token with: python3 tools/refresh_token.py"
            )
        plaintext = _decrypt_blob(blob, media.km_b64)
        dest_dir = Path(destination_dir).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = _media_ext_from_bytes(plaintext, ".jpg")
        dest_path = dest_dir / f"{_safe_name(media.chat_id)}-{message_id}{ext}"
        dest_path.write_bytes(plaintext)
        return {
            "message_id": media.message_id,
            "chat_id": media.chat_id,
            "downloaded": True,
            "decrypted": True,
            "path": str(dest_path),
            "bytes": len(plaintext),
            "mime_type": mimetypes.guess_type(dest_path.name)[0] or "image/jpeg",
        }

    if not media.download_url and not media.preview_url:
        raise ValueError(f"message_id {message_id} has no downloadable media URL")

    url = media.preview_url if prefer_preview and media.preview_url else media.download_url or media.preview_url
    dest_dir = Path(destination_dir).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = _guess_ext(url, media.media_type, media.image_flag)
    filename = f"{_safe_name(media.chat_id)}-{message_id}{ext}"
    dest_path = dest_dir / filename

    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp, open(dest_path, "wb") as f:
        data = resp.read()
        f.write(data)
        content_type = resp.headers.get_content_type()

    if dest_path.suffix == ".bin" and content_type:
        guessed_ext = mimetypes.guess_extension(content_type) or ""
        if guessed_ext:
            renamed = dest_path.with_suffix(guessed_ext)
            dest_path.rename(renamed)
            dest_path = renamed

    return {
        "message_id": media.message_id,
        "chat_id": media.chat_id,
        "downloaded": True,
        "path": str(dest_path),
        "source_url": url,
        "is_public": media.is_public,
        "used_preview": bool(prefer_preview and media.preview_url),
        "media_type": media.media_type,
        "bytes": dest_path.stat().st_size,
        "mime_type": mimetypes.guess_type(dest_path.name)[0] or "application/octet-stream",
    }


def _is_image_media(media: Media) -> bool:
    return bool(media.image_flag) or media.media_type == 1


def extract_cached_media(
    destination_dir: str = "~/Downloads/line-media",
    min_bytes: int = 50_000,
    limit: int = 20,
) -> dict[str, object]:
    """
    Copy already-rendered LINE cache files from Waydroid to the host.

    This does not decrypt or download media. It only exports files that LINE
    has already written to its normal image cache after the user/app viewed
    them.
    """
    cache_files = _list_cache_files(min_bytes=min_bytes)
    saved = _save_cache_files(
        cache_files,
        destination_dir,
        prefix="line-cache",
        limit=limit,
    )
    return {
        "count": len(saved),
        "saved_files": saved,
        "cache_candidates": len(cache_files),
    }


def pull_message_image(
    message_id: int,
    destination_dir: str = "~/Downloads/line-media",
    prefer_preview: bool = False,
    wait_seconds: int = 8,
    min_bytes: int = 50_000,
) -> dict[str, object]:
    """
    Save an image for one LINE media message for agent consumption.

    Non-E2EE images are downloaded by their stored URL. E2EE/cache-only images
    use LINE's own UI render path: open the chat, wait, then export newly
    written cache files.
    """
    media = get_media_info(message_id)
    if not _is_image_media(media):
        raise ValueError(f"message_id {message_id} is not an image media message")

    # Try direct download (handles both plain and E2EE via CDN+decrypt)
    if media.e2ee or (media.download_url or media.preview_url):
        try:
            downloaded = download_media(
                message_id=message_id,
                destination_dir=destination_dir,
                prefer_preview=prefer_preview,
            )
            return {
                "message_id": message_id,
                "chat_id": media.chat_id,
                "mode": "decrypt" if media.e2ee else "download",
                "downloaded_files": [downloaded],
                "cache_files": [],
                "count": 1,
            }
        except Exception:
            pass  # fall through to UI cache path

    cached = open_chat_and_cache(
        chat_id=media.chat_id,
        wait_seconds=wait_seconds,
        destination_dir=destination_dir,
        min_bytes=min_bytes,
    )
    return {
        "message_id": message_id,
        "chat_id": media.chat_id,
        "mode": "cache",
        "downloaded_files": [],
        "cache_files": cached["saved_files"],
        "new_cached_files": cached["new_cached_files"],
        "count": cached["count"],
    }


def pull_chat_images(
    chat_id: str,
    destination_dir: str = "~/Downloads/line-media",
    limit: int = 20,
    prefer_preview: bool = False,
    wait_seconds: int = 8,
    min_bytes: int = 50_000,
) -> dict[str, object]:
    """
    Save recent images from one LINE chat for agent consumption.

    Directly downloadable images are saved individually. If the chat contains
    E2EE/cache-only images, LINE is opened once and any newly rendered cache
    files are exported.
    """
    media_items = [media for media in list_media(chat_id=chat_id, limit=limit) if _is_image_media(media)]
    downloaded: list[dict[str, object]] = []
    download_errors: list[dict[str, object]] = []
    needs_cache = False

    for media in media_items:
        try:
            downloaded.append(download_media(
                message_id=media.message_id,
                destination_dir=destination_dir,
                prefer_preview=prefer_preview,
            ))
        except Exception as exc:
            needs_cache = True
            download_errors.append({
                "message_id": media.message_id,
                "error": str(exc),
            })

    cache_result: dict[str, object] = {
        "count": 0,
        "saved_files": [],
        "new_cached_files": [],
    }
    if needs_cache:
        cache_result = open_chat_and_cache(
            chat_id=chat_id,
            wait_seconds=wait_seconds,
            destination_dir=destination_dir,
            min_bytes=min_bytes,
        )

    return {
        "chat_id": chat_id,
        "media_count": len(media_items),
        "downloaded_count": len(downloaded),
        "cache_count": int(cache_result["count"]),
        "downloaded_files": downloaded,
        "cache_files": cache_result["saved_files"],
        "new_cached_files": cache_result["new_cached_files"],
        "download_errors": download_errors,
    }


def _tap_image_in_chat() -> list[tuple[int, int]]:
    """
    Dump the Waydroid UI hierarchy and tap any ImageView elements found
    in the chat area. Returns list of (x, y) coords tapped.

    Falls back to a grid sweep of the lower-center screen area if
    uiautomator dump fails or finds nothing.
    """
    import re as _re
    import xml.etree.ElementTree as _ET

    tapped: list[tuple[int, int]] = []

    # Dump UI hierarchy
    subprocess.run(
        ["sudo", "waydroid", "shell", "--",
         "uiautomator", "dump", "/data/local/tmp/ui.xml"],
        capture_output=True, timeout=10,
    )
    r = subprocess.run(
        ["sudo", "waydroid", "shell", "--", "cat", "/data/local/tmp/ui.xml"],
        capture_output=True, text=True, timeout=10,
    )

    coords: list[tuple[int, int]] = []
    if r.returncode == 0 and r.stdout.strip():
        try:
            root = _ET.fromstring(r.stdout.strip())
            for node in root.iter("node"):
                cls = node.get("class", "")
                bounds = node.get("bounds", "")
                # Target: ImageView elements with reasonable size (likely chat photos)
                if "ImageView" not in cls:
                    continue
                m = _re.findall(r"\d+", bounds)
                if len(m) != 4:
                    continue
                x1, y1, x2, y2 = int(m[0]), int(m[1]), int(m[2]), int(m[3])
                w, h = x2 - x1, y2 - y1
                # Filter: at least 80×80 px and not the full screen (app chrome)
                if w < 80 or h < 80 or w > 900 or h > 600:
                    continue
                coords.append(((x1 + x2) // 2, (y1 + y2) // 2))
        except Exception:
            pass

    # Fallback: sweep the lower-center area where chat messages appear
    if not coords:
        # Screen 1280×688; LINE chat area is roughly x=100-500, y=200-620
        coords = [(250, 500), (250, 380), (250, 260), (640, 440)]

    for x, y in coords:
        subprocess.run(
            ["sudo", "waydroid", "shell", "--", "input", "tap", str(x), str(y)],
            capture_output=True, timeout=8,
        )
        tapped.append((x, y))
        time.sleep(0.8)

    return tapped


def open_chat_and_cache(
    chat_id: str,
    wait_seconds: int = 12,
    destination_dir: str = "~/Downloads/line-media",
    min_bytes: int = 50_000,
) -> dict[str, object]:
    """
    Open a LINE chat, tap image messages to trigger E2EE download, then
    export newly cached files to the host.

    Requires LINE UI to be visible (waydroid show-full-ui must be running).
    """
    before = {str(item["file"]) for item in _list_cache_files(min_bytes=min_bytes)}

    # Open chat
    subprocess.run(
        ["sudo", "waydroid", "shell", "--",
         "am", "start", "-n", "jp.naver.line.android/.activity.ChatActivity",
         "--es", "chatId", chat_id],
        capture_output=True,
    )
    time.sleep(3)

    # Tap image elements to trigger download
    tapped = _tap_image_in_chat()
    time.sleep(wait_seconds)

    after = _list_cache_files(min_bytes=min_bytes)
    new_files = [item for item in after if str(item["file"]) not in before]
    saved = _save_cache_files(
        new_files,
        destination_dir,
        prefix=f"{_safe_name(chat_id)}",
        limit=len(new_files),
    )

    return {
        "chat_id": chat_id,
        "tapped_coords": tapped,
        "new_cached_files": new_files,
        "saved_files": saved,
        "count": len(saved),
    }


if __name__ == "__main__":
    print("=== Chats (limit 10) ===")
    chats = list_chats(10)
    for c in chats:
        print(f"  {c.chat_id[:16]}  {c.name or '(no name)':30s}  last={c.last_message_at}  unread={c.unread_count}")

    if chats:
        print(f"\n=== Messages from most recent chat: {chats[0].chat_id[:16]} ===")
        msgs = get_messages(chats[0].chat_id, 5)
        for m in msgs:
            print(f"  [{m.type}] {m.sender_name[:24]:24s}  {m.text[:80]!r}")

    print("\n=== Media (limit 5) ===")
    for media in list_media(limit=5):
        print(
            f"  msg={media.message_id} chat={media.chat_name[:20]:20s} "
            f"sender={media.sender_name[:20]:20s} "
            f"type={media.media_type} public={media.is_public} "
            f"url={'yes' if media.download_url else 'no'}"
        )
