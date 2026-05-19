#!/usr/bin/env python3
"""LINE MCP stdio server.

Exposes read-only tools over the Waydroid LINE SQLite databases.
"""

from __future__ import annotations

import json
import functools
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server import FastMCP


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"

import sys

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from line_db import download_media, extract_cached_media, find_person, get_chat, get_media_info, get_message_context, get_message_raw, get_messages, list_chats, list_latest_inbound_messages, list_media, list_reply_candidates, list_unread_chats, open_chat_and_cache, pull_chat_images, pull_message_image, save_auth_token, search_messages, summarize_recent_activity  # noqa: E402


server = FastMCP(
    name="line-mcp",
    instructions="Read-only MCP server for LINE chats stored in a Waydroid container.",
    host="0.0.0.0",
    port=8765,
)

_line_call_gate = threading.BoundedSemaphore(
    max(1, int(os.environ.get("LINE_MCP_MAX_CONCURRENCY", "2")))
)


def _serialized_line_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _line_call_gate:
            return func(*args, **kwargs)

    return wrapper


def _limit(value: int, *, default: int, maximum: int) -> int:
    if value <= 0:
        return default
    return min(value, maximum)


def _to_dicts(items: list[Any]) -> list[dict[str, Any]]:
    rows = [asdict(item) for item in items]
    for row in rows:
        _annotate_direction(row)
    return rows


def _annotate_direction(row: dict[str, Any]) -> None:
    if "sender_id" in row:
        if row.get("sender_id"):
            row["direction"] = "inbound"
        else:
            row["direction"] = "outgoing"
            if not row.get("sender_name"):
                row["sender_name"] = "You"
    if "latest_sender_id" in row:
        if row.get("latest_sender_id"):
            row["latest_direction"] = "inbound"
        else:
            row["latest_direction"] = "outgoing"
            if not row.get("latest_sender_name"):
                row["latest_sender_name"] = "You"


@server.tool(name="list_chats")
@_serialized_line_call
def list_chats_tool(limit: int = 50) -> list[dict[str, Any]]:
    """List recent chats ordered by last activity."""
    return _to_dicts(list_chats(limit=_limit(limit, default=50, maximum=200)))


@server.tool(name="list_unread_chats")
@_serialized_line_call
def list_unread_chats_tool(limit: int = 50) -> list[dict[str, Any]]:
    """List chats with unread_count > 0, ordered by last activity."""
    return _to_dicts(list_unread_chats(limit=_limit(limit, default=50, maximum=200)))


