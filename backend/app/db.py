"""SQLite connections and the app-owned operational tables."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import DB_PATH, ORIGINAL_DB_PATH


APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    scope TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ticket_id INTEGER,
    created_at TEXT NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_events_run_id ON agent_events(run_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('payment', 'purchase_order')),
    ref_type TEXT,
    ref_id INTEGER,
    amount REAL NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, id);
CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id INTEGER NOT NULL UNIQUE,
    ticket_id INTEGER NOT NULL,
    vendor_id INTEGER NOT NULL,
    sku TEXT NOT NULL,
    size TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_cost REAL NOT NULL,
    total REAL NOT NULL,
    lead_days INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT
);
CREATE TABLE IF NOT EXISTS customer_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Working database is missing: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def initialize_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        if not ORIGINAL_DB_PATH.exists():
            raise FileNotFoundError("Original shop database is missing; restore hw5/data/campus_customs.db first.")
        shutil.copy2(ORIGINAL_DB_PATH, DB_PATH)
    with connect() as conn:
        conn.executescript(APP_SCHEMA)


def reset_database() -> None:
    """Replace the working file with the untouched original, then recreate app tables."""
    if not ORIGINAL_DB_PATH.exists():
        raise FileNotFoundError("Original shop database is missing; reset was not performed.")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging = DB_PATH.with_suffix(DB_PATH.suffix + ".resetting")
    try:
        shutil.copy2(ORIGINAL_DB_PATH, staging)
        staging.replace(DB_PATH)
    finally:
        if staging.exists():
            staging.unlink()
    initialize_database()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def add_event(
    run_id: str | None,
    ticket_id: int | None,
    agent: str,
    kind: str,
    content: str,
    details: dict[str, Any] | None = None,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO agent_events(run_id,ticket_id,created_at,agent,kind,content,details_json) VALUES(?,?,?,?,?,?,?)",
            (run_id, ticket_id, utc_now(), agent, kind, content, json_dump(details or {})),
        )
        return int(cursor.lastrowid)


def list_events(after_id: int = 0, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM agent_events WHERE id>? AND run_id=? ORDER BY id LIMIT ?",
                (after_id, run_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_events WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json") or "{}")
        result.append(item)
    return result


def save_run(run_id: str, status: str, scope: str, *, finished: bool = False, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id,status,scope,started_at,finished_at,error) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, finished_at=excluded.finished_at, error=excluded.error",
            (run_id, status, scope, utc_now(), utc_now() if finished else None, error),
        )


def update_run(run_id: str, status: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET status=?, finished_at=?, error=? WHERE id=?",
            (status, utc_now() if status in {"completed", "failed", "cancelled"} else None, error, run_id),
        )


def latest_run() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return as_dict(row)
