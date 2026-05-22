from pathlib import Path
import sqlite3
import sys

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import refresh_token  # noqa: E402


def _make_db(path: Path, value):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE setting(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO setting(key, value) VALUES (?, ?)", (refresh_token.SETTING_KEY, value))
    conn.commit()
    conn.close()


def test_read_token_triggers_chat_open_when_db_value_is_null(tmp_path, monkeypatch):
    db_path = tmp_path / "naver_line"
    _make_db(db_path, None)
    monkeypatch.setattr(refresh_token, "_host_db", lambda: db_path)
    calls = []

    def fake_trigger(chat_id, wait_seconds):
        calls.append((chat_id, wait_seconds))
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE setting SET value=? WHERE key=?",
            ("abc=metadata", refresh_token.SETTING_KEY),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(refresh_token, "trigger_token_regeneration", fake_trigger)
    monkeypatch.setattr(
        refresh_token,
        "list_private_unread_image_chat_ids",
        lambda limit=None: [("Private Image", "privateImage")],
    )
    monkeypatch.setattr(refresh_token, "list_direct_share_chat_ids", lambda: [("Chat A", "chatA")])

    assert refresh_token.read_token_from_db(wait_seconds=0) == "abc="
    assert calls == [("privateImage", 0)]


def test_read_token_falls_back_to_direct_share_when_no_private_unread_images(tmp_path, monkeypatch):
    db_path = tmp_path / "naver_line"
    _make_db(db_path, None)
    monkeypatch.setattr(refresh_token, "_host_db", lambda: db_path)
    calls = []

    def fake_trigger(chat_id, wait_seconds):
        calls.append((chat_id, wait_seconds))
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE setting SET value=? WHERE key=?",
            ("abc=metadata", refresh_token.SETTING_KEY),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(refresh_token, "trigger_token_regeneration", fake_trigger)
    monkeypatch.setattr(refresh_token, "list_private_unread_image_chat_ids", lambda limit=None: [])
    monkeypatch.setattr(refresh_token, "list_direct_share_chat_ids", lambda: [("Chat A", "chatA")])

    assert refresh_token.read_token_from_db(wait_seconds=0) == "abc="
    assert calls == [("chatA", 0)]


def test_read_token_does_not_trigger_when_token_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "naver_line"
    _make_db(db_path, "abc=metadata")
    monkeypatch.setattr(refresh_token, "_host_db", lambda: db_path)
    monkeypatch.setattr(
        refresh_token,
        "trigger_token_regeneration",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not trigger")),
    )

    assert refresh_token.read_token_from_db(chat_id="chatA", wait_seconds=0) == "abc="
