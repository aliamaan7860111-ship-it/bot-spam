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
    """One scheduler pass. Returns the number of rescues fired.

    trigger_fn is injected so tests can fake the WhatChimp call; production
    passes trigger_bot_flow.
    """
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
