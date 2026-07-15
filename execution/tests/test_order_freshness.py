import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notion_client as n


def test_is_order_fresh():
    now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    # Fresh orders (within 24h) -> send
    assert n.is_order_fresh((now - timedelta(hours=2)).isoformat(), 24, now) is True
    assert n.is_order_fresh((now - timedelta(hours=23, minutes=59)).isoformat(), 24, now) is True
    # Stale orders (older than 24h) -> skip, never confirm late
    assert n.is_order_fresh((now - timedelta(hours=25)).isoformat(), 24, now) is False
    assert n.is_order_fresh((now - timedelta(days=90)).isoformat(), 24, now) is False  # the backlog case
    # Unknown / unparseable created -> treated as NOT fresh (safe: never blast stale)
    assert n.is_order_fresh("", 24, now) is False
    assert n.is_order_fresh(None, 24, now) is False
    assert n.is_order_fresh("not-a-date", 24, now) is False
    # Naive (no timezone) timestamp is treated as UTC
    assert n.is_order_fresh("2026-07-15T10:00:00", 24, now) is True
