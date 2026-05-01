#!/usr/bin/env python3
"""Benchmark LINE direct SQLite read concurrency.

This measures line_db's DB layer directly, not the MCP server lock. It prints
only aggregate timing/error data, not message contents.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass

import line_db


QUERIES = {
    "recent_chats": (
        "SELECT chat_id FROM chat ORDER BY last_created_time DESC LIMIT 50",
        False,
    ),
    "recent_messages": (
        """
        SELECT id, chat_id, created_time
        FROM chat_history
        ORDER BY created_time DESC
        LIMIT 100
        """,
        False,
    ),
    "joined_chats": (
        """
        SELECT c.chat_id, COALESCE(g.name, con.overridden_name, con.profile_name, c.chat_name, '') AS name
        FROM chat c
        LEFT JOIN groups g ON g.id = c.chat_id
        LEFT JOIN cdb.contacts con ON con.mid = c.chat_id
        ORDER BY c.last_created_time DESC
        LIMIT 50
        """,
        True,
    ),
}


@dataclass
class Sample:
    elapsed_ms: float
    ok: bool
    error: str = ""


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[index]


def _run_one(query_name: str) -> Sample:
    sql, attach_contact = QUERIES[query_name]
    start = time.perf_counter()
    try:
        line_db._q(sql, attach_contact=attach_contact)
        return Sample((time.perf_counter() - start) * 1000, True)
    except Exception as exc:
        return Sample((time.perf_counter() - start) * 1000, False, type(exc).__name__)


def _run_level(query_name: str, concurrency: int, requests: int) -> dict[str, object]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        samples = list(executor.map(lambda _: _run_one(query_name), range(requests)))
    wall = time.perf_counter() - started

    ok = [sample.elapsed_ms for sample in samples if sample.ok]
    errors = Counter(sample.error for sample in samples if not sample.ok)
    return {
        "concurrency": concurrency,
        "requests": requests,
        "ok": len(ok),
        "errors": dict(errors),
        "rps": len(ok) / wall if wall else 0.0,
        "mean_ms": statistics.mean(ok) if ok else 0.0,
        "p50_ms": _percentile(ok, 0.50),
        "p95_ms": _percentile(ok, 0.95),
        "p99_ms": _percentile(ok, 0.99),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", choices=sorted(QUERIES), default="joined_chats")
    parser.add_argument("--concurrency", default="1,2,4,8,16")
    parser.add_argument("--requests", type=int, default=120)
    args = parser.parse_args()

    os.environ.setdefault("LINE_MCP_DB_MODE", "direct")

    levels = [int(value.strip()) for value in args.concurrency.split(",") if value.strip()]
    print(f"mode={os.environ.get('LINE_MCP_DB_MODE')} query={args.query} requests_per_level={args.requests}")
    print("conc\tok\terrors\trps\tmean_ms\tp50_ms\tp95_ms\tp99_ms")
    for level in levels:
        result = _run_level(args.query, level, args.requests)
        print(
            f"{result['concurrency']}\t{result['ok']}\t{result['errors']}\t"
            f"{result['rps']:.1f}\t{result['mean_ms']:.2f}\t{result['p50_ms']:.2f}\t"
            f"{result['p95_ms']:.2f}\t{result['p99_ms']:.2f}"
        )


if __name__ == "__main__":
    main()
