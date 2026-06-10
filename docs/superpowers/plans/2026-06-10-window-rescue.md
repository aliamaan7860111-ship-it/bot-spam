# 24h Window Rescue (`grq-rescue`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire a free "Apologies for the delayed response" bot flow (two buttons: Connect with Agent / Not Interested) at the 23-hour mark for WhatsApp conversations nobody answered, so a button tap re-opens Meta's 24h window for free.

**Architecture:** `rpgrq_webhook_server.py` (which already owns all 5 bots' incoming/outgoing WhatChimp webhooks) gets a fire-and-forget tee that copies every message event to a new localhost-only service, `grq-rescue` (port 8085). The rescue service keeps per-conversation clocks in SQLite and a 60s scheduler that calls WhatChimp `POST /whatsapp/trigger-bot` for conversations unanswered for 23h–23h45m. Spec: `docs/superpowers/specs/2026-06-10-window-rescue-design.md`.

**Tech Stack:** Python 3 stdlib asyncio (raw `asyncio.start_server`, same pattern as rpgrq), `httpx` (already a dependency) for the trigger call, `sqlite3` stdlib for state, `unittest`-style tests under `execution/tests/` (run with pytest), systemd service template.

**Key API facts** (from `docs/whatchimp/whatchimp-api--trigger-bot-flow.md`):
- `POST https://app.whatchimp.com/api/v1/whatsapp/trigger-bot`, JSON body: `apiToken`, `phone_number_id`, `bot_flow_unique_id`, `phone_number` (numeric, no `+`).
- Success response `{"status":"1","message":"Bot has been trigger successfully."}` — the message text has a typo; key on `status` only.
- `trigger-bot` returns success even when the session window is already closed (the message then silently fails at the platform layer) — that's why the scheduler must never fire past 23h45m.
- Not idempotent — re-triggering duplicates the flow. Caller-side dedup is the `rescued_at` flag.

**Webhook payload fields** (confirmed in `rpgrq_webhook_server.py`): `chat_id` (phone), `whatsapp_bot_id`, `whatsapp_bot_name`, `wa_message_id`, `label_names`. The **message-text field name is unconfirmed** — `extract_text()` therefore tries several candidate keys, and the directive mandates a live payload capture before any brand is enabled.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `execution/rescue_store.py` | Create | SQLite state layer: conversation clocks, eligibility query, rescue/attempt marking |
| `execution/rescue_server.py` | Create | Event classification, HTTP receiver, scheduler loop, trigger-bot call, `RESCUE_CONFIG` |
| `execution/tests/test_rescue.py` | Create | Unit tests for store, classification, scheduler tick |
| `execution/rpgrq_webhook_server.py` | Modify | Add `tee_to_rescue()` + two `asyncio.create_task` calls at dispatch |
| `execution/grq-rescue.service.template` | Create | systemd unit |
| `directives/window_rescue.md` | Create | WhatChimp UI setup, payload capture procedure, flow-ID registry, go-live checklist |

`RESCUE_CONFIG` lives in `rescue_server.py` (not `whatchimp_client.py`) — same precedent as `rpgrq_whatchimp.py`: per-service WhatChimp helpers stay with the service; `whatchimp_client.py` stays order-flow-focused. The service is async, so it gets its own `httpx`-based trigger call instead of reusing the sync `requests` client.

---

### Task 1: `rescue_store.py` — SQLite state layer

**Files:**
- Create: `execution/rescue_store.py`
- Test: `execution/tests/test_rescue.py`

- [ ] **Step 1: Write the failing tests**

Create `execution/tests/test_rescue.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rescue_store as store

H = 3600.0
MIN_AGE = 23 * H          # fire from 23h00m...
MAX_AGE = 23 * H + 45 * 60  # ...until 23h45m


def fresh_conn():
    return store.connect(":memory:")


class TestStoreEligibility(unittest.TestCase):
    def test_unanswered_conversation_becomes_eligible_at_23h(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        # 1 second before the 23h mark: not eligible
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE - 1, MIN_AGE, MAX_AGE), [])
        # at the 23h mark: eligible
        rows = store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "9715551234")
        self.assertEqual(rows[0]["bot_id"], "381990")

    def test_missed_firing_window_is_skipped(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        # past 23h45m (e.g. service was down): never fire — window might be closed
        self.assertEqual(store.eligible(conn, 1000.0 + MAX_AGE + 1, MIN_AGE, MAX_AGE), [])

    def test_answered_conversation_not_eligible(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "381990", "9715551234", "VIREX UAE", ts=2000.0)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE), [])

    def test_new_inbound_after_answer_re_arms(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "381990", "9715551234", "VIREX UAE", ts=2000.0)
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=3000.0)
        rows = store.eligible(conn, 3000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)

    def test_rescued_conversation_not_eligible_again(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 60, MIN_AGE, MAX_AGE), [])

    def test_real_inbound_clears_rescued_flag_and_attempts(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=2000.0)
        store.bump_attempts(conn, "381990", "9715551234", ts=2000.0)
        # customer sends a real message → fully re-armed
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=5000.0)
        rows = store.eligible(conn, 5000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 0)

    def test_button_tap_does_not_re_arm(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        store.record_button_tap(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0 + MIN_AGE + 60)
        # tap recorded, but conversation stays rescued/ineligible forever after
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 120, MIN_AGE, MAX_AGE), [])
        self.assertEqual(
            store.eligible(conn, 1000.0 + MIN_AGE + 60 + MIN_AGE, MIN_AGE, MAX_AGE), []
        )

    def test_attempts_cap_blocks_eligibility(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        for _ in range(store.MAX_ATTEMPTS):
            store.bump_attempts(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 60, MIN_AGE, MAX_AGE), [])

    def test_outbound_only_conversation_never_eligible(self):
        # e.g. an OFD template sent to someone who never wrote in
        conn = fresh_conn()
        store.record_outbound(conn, "381990", "9715559999", "VIREX UAE", ts=1000.0)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE), [])

    def test_conversations_are_keyed_per_bot(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "382073", "9715551234", "DIALO UAE", ts=2000.0)
        # the Dialo reply must not mark the Virex conversation answered
        rows = store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bot_id"], "381990")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest execution/tests/test_rescue.py -v`
Expected: collection error / failures with `ModuleNotFoundError: No module named 'rescue_store'`

- [ ] **Step 3: Write the implementation**

Create `execution/rescue_store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest execution/tests/test_rescue.py -v`
Expected: 10 PASSED

- [ ] **Step 5: Commit**

```bash
git add execution/rescue_store.py execution/tests/test_rescue.py
git commit -m "feat(rescue): SQLite state layer for 24h window rescue"
```

---

### Task 2: `rescue_server.py` — event classification

**Files:**
- Create: `execution/rescue_server.py`
- Test: `execution/tests/test_rescue.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `execution/tests/test_rescue.py`:

```python
import rescue_server as rs


class TestClassifyEvent(unittest.TestCase):
    BASE = {
        "chat_id": "9715551234",
        "whatsapp_bot_id": "381990",
        "whatsapp_bot_name": "Virex UAE",
        "wa_message_id": "wamid.test1",
    }

    def test_incoming_text_is_real_inbound(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "where is my order?"})
        self.assertEqual(ev["kind"], "real_inbound")
        self.assertEqual(ev["bot_id"], "381990")
        self.assertEqual(ev["phone"], "9715551234")

    def test_incoming_button_label_is_button_tap(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "Connect with Agent"})
        self.assertEqual(ev["kind"], "button_tap")

    def test_button_match_is_case_insensitive(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "not interested"})
        self.assertEqual(ev["kind"], "button_tap")

    def test_extra_button_texts_from_env(self):
        # postback hashes captured live get added via RESCUE_EXTRA_BUTTON_TEXTS
        old = rs.BUTTON_TEXTS
        rs.BUTTON_TEXTS = rs.BUTTON_TEXTS | {"q9d4f8y6gzrkw_4"}
        try:
            ev = rs.classify_event("in", {**self.BASE, "message": "q9D4f8Y6gzRKW_4"})
            self.assertEqual(ev["kind"], "button_tap")
        finally:
            rs.BUTTON_TEXTS = old

    def test_text_extracted_from_alternate_keys(self):
        # exact field name is unconfirmed until live capture — accept common variants
        for key in ("message", "message_text", "text", "user_message", "msg"):
            ev = rs.classify_event("in", {**self.BASE, key: "hello"})
            self.assertEqual(ev["kind"], "real_inbound", f"key={key}")

    def test_incoming_without_text_is_real_inbound(self):
        # media-only message: still a real customer message
        ev = rs.classify_event("in", dict(self.BASE))
        self.assertEqual(ev["kind"], "real_inbound")

    def test_outgoing_is_outbound(self):
        ev = rs.classify_event("out", {**self.BASE, "message": "hi, agent here"})
        self.assertEqual(ev["kind"], "outbound")

    def test_missing_phone_or_bot_id_returns_none(self):
        self.assertIsNone(rs.classify_event("in", {"whatsapp_bot_id": "381990"}))
        self.assertIsNone(rs.classify_event("in", {"chat_id": "9715551234"}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest execution/tests/test_rescue.py -v -k Classify`
Expected: FAIL with `ModuleNotFoundError: No module named 'rescue_server'`

- [ ] **Step 3: Write the implementation**

Create `execution/rescue_server.py`:

```python
"""
rescue_server.py — grq-rescue: 24h window rescue service.

Meta closes the customer-service window 24h after the customer's last message;
after that only paid templates can be sent. This service watches the message
stream (teed from rpgrq_webhook_server, which owns the per-bot webhooks) and,
for conversations nobody answered, fires the per-brand "Window Rescue" bot flow
at the 23h mark — a free session message whose button tap re-opens the window.

Endpoints (bind 127.0.0.1 only — fed by the local tee, not exposed via Caddy):
  GET  /        health check ("rescue up")
  POST /events  {"direction": "in"|"out", "payload": {...raw WhatChimp webhook...}}

Spec: docs/superpowers/specs/2026-06-10-window-rescue-design.md
Setup + go-live checklist: directives/window_rescue.md

Run: python3 execution/rescue_server.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
load_dotenv(PROJECT_ROOT / ".env")

import rescue_store as store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rescue")

# ── Config ──
PORT = int(os.getenv("RESCUE_PORT", "8085"))
DB_PATH = os.getenv("RESCUE_DB_PATH", str(PROJECT_ROOT / "rescue.db"))
POLL_SECONDS = int(os.getenv("RESCUE_POLL_SECONDS", "60"))
FIRE_AFTER_S = float(os.getenv("RESCUE_FIRE_AFTER_HOURS", "23")) * 3600
FIRE_WINDOW_S = float(os.getenv("RESCUE_FIRE_WINDOW_MINUTES", "45")) * 60
MAX_AGE_S = FIRE_AFTER_S + FIRE_WINDOW_S

WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")
API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

# 24h window rescue routing. Key = whatsapp_bot_id (the stable per-brand ID in
# webhook payloads — same source of truth as rpgrq_whatchimp.WHATCHIMP_BOT_ID_TO_BRAND).
# bot_flow_unique_id comes from Bot Manager UI after the "Window Rescue" flow is
# built for that bot (see directives/window_rescue.md). enabled stays False until
# the flow exists AND its button payloads have been captured live.
RESCUE_CONFIG = {
    "381990": {"brand": "VIREX UAE", "phone_number_id": "1073890042476443",
               "bot_flow_unique_id": "", "enabled": False},
    "382073": {"brand": "DIALO UAE", "phone_number_id": "1002123586328400",
               "bot_flow_unique_id": "", "enabled": False},
    "382036": {"brand": "AMARA", "phone_number_id": "1045332455333591",
               "bot_flow_unique_id": "", "enabled": False},
    "352261": {"brand": "ELARA", "phone_number_id": "1031340813395459",
               "bot_flow_unique_id": "", "enabled": False},
    # LUNE: whatsapp_bot_id never captured (phone_number_id is 1138942462625909).
    # Add its entry when the first Lune event shows up in the rescue log.
}

# Inbound texts that are rescue-button taps, not real customer messages.
# Button labels by default; live-captured postback hashes are appended via env
# RESCUE_EXTRA_BUTTON_TEXTS (comma-separated). All matching is lowercased.
BUTTON_TEXTS = {"connect with agent", "not interested"} | {
    t.strip().lower()
    for t in os.getenv("RESCUE_EXTRA_BUTTON_TEXTS", "").split(",")
    if t.strip()
}

# Exact text field name in WhatChimp webhook payloads is undocumented — try
# common variants. The live payload capture (directive step 2) confirms which.
TEXT_KEYS = ("message", "message_text", "text", "user_message", "msg")


def extract_text(payload: dict) -> str:
    for key in TEXT_KEYS:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def classify_event(direction: str, payload: dict) -> dict | None:
    """Classify one teed webhook event.

    Returns {"kind": "real_inbound"|"button_tap"|"outbound", "bot_id", "phone",
    "brand", "text"} or None when the payload can't identify a conversation.
    """
    phone = str(payload.get("chat_id") or "").strip()
    bot_id = str(payload.get("whatsapp_bot_id") or "").strip()
    if not phone or not bot_id:
        return None

    brand = str(payload.get("whatsapp_bot_name") or "").strip()
    text = extract_text(payload)

    if direction == "out":
        kind = "outbound"
    elif text.lower() in BUTTON_TEXTS:
        kind = "button_tap"
    else:
        # No extractable text (e.g. media) still counts as a real message.
        kind = "real_inbound"
    return {"kind": kind, "bot_id": bot_id, "phone": phone, "brand": brand, "text": text}


def handle_event(conn: sqlite3.Connection, direction: str, payload: dict, now: float | None = None) -> None:
    now = time.time() if now is None else now
    ev = classify_event(direction, payload)
    if ev is None:
        log.warning(f"event missing chat_id/bot_id, payload keys={sorted(payload.keys())}")
        return
    if ev["kind"] == "real_inbound":
        store.record_real_inbound(conn, ev["bot_id"], ev["phone"], ev["brand"], now)
    elif ev["kind"] == "button_tap":
        store.record_button_tap(conn, ev["bot_id"], ev["phone"], ev["brand"], now)
        log.info(f"🔘 button tap on {ev['brand']} / {ev['phone']}: {ev['text']!r}")
    else:
        store.record_outbound(conn, ev["bot_id"], ev["phone"], ev["brand"], now)
```

(HTTP server, trigger call, and scheduler come in Tasks 3–4; this file grows in place.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest execution/tests/test_rescue.py -v`
Expected: all PASSED (10 store + 8 classify)

- [ ] **Step 5: Commit**

```bash
git add execution/rescue_server.py execution/tests/test_rescue.py
git commit -m "feat(rescue): event classification (real inbound vs button tap vs outbound)"
```

---

### Task 3: `rescue_server.py` — trigger-bot call + scheduler tick

**Files:**
- Modify: `execution/rescue_server.py` (append)
- Test: `execution/tests/test_rescue.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `execution/tests/test_rescue.py`:

```python
import asyncio


class TestRunTick(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        self.calls = []
        # restore module config after each test
        self._old_config = rs.RESCUE_CONFIG
        rs.RESCUE_CONFIG = {
            "381990": {"brand": "VIREX UAE", "phone_number_id": "1073890042476443",
                       "bot_flow_unique_id": "flow_virex_rescue", "enabled": True},
            "382073": {"brand": "DIALO UAE", "phone_number_id": "1002123586328400",
                       "bot_flow_unique_id": "", "enabled": False},
        }

    def tearDown(self):
        rs.RESCUE_CONFIG = self._old_config

    async def fake_trigger_ok(self, phone, phone_number_id, flow_id):
        self.calls.append((phone, phone_number_id, flow_id))
        return True

    async def fake_trigger_fail(self, phone, phone_number_id, flow_id):
        self.calls.append((phone, phone_number_id, flow_id))
        return False

    def test_fires_for_eligible_enabled_brand_and_marks_rescued(self):
        store.record_real_inbound(self.conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        now = 1000.0 + MIN_AGE + 60
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=now))
        self.assertEqual(fired, 1)
        self.assertEqual(self.calls, [("9715551234", "1073890042476443", "flow_virex_rescue")])
        # second tick: rescued_at set → no re-fire
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=now + 60))
        self.assertEqual(fired, 0)
        self.assertEqual(len(self.calls), 1)

    def test_disabled_brand_is_skipped(self):
        store.record_real_inbound(self.conn, "382073", "9715551234", "DIALO UAE", ts=1000.0)
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=1000.0 + MIN_AGE + 60))
        self.assertEqual(fired, 0)
        self.assertEqual(self.calls, [])

    def test_unknown_bot_id_is_skipped(self):
        store.record_real_inbound(self.conn, "999999", "9715551234", "???", ts=1000.0)
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=1000.0 + MIN_AGE + 60))
        self.assertEqual(fired, 0)

    def test_failed_trigger_bumps_attempts_and_caps_at_max(self):
        store.record_real_inbound(self.conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        now = 1000.0 + MIN_AGE + 60
        for _ in range(store.MAX_ATTEMPTS + 2):  # more ticks than the cap
            asyncio.run(rs.run_tick(self.conn, self.fake_trigger_fail, now=now))
        # called exactly MAX_ATTEMPTS times, then silenced
        self.assertEqual(len(self.calls), store.MAX_ATTEMPTS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest execution/tests/test_rescue.py -v -k RunTick`
Expected: FAIL with `AttributeError: module 'rescue_server' has no attribute 'run_tick'`

- [ ] **Step 3: Write the implementation**

Append to `execution/rescue_server.py`:

```python
# ────────────────────────────────────────────────────────────
# Trigger + scheduler
# ────────────────────────────────────────────────────────────

_http: httpx.AsyncClient | None = None  # created in main()


async def trigger_bot_flow(phone: str, phone_number_id: str, bot_flow_unique_id: str) -> bool:
    """Fire a bot flow at a subscriber via POST /trigger-bot (JSON body, 10 Feb 2026 API batch)."""
    body = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_number_id,
        "bot_flow_unique_id": bot_flow_unique_id,
        "phone_number": phone,
    }
    try:
        resp = await _http.post(f"{API_BASE}/trigger-bot", json=body)
        data = resp.json()
    except Exception as e:
        log.error(f"trigger-bot request failed for {phone}: {e}")
        return False
    # Success message is "Bot has been trigger successfully." (their typo) — key on status only.
    if str(data.get("status")) == "1":
        return True
    log.error(f"trigger-bot rejected for {phone}: {data}")
    return False


async def run_tick(conn: sqlite3.Connection, trigger_fn, now: float | None = None) -> int:
    """One scheduler pass. Returns the number of rescues fired."""
    now = time.time() if now is None else now
    fired = 0
    for row in store.eligible(conn, now, FIRE_AFTER_S, MAX_AGE_S):
        cfg = RESCUE_CONFIG.get(row["bot_id"])
        if not cfg or not cfg["enabled"] or not cfg["bot_flow_unique_id"]:
            continue
        ok = await trigger_fn(row["phone"], cfg["phone_number_id"], cfg["bot_flow_unique_id"])
        if ok:
            store.mark_rescued(conn, row["bot_id"], row["phone"], now)
            log.info(f"🛟 rescue fired: {cfg['brand']} / {row['phone']}")
            fired += 1
        else:
            store.bump_attempts(conn, row["bot_id"], row["phone"], now)
            log.warning(
                f"rescue failed: {cfg['brand']} / {row['phone']} "
                f"(attempt {row['attempts'] + 1}/{store.MAX_ATTEMPTS})"
            )
    return fired


async def scheduler_loop(conn: sqlite3.Connection) -> None:
    while True:
        try:
            await run_tick(conn, trigger_bot_flow)
        except Exception as e:
            import traceback
            log.error(f"scheduler tick crashed: {e}\n{traceback.format_exc()}")
        await asyncio.sleep(POLL_SECONDS)
```

Note for the implementer: `run_tick` takes `trigger_fn` as a parameter so tests
inject a fake without monkeypatching HTTP; production passes `trigger_bot_flow`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest execution/tests/test_rescue.py -v`
Expected: all PASSED (22 tests)

- [ ] **Step 5: Commit**

```bash
git add execution/rescue_server.py execution/tests/test_rescue.py
git commit -m "feat(rescue): trigger-bot call and 60s scheduler tick with retry cap"
```

---

### Task 4: `rescue_server.py` — HTTP server + main()

**Files:**
- Modify: `execution/rescue_server.py` (append)

No unit test for the socket plumbing (same as rpgrq's server, which has none) —
verified by the smoke test in Step 2.

- [ ] **Step 1: Write the implementation**

Append to `execution/rescue_server.py`:

```python
# ────────────────────────────────────────────────────────────
# HTTP server (same raw-asyncio pattern as rpgrq_webhook_server)
# ────────────────────────────────────────────────────────────

async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    conn: sqlite3.Connection,
):
    try:
        header_bytes = bytearray()
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            header_bytes.extend(chunk)
            if b"\r\n\r\n" in header_bytes:
                break
            if len(header_bytes) > 65536:
                break

        if b"\r\n\r\n" not in header_bytes:
            writer.close()
            return

        head, _, rest = header_bytes.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        first_line = lines[0].split()
        if len(first_line) < 2:
            writer.close()
            return
        method, path = first_line[0].upper(), first_line[1].split("?", 1)[0]

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if method in ("GET", "HEAD") and path in ("/", "/health"):
            body = b"rescue up"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + (b"" if method == "HEAD" else body)
            )
            await writer.drain()
            writer.close()
            return

        if method != "POST" or path != "/events":
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        length = int(headers.get("content-length", "0") or 0)
        body = bytes(rest)
        while len(body) < length:
            chunk = await reader.read(min(65536, length - len(body)))
            if not chunk:
                break
            body += chunk

        # Ack immediately — the tee has a 2s timeout and must never be blocked.
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

        try:
            event = json.loads(body[:length].decode("utf-8", errors="replace"))
        except Exception as e:
            log.warning(f"bad /events body: {e}")
            return
        direction = str(event.get("direction") or "")
        payload = event.get("payload") or {}
        if direction not in ("in", "out") or not isinstance(payload, dict):
            log.warning(f"malformed event: direction={direction!r}")
            return
        log.debug(f"event {direction}: {json.dumps(payload, ensure_ascii=False)[:500]}")
        handle_event(conn, direction, payload)

    except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError, TimeoutError) as e:
        log.debug(f"connection dropped: {e}")
        try:
            writer.close()
        except Exception:
            pass
    except Exception as e:
        import traceback
        log.error(f"connection handler crashed: {e}\n{traceback.format_exc()}")
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global _http
    log.info("=" * 60)
    log.info("  grq-rescue — 24h Window Rescue Service")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Port: {PORT}  DB: {DB_PATH}")
    log.info(f"  Fire window: {FIRE_AFTER_S / 3600:.2f}h – {MAX_AGE_S / 3600:.2f}h")
    enabled = [c["brand"] for c in RESCUE_CONFIG.values() if c["enabled"]]
    log.info(f"  Enabled brands: {enabled or 'NONE (all pending flow setup)'}")
    log.info("=" * 60)

    conn = store.connect(DB_PATH)
    _http = httpx.AsyncClient(timeout=30.0)

    async def on_conn(reader, writer):
        await handle_connection(reader, writer, conn)

    # 127.0.0.1 only: events come from the rpgrq tee on the same VM.
    server = await asyncio.start_server(on_conn, "127.0.0.1", PORT)
    log.info(f"🌐 listening on 127.0.0.1:{PORT}")

    scheduler = asyncio.create_task(scheduler_loop(conn))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows: signal handlers unsupported in ProactorEventLoop
    await stop_event.wait()

    log.info("shutting down")
    scheduler.cancel()
    server.close()
    await server.wait_closed()
    await _http.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: Smoke test locally**

Terminal 1: `python execution/rescue_server.py`
Expected log: banner + `listening on 127.0.0.1:8085` + `Enabled brands: NONE (all pending flow setup)`

Terminal 2 (PowerShell):

```powershell
curl.exe -s http://127.0.0.1:8085/
# expect: rescue up
curl.exe -s -X POST http://127.0.0.1:8085/events -H "Content-Type: application/json" -d '{\"direction\":\"in\",\"payload\":{\"chat_id\":\"9715551234\",\"whatsapp_bot_id\":\"381990\",\"whatsapp_bot_name\":\"Virex UAE\",\"message\":\"hello\"}}'
# expect: ok
```

Then verify the row landed: `python -c "import sqlite3; c=sqlite3.connect('rescue.db'); print(c.execute('select * from conversations').fetchall())"`
Expected: one row for `('381990', '9715551234', ...)`. Delete the scratch DB afterwards: `Remove-Item rescue.db`. Stop the server with Ctrl+C.

- [ ] **Step 3: Run full test suite (regression)**

Run: `python -m pytest execution/tests/ -v`
Expected: all PASSED (rescue + OFD + existing suites)

- [ ] **Step 4: Commit**

```bash
git add execution/rescue_server.py
git commit -m "feat(rescue): localhost HTTP receiver and service main loop"
```

---

### Task 5: Tee in `rpgrq_webhook_server.py`

**Files:**
- Modify: `execution/rpgrq_webhook_server.py` (env constant near `PORT`; helper above `handle_connection`; dispatch lines ~471-475)
- Test: `execution/tests/test_rescue.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `execution/tests/test_rescue.py`:

```python
class TestRescueTee(unittest.TestCase):
    def test_tee_swallows_connection_errors(self):
        # The tee must NEVER raise into the leads pipeline — even with rescue down.
        from execution import rpgrq_webhook_server as rws

        class ExplodingClient:
            async def post(self, *a, **kw):
                raise OSError("connection refused")

        # must not raise
        asyncio.run(rws.tee_to_rescue(ExplodingClient(), "in", {"chat_id": "x"}))

    def test_tee_disabled_when_url_empty(self):
        from execution import rpgrq_webhook_server as rws

        class MustNotBeCalled:
            async def post(self, *a, **kw):
                raise AssertionError("tee should be disabled")

        old = rws.RESCUE_EVENTS_URL
        rws.RESCUE_EVENTS_URL = ""
        try:
            asyncio.run(rws.tee_to_rescue(MustNotBeCalled(), "in", {"chat_id": "x"}))
        finally:
            rws.RESCUE_EVENTS_URL = old
```

Note: `rpgrq_webhook_server` imports as `from execution import ...`, so this test
imports it the same way (the repo root is `parent.parent` of the tests dir — add
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))` next to
the existing insert at the top of the test file).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest execution/tests/test_rescue.py -v -k Tee`
Expected: FAIL with `AttributeError: ... no attribute 'tee_to_rescue'`

- [ ] **Step 3: Write the implementation**

In `execution/rpgrq_webhook_server.py`, below `PORT = int(os.getenv("RPGRQ_PORT", "8082"))` add:

```python
# grq-rescue tee: every message event is copied (fire-and-forget) to the 24h
# window rescue service on the same VM. Empty URL disables the tee entirely.
RESCUE_EVENTS_URL = os.getenv("RESCUE_EVENTS_URL", "http://127.0.0.1:8085/events")
```

Above `handle_connection` (after the `handle_outgoing` function ends) add:

```python
async def tee_to_rescue(http_client: httpx.AsyncClient, direction: str, payload: dict) -> None:
    """Copy one webhook event to grq-rescue. Failures are logged and swallowed —
    the rescue service being down must never affect the leads pipeline."""
    if not RESCUE_EVENTS_URL:
        return
    try:
        await http_client.post(
            RESCUE_EVENTS_URL,
            json={"direction": direction, "payload": payload},
            timeout=2.0,
        )
    except Exception as e:
        log.warning(f"rescue tee failed (ignored): {e}")
```

In `handle_connection`, change the dispatch:

```python
        if path_only == "/rpgrq/incoming":
            asyncio.create_task(tee_to_rescue(http_client, "in", payload))
            await handle_incoming(http_client, rr, payload)
        elif path_only == "/rpgrq/outgoing":
            asyncio.create_task(tee_to_rescue(http_client, "out", payload))
            await handle_outgoing(http_client, payload)
```

Note: the tee fires before rpgrq's own dedup, so grq-rescue may receive duplicate
events. That's fine — every store operation is an idempotent timestamp upsert.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest execution/tests/test_rescue.py -v`
Expected: all PASSED (24 tests)

- [ ] **Step 5: Local end-to-end check**

With `python execution/rescue_server.py` running in one terminal and
`python execution/rpgrq_webhook_server.py` in another, POST a fake webhook at
rpgrq and confirm it lands in the rescue DB:

```powershell
curl.exe -s -X POST http://127.0.0.1:8082/rpgrq/incoming -H "Content-Type: application/json" -d '{\"chat_id\":\"9715550000\",\"whatsapp_bot_id\":\"381990\",\"whatsapp_bot_name\":\"Virex UAE\",\"wa_message_id\":\"wamid.teetest\",\"message\":\"tee test\"}'
python -c "import sqlite3; c=sqlite3.connect('rescue.db'); print(c.execute('select bot_id, phone, last_real_inbound_at from conversations').fetchall())"
```

Expected: row for `('381990', '9715550000', <timestamp>)`. (rpgrq will log an
unknown-ticket warning for the fake phone — expected, ignore.) Clean up `rescue.db`.

- [ ] **Step 6: Commit**

```bash
git add execution/rpgrq_webhook_server.py execution/tests/test_rescue.py
git commit -m "feat(rescue): tee message events from rpgrq webhook server to grq-rescue"
```

---

### Task 6: systemd unit + directive

**Files:**
- Create: `execution/grq-rescue.service.template`
- Create: `directives/window_rescue.md`

- [ ] **Step 1: Create the service template**

Create `execution/grq-rescue.service.template` (mirrors `rpgrq-webhook.service.template`):

```ini
[Unit]
Description=GRQ 24h Window Rescue Service
After=network.target

[Service]
Type=simple
User=YOUR_LINUX_USERNAME
WorkingDirectory=/home/YOUR_LINUX_USERNAME/automation
ExecStart=/usr/bin/python3 execution/rescue_server.py
Restart=always
RestartSec=10
StandardOutput=append:/home/YOUR_LINUX_USERNAME/automation/rescue.log
StandardError=append:/home/YOUR_LINUX_USERNAME/automation/rescue.log
Environment=RESCUE_PORT=8085

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the directive**

Create `directives/window_rescue.md`:

```markdown
# 24h Window Rescue (`grq-rescue`)

When a customer messages a brand number and nobody answers, Meta's 24h
customer-service window closes and only paid templates can reach them. At the
23h mark, `grq-rescue` fires that brand's **Window Rescue** bot flow — a free
session message:

> "Apologies for the delayed response, how may we help you?"
> [Connect with Agent] [Not Interested]

A button tap is a customer inbound → the 24h window re-opens for free.

Spec: `docs/superpowers/specs/2026-06-10-window-rescue-design.md`
Code: `execution/rescue_server.py`, `execution/rescue_store.py`, tee in
`execution/rpgrq_webhook_server.py`. Service: `grq-rescue` (127.0.0.1:8085).

## Rules (locked with user, 2026-06-10)

- Fire at 23h00m–23h45m after the customer's last *real* message; never later
  (missed window = skip — trigger-bot "succeeds" even when Meta then drops the
  message).
- Only unanswered conversations (no outbound of any kind since their message).
- One rescue per conversation; only a new real customer message re-arms.
- Max 3 trigger attempts, then stop (OFD retry-forever lesson).
- Both buttons assign the conversation to an agent in WhatChimp.

## WhatChimp UI setup (once per bot × 5)

1. Bot Manager → <bot> → Bot Reply → new flow named **Window Rescue v1**
   (don't rename after go-live; version instead).
2. First node — text: `Apologies for the delayed response, how may we help you?`
3. Quick-reply button **Connect with Agent** →
   reply node: `You're connected — an agent will be with you shortly.`
   + side-effect: *Assign Conversation* to the brand's agent/team.
4. Quick-reply button **Not Interested** →
   reply node: `No problem — feel free to reach out anytime!`
   + side-effect: *Assign Conversation* to the brand's agent/team.
5. Copy the flow's `bot_flow_unique_id` from Bot Manager into the registry
   below and into `RESCUE_CONFIG` in `execution/rescue_server.py`.

## Flow ID registry

| Brand | whatsapp_bot_id | phone_number_id | bot_flow_unique_id | enabled |
|---|---|---|---|---|
| VIREX UAE | 381990 | 1073890042476443 | _(fill after UI setup)_ | no |
| DIALO UAE | 382073 | 1002123586328400 | _(fill)_ | no |
| AMARA | 382036 | 1045332455333591 | _(fill)_ | no |
| ELARA | 352261 | 1031340813395459 | _(fill)_ | no |
| LUNE | _(not yet captured — watch rescue log)_ | 1138942462625909 | _(fill)_ | no |

## Go-live checklist (per brand — pilot first, then the rest)

1. Build the flow (above), fill `bot_flow_unique_id`, keep `enabled: False`.
2. **Capture button payloads** (payload shapes are undocumented):
   from a test phone, message the brand number, manually trigger the flow
   (`trigger-bot` curl or a temporary low `RESCUE_FIRE_AFTER_HOURS`), tap each
   button, then read the teed payloads in `rescue.log` (`event in: {...}` lines,
   DEBUG level). Note the exact inbound text/postback of each button.
3. If tap text ≠ the button labels, add the captured strings to
   `RESCUE_EXTRA_BUTTON_TEXTS` (comma-separated, in `.env` on the VM).
   **This step is mandatory** — a misclassified tap re-arms the clock and can
   cause a second apology message.
4. Confirm the inbound text field name matches `TEXT_KEYS` in
   `rescue_server.py`; if WhatChimp uses a different key, add it.
5. Set `enabled: True` for the brand, commit, deploy, restart `grq-rescue`.
6. End-to-end: real message from test phone → no reply → confirm rescue
   arrives at 23h (or temporarily lower `RESCUE_FIRE_AFTER_HOURS`), tap
   *Connect with Agent*, confirm reply + agent assignment + no second rescue.

## Deploy

```
git push  (bot-spam repo / main)
ssh <vm>
cd ~/automation && git pull
sudo cp execution/grq-rescue.service.template /etc/systemd/system/grq-rescue.service  # first time; edit user paths
sudo systemctl daemon-reload && sudo systemctl enable grq-rescue                       # first time
sudo systemctl restart grq-rescue
sudo systemctl restart <rpgrq service>   # picks up the tee
```

Health: `curl http://127.0.0.1:8085/` → `rescue up`. Logs: `~/automation/rescue.log`.

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `RESCUE_PORT` | 8085 | listen port (127.0.0.1 only) |
| `RESCUE_DB_PATH` | `<repo>/rescue.db` | SQLite state |
| `RESCUE_POLL_SECONDS` | 60 | scheduler interval |
| `RESCUE_FIRE_AFTER_HOURS` | 23 | clock age to fire at |
| `RESCUE_FIRE_WINDOW_MINUTES` | 45 | firing window width |
| `RESCUE_EXTRA_BUTTON_TEXTS` | _(empty)_ | captured postback strings, comma-sep |
| `RESCUE_EVENTS_URL` | `http://127.0.0.1:8085/events` | tee target (set empty in rpgrq env to disable tee) |

## Known gaps (accepted in spec)

- Conversations already mid-window at first deploy are invisible until the
  customer's next message.
- Any outbound counts as "answered", including OFD/abandoned-cart templates.
- Events arriving while grq-rescue is down are lost (tee is fire-and-forget).
```

- [ ] **Step 3: Update execution/README pointer (convention: "update the directive that calls it")**

No change needed to `execution/README.md` itself (it documents conventions, not
scripts). Skip if nothing references script inventories.

- [ ] **Step 4: Commit**

```bash
git add execution/grq-rescue.service.template directives/window_rescue.md
git commit -m "feat(rescue): systemd unit template and window-rescue directive"
```

---

### Task 7: Final verification + push

- [ ] **Step 1: Full test suite**

Run: `python -m pytest execution/tests/ -v`
Expected: all PASSED, zero failures

- [ ] **Step 2: Plan-vs-spec review**

Re-read `docs/superpowers/specs/2026-06-10-window-rescue-design.md` and confirm
each spec section maps to shipped code: tee ✓, store ✓, classification ✓,
scheduler + firing window + retry cap ✓, config ✓, service template ✓,
directive with capture procedure ✓.

- [ ] **Step 3: Push**

```bash
git push origin main
```

Deployment to the VM and the WhatChimp UI flow setup are user-driven (see
directive go-live checklist) — flows must be built and payloads captured before
any brand is enabled.

---

## Self-review notes (already applied)

- **Spec coverage:** every spec component has a task; the spec's "Verification
  before go-live" lives in the directive's checklist (it needs the user's test
  phone and the WhatChimp dashboard, so it's deliberately not an agent task).
- **Type consistency:** store functions take `(conn, bot_id, phone, brand, ts)`
  / `(conn, bot_id, phone, ts)` consistently; `run_tick(conn, trigger_fn, now)`
  signature matches tests; `eligible(conn, now, min_age_s, max_age_s)` matches
  both callers.
- **Deliberate exclusions (YAGNI):** no Slack/Telegram notify (user chose
  WhatChimp-only assignment), no Caddy route (localhost service), no backfill,
  no per-brand fire-time override (single env knob is enough until asked).
```
