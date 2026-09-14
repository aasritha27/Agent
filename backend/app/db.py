"""SQLite persistence — no external database service needed.

One file (DATA_DIR/app.db) holds everything: users, university data,
schedules and room requests. Uploaded request letters live as regular
files under DATA_DIR/letters. The schema is created automatically on
first use, so a fresh volume boots into a working app.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any

from .config import DATA_DIR

_write_lock = threading.Lock()

DB_PATH = os.path.join(DATA_DIR, "app.db")
LETTERS_DIR = os.path.join(DATA_DIR, "letters")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'viewer',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS faculty (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    max_per_day INTEGER NOT NULL DEFAULT 4
);
CREATE TABLE IF NOT EXISTS rooms (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    type     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    students INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS subjects (
    id   TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS section_subjects (
    section_id      TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    faculty_id      TEXT NOT NULL,
    lecture_hours   INTEGER NOT NULL DEFAULT 3,
    practical_hours INTEGER NOT NULL DEFAULT 2,
    PRIMARY KEY (section_id, subject_id)
);
CREATE TABLE IF NOT EXISTS schedules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    status     TEXT NOT NULL,
    sessions   TEXT NOT NULL,
    stats      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS room_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL,
    section_id     TEXT NOT NULL,
    preferred_room TEXT,
    reason         TEXT NOT NULL,
    letter_path    TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    decided_by     TEXT,
    decided_at     TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LETTERS_DIR, exist_ok=True)
    with _connect() as c:
        c.executescript(SCHEMA)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _write_lock, _connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> None:
    with _write_lock, _connect() as c:
        c.execute(sql, params)


# ---- users / roles ---------------------------------------------------------

def get_role(user_id: str) -> str:
    rows = query("SELECT role FROM users WHERE id = ?", (user_id,))
    return rows[0]["role"] if rows else "viewer"


def coordinator_count() -> int:
    rows = query("SELECT COUNT(*) AS n FROM users WHERE role = 'coordinator'")
    return rows[0]["n"] if rows else 0


def set_role(user_id: str, role: str) -> None:
    execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def get_user_by_email(email: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
    return rows[0] if rows else None


def get_user(user_id: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


def create_user(user_id: str, email: str, password_hash: str, full_name: str = "") -> None:
    execute(
        "INSERT INTO users (id, email, password_hash, full_name) VALUES (?, ?, ?, ?)",
        (user_id, email.strip().lower(), password_hash, full_name),
    )


# ---- university data -------------------------------------------------------

def load_university() -> dict[str, Any]:
    return {
        "faculty": query("SELECT * FROM faculty ORDER BY name"),
        "rooms": query("SELECT * FROM rooms ORDER BY name"),
        "sections": query("SELECT * FROM sections ORDER BY name"),
        "subjects": query("SELECT * FROM subjects ORDER BY code"),
        "section_subjects": query("SELECT * FROM section_subjects"),
    }


def upsert(table: str, rows: list[dict[str, Any]], key: str) -> None:
    """Insert-or-update by primary key (mirrors the old upsert behaviour)."""
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c} = excluded.{c}" for c in cols if c != key)
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}"
    )
    with _write_lock, _connect() as c:
        c.executemany(sql, [tuple(r.get(col) for col in cols) for r in rows])


# ---- schedules ---------------------------------------------------------------

def archive_schedules() -> None:
    execute("UPDATE schedules SET status = 'archived' WHERE status = 'active'")


def insert_schedule(sessions: list, stats: dict) -> int:
    with _write_lock, _connect() as c:
        cur = c.execute(
            "INSERT INTO schedules (status, sessions, stats) VALUES ('active', ?, ?)",
            (json.dumps(sessions), json.dumps(stats)),
        )
        return cur.lastrowid


def active_schedule() -> dict[str, Any] | None:
    rows = query(
        "SELECT * FROM schedules WHERE status = 'active' "
        "ORDER BY created_at DESC, id DESC LIMIT 1"
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "stats": json.loads(row["stats"]) if row["stats"] else None,
        "sessions": json.loads(row["sessions"]),
    }


# ---- room requests ---------------------------------------------------------

def insert_request(user_id: str, section_id: str, preferred_room: str | None,
                   reason: str, letter_path: str | None) -> int:
    with _write_lock, _connect() as c:
        cur = c.execute(
            "INSERT INTO room_requests (user_id, section_id, preferred_room, reason, "
            "letter_path) VALUES (?, ?, ?, ?, ?)",
            (user_id, section_id, preferred_room, reason, letter_path),
        )
        return cur.lastrowid


def list_requests(user_id: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.*, u.full_name AS user_name, u.email AS user_email "
        "FROM room_requests r LEFT JOIN users u ON u.id = r.user_id"
    )
    params: tuple = ()
    if user_id:
        sql += " WHERE r.user_id = ?"
        params = (user_id,)
    sql += " ORDER BY r.created_at DESC, r.id DESC"
    return query(sql, params)


def get_request(request_id: int) -> dict[str, Any] | None:
    rows = query("SELECT * FROM room_requests WHERE id = ?", (request_id,))
    return rows[0] if rows else None


def decide_request(request_id: int, status: str, decided_by: str) -> None:
    execute(
        "UPDATE room_requests SET status = ?, decided_by = ?, "
        "decided_at = datetime('now') WHERE id = ?",
        (status, decided_by, request_id),
    )


# ---- solver glue -----------------------------------------------------------

def solver_inputs(uni: dict[str, Any]) -> tuple:
    """(days, num_slots, sections, rooms, faculty_max, assignments) from DB rows."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    num_slots = 8
    sections = [
        {"id": s["id"], "name": s["name"], "students": s["students"]} for s in uni["sections"]
    ]
    rooms = [{"id": r["id"], "capacity": r["capacity"], "type": r["type"]} for r in uni["rooms"]]
    faculty_max = {f["id"]: f.get("max_per_day", 4) for f in uni["faculty"]}
    assignments = [
        {
            "section_id": g["section_id"],
            "subject_id": g["subject_id"],
            "faculty_id": g["faculty_id"],
            "lecture_hours": g.get("lecture_hours", 3),
            "practical_hours": g.get("practical_hours", 2),
        }
        for g in uni["section_subjects"]
    ]
    return days, num_slots, sections, rooms, faculty_max, assignments
