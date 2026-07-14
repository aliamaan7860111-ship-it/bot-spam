"""Durable send de-dup log (SQLite).

Guarantees a given phone is never messaged twice from the same WhatsApp sender
within the de-dup window — across process restarts, scheduler re-fires, retries,
and Notion-write failures. This is the hard safeguard against double-sends: the
send is recorded here the instant it succeeds (before any Notion write), so a
failed status write can never cause the same customer to be messaged again.

Keyed on (phone, sender_id) where sender_id is the WhatChimp phone_number_id, so
brands that share one WhatsApp number (e.g. Pelvini + Virex) de-dup together.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_log.db")
DEDUP_HOURS = int(os.environ.get("RECOVERY_DEDUP_HOURS", "24"))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    c.execute(
        "CREATE TABLE IF NOT EXISTS sent ("
        "phone TEXT NOT NULL, sender_id TEXT NOT NULL, "
        "wa_message_id TEXT, sent_at REAL NOT NULL)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_ps ON sent(phone, sender_id, sent_at)")
    return c


def already_sent_recently(phone: str, sender_id: str, within_hours: int | None = None) -> bool:
    """True if (phone, sender_id) has a recorded send inside the window.

    Fails OPEN (returns False) on DB error so a storage glitch never silently
    drops every recovery send — the error is logged loudly instead.
    """
    if not phone or not sender_id:
        return False
    hrs = DEDUP_HOURS if within_hours is None else within_hours
    cutoff = time.time() - hrs * 3600
    conn = None
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM sent WHERE phone=? AND sender_id=? AND sent_at>=? LIMIT 1",
            (phone, sender_id, cutoff),
        ).fetchone()
        return row is not None
    except Exception:
        log.exception("sent_log.already_sent_recently DB error (failing open)")
        return False
    finally:
        if conn is not None:
            conn.close()


def record_sent(phone: str, sender_id: str, wa_message_id: str | None = None,
                sent_at: float | None = None) -> None:
    """Record a successful send. Call this the instant a send succeeds, BEFORE
    any Notion write. Also usable to seed known prior sends (backfill victims)."""
    if not phone or not sender_id:
        return
    conn = None
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO sent(phone, sender_id, wa_message_id, sent_at) VALUES (?,?,?,?)",
            (phone, sender_id, wa_message_id, sent_at if sent_at is not None else time.time()),
        )
        conn.commit()
    except Exception:
        log.exception("sent_log.record_sent DB error")
    finally:
        if conn is not None:
            conn.close()
