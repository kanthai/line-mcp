#!/usr/bin/env python3
"""Mirror the live LINE SQLite databases into PostgreSQL.

This is the feeder for ``LINE_MCP_DB_MODE=postgres``: line-mcp reads the mirror
(``line_raw`` + ``cdb`` schemas) instead of touching the live SQLite files, which
lets the MCP server run unprivileged and serve many concurrent readers.

Tables
------
Full-scan every pass (small, updated in place):
    line_raw.chat, line_raw.chat_member, line_raw.groups, line_raw.membership, cdb.contacts
Incremental (by ``id`` watermark, plus a 24 h re-scan window for recalled/edited rows):
    line_raw.chat_history

Environment
-----------
DATABASE_URL                 postgresql://user:pass@host:5432/db   (required)
LINE_MCP_HOST_DB             path to naver_line SQLite  (default: redroid-data volume path)
LINE_MCP_HOST_CONTACT_DB     path to contact SQLite     (default: redroid-data volume path)
LINE_SYNC_INTERVAL_SECONDS   seconds between passes (default 5; CT103 runs 30)
LOG_LEVEL                    python logging level (default INFO)

Run once with ``--once`` (useful for the initial import / smoke test), otherwise loops forever.
The process must be able to open the SQLite files read-only — on a Redroid host that
means root (Docker resets /var/lib/docker to 0710 on start), see systemd/line-sync-postgres.service.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import psycopg

LOG = logging.getLogger("line-sync-postgres")

_REDROID_DB_DIR = "/var/lib/docker/volumes/redroid-data/_data/data/jp.naver.line.android/databases"
NAVER_LINE_DB = Path(os.environ.get("LINE_MCP_HOST_DB", f"{_REDROID_DB_DIR}/naver_line"))
CONTACT_DB = Path(os.environ.get("LINE_MCP_HOST_CONTACT_DB", f"{_REDROID_DB_DIR}/contact"))
INTERVAL = int(os.environ.get("LINE_SYNC_INTERVAL_SECONDS", "5"))

# Tables with full scan (small, frequently updated in-place)
FULL_SCAN_TABLES = [
    ("line_raw", NAVER_LINE_DB, "chat"),
    ("line_raw", NAVER_LINE_DB, "chat_member"),
    ("line_raw", NAVER_LINE_DB, "groups"),
    ("line_raw", NAVER_LINE_DB, "membership"),   # group membership: id=group_id, m_id=member_mid
    ("cdb",      CONTACT_DB,    "contacts"),
]

# Incremental sync: (schema, path, table, watermark_col, rescan_time_col)
INCREMENTAL_TABLES = [
    ("line_raw", NAVER_LINE_DB, "chat_history", "id", "created_time"),
]

RESCAN_HOURS = 24  # LINE allows message recall within 24h; rescan this window for in-place updates

# SQLite declares these as TEXT/INTEGER inconsistently; line-mcp's queries need them as BIGINT.
_BIGINT_COLUMNS = {"created_time", "last_created_time", "delivered_time", "last_message_display_time"}

# Indexes line-mcp's postgres read path relies on (see tools/line_db.py).
INDEX_SQL = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS idx_line_raw_chat_history_chat_time "
    "ON line_raw.chat_history (chat_id, created_time)",
    "CREATE INDEX IF NOT EXISTS idx_chat_history_content_trgm "
    "ON line_raw.chat_history USING gin (content gin_trgm_ops)",
]


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_info(path: Path, table: str):
    conn = _ro(path)
    try:
        return conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    finally:
        conn.close()


def sqlite_rows(path: Path, table: str):
    conn = _ro(path)
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {qident(table)}").fetchall()]
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()]
        return cols, rows
    finally:
        conn.close()


def sqlite_rows_since(path: Path, table: str, col: str, min_val: int):
    """Read only rows where col > min_val from SQLite (read-only)."""
    conn = _ro(path)
    try:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()]
        rows = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {qident(table)} WHERE {qident(col)} > ?", (min_val,)
            ).fetchall()
        ]
        return cols, rows
    finally:
        conn.close()


def sqlite_rows_time_window(path: Path, table: str, time_col: str, id_col: str, since_ms: int, max_id: int):
    """Read rows where time_col >= since_ms AND id_col <= max_id (already-synced rows that may have changed)."""
    conn = _ro(path)
    try:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({qident(table)})").fetchall()]
        rows = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {qident(table)} WHERE CAST({qident(time_col)} AS INTEGER) >= ? AND {qident(id_col)} <= ?",
                (since_ms, max_id),
            ).fetchall()
        ]
        return cols, rows
    finally:
        conn.close()


def create_raw_table(cur, schema: str, table: str, cols: list[sqlite3.Row]):
    definitions = []
    pk_cols = []
    for col in cols:
        name = col["name"]
        ctype = (col["type"] or "").upper()
        if "INT" in ctype or name in _BIGINT_COLUMNS:
            pg_type = "bigint"
        elif "BLOB" in ctype:
            pg_type = "bytea"
        else:
            pg_type = "text"
        definitions.append(f"{qident(name)} {pg_type}")
        if col["pk"]:
            pk_cols.append(name)
    if pk_cols:
        definitions.append("PRIMARY KEY (" + ", ".join(qident(c) for c in pk_cols) + ")")
    cur.execute(f"CREATE TABLE IF NOT EXISTS {qident(schema)}.{qident(table)} ({', '.join(definitions)})")


def upsert_rows(cur, schema: str, table: str, columns: list[str], rows: list[dict], conflict_cols: list[str] | None = None):
    if not rows:
        return 0
    col_sql = ", ".join(qident(c) for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    if conflict_cols:
        update_cols = [c for c in columns if c not in conflict_cols]
        if update_cols:
            action = "DO UPDATE SET " + ", ".join(f"{qident(c)} = EXCLUDED.{qident(c)}" for c in update_cols)
        else:
            action = "DO NOTHING"
        conflict = "ON CONFLICT (" + ", ".join(qident(c) for c in conflict_cols) + f") {action}"
    else:
        conflict = "ON CONFLICT DO NOTHING"
    sql = f"INSERT INTO {qident(schema)}.{qident(table)} ({col_sql}) VALUES ({placeholders}) {conflict}"
    values = [tuple(row.get(c) for c in columns) for row in rows]
    cur.executemany(sql, values)
    return len(rows)


def get_watermark(cur, schema: str, table: str, col: str) -> int:
    """Return MAX(col) from Postgres, or 0 if table doesn't exist yet."""
    try:
        cur.execute(f"SELECT MAX({qident(col)}) FROM {qident(schema)}.{qident(table)}")
        val = cur.fetchone()[0]
        return val if val is not None else 0
    except Exception:
        return 0