@server.tool(name="get_messages")
@_serialized_line_call
def get_messages_tool(chat_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent messages for one chat_id."""
    return _to_dicts(get_messages(chat_id=chat_id, limit=_limit(limit, default=50, maximum=200)))


@server.tool(name="list_latest_inbound_messages")
@_serialized_line_call
def list_latest_inbound_messages_tool(limit: int = 10) -> list[dict[str, Any]]:
    """
    List newest messages received from other LINE users.

    Unlike list_chats, this excludes your outgoing replies by requiring
    chat_history.from_mid/sender_id to be non-empty.
    """
    return _to_dicts(list_latest_inbound_messages(limit=_limit(limit, default=10, maximum=100)))


@server.tool(name="get_latest_inbound_message")
@_serialized_line_call
def get_latest_inbound_message_tool() -> dict[str, Any] | None:
    """Get the single newest message received from another LINE user, or null if none are found."""
    messages = list_latest_inbound_messages(limit=1)
    return asdict(messages[0]) if messages else None


@server.tool(name="list_reply_candidates")
@_serialized_line_call
def list_reply_candidates_tool(limit: int = 25) -> list[dict[str, Any]]:
    """
    List chats where someone else's latest message is newer than your latest outgoing reply.

    Use this for "who do I owe replies to?" triage. Group chats may still need
    human judgment because not every later inbound group message requires your attention.
    """
    return _to_dicts(list_reply_candidates(limit=_limit(limit, default=25, maximum=100)))


@server.tool(name="get_message_context")
@_serialized_line_call
def get_message_context_tool(message_id: int, before: int = 10, after: int = 5) -> dict[str, Any]:
    """Return messages around a specific message_id from the same chat."""
    context = get_message_context(
        message_id=message_id,
        before=_limit(before, default=10, maximum=100),
        after=_limit(after, default=5, maximum=50),
    )
    chat = context["chat"]
    return {
        "chat": asdict(chat) if chat is not None else None,
        "target_message_id": context["target_message_id"],
        "messages": _to_dicts(context["messages"]),
    }


@server.tool(name="find_person")
@_serialized_line_call
def find_person_tool(query: str, limit: int = 20, message_limit: int = 20) -> dict[str, Any]:
    """
    Search for a LINE person by contact/chat/sender name.

    Use this before saying a person is not found. It handles people who appear
    as senders inside group chats, not just direct chat names.
    """
    result = find_person(
        query=query,
        limit=_limit(limit, default=20, maximum=100),
        message_limit=_limit(message_limit, default=20, maximum=100),
    )
    return {
        "query": result["query"],
        "chat_matches": _to_dicts(result["chat_matches"]),
        "recent_messages": _to_dicts(result["recent_messages"]),
    }


@server.tool(name="summarize_recent_activity")
@_serialized_line_call
def summarize_recent_activity_tool(
    hours: int = 24,
    chat_limit: int = 30,
    messages_per_chat: int = 24,
) -> dict[str, Any]:
    """
    Return structured LINE activity for the last N hours.

    Use this for requests like "summarize the last 24h". The tool returns
    grouped activity, representative messages, and needs_reply flags; the
    assistant should write the final human summary.
    """
    result = summarize_recent_activity(
        hours=_limit(hours, default=24, maximum=168),
        chat_limit=_limit(chat_limit, default=30, maximum=100),
        messages_per_chat=_limit(messages_per_chat, default=24, maximum=100),
    )
    activities = _to_dicts(result["activities"])
    return {
        "since_ms": result["since_ms"],
        "hours": result["hours"],
        "chat_count": result["chat_count"],
        "source_limits": {
            "hours": result["hours"],
            "chat_limit": _limit(chat_limit, default=30, maximum=100),
            "messages_per_chat": _limit(messages_per_chat, default=24, maximum=100),
        },
        "source_chat_names": [activity.get("chat_name", "") for activity in activities],
        "summary_rules": [
            "Only summarize chats and messages present in this tool result.",
            "Do not introduce chat names, people, topics, counts, unread counts, or media that are absent from activities/messages_by_chat.",
            "Treat direction='outgoing' and latest_direction='outgoing' as messages sent by the user, not by the chat/person name.",
            "Treat needs_reply as a raw database signal; group chats and official accounts may not require user action.",
        ],
        "activities": activities,
        "messages_by_chat": {
            chat_id: _to_dicts(messages)
            for chat_id, messages in result["messages_by_chat"].items()
        },
    }


@server.tool(name="get_chat_summary")
@_serialized_line_call
def get_chat_summary_tool(
    chat_id: str,
    message_limit: int = 20,
    media_limit: int = 10,
) -> dict[str, Any]:
    """Get one chat with recent messages and recent media in one response."""
    chat = get_chat(chat_id)
    if chat is None:
        raise ValueError(f"chat_id not found: {chat_id}")
    return {
        "chat": asdict(chat),
        "messages": _to_dicts(get_messages(chat_id=chat_id, limit=_limit(message_limit, default=20, maximum=100))),
        "media": _to_dicts(list_media(chat_id=chat_id, limit=_limit(media_limit, default=10, maximum=50))),
    }


@server.tool(name="search_messages")
@_serialized_line_call
def search_messages_tool(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search message text across chat history."""
    return _to_dicts(search_messages(query=query, limit=_limit(limit, default=20, maximum=100)))


@server.tool(name="list_media")
@_serialized_line_call
def list_media_tool(chat_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """List recent media messages, optionally filtered to one chat_id."""
    return _to_dicts(list_media(chat_id=chat_id or None, limit=_limit(limit, default=20, maximum=100)))


@server.tool(name="get_media_info")
@_serialized_line_call
def get_media_info_tool(message_id: int) -> dict[str, Any]:
    """Get metadata and URLs for one media message."""
    return asdict(get_media_info(message_id))


@server.tool(name="download_media")
@_serialized_line_call
def download_media_tool(
    message_id: int,
    destination_dir: str = "~/Downloads/line-media",
    prefer_preview: bool = False,
) -> dict[str, Any]:
    """Download one media attachment to disk by message_id."""
    return download_media(
        message_id=message_id,
        destination_dir=destination_dir,
        prefer_preview=prefer_preview,
    )


@server.tool(name="pull_message_image")
@_serialized_line_call
def pull_message_image_tool(
    message_id: int,
    destination_dir: str = "~/Downloads/line-media",
    prefer_preview: bool = False,
    wait_seconds: int = 8,
    min_bytes: int = 50_000,
) -> dict[str, Any]:
    """
    Save one LINE image message for agent use.

    Direct URL images are downloaded. E2EE/cache-only images open the owning
    chat in LINE and export newly rendered cache files.
    """
    return pull_message_image(
        message_id=message_id,
        destination_dir=destination_dir,
        prefer_preview=prefer_preview,
        wait_seconds=wait_seconds,
        min_bytes=min_bytes,
    )


@server.tool(name="pull_chat_images")
@_serialized_line_call
def pull_chat_images_tool(
    chat_id: str,
    destination_dir: str = "~/Downloads/line-media",
    limit: int = 20,
    prefer_preview: bool = False,
    wait_seconds: int = 8,
    min_bytes: int = 50_000,
) -> dict[str, Any]:
    """
    Save recent LINE images from one chat for agent use.

    Direct URL images are downloaded. If any image needs LINE's rendered cache,
    the chat is opened once and newly cached files are exported.
    """
    return pull_chat_images(
        chat_id=chat_id,
        destination_dir=destination_dir,
        limit=_limit(limit, default=20, maximum=100),
        prefer_preview=prefer_preview,
        wait_seconds=wait_seconds,
        min_bytes=min_bytes,
    )


@server.tool(name="open_chat_and_cache")
@_serialized_line_call
def open_chat_and_cache_tool(
    chat_id: str,
    wait_seconds: int = 8,
    destination_dir: str = "~/Downloads/line-media",
    min_bytes: int = 50_000,
) -> dict[str, object]:
    """
    Open a LINE chat via am-start deeplink, triggering LINE to decrypt and cache E2EE media.
    Exports newly cached files larger than min_bytes. Requires LINE UI to be visible (waydroid show-full-ui running).
    Use this when download_media fails with an E2EE error.
    """
    return open_chat_and_cache(
        chat_id=chat_id,
        wait_seconds=wait_seconds,
        destination_dir=destination_dir,
        min_bytes=min_bytes,
    )


@server.tool(name="extract_cached_media")
@_serialized_line_call
def extract_cached_media_tool(
    destination_dir: str = "~/Downloads/line-media",
    min_bytes: int = 50_000,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Export media files that LINE has already rendered into its normal Waydroid cache.

    This does not decrypt, hook, or download media; it copies cache files that
    already exist after LINE has displayed them.
    """
    return extract_cached_media(
        destination_dir=destination_dir,
        min_bytes=min_bytes,
        limit=_limit(limit, default=20, maximum=100),
    )


@server.tool(name="set_auth_token")
@_serialized_line_call
def set_auth_token_tool(x_line_access: str) -> dict[str, str]:
    """
    Store a fresh X-Line-Access token for E2EE CDN downloads.

    The token is session-scoped. Use refresh_cdn_token first — it reads the
    live token directly from LINE's SQLite database without manual capture.
    Only use this tool when refresh_cdn_token fails (e.g. LINE hasn't started yet).
    The token will be persisted to ~/.config/line-mcp/auth.json.
    """
    save_auth_token(x_line_access)
    return {"status": "saved", "length": str(len(x_line_access))}


@server.tool(name="refresh_cdn_token")
@_serialized_line_call
def refresh_cdn_token_tool() -> dict[str, Any]:
    """
    Read a fresh X-Line-Access CDN token directly from LINE's SQLite database
    and save it to ~/.config/line-mcp/auth.json.

    LINE stores the live session token in the naver_line database under
    setting.OBS_ENCRYPTED_ACCESS_TOKEN. No network interception needed.

    Returns {"status": "ok", "token_length": N} on success, or
            {"status": "error", "detail": "..."} on failure.
    """
    refresh_py = TOOLS_DIR / "refresh_token.py"
    result = subprocess.run(
        [sys.executable, str(refresh_py)],
        capture_output=True, text=True, timeout=30,
        cwd=str(TOOLS_DIR),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error")[-600:]
        return {"status": "error", "detail": detail}

    auth_file = Path.home() / ".config" / "line-mcp" / "auth.json"
    try:
        token = json.loads(auth_file.read_text()).get("x_line_access", "")
        return {"status": "ok", "token_length": len(token)}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@server.tool(name="get_message_raw")
@_serialized_line_call
def get_message_raw_tool(message_id: int) -> dict[str, Any]:
    """
    Return the raw parameter blob for one message, with any *_JSON fields auto-decoded.

    Useful for Flex messages (type 22) and Markup messages (type 17) where the
    meaningful content — account balance, action URLs, card layouts — is embedded
    in FLEX_JSON or MARKUP_JSON inside the parameter blob rather than in text or
    a downloaded image.

    Returns: message_id, chat_id, sender_id, type, created_at, params (dict).
    """
    return get_message_raw(message_id)


class _BearerAuthMiddleware:
    """Pure ASGI middleware — safe for SSE/streaming (BaseHTTPMiddleware breaks SSE)."""

    def __init__(self, app, api_key: str):
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode()
            qs = scope.get("query_string", b"").decode()
            qparams = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            header_ok = auth.startswith("Bearer ") and auth[7:] == self._api_key
            query_ok = qparams.get("key", "") == self._api_key
            if not (header_ok or query_ok):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main() -> None:
    api_key = os.environ.get("LINE_MCP_API_KEY", "")
    if not api_key:
        raise RuntimeError("LINE_MCP_API_KEY environment variable is not set")
    app = _BearerAuthMiddleware(server.streamable_http_app(), api_key=api_key)
    uvicorn.run(app, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
