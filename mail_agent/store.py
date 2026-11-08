"""SQLite persistence: processed mails (so Claude never re-reads a mail), open
items that carry over to tomorrow's digest, and created calendar events
(idempotency)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import TriagedEmail

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    final_priority TEXT,
    due TEXT,
    action_required INTEGER,
    done INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_open ON messages(done, action_required, due);
CREATE TABLE IF NOT EXISTS calendar_events (
    key TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_event_id TEXT,
    title TEXT,
    start TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- delete_mail | add_events
    status TEXT NOT NULL,           -- pending | approved | rejected | done | failed
    title TEXT,
    payload TEXT NOT NULL,          -- JSON: items with message_id, account, subject, summary, reason
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    summary TEXT,
    model_priority TEXT,
    corrected_priority TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(messages)")}
            if "internet_message_id" not in cols:  # migrate DBs created by v0.1
                self._conn.execute("ALTER TABLE messages ADD COLUMN internet_message_id TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_imid ON messages(internet_message_id)")

    # -- messages -----------------------------------------------------------
    def processed_ids(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        with self._lock:
            q = f"SELECT id FROM messages WHERE id IN ({','.join('?' * len(ids))})"
            return {r["id"] for r in self._conn.execute(q, ids)}

    def save_triaged(self, items: list[TriagedEmail]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                it.email.id, it.email.received_at.isoformat(), it.email.subject, it.email.sender_email,
                it.final_priority.value, it.due.isoformat() if it.due else None,
                int(it.triage.action_required), it.model_dump_json(), now, it.email.internet_message_id,
            )
            for it in items
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO messages (id, received_at, subject, sender, final_priority, due,
                                         action_required, payload, processed_at, internet_message_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET final_priority=excluded.final_priority, due=excluded.due,
                       action_required=excluded.action_required, payload=excluded.payload""",
                rows,
            )

    def open_items(self, exclude_ids: set[str], not_before: datetime) -> list[TriagedEmail]:
        """Action items from earlier runs that are not done and not long past due."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload, due FROM messages WHERE done = 0 AND action_required = 1"
            ).fetchall()
        out = []
        for r in rows:
            if r["id"] in exclude_ids:
                continue
            if r["due"] and datetime.fromisoformat(r["due"]) < not_before:
                continue
            item = TriagedEmail.model_validate_json(r["payload"])
            item.carried_over = True
            out.append(item)
        return out

    def get_item(self, message_id: str | None = None, internet_message_id: str | None = None) -> TriagedEmail | None:
        with self._lock:
            row = None
            if message_id:
                row = self._conn.execute("SELECT payload, done FROM messages WHERE id = ?", (message_id,)).fetchone()
            if row is None and internet_message_id:
                row = self._conn.execute(
                    "SELECT payload, done FROM messages WHERE internet_message_id = ?", (internet_message_id,)
                ).fetchone()
        return TriagedEmail.model_validate_json(row["payload"]) if row else None

    def is_done(self, message_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT done FROM messages WHERE id = ?", (message_id,)).fetchone()
        return bool(row and row["done"])

    def recent_items(self, since: datetime) -> list[TriagedEmail]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM messages WHERE done = 0 AND received_at >= ? ORDER BY received_at DESC",
                (since.isoformat(),),
            ).fetchall()
        return [TriagedEmail.model_validate_json(r["payload"]) for r in rows]

    def add_feedback(self, item: TriagedEmail, corrected_priority: str, note: str | None) -> None:
        item.final_priority = type(item.final_priority)(corrected_priority)
        item.user_corrected = True
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO feedback (message_id, subject, sender, summary, model_priority, corrected_priority,"
                " note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (item.email.id, item.email.subject, item.email.sender_email, item.triage.summary,
                 item.triage.priority.value, corrected_priority, note, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.execute(
                "UPDATE messages SET final_priority = ?, payload = ? WHERE id = ?",
                (corrected_priority, item.model_dump_json(), item.email.id),
            )

    def recent_feedback(self, limit: int = 15) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT subject, sender, summary, model_priority, corrected_priority, note FROM feedback "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_done(self, message_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE messages SET done = 1 WHERE id = ?", (message_id,))
            return cur.rowcount > 0

    # -- calendar -----------------------------------------------------------
    def calendar_event_exists(self, key: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM calendar_events WHERE key = ?", (key,)).fetchone() is not None

    # -- pending actions (human-in-the-loop for destructive steps) ----------
    def add_action(self, kind: str, title: str, items: list[dict], status: str = "pending") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO actions (kind, status, title, payload, created_at) VALUES (?,?,?,?,?)",
                (kind, status, title, json.dumps(items, ensure_ascii=False), now),
            )
            return int(cur.lastrowid)

    def get_action(self, action_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        return self._action_row(r) if r else None

    def list_actions(self, status: str | None = "pending", limit: int = 50) -> list[dict]:
        with self._lock:
            if status:
                rows = self._conn.execute("SELECT * FROM actions WHERE status = ? ORDER BY id DESC LIMIT ?",
                                          (status, limit)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._action_row(r) for r in rows]

    def set_action_status(self, action_id: int, status: str, result=None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE actions SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 datetime.now(timezone.utc).isoformat(), action_id),
            )

    def pending_message_ids(self) -> set[str]:
        """Message ids already waiting in a pending delete proposal (avoid proposing twice)."""
        ids: set[str] = set()
        for a in self.list_actions("pending", limit=500):
            ids |= {i["message_id"] for i in a["items"]}
        return ids

    @staticmethod
    def _action_row(r) -> dict:
        d = dict(r)
        d["items"] = json.loads(d.pop("payload"))
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d

    def calendar_event_id(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT provider_event_id FROM calendar_events WHERE key = ?", (key,)).fetchone()
        return row["provider_event_id"] if row else None

    def save_calendar_event(self, key: str, message_id: str, provider: str, event_id: str | None,
                            title: str, start: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO calendar_events VALUES (?,?,?,?,?,?,?)",
                (key, message_id, provider, event_id, title, start, datetime.now(timezone.utc).isoformat()),
            )

    # -- meta -----------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value if isinstance(value, str) else json.dumps(value)),
            )
