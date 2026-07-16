"""order_dedup.py — atomic, durable duplicate guard for Shopify order ingestion.

A single SQLite table with a PRIMARY KEY on order_id makes claiming an order
atomic: even if two backfill loops (or a webhook and a backfill) try to process
the same order at the exact same instant, only ONE claim() returns True. This
is what actually prevents duplicate rows in the Notion CRM — Notion has no
unique constraint, so a "query-then-insert" check always races.

Fail-CLOSED on unexpected DB errors: we would rather briefly skip an order (the
backfill retries it next cycle) than risk a duplicate.

Usage:
    if not order_dedup.claim(order_id):
        return  # someone else already owns this order
    ok = await create_notion_order(...)
    if not ok:
        order_dedup.release(order_id)  # allow a later retry
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone

_DB_PATH = os.getenv("SHOPIFY_DEDUP_DB", "/home/bilal/automation/.tmp/shopify_dedup.db")
_init_lock = threading.Lock()
_initialized_paths = set()


def _ensure_init():
    """Create the table + enable WAL exactly once per DB path (thread-safe)."""
    if _DB_PATH in _initialized_paths:
        return
    with _init_lock:
        if _DB_PATH in _initialized_paths:
            return
        conn = sqlite3.connect(_DB_PATH, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS processed_orders "
                "(order_id TEXT PRIMARY KEY, claimed_at TEXT)"
            )
            conn.commit()
        finally:
            conn.close()
        _initialized_paths.add(_DB_PATH)


def _connect():
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")  # wait for the write lock, don't error
    return conn


def claim(order_id: str) -> bool:
    """Atomically claim an order. True = newly claimed (process it),
    False = already claimed OR could not be verified (skip; the backfill retries).
    Fail-closed so a transient DB error never causes a duplicate."""
    if not order_id:
        return False
    _ensure_init()
    conn = None
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO processed_orders(order_id, claimed_at) VALUES (?, ?)",
            (order_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # already claimed
    except Exception:
        return False  # fail-closed: never risk a duplicate
    finally:
        if conn is not None:
            conn.close()


def release(order_id: str) -> None:
    """Release a claim so the order can be retried (e.g. after a failed insert)."""
    if not order_id:
        return
    _ensure_init()
    conn = None
    try:
        conn = _connect()
        conn.execute("DELETE FROM processed_orders WHERE order_id = ?", (order_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
