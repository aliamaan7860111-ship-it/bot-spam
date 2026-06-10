"""
rescue_store.py — SQLite state layer for the 24h window rescue service (grq-rescue).

One row per (bot_id, phone) conversation. Timestamps are epoch seconds (float),
stamped at event arrival — WhatChimp webhook payloads don't carry a documented
timestamp field, and the tee from rpgrq_webhook_server is real-time anyway.
"""
from __future__ import annotations

import sqlite3

# Hard cap on trigger-bot attempts per armed conversation. Lesson from the OFD
# retry-forever bug: a permanently failing send must not be re-attempted every tick.
MAX_ATTEMPTS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    bot_id   TEXT NOT NULL,
    phone    TEXT NOT NULL,
    brand    TEXT NOT NULL DEFAULT '',
    last_real_inbound_at REAL,
    last_outbound_at     REAL,
    last_button_tap_at   REAL,
    rescued_at           REAL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    updated_at           REAL NOT NULL,
    PRIMARY KEY (bot_id, phone)
)
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def record_real_inbound(conn: sqlite3.Connection, bot_id: str, phone: str, brand: str, ts: float) -> None:
    """A real customer message: restarts the 23h clock and re-arms the rescue."""
    conn.execute(
        """INSERT INTO conversations (bot_id, phone, brand, last_real_inbound_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(bot_id, phone) DO UPDATE SET
               brand = excluded.brand,
               last_real_inbound_at = excluded.last_real_inbound_at,
               rescued_at = NULL,
               attempts = 0,
               updated_at = excluded.updated_at""",
        (bot_id, phone, brand, ts, ts),
    )
    conn.commit()


def record_button_tap(conn: sqlite3.Connection, bot_id: str, phone: str, brand: str, ts: float) -> None:
    """A rescue-button tap: recorded for observability only — must NOT re-arm a rescue."""
    conn.execute(
        """INSERT INTO conversations (bot_id, phone, brand, last_button_tap_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(bot_id, phone) DO UPDATE SET
               last_button_tap_at = excluded.last_button_tap_at,
               updated_at = excluded.updated_at""",
        (bot_id, phone, brand, ts, ts),
    )
    conn.commit()


def record_outbound(conn: sqlite3.Connection, bot_id: str, phone: str, brand: str, ts: float) -> None:
    """Any outbound (agent, bot, template) marks the conversation answered."""
    conn.execute(
        """INSERT INTO conversations (bot_id, phone, brand, last_outbound_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(bot_id, phone) DO UPDATE SET
               last_outbound_at = excluded.last_outbound_at,
               updated_at = excluded.updated_at""",
        (bot_id, phone, brand, ts, ts),
    )
    conn.commit()


def eligible(conn: sqlite3.Connection, now: float, min_age_s: float, max_age_s: float) -> list[sqlite3.Row]:
    """Conversations due for a rescue: unanswered, un-rescued, inside the firing window.

    Conversations older than max_age_s are deliberately excluded — past 23h45m we
    can't be sure the trigger lands before Meta closes the window, and trigger-bot
    reports success even when the platform then drops the message.
    """
    return conn.execute(
        """SELECT * FROM conversations
           WHERE last_real_inbound_at IS NOT NULL
             AND (last_outbound_at IS NULL OR last_outbound_at < last_real_inbound_at)
             AND rescued_at IS NULL
             AND attempts < ?
             AND last_real_inbound_at <= ? - ?
             AND last_real_inbound_at >= ? - ?""",
        (MAX_ATTEMPTS, now, min_age_s, now, max_age_s),
    ).fetchall()


def mark_rescued(conn: sqlite3.Connection, bot_id: str, phone: str, ts: float) -> None:
    conn.execute(
        "UPDATE conversations SET rescued_at = ?, updated_at = ? WHERE bot_id = ? AND phone = ?",
        (ts, ts, bot_id, phone),
    )
    conn.commit()


def bump_attempts(conn: sqlite3.Connection, bot_id: str, phone: str, ts: float) -> None:
    conn.execute(
        "UPDATE conversations SET attempts = attempts + 1, updated_at = ? WHERE bot_id = ? AND phone = ?",
        (ts, bot_id, phone),
    )
    conn.commit()
