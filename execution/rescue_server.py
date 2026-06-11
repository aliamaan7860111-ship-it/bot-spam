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
# bot_flow_unique_id comes from Bot Manager UI ("Chat Rescue" flow per bot; see
# directives/window_rescue.md). All 5 brands verified and enabled 2026-06-11:
# flows compiled (trigger keyword required!), trigger-bot tested, button taps
# captured live. Disable a brand here if its flow is edited/broken.
RESCUE_CONFIG = {
    "381990": {"brand": "VIREX UAE", "phone_number_id": "1073890042476443",
               "bot_flow_unique_id": "1900212", "enabled": True},
    "382073": {"brand": "DIALO UAE", "phone_number_id": "1002123586328400",
               "bot_flow_unique_id": "1900210", "enabled": True},
    "382036": {"brand": "AMARA", "phone_number_id": "1045332455333591",
               "bot_flow_unique_id": "1900209", "enabled": True},
    "352261": {"brand": "ELARA", "phone_number_id": "1031340813395459",
               "bot_flow_unique_id": "1900207", "enabled": True},
    # LUNE bot_id captured live 2026-06-10 from the rescue log (first teed event).
    "382778": {"brand": "LUNE", "phone_number_id": "1138942462625909",
               "bot_flow_unique_id": "1900211", "enabled": True},
}

def resolve_config(bot_id: str) -> dict | None:
    """Resolve brand config by whatsapp_bot_id, falling back to phone_number_id —
    payloads from some bots put phone_number_id where whatsapp_bot_id usually goes
    (rpgrq's brand mapping has the same fallback)."""
    cfg = RESCUE_CONFIG.get(bot_id)
    if cfg:
        return cfg
    for c in RESCUE_CONFIG.values():
        if c["phone_number_id"] == bot_id:
            return c
    return None

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
    else:
        # Captured live 2026-06-11: button taps arrive as '#Button Reply#<button title>'.
        # Only OUR rescue buttons are non-arming; other flows' taps (e.g. AC recovery
        # 'Complete Order') stay real inbounds — they're genuine customer interactions.
        norm = text.lower().strip()
        if norm.startswith("#button reply#"):
            norm = norm[len("#button reply#"):].strip()
            kind = "button_tap" if norm in BUTTON_TEXTS else "real_inbound"
        elif norm in BUTTON_TEXTS:
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
        # INFO on purpose: this line is how button postback strings get captured
        # during go-live (directive step 2) — a tap that logs here instead of as
        # a button tap means its string belongs in RESCUE_EXTRA_BUTTON_TEXTS.
        log.info(f"📥 inbound on {ev['brand']} / {ev['phone']} (bot_id={ev['bot_id']}): {ev['text'][:80]!r}")
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
    # Footgun found live 2026-06-10: trigger-bot can return status "1" WITH the
    # error message "Something went wrong when trigger the bot flow". So status
    # alone isn't trustworthy here — require the success message too (which is
    # "Bot has been trigger successfully.", their typo).
    message = str(data.get("message") or "")
    if str(data.get("status")) == "1" and "success" in message.lower():
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
        cfg = resolve_config(row["bot_id"])
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