def sync_table(cur, schema: str, path: Path, table: str) -> int:
    cols_info = table_info(path, table)
    if not cols_info:
        return 0
    create_raw_table(cur, schema, table, cols_info)
    cols, rows = sqlite_rows(path, table)
    pk_cols = [c["name"] for c in cols_info if c["pk"]]
    conflict_cols = pk_cols or (["chat_id", "mid"] if table == "chat_member" else None)
    return upsert_rows(cur, schema, table, cols, rows, conflict_cols)


def sync_table_incremental(cur, schema: str, path: Path, table: str, watermark_col: str, rescan_time_col: str | None = None) -> int:
    cols_info = table_info(path, table)
    if not cols_info:
        return 0
    create_raw_table(cur, schema, table, cols_info)
    watermark = get_watermark(cur, schema, table, watermark_col)

    # New rows (id > watermark)
    cols, new_rows = sqlite_rows_since(path, table, watermark_col, watermark)

    # Re-scan 24h window to catch in-place updates (recalled/edited messages)
    rescan_rows: list[dict] = []
    if rescan_time_col and watermark > 0:
        since_ms = int(time.time() * 1000) - RESCAN_HOURS * 3600 * 1000
        _, rescan_rows = sqlite_rows_time_window(path, table, rescan_time_col, watermark_col, since_ms, watermark)

    if not new_rows and not rescan_rows:
        return 0

    # Merge: rescan (older) first, new rows win on id collision
    merged: dict[int, dict] = {r[watermark_col]: r for r in rescan_rows}
    for r in new_rows:
        merged[r[watermark_col]] = r
    all_rows = list(merged.values())

    pk_cols = [c["name"] for c in cols_info if c["pk"]]
    conflict_cols = pk_cols or None
    upsert_rows(cur, schema, table, cols, all_rows, conflict_cols)
    if new_rows:
        LOG.debug("%s.%s: watermark %d → +%d new, %d rescan", schema, table, watermark, len(new_rows), len(rescan_rows))
    return len(new_rows)


def ensure_indexes(cur) -> None:
    for sql in INDEX_SQL:
        try:
            cur.execute(sql)
        except Exception:  # table may not exist yet on the very first pass
            LOG.debug("index step skipped: %s", sql, exc_info=True)


def sync_once(dsn: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS line_raw")
            cur.execute("CREATE SCHEMA IF NOT EXISTS cdb")
            for schema, path, table in FULL_SCAN_TABLES:
                if path.exists():
                    counts[f"{schema}.{table}"] = sync_table(cur, schema, path, table)
            for schema, path, table, wcol, rescan_tcol in INCREMENTAL_TABLES:
                if path.exists():
                    counts[f"{schema}.{table}"] = sync_table_incremental(cur, schema, path, table, wcol, rescan_tcol)
            ensure_indexes(cur)
        conn.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--once", action="store_true", help="run a single sync pass and exit")
    args = ap.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        LOG.error("DATABASE_URL is not set")
        return 2
    if not NAVER_LINE_DB.exists():
        LOG.warning("LINE DB not found at %s (is Redroid up / LINE logged in / are we root?)", NAVER_LINE_DB)

    if args.once:
        counts = sync_once(dsn)
        LOG.info("synced %s", counts)
        return 0

    while True:
        try:
            counts = sync_once(dsn)
            total = sum(counts.values())
            if total:
                LOG.info("synced %s", counts)
        except Exception:
            LOG.exception("sync failed")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
