import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import line_db  # noqa: E402


def test_q_reads_direct_host_sqlite_when_enabled(tmp_path, monkeypatch):
    main_db = tmp_path / "naver_line"
    contact_db = tmp_path / "contact"

    conn = sqlite3.connect(main_db)
    conn.execute("CREATE TABLE chat(chat_id TEXT)")
    conn.execute("INSERT INTO chat VALUES ('c1')")
    conn.commit()
    conn.close()

    contact_conn = sqlite3.connect(contact_db)
    contact_conn.execute("CREATE TABLE contacts(mid TEXT, profile_name TEXT)")
    contact_conn.execute("INSERT INTO contacts VALUES ('c1', 'Display Name')")
    contact_conn.commit()
    contact_conn.close()

    monkeypatch.setenv("LINE_MCP_DB_MODE", "direct")
    monkeypatch.setattr(line_db, "HOST_DB", main_db)
    monkeypatch.setattr(line_db, "HOST_CONTACT_DB", contact_db)

    rows = line_db._q(
        "SELECT chat.chat_id, cdb.contacts.profile_name FROM chat "
        "JOIN cdb.contacts ON cdb.contacts.mid = chat.chat_id",
        attach_contact=True,
    )

    assert rows == [{"chat_id": "c1", "profile_name": "Display Name"}]


def test_q_auto_falls_back_to_waydroid_when_direct_read_fails(monkeypatch):
    monkeypatch.setenv("LINE_MCP_DB_MODE", "auto")
    monkeypatch.setattr(line_db, "HOST_DB", Path("/missing/naver_line"))

    with patch.object(line_db, "_query_via_waydroid", return_value=[{"ok": 1}]) as waydroid:
        assert line_db._q("SELECT 1 AS ok") == [{"ok": 1}]

    waydroid.assert_called_once_with("SELECT 1 AS ok", attach_contact=False)
