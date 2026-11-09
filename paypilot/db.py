"""SQLite persistence for sessions, proposals, and the mock ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level="DEFERRED")
        self._conn.row_factory = sqlite3.Row
        self.migrate()

    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    paypal_mode TEXT NOT NULL,
                    intent_provider TEXT NOT NULL,
                    last_invoice_id TEXT,
                    last_invoice_status TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    body TEXT NOT NULL,
                    proposal_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    moves_money INTEGER NOT NULL,
                    paypal_call TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    confirm_blocked INTEGER NOT NULL DEFAULT 0,
                    mismatches_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    proposal_id TEXT,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    resource_id TEXT,
                    summary TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    moves_money INTEGER NOT NULL,
                    mock INTEGER NOT NULL,
                    debug_id TEXT
                );
                CREATE TABLE IF NOT EXISTS mock_invoices (
                    session_id TEXT NOT NULL,
                    invoice_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, invoice_id)
                );
                CREATE TABLE IF NOT EXISTS mock_links (
                    session_id TEXT NOT NULL,
                    link_id TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, link_id)
                );
                CREATE TABLE IF NOT EXISTS mock_capture_refunds (
                    refund_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    request_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_resources (
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    PRIMARY KEY (session_id, kind, resource_id)
                );
                """
            )
            self._ensure_proposal_columns()
            self._conn.commit()

    def _ensure_proposal_columns(self) -> None:
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(proposals)")}
        if "confirm_blocked" not in columns:
            self._conn.execute(
                "ALTER TABLE proposals ADD COLUMN confirm_blocked INTEGER NOT NULL DEFAULT 0"
            )
        if "mismatches_json" not in columns:
            self._conn.execute(
                "ALTER TABLE proposals ADD COLUMN mismatches_json TEXT NOT NULL DEFAULT '[]'"
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self._depth += 1
            try:
                yield
                if self._depth == 1:
                    self._conn.commit()
            except Exception:
                if self._depth == 1:
                    self._conn.rollback()
                raise
            finally:
                self._depth -= 1

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def insert_session(self, session_id: str, mode: str, provider: str) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO sessions (id, created_at, paypal_mode, intent_provider) VALUES (?, ?, ?, ?)",
                (session_id, utc_now(), mode, provider),
            )

    def get_session(self, session_id: str) -> dict | None:
        row = self.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def set_last_invoice(self, session_id: str, invoice_id: str, status: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE sessions SET last_invoice_id = ?, last_invoice_status = ? WHERE id = ?",
                (invoice_id, status, session_id),
            )

    def add_message(self, session_id: str, role: str, body: str, proposal_id: str | None) -> int:
        with self.transaction():
            cursor = self._conn.execute(
                """
                INSERT INTO messages (session_id, role, body, proposal_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, body, proposal_id, utc_now()),
            )
            return int(cursor.lastrowid)

    def list_messages(self, session_id: str) -> list[dict]:
        rows = self.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def supersede_pending(self, session_id: str) -> None:
        with self.transaction():
            self._conn.execute(
                """
                UPDATE proposals
                SET status = 'superseded', resolved_at = ?
                WHERE session_id = ? AND status = 'pending'
                """,
                (utc_now(), session_id),
            )

    def insert_proposal(self, row: dict) -> None:
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO proposals (
                    id, session_id, action, payload_json, summary, warnings_json, preview_json,
                    status, requires_confirmation, moves_money, paypal_call, request_id,
                    result_json, error, created_at, resolved_at, confirm_blocked, mismatches_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["session_id"],
                    row["action"],
                    row["payload_json"],
                    row["summary"],
                    row["warnings_json"],
                    row["preview_json"],
                    row["status"],
                    row["requires_confirmation"],
                    row["moves_money"],
                    row["paypal_call"],
                    row["request_id"],
                    row.get("result_json"),
                    row.get("error"),
                    row["created_at"],
                    row.get("resolved_at"),
                    row.get("confirm_blocked", 0),
                    row.get("mismatches_json", "[]"),
                ),
            )

    def get_proposal(self, proposal_id: str) -> dict | None:
        row = self.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def update_proposal(self, proposal_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.transaction():
            self._conn.execute(
                f"UPDATE proposals SET {assignments} WHERE id = ?",
                (*fields.values(), proposal_id),
            )

    def add_activity(self, row: dict) -> None:
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO activity (
                    session_id, proposal_id, created_at, mode, method, path, status_code,
                    resource_id, summary, detail_json, moves_money, mock, debug_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["session_id"],
                    row.get("proposal_id"),
                    row["created_at"],
                    row["mode"],
                    row["method"],
                    row["path"],
                    row["status_code"],
                    row.get("resource_id"),
                    row["summary"],
                    row["detail_json"],
                    int(row["moves_money"]),
                    int(row["mock"]),
                    row.get("debug_id"),
                ),
            )

    def list_activity(self, session_id: str) -> list[dict]:
        rows = self.execute(
            "SELECT * FROM activity WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_mock_invoice(self, session_id: str, invoice_id: str, data: dict) -> None:
        payload = json.dumps(data)
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO mock_invoices (session_id, invoice_id, data_json)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, invoice_id) DO UPDATE SET data_json = excluded.data_json
                """,
                (session_id, invoice_id, payload),
            )

    def get_mock_invoice(self, session_id: str, invoice_id: str) -> dict | None:
        row = self.execute(
            "SELECT data_json FROM mock_invoices WHERE session_id = ? AND invoice_id = ?",
            (session_id, invoice_id),
        ).fetchone()
        return json.loads(row["data_json"]) if row else None

    def list_mock_invoices(self, session_id: str) -> list[dict]:
        rows = self.execute(
            "SELECT data_json FROM mock_invoices WHERE session_id = ? ORDER BY invoice_id",
            (session_id,),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def save_mock_link(self, session_id: str, link_id: str, data: dict) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO mock_links (session_id, link_id, data_json) VALUES (?, ?, ?)",
                (session_id, link_id, json.dumps(data)),
            )

    def add_capture_refund(self, refund_id: str, session_id: str, capture_id: str, amount: str, data: dict) -> None:
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO mock_capture_refunds (refund_id, session_id, capture_id, amount, data_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (refund_id, session_id, capture_id, amount, json.dumps(data)),
            )

    def capture_refund_amounts(self, session_id: str, capture_id: str) -> list[str]:
        rows = self.execute(
            "SELECT amount FROM mock_capture_refunds WHERE session_id = ? AND capture_id = ?",
            (session_id, capture_id),
        ).fetchall()
        return [row["amount"] for row in rows]

    def get_idempotent(self, request_id: str) -> dict | None:
        row = self.execute(
            "SELECT result_json FROM idempotency WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def put_idempotent(self, request_id: str, result: dict) -> None:
        with self.transaction():
            self._conn.execute(
                "INSERT INTO idempotency (request_id, result_json) VALUES (?, ?)",
                (request_id, json.dumps(result)),
            )

    def remember_resource(self, session_id: str, kind: str, resource_id: str) -> None:
        with self.transaction():
            self._conn.execute(
                """
                INSERT OR IGNORE INTO session_resources (session_id, kind, resource_id)
                VALUES (?, ?, ?)
                """,
                (session_id, kind, resource_id),
            )

    def resources_for(self, session_id: str, kind: str) -> list[str]:
        rows = self.execute(
            "SELECT resource_id FROM session_resources WHERE session_id = ? AND kind = ? ORDER BY resource_id",
            (session_id, kind),
        ).fetchall()
        return [row["resource_id"] for row in rows]
