"""
order_bridge.py
===============
Main bridge script: connects Notion CRM to Telegram for order management.

Runs two concurrent tasks:
    1. Notion Poller — checks for newly confirmed orders every 30s,
       sends them to the Telegram sourcing group
    2. Telegram Bot — listens for #cost, #note, #reset, #status commands and /print
       in the sourcing group

Uses a local JSON file (.tmp/notified_orders.json) to track which orders
have already been sent — no Notion database modifications required.

Usage:
    python execution/order_bridge.py             # run the bridge
    python execution/order_bridge.py --test      # test connections only
    python execution/order_bridge.py --poll-once  # poll once and exit

Requires in .env:
    NOTION_API_KEY, NOTION_DATABASE_ID,
    TELEGRAM_BOT_TOKEN, TELEGRAM_FULFILLMENT_GROUP_ID
"""

import os
import sys
import json
import asyncio
import argparse
import logging
import signal
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# Resolve paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

# Load .env
load_dotenv(PROJECT_ROOT / ".env")

# Local imports
import notion_client as notion
import telegram_client as tg

# --- TJR Logistics integration (own-driver labels, separate from Filex) ---
# Defensive: if the TJR package isn't deployed yet, the bot still starts and the
# TJR step is simply skipped (guarded by _TJR_AVAILABLE in cmd_print_all).
try:
    sys.path.insert(0, os.environ.get("TJR_PACKAGE_PATH", r"C:\Users\PMLS\Desktop\Personal\tjr-logistics"))
    from tjr_core.run_print import process_private_driver_orders
    from tjr_core.supabase_repo import SupabaseOrderRepository
    _TJR_AVAILABLE = True
except Exception as _tjr_err:
    _TJR_AVAILABLE = False
    logging.getLogger("order_bridge").warning("TJR integration unavailable: %s", _tjr_err)

# Private-driver orders are SKIPPED in /print all for now (no Filex, no TJR label).
# Set TJR_PRINT_ENABLED=1 to re-enable routing them to TJR own-driver labels.
_TJR_PRINT_ENABLED = os.getenv("TJR_PRINT_ENABLED", "0") == "1"
import error_reporter  # loud, central alerting on TJR failures/skips (never silent)
import whatchimp_client as wc
import filex_status_mapper

# nc alias used by /filex_webhook handler — kept distinct from `notion`
# so future swaps stay localized.
nc = notion

FILEX_WEBHOOK_TOKEN = os.getenv("FILEX_WEBHOOK_TOKEN")

# Filex's eventTime arrives without a tz suffix; empirically it tracks PKT.
# Tagging it as UTC made stored Last Update render 5h ahead in Notion.
FILEX_TZ = timezone(timedelta(hours=5))


def _parse_filex_dt(dt_str: str):
    """Parse Filex's '2026-04-15T00:00:00' format. Naive values are PKT.
    Returns a tz-aware datetime, or None on failure."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=FILEX_TZ)
        return dt
    except ValueError:
        return None

from telegram import Bot
from telegram.ext import Application, CommandHandler
from telegram.error import TimedOut, TelegramError

from filex_client import FilexClient
from filex_payload_builder import build_payload, build_merged_payload, ValidationError


async def _safe_send_message(bot, chat_id: int, text: str) -> None:
    """Send a Telegram message, log timeouts/errors instead of crashing."""
    try:
        await bot.send_message(chat_id, text)
    except (TimedOut, TelegramError) as e:
        log.warning("Telegram send_message failed: %s | text=%s", e, text[:100])


async def _safe_send_document(bot, chat_id: int, document: bytes, filename: str, **kwargs) -> bool:
    """Send a Telegram document, log timeouts/errors instead of crashing. Returns True on success."""
    try:
        await bot.send_document(chat_id, document=document, filename=filename, **kwargs)
        return True
    except (TimedOut, TelegramError) as e:
        log.error("Telegram send_document failed: %s | filename=%s", e, filename)
        return False

# ---------------------------------------------------------------------------
# Multi-Brand Configuration
# ---------------------------------------------------------------------------
BRAND_MAP = {
    "PT": "Elara",
    "AM": "Amara's Room",
    "VX": "Virex UAE",
    "Di": "Dialo UAE",
    "LU": "Lune Collection",
    "R": "Rimal UAE",
    "O": "Orlento",
    "VL": "Velix",
    "E": "Elara",
    "DX": "Diwan",
    "PV": "Pelvini",
    "VS": "Viresta",
}

def get_brand_from_order_id(order_id: str) -> str:
    """Extracts the brand name from the order ID prefix (2-char first, then 1-char e.g. Rimal 'R')."""
    if not isinstance(order_id, str):
        return "Elara"
    return BRAND_MAP.get(order_id[:2]) or BRAND_MAP.get(order_id[:1], "Elara")


async def notion_write_with_retry(
    write_fn,
    *args,
    attempts: int = 3,
    base_delay: float = 1.0,
    description: str = "Notion write",
) -> bool:
    """
    Call a synchronous Notion write function with exponential backoff.
    Returns True on success, False if all attempts fail.

    Delays between attempts: base_delay, base_delay*2, base_delay*4
    (so 1s, 2s, 4s by default).
    """
    for attempt in range(1, attempts + 1):
        try:
            ok = write_fn(*args)
            if ok:
                return True
            log.warning(f"  {description}: attempt {attempt} returned False")
        except Exception as e:
            log.warning(f"  {description}: attempt {attempt} raised {e!r}")

        if attempt < attempts:
            delay = base_delay * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

    log.error(f"  {description}: all {attempts} attempts failed")
    return False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

TMP_DIR = PROJECT_ROOT / ".tmp"
TMP_DIR.mkdir(exist_ok=True)


def setup_logging() -> logging.Logger:
    """Set up logging to console + file."""
    logger = logging.getLogger("order_bridge")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers on reimport
    if logger.handlers:
        return logger

    # Console — INFO
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S"))

    # File — DEBUG
    fh = logging.FileHandler(TMP_DIR / "order_bridge.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"))

    logger.addHandler(ch)
    logger.addHandler(fh)

    # Also configure sub-loggers
    for name in ["order_bridge.notion", "order_bridge.telegram", "whatchimp_client"]:
        sub = logging.getLogger(name)
        sub.setLevel(logging.DEBUG)

    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Tracking happens via ORDER STATUS transitions (Confirmed | Processing -> Processed).
# ALBUMS SENT counter (Notion) gives per-album resume on multi-album orders.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Poll interval
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# In-memory lock: prevents duplicate sends when Notion API is slow to update
_sending_in_progress: set[str] = set()


# ---------------------------------------------------------------------------
# Notion Poller
# ---------------------------------------------------------------------------

async def poll_notion_once(bot: Bot) -> int:
    """
    Poll Notion for orders with status `Confirmed | Processing`, send each directly
    to the fulfillment group as an atomic media album (caption on the last album).
    Resumes from ALBUMS SENT on multi-album orders. Flips status to `Processed`
    only when all albums for the order have landed.

    Returns number of orders fully delivered this cycle.
    """
    fulfillment_group = tg.TELEGRAM_FULFILLMENT_GROUP_ID
    if not fulfillment_group:
        log.error("TELEGRAM_FULFILLMENT_GROUP_ID is not set")
        return 0

    orders = notion.query_confirmed_orders()
    if not orders:
        return 0

    # Filter out orders currently being sent (in-memory lock guards against re-poll race).
    new_orders = [
        o for o in orders
        if o.get("order_id", "") not in _sending_in_progress
    ]
    if not new_orders:
        return 0

    log.info(f"Found {len(new_orders)} order(s) needing fulfillment send")

    processed = 0
    for order in new_orders:
        order_id = order.get("order_id", "?")
        _sending_in_progress.add(order_id)

        try:
            start_album_index = order.get("albums_sent", 0) or 0
            page_id = order["page_id"]

            # Build the per-album checkpoint callback. Writes ALBUMS SENT (always)
            # and FULFILLMENT MESSAGE ID (only when the caption-bearing album lands).
            async def on_album_sent(new_count: int, caption_msg_id):
                await notion_write_with_retry(
                    notion.update_albums_sent,
                    page_id, new_count,
                    description=f"ALBUMS SENT={new_count} for {order_id}",
                )
                if caption_msg_id is not None:
                    await notion_write_with_retry(
                        notion.update_fulfillment_message_id,
                        page_id, caption_msg_id,
                        description=f"FULFILLMENT MESSAGE ID={caption_msg_id} for {order_id}",
                    )

            log.info(f"📤 Sending order {order_id} to fulfillment group (resume from album {start_album_index})...")

            send_result = await tg.send_order_to_group(
                bot, fulfillment_group, order,
                header="✅ NEW ORDER FOR FULFILLMENT",
                start_album_index=start_album_index,
                on_album_sent=on_album_sent,
            )

            if not send_result["success"]:
                log.error(f"  ✗ Send failed for {order_id} — will retry next poll")
                continue

            # Final flip to Processed only when all planned albums are accounted for.
            total_albums = send_result.get("total_albums", 0)
            done_count = start_album_index + send_result.get("albums_sent_this_call", 0)
            if total_albums == 0 or done_count >= total_albums:
                ok = await notion_write_with_retry(
                    notion.mark_order_processed,
                    page_id,
                    description=f"status->Processed for {order_id}",
                )
                if ok:
                    log.info(f"  ✓ Order {order_id} fully delivered, status -> Processed")
                    processed += 1
                else:
                    try:
                        await bot.send_message(
                            chat_id=fulfillment_group,
                            text=f"⚠️ {order_id}: status flip to Processed failed after 3 retries (will retry on next poll)",
                        )
                    except Exception:
                        pass
            else:
                log.warning(f"  Partial send for {order_id}: {done_count}/{total_albums} albums sent — next poll resumes")

            # Image count vs items mismatch warning (only once, when fully done)
            delivered = send_result.get("image_count", 0)
            expected = send_result.get("expected_count", 0)
            if (total_albums == 0 or done_count >= total_albums) and expected > 0 and delivered < expected:
                missing = expected - delivered
                try:
                    await bot.send_message(
                        chat_id=fulfillment_group,
                        text=f"⚠️ {order_id}: {delivered} of {expected} images sent, {missing} missing",
                    )
                except Exception as e:
                    log.error(f"  Failed to post image-count warning: {e}")

        finally:
            _sending_in_progress.discard(order_id)
            # Rate limit between orders to keep well below Telegram per-chat limits.
            if processed < len(new_orders):
                await asyncio.sleep(1.5)

    return processed


async def notion_poller_loop(bot: Bot):
    """Continuously poll Notion every POLL_INTERVAL_SECONDS."""
    log.info(f"🔄 Notion poller started (Cutoff: {ORDER_CUTOFF_DATE_STR})")

    while True:
        try:
            count = await poll_notion_once(bot)
            if count > 0:
                log.info(f"  Processed {count} order(s) this cycle")
        except Exception as e:
            log.error(f"Poller error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# (WhatsApp logic moved to dedicated whatsapp_confirmation_bot.py)


# ---------------------------------------------------------------------------
# Health Check Server (for cloud hosting / uptime monitoring)
# ---------------------------------------------------------------------------

# Only process orders created after this date (from .env)
# Using specific key for Telegram
ORDER_CUTOFF_DATE_STR = os.getenv("TELEGRAM_ORDER_CUTOFF_DATE", os.getenv("ORDER_CUTOFF_DATE", "2026-02-23T17:23:00+05:00"))
ORDER_CUTOFF_DATE = datetime.fromisoformat(ORDER_CUTOFF_DATE_STR)

async def start_health_server():
    """
    Tiny HTTP server for health checks. UptimeRobot
    pings this to verify the service is alive.
    Handles GET and HEAD requests.
    """
    port = int(os.getenv("PORT", "8080"))
    max_retries = 5
    retry_delay = 5

    async def handle_request(reader, writer):
        try:
            data = await reader.read(4096)
            if not data:
                return
                
            request_text = data.decode('utf-8', errors='ignore')
            lines = request_text.split('\r\n')
            if not lines:
                return
                
            # Parse method and path (e.g. "GET / HTTP/1.1" or "HEAD / HTTP/1.1")
            first_line = lines[0].split()
            if not first_line:
                return
                
            method = first_line[0].upper()
            path = first_line[1] if len(first_line) > 1 else "/"
            
            # --- Webhook Handling ---
            if "/filex_webhook" in path:
                log.info(f"📦 Filex Webhook ({method}): {path}")

                # Filex only sends POST. Reject everything else with a 405-ish
                # 200 (we never want Filex to retry on shape mismatch).
                if method != "POST":
                    body = json.dumps({
                        "code": 200,
                        "isUpdeted": False,
                        "message": f"Method {method} not supported",
                    })
                    status_line = "HTTP/1.1 200 OK\r\n"
                    headers = (
                        f"Content-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        f"Connection: close\r\n"
                        f"\r\n"
                    )
                else:
                    # 1. Read body
                    body_text = ""
                    try:
                        content_len = 0
                        try:
                            header_end_idx = lines.index("")
                        except ValueError:
                            header_end_idx = len(lines)

                        for line in lines[1:header_end_idx]:
                            if line.lower().startswith("content-length:"):
                                content_len = int(line.split(":")[1].strip())

                        body_parts = request_text.split('\r\n\r\n', 1)
                        if len(body_parts) > 1:
                            body_text = body_parts[1]

                        if content_len > 0 and len(body_text.encode()) < content_len:
                            remaining = content_len - len(body_text.encode())
                            try:
                                extra_bytes = await asyncio.wait_for(
                                    reader.readexactly(remaining), timeout=2.0
                                )
                                body_text += extra_bytes.decode()
                            except Exception:
                                pass
                    except Exception as e:
                        log.debug(f"  filex_webhook: error reading body: {e}")

                    # 2. Parse JSON
                    try:
                        payload = json.loads(body_text or "{}")
                        if not isinstance(payload, dict):
                            raise ValueError("payload is not an object")
                    except (ValueError, json.JSONDecodeError) as e:
                        log.warning(f"  filex_webhook: bad JSON — {e}")
                        body = json.dumps({
                            "code": 400,
                            "isUpdeted": False,
                            "message": f"Bad JSON: {e}",
                        })
                        status_line = "HTTP/1.1 400 Bad Request\r\n"
                        headers = (
                            f"Content-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\n"
                            f"Connection: close\r\n"
                            f"\r\n"
                        )
                        response = status_line + headers + body
                        writer.write(response.encode())
                        await writer.drain()
                        return

                    # 3. Verify token
                    if not FILEX_WEBHOOK_TOKEN or payload.get("Token") != FILEX_WEBHOOK_TOKEN:
                        # peer info for log forensics
                        peer = "?"
                        try:
                            peer_info = writer.get_extra_info("peername")
                            if peer_info:
                                peer = f"{peer_info[0]}:{peer_info[1]}"
                        except Exception:
                            pass
                        # X-Forwarded-For header takes priority if present
                        for line in lines[1:]:
                            if line.lower().startswith("x-forwarded-for:"):
                                peer = line.split(":", 1)[1].strip()
                                break
                        log.warning(f"  filex_webhook: token mismatch from {peer}")
                        body = json.dumps({
                            "code": 401,
                            "isUpdeted": False,
                            "message": "Unauthorized",
                        })
                        status_line = "HTTP/1.1 401 Unauthorized\r\n"
                        headers = (
                            f"Content-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\n"
                            f"Connection: close\r\n"
                            f"\r\n"
                        )
                        response = status_line + headers + body
                        writer.write(response.encode())
                        await writer.drain()
                        return

                    shipper_ref = (payload.get("ShipperRef") or "").strip()
                    status_text = (payload.get("Status") or "").strip()
                    notes = (payload.get("Notes") or "").strip()
                    order_date = (payload.get("OrderDate") or "").strip()

                    log.info(
                        f"  ✓ Filex update: ShipperRef={shipper_ref!r} "
                        f"Status={status_text!r} OrderDate={order_date!r}"
                    )

                    # 4. Look up Notion order
                    order = None
                    if shipper_ref:
                        try:
                            order = nc.find_order_by_shipper_ref(shipper_ref)
                        except Exception as e:
                            log.error(f"  filex_webhook: Notion lookup failed for {shipper_ref}: {e}")

                    incoming_tn = (payload.get("TrackingNo") or payload.get("Track_id") or "").strip()

                    # If ShipperRef didn't match, fall back to tracking number lookup.
                    # This handles cases where Notion stores the ORDER ID with formatting that
                    # differs from what we sent to Filex (e.g. "WA 67" in Notion vs "WA67" sent).
                    if not order and incoming_tn:
                        try:
                            linked = nc.query_orders_by_tracking(incoming_tn)
                        except Exception as e:
                            log.error(
                                f"  filex_webhook: fallback tracking lookup failed for {incoming_tn}: {e}"
                            )
                            linked = []
                        if linked:
                            order = linked[0]
                            log.info(
                                "  filex_webhook: ShipperRef %r not found, but tracking %s matched %d Notion row(s)",
                                shipper_ref, incoming_tn, len(linked),
                            )

                    if order is None:
                        log.warning(
                            f"  filex_webhook: unknown ShipperRef {shipper_ref!r} and tracking {incoming_tn!r}"
                        )
                        body = json.dumps({
                            "code": 200,
                            "isUpdeted": False,
                            "message": f"Unknown ShipperRef {shipper_ref}",
                        })
                        status_line = "HTTP/1.1 200 OK\r\n"
                        headers = (
                            f"Content-Type: application/json\r\n"
                            f"Content-Length: {len(body)}\r\n"
                            f"Connection: close\r\n"
                            f"\r\n"
                        )
                        response = status_line + headers + body
                        writer.write(response.encode())
                        await writer.drain()
                        return

                    # 5. Stale-event guard
                    incoming_dt = _parse_filex_dt(order_date)
                    stored_last_update = order.get("last_update") or ""
                    if incoming_dt and stored_last_update:
                        try:
                            stored_dt = datetime.fromisoformat(
                                stored_last_update.replace("Z", "+00:00")
                            )
                            if stored_dt.tzinfo is None:
                                stored_dt = stored_dt.replace(tzinfo=timezone.utc)
                            if incoming_dt < stored_dt:
                                log.info(
                                    f"  filex_webhook: stale event for {shipper_ref} "
                                    f"({incoming_dt.isoformat()} < {stored_dt.isoformat()})"
                                )
                                body = json.dumps({
                                    "code": 200,
                                    "isUpdeted": True,
                                    "message": "Stale event ignored",
                                })
                                status_line = "HTTP/1.1 200 OK\r\n"
                                headers = (
                                    f"Content-Type: application/json\r\n"
                                    f"Content-Length: {len(body)}\r\n"
                                    f"Connection: close\r\n"
                                    f"\r\n"
                                )
                                response = status_line + headers + body
                                writer.write(response.encode())
                                await writer.drain()
                                return
                        except (ValueError, AttributeError):
                            # malformed stored date — proceed with update
                            pass

                    # 6. Find all linked rows (orders sharing the same tracking number — merged shipments)
                    incoming_tn = (payload.get("TrackingNo") or payload.get("Track_id") or "").strip()
                    all_orders_to_update = [order]
                    if incoming_tn:
                        try:
                            linked = nc.query_orders_by_tracking(incoming_tn)
                        except Exception as e:
                            log.error(
                                f"  filex_webhook: tracking lookup failed for {incoming_tn}: {e}"
                            )
                            linked = []
                        for o in linked:
                            if o["page_id"] != order["page_id"]:
                                all_orders_to_update.append(o)
                        if len(all_orders_to_update) > 1:
                            log.info(
                                f"  ↳ Fanout: {len(all_orders_to_update)} linked rows share "
                                f"tracking {incoming_tn} (refs: "
                                f"{[o.get('order_id') for o in all_orders_to_update]})"
                            )

                    # 7. Map status and update Notion (across all linked rows)
                    current_filex_status = order.get("filex_status")
                    new_status = filex_status_mapper.map_status(status_text, current_filex_status)
                    new_order_status = filex_status_mapper.order_status_from_filex(
                        new_status, order.get("order_status"),
                    )

                    for target in all_orders_to_update:
                        page_id = target["page_id"]
                        target_ref = target.get("order_id") or shipper_ref
                        try:
                            if new_status:
                                nc.set_filex_status(page_id, new_status)
                            nc.set_filex_notes(page_id, notes)
                            if incoming_dt:
                                nc.set_last_update(page_id, incoming_dt.isoformat())
                            if new_order_status:
                                nc.update_order_status(page_id, new_order_status)
                            log.info(
                                f"  ✓ Notion updated for {target_ref}: "
                                f"FILEX STATUS → {new_status!r}"
                                + (f", ORDER STATUS → {new_order_status!r}" if new_order_status else "")
                            )
                        except Exception as e:
                            log.error(
                                f"  filex_webhook: Notion write failed for {target_ref}: {e}"
                            )

                    body = json.dumps({
                        "code": 200,
                        "isUpdeted": True,
                        "message": "Updated successfully",
                    })
                    status_line = "HTTP/1.1 200 OK\r\n"
                    headers = (
                        f"Content-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        f"Connection: close\r\n"
                        f"\r\n"
                    )

            elif "/whatchimp_webhook" in path:
                log.info(f"🔔 Incoming Webhook ({method}): {path}")
                log.info(f"  📦 RAW REQUEST:\n{request_text[:2000]}")
                
                # 1. Capture Query Parameters from URL
                params = {}
                if "?" in path:
                    query = path.split("?", 1)[1]
                    for pair in query.split("&"):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            params[k] = v
                
                # 2. Capture Parameters from POST body (JSON)
                if method == "POST":
                    try:
                        # Extract content after headers
                        content_len = 0
                        # Find the first blank line which separates headers from body
                        header_end_idx = 0
                        try:
                            header_end_idx = lines.index("")
                        except ValueError:
                            header_end_idx = len(lines)
                            
                        for line in lines[1:header_end_idx]:
                            if line.lower().startswith("content-length:"):
                                content_len = int(line.split(":")[1].strip())
                        
                        if content_len > 0:
                            # The body might already be in 'request_text' after the headers
                            # (separated by double \r\n)
                            body_parts = request_text.split('\r\n\r\n', 1)
                            if len(body_parts) > 1:
                                body_text = body_parts[1]
                            else:
                                body_text = ""
                                
                            # If we haven't read enough yet, read the rest
                            if len(body_text.encode()) < content_len:
                                remaining = content_len - len(body_text.encode())
                                try:
                                    extra_bytes = await asyncio.wait_for(reader.readexactly(remaining), timeout=2.0)
                                    body_text += extra_bytes.decode()
                                except:
                                    pass
                                    
                            log.debug(f"  POST Body: {body_text}")
                            
                            # Parse JSON if applicable
                            try:
                                post_data = json.loads(body_text)
                                if isinstance(post_data, dict):
                                    params.update(post_data)
                            except json.JSONDecodeError:
                                # Might be form-data? WhatChimp SKILL says it uses form-data too
                                for pair in body_text.split("&"):
                                    if "=" in pair:
                                        k, v = pair.split("=", 1)
                                        params[k] = v
                    except Exception as e:
                        log.debug(f"  Error reading POST body: {e}")
                
                log.info(f"  🔑 Parsed params: {params}")
                
                order_id = params.get("order_id")
                action = params.get("action")
                chat_id = params.get("chat_id") # Phone number from WhatChimp POST body

                # WhatChimp's `#order_id#` merge tag resolves to empty in flow webhook URLs
                # (the URL query arrives as `order_id=`), so the fallback below is the
                # primary resolution path for every button click. We identify the brand
                # from the payload's `postbackid` (unique per confirm button) or sender
                # phone, then scope the lookup to that brand's subscriber list only.
                brand_prefix = ""
                if not order_id and chat_id:
                    brand_prefix = wc.identify_brand_from_webhook(params)
                    if brand_prefix:
                        cfg = wc.BRAND_CONFIG[brand_prefix]
                        log.info(
                            f"  🔍 order_id missing — identified brand as {cfg['brand_display']} "
                            f"({brand_prefix}) via postbackid/sender. Looking up {chat_id} on "
                            f"phone_number_id {cfg['phone_number_id']}..."
                        )
                        try:
                            custom_fields = wc.get_subscriber_custom_fields(
                                chat_id, phone_number_id=cfg["phone_number_id"]
                            )
                            order_id = custom_fields.get("order_id")
                            if order_id:
                                log.info(f"    ✓ Recovered order_id from {brand_prefix} subscriber: {order_id}")
                            else:
                                log.warning(f"    ✗ {brand_prefix} subscriber has no order_id for {chat_id}")
                        except Exception as e:
                            log.error(f"    ✗ {brand_prefix} subscriber lookup failed: {e}")
                    else:
                        # Couldn't identify the brand — sweep all 5 as last resort
                        log.warning(
                            f"  ⚠️ Could not identify brand from webhook payload "
                            f"(postbackid={params.get('postbackid')!r}, "
                            f"bot={params.get('whatsapp_bot_username')!r}). "
                            f"Falling back to cross-brand sweep."
                        )
                        try:
                            custom_fields = wc.get_subscriber_custom_fields(chat_id)
                            order_id = custom_fields.get("order_id")
                            if order_id:
                                log.warning(f"    ⚠️ Cross-brand sweep returned: {order_id} (may be wrong brand!)")
                        except Exception as e:
                            log.error(f"    ✗ Cross-brand lookup failed: {e}")

                # Fallback: the subscriber had no synced order_id (e.g. the send-time
                # pre-sync to WhatChimp timed out). Recover the order straight from the
                # CRM by phone + brand so the confirm note is never silently lost.
                if not order_id and chat_id and brand_prefix:
                    try:
                        matches = notion.find_orders_by_phone(chat_id, brand_prefix=brand_prefix)
                        if matches:
                            order_id = matches[0].get("order_id")
                            log.info(
                                f"  🛟 Recovered order_id via CRM phone fallback: {order_id} "
                                f"({len(matches)} {brand_prefix} match(es) for {chat_id})"
                            )
                        else:
                            log.warning(f"  ✗ CRM phone fallback: no {brand_prefix} order for {chat_id}")
                    except Exception as e:
                        log.error(f"  ✗ CRM phone fallback failed: {e}")

                if order_id and (action in ("confirm", "process") or not action):
                    # Default action to 'process' if missing but we found an order_id
                    current_action = action or "process"
                    log.info(f"  ✓ Processing button click for order {order_id}")
                    
                    try:
                        # Look up the order in Notion
                        target_order = notion.find_order_by_id(order_id)
                        if target_order:
                            # Update the internal note to "Bot log: Confirmed"
                            success = notion.update_internal_note(target_order["page_id"], "Bot log: Confirmed")
                            if success:
                                log.info(f"    ✓ Notion updated: Note -> Bot log: Confirmed")
                                body = '{"status": 1, "message": "Updated Notion"}'
                            else:
                                log.error(f"    ✗ Failed to update Notion note")
                                body = '{"status": 0, "message": "Notion update failed"}'
                        else:
                            log.warning(f"    ⚠️ Order {order_id} not found in Notion")
                            body = '{"status": 1, "message": "Order not found but acknowledged"}'
                    except Exception as e:
                        log.error(f"    ✗ Notion search/update failed: {str(e)}")
                        body = '{"status": 0, "message": "Notion error"}'
                else:
                    log.warning(f"  ⚠️ Webhook received with insufficient params (Need order_id and action)")
                    body = '{"status": 1, "message": "Insufficient parameters"}'
                
                status_line = "HTTP/1.1 200 OK\r\n"
                headers = (
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
            else:
                body = "OK | Order Bridge is Running"
                status_line = "HTTP/1.1 200 OK\r\n"
                headers = (
                    f"Content-Type: text/plain\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                )
            
            response = status_line + headers
            # HEAD requests should only return status line and headers
            if method != "HEAD":
                response += body
                
            writer.write(response.encode())
            await writer.drain()
        except Exception as e:
            log.debug(f"Health server handler error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

    for attempt in range(max_retries):
        try:
            server = await asyncio.start_server(handle_request, "0.0.0.0", port)
            log.info(f"🌐 Health check server active on port {port} (supports GET/HEAD)")
            async with server:
                await server.serve_forever()
            break # Success
        except OSError as e:
            if e.errno in (98, 10048): # Port already in use
                if attempt < max_retries - 1:
                    log.warning(f"⚠️ Health server port {port} is in use (attempt {attempt+1}/{max_retries}). Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                else:
                    log.error(f"❌ Failed to start health server after {max_retries} attempts: Port {port} is still in use.")
            else:
                log.error(f"❌ Failed to start health server: {e}")
                break


# ---------------------------------------------------------------------------
# Filex /print all command
# ---------------------------------------------------------------------------

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")
FILEX_TRACKING_BASE  = os.getenv("FILEX_TRACKING_BASE_URL", "https://www.filexexpress.ae/track?awb=")

# tg.TELEGRAM_FULFILLMENT_GROUP_ID is a string; coerce here for chat-id compares.
try:
    FULFILLMENT_GROUP_ID = int(tg.TELEGRAM_FULFILLMENT_GROUP_ID) if tg.TELEGRAM_FULFILLMENT_GROUP_ID else 0
except (TypeError, ValueError):
    FULFILLMENT_GROUP_ID = 0

# Single shared Filex client (token cached across calls)
_filex_client: FilexClient | None = None


def get_filex_client() -> FilexClient:
    """Lazy-init a singleton FilexClient so its auth token is reused."""
    global _filex_client
    if _filex_client is None:
        _filex_client = FilexClient(
            FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE,
        )
    return _filex_client


def _lock_pages(page_ids_by_ref: dict[str, list[str]]) -> None:
    """Set Filex Submitted=✓ on every page in the lock set."""
    for page_ids in page_ids_by_ref.values():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, True)


def _unlock_pages(page_ids_by_ref: dict[str, list[str]]) -> None:
    """Revert Filex Submitted=☐ on every page in the lock set."""
    for page_ids in page_ids_by_ref.values():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, False)


def _write_tracking_to_notion(
    page_ids_by_ref: dict[str, list[str]],
    tracking_pairs: list[dict],
) -> list[str]:
    """Write tracking + status + timestamps to every Notion row that placed.
    Returns the list of tracking numbers that were written (in placebulk order)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    written = []
    for entry in tracking_pairs:
        ref = entry.get("barcode")
        tn = entry.get("tracking_no")
        page_ids = page_ids_by_ref.get(ref, [])
        if not page_ids:
            log.warning("Returned ref %s not in our locked set", ref)
            continue
        for page_id in page_ids:
            nc.set_tracking_info(page_id, tn, FILEX_TRACKING_BASE + tn)
            nc.set_filex_status(page_id, "Label Created")
            nc.set_dispatched_at(page_id, now_iso)
            nc.set_last_update(page_id, now_iso)
        if len(page_ids) > 1:
            log.info(f"  ↳ Wrote tracking {tn} to {len(page_ids)} merged Notion rows ({ref})")
        if tn:
            written.append(tn)
    return written


# Validation reason categories — must match prefixes in build_payload's ValidationError messages.
_SKIP_CATEGORIES: list[tuple[str, str]] = [
    ("missing city in address", "Missing city in address"),
    ("invalid total",            "Invalid total"),
    ("missing phone",            "Missing phone"),
    ("missing customer name",    "Missing customer name"),
    ("missing address",          "Missing address"),
]


def _categorize_validation_error(msg: str) -> str:
    """Map a ValidationError message to one of the fixed category headers."""
    for prefix, header in _SKIP_CATEGORIES:
        if msg.startswith(prefix):
            return header
    return "Other"


def _format_validation_skip_message(
    skips_by_category: dict[str, list[tuple[str, str]]],
) -> str:
    """Build the single Telegram message body for validation skips.

    skips_by_category maps the category header to a list of (order_id, reason_detail).
    """
    total = sum(len(v) for v in skips_by_category.values())
    lines = [
        f"⚠️ Skipped {total} order(s) — fix and re-run /print or use /print <ID>:",
        "",
    ]
    for _, header in _SKIP_CATEGORIES:
        items = skips_by_category.get(header, [])
        if not items:
            continue
        lines.append(f"{header} ({len(items)}):")
        for oid, detail in items:
            short = (detail[:60] + "…") if len(detail) > 60 else detail
            lines.append(f"  • {oid} — {short}")
        lines.append("")
    other = skips_by_category.get("Other", [])
    if other:
        lines.append(f"Other ({len(other)}):")
        for oid, detail in other:
            short = (detail[:60] + "…") if len(detail) > 60 else detail
            lines.append(f"  • {oid} — {short}")
    return "\n".join(lines).rstrip()


def _format_already_labeled_message(rows: list[dict]) -> str:
    """Build the single Telegram message body for 'already has Filex label' skips."""
    lines = [
        f"ℹ️ {len(rows)} order(s) already have a Filex label — verify and tick \"Filex Submitted\":",
    ]
    for r in rows:
        oid = r.get("order_id", "?")
        status = r.get("filex_status", "?") or "?"
        tn = r.get("tracking_number", "") or "(no tracking)"
        lines.append(f"  • {oid} — status: {status}, tracking: {tn}")
    return "\n".join(lines)


async def _send_long_message(bot, chat_id: int, body: str, fallback_filename: str) -> None:
    """Send a single Telegram message; fall back to .txt attachment if too long."""
    LIMIT = 3800  # defensive margin under Telegram's 4096 char cap
    if len(body) <= LIMIT:
        await _safe_send_message(bot, chat_id, body)
        return
    await _safe_send_document(
        bot,
        chat_id,
        document=body.encode("utf-8"),
        filename=fallback_filename,
    )


async def _run_private_driver_labels(bot, chat_id):
    """Create Private-Driver orders in TJR (assigned to Subhan) and send their
    upgraded dashboard labels. Only orders with Private Driver checked that are NOT
    yet labeled (Private Label Created=False) are pulled — and they're marked after,
    so the SAME orders are never pulled again. Returns the result dict, or None on a
    hard failure. Shared by /print all (batch) and /pvt all (standalone)."""
    try:
        _tjr = process_private_driver_orders(SupabaseOrderRepository(), os.environ["TJR_TENANT_ID"])
    except Exception as e:
        await _safe_send_message(bot, chat_id, f"⚠️ TJR label step FAILED — private-driver orders not printed: {e}")
        log.error("TJR label step failed", exc_info=True)
        error_reporter.report("TJR private-driver label step failed", error_type="tjr_print_failed", exc=e)
        return None
    if _tjr["pdf"]:
        await _safe_send_document(
            bot, chat_id,
            document=_tjr["pdf"],
            filename=f"TJR_labels_{datetime.now().strftime('%Y-%m-%d')}_{len(_tjr['created'])}.pdf",
            caption=f"✅ TJR Logistics: {len(_tjr['created'])} private-driver label(s), assigned to driver.",
        )
    if _tjr["skipped"]:
        _lines = "\n".join(f"• {s['ref']}: {s['reason']}" for s in _tjr["skipped"])
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ TJR could NOT label {len(_tjr['skipped'])} private-driver order(s) — handle manually:\n{_lines}",
        )
        error_reporter.report(
            f"TJR skipped {len(_tjr['skipped'])} private-driver order(s) — no label generated",
            error_type="tjr_skipped",
            context={"refs": [s["ref"] for s in _tjr["skipped"]]},
        )
    if _tjr.get("label_error"):
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {len(_tjr['created'])} order(s) created in TJR (assigned to Subhan) but the LABEL "
            f"render failed — pull them from the dashboard. Error: {_tjr['label_error']}",
        )
        error_reporter.report(
            "TJR dashboard label render failed",
            error_type="tjr_label_fetch_failed",
            context={"created": len(_tjr["created"]), "error": _tjr["label_error"]},
        )
    return _tjr


async def cmd_pvt(update, context):
    """Dispatch /pvt:
       - /pvt all → label ALL Private-Driver orders (assigned to the private driver),
         with NO Filex step. Only un-labeled ones are pulled; never re-pulls labeled ones.
       - /pvt     → usage hint
    """
    args = context.args or []
    chat_id = update.effective_chat.id
    if len(args) == 1 and args[0].lower() == "all":
        await cmd_pvt_all(update, context)
        return
    await _safe_send_message(
        context.bot, chat_id,
        "Usage:\n  /pvt all — label every Private Driver order (assigned to the private driver). No Filex.",
    )


async def cmd_pvt_all(update, context):
    """/pvt all — generate TJR labels for Private-Driver orders ONLY (no Filex)."""
    chat_id = update.effective_chat.id
    bot = context.bot
    if FULFILLMENT_GROUP_ID and chat_id != FULFILLMENT_GROUP_ID:
        await _safe_send_message(bot, chat_id, "/pvt is only available in the fulfillment group.")
        return
    if not _TJR_AVAILABLE:
        await _safe_send_message(bot, chat_id, "⚠️ TJR integration is not available.")
        return
    user_id = update.effective_user.id if update.effective_user else "?"
    log.info("/pvt all triggered by %s in chat %s", user_id, chat_id)
    _tjr = await _run_private_driver_labels(bot, chat_id)
    if _tjr is not None and not _tjr["pdf"] and not _tjr["skipped"] and not _tjr.get("label_error"):
        await _safe_send_message(bot, chat_id, "No new private-driver orders to label.")


async def cmd_print(update, context):
    """Dispatch /print:
       - /print all           → bulk placement
       - /print <ORDER_ID>    → single placement / label re-fetch
       - /print               → usage hint
       - /print AM 3030       → joined as 'AM 3030' for the Notion lookup
    """
    args = context.args or []
    if not args:
        chat_id = update.effective_chat.id
        await _safe_send_message(
            context.bot, chat_id,
            "Usage:\n  /print all          — print every Processed order\n"
            "  /print <ORDER_ID>   — print one order (e.g. /print AM3030)",
        )
        return
    if len(args) == 1 and args[0].lower() == "all":
        await cmd_print_all(update, context)
        return
    order_id = " ".join(args)
    await cmd_print_one(update, context, order_id)


async def cmd_print_all(update, context):
    """
    /print all  — place all eligible Notion orders in Filex, write back
    tracking info, send the combined airway-bill PDF to the fulfillment group.
    """
    chat_id = update.effective_chat.id
    bot = context.bot

    # Restrict to fulfillment group only
    if FULFILLMENT_GROUP_ID and chat_id != FULFILLMENT_GROUP_ID:
        await _safe_send_message(bot, chat_id, "/print is only available in the fulfillment group.")
        return

    user_id = update.effective_user.id if update.effective_user else "?"
    log.info("/print all triggered by %s in chat %s", user_id, chat_id)

    # 0. Private-driver orders -> TJR own-driver labels (assigned to Subhan), a
    #    SEPARATE PDF from the Filex flow below (Filex excludes these orders). The
    #    same step is runnable on its own via /pvt all. Failures are never swallowed.
    if _TJR_AVAILABLE and _TJR_PRINT_ENABLED:
        await _run_private_driver_labels(bot, chat_id)
    else:
        # TJR auto-step off here — just tell the team how many wait (use /pvt all).
        try:
            _pd = nc.query_private_driver_processed()
            if _pd:
                await _safe_send_message(bot, chat_id, f"ℹ️ {len(_pd)} private-driver order(s) not printed here — use /pvt all.")
        except Exception as e:
            log.warning("private-driver skip count failed: %s", e)

    # 1. Query every Processed order — no checkbox gates.
    eligible = nc.query_filex_processed()
    if not eligible:
        await _safe_send_message(bot, chat_id, "No orders with status Processed.")
        return

    # 2. Classify each row.
    skips_by_category: dict[str, list[tuple[str, str]]] = {}
    already_labeled: list[dict] = []
    to_place: list[dict] = []

    for order in eligible:
        if (order.get("filex_status") or "").strip():
            already_labeled.append(order)
            continue
        to_place.append(order)

    # 3. Group placeable orders by phone for auto-merge.
    from collections import defaultdict
    from whatchimp_client import clean_phone_number
    grouped: dict[str, list[dict]] = defaultdict(list)
    orphans: list[dict] = []
    for order in to_place:
        raw_phone = order.get("phone", "") or ""
        if not raw_phone:
            orphans.append(order)
            continue
        normalized = clean_phone_number(raw_phone)
        if not normalized:
            orphans.append(order)
            continue
        grouped[normalized].append(order)

    # 4. Build payloads — validation errors collected, not sent per-order.
    payloads: list[dict] = []
    page_ids_by_ref: dict[str, list[str]] = {}
    # Parallel mapping: ref -> list of original order dicts. For merged
    # shipments this gives us per-merged-order access to fulfillment_message_id
    # so we can post the same label PDF as a reply in EACH order's thread.
    orders_by_ref: dict[str, list[dict]] = {}

    def _record_skip(order_id: str, err_msg: str) -> None:
        category = _categorize_validation_error(err_msg)
        skips_by_category.setdefault(category, []).append((order_id, err_msg))

    for phone, group in grouped.items():
        if len(group) == 1:
            order = group[0]
            try:
                payload = build_payload(order)
            except ValidationError as e:
                _record_skip(order.get("order_id", "?"), str(e))
                continue
            payloads.append(payload)
            page_ids_by_ref[payload["ShipperRef"]] = [order["page_id"]]
            orders_by_ref[payload["ShipperRef"]] = [order]
        else:
            try:
                merged_payload = build_merged_payload(group)
            except ValidationError as e:
                # Merged group fails on the FIRST order's validation; report all members.
                for o in group:
                    _record_skip(o.get("order_id", "?"), str(e))
                continue
            payloads.append(merged_payload)
            page_ids_by_ref[merged_payload["ShipperRef"]] = [o["page_id"] for o in group]
            orders_by_ref[merged_payload["ShipperRef"]] = list(group)
            log.info(
                f"  ↳ Merging {len(group)} orders for phone {phone}: "
                f"{[o.get('order_id') for o in group]}"
            )

    for order in orphans:
        try:
            payload = build_payload(order)
        except ValidationError as e:
            _record_skip(order.get("order_id", "?"), str(e))
            continue
        payloads.append(payload)
        page_ids_by_ref[payload["ShipperRef"]] = [order["page_id"]]
        orders_by_ref[payload["ShipperRef"]] = [order]

    # 5. Send the two skip messages (single API call each).
    if skips_by_category:
        body = _format_validation_skip_message(skips_by_category)
        await _send_long_message(bot, chat_id, body, "validation_skips.txt")
    if already_labeled:
        body = _format_already_labeled_message(already_labeled)
        await _send_long_message(bot, chat_id, body, "already_labeled.txt")

    if not payloads:
        await _safe_send_message(bot, chat_id, "No labels generated — all eligible orders were skipped.")
        return

    # 6. Lock, place, update.
    _lock_pages(page_ids_by_ref)
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        _unlock_pages(page_ids_by_ref)
        await _safe_send_message(bot, chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    tracking_pairs = result.get("trackingnos", [])
    tracking_numbers = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)

    # 7. Fetch a single combined PDF for all placed shipments and post it
    # with a summary caption (totals + merged groups).
    if not tracking_numbers:
        await _safe_send_message(
            bot, chat_id,
            "⚠️ Filex returned 'success' but no tracking numbers — verify in Filex portal.",
        )
        return

    # Count orders vs shipments. Mergers collapse N orders into 1 shipment.
    total_orders = 0
    merge_groups: list[str] = []
    for entry in tracking_pairs:
        if not entry.get("tracking_no"):
            continue
        ref = entry.get("barcode")
        merged = orders_by_ref.get(ref) or []
        total_orders += len(merged) if merged else 1
        if len(merged) > 1:
            merge_groups.append(" + ".join(o.get("order_id", "?") for o in merged))
    total_shipments = len(tracking_numbers)

    try:
        pdf_bytes = client.get_label_pdf(tracking_numbers)
    except Exception as e:
        await _safe_send_message(
            bot, chat_id,
            f"✅ Placed {total_orders} order(s) into {total_shipments} shipment(s), "
            f"but label PDF fetch failed: {e}\n"
            f"Use /print <ID> for each order to retrieve the label.",
        )
        log.error("filex label pdf fetch failed", exc_info=True)
        return

    summary = f"✅ Placed {total_orders} order(s) into {total_shipments} shipment(s). Filex labels attached."
    if merge_groups:
        merge_block = "\n".join(f"• {g}" for g in merge_groups)
        caption_full = f"{summary}\n\nMerged shipments:\n{merge_block}"
    else:
        caption_full = summary

    # Telegram caption hard cap is 1024 chars. If we overflow, send a short
    # caption and post the merged-groups list as a separate follow-up message.
    follow_up: str | None = None
    if len(caption_full) > 1024:
        caption_to_send = summary
        follow_up = "Merged shipments:\n" + "\n".join(f"• {g}" for g in merge_groups)
    else:
        caption_to_send = caption_full

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"filex_labels_{today}_{total_shipments}.pdf"
    await _safe_send_document(
        bot, chat_id,
        document=pdf_bytes,
        filename=filename,
        caption=caption_to_send,
    )
    if follow_up:
        await _safe_send_message(bot, chat_id, follow_up)
    log.info(
        "/print all completed: %d orders, %d shipments, PDF %d bytes, %d merged groups",
        total_orders, total_shipments, len(pdf_bytes), len(merge_groups),
    )


def _resolve_order_id(raw: str) -> dict | None:
    """Look up an order in Notion, tolerating WA127/WA 127 spelling variants.

    Returns the parsed order dict on first match, or None if no variant matches.
    """
    candidates = [raw]
    if " " not in raw and len(raw) >= 3 and raw[:2].isalpha() and raw[2:3].isdigit():
        candidates.append(f"{raw[:2]} {raw[2:]}")
    if " " in raw:
        candidates.append(raw.replace(" ", ""))
    seen = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        order = nc.find_order_by_shipper_ref(cand)
        if order:
            return order
    return None


async def cmd_print_one(update, context, order_id: str) -> None:
    """/print <ORDER_ID> — single-order placement OR label re-fetch."""
    chat_id = update.effective_chat.id
    bot = context.bot

    if FULFILLMENT_GROUP_ID and chat_id != FULFILLMENT_GROUP_ID:
        await _safe_send_message(bot, chat_id, "/print is only available in the fulfillment group.")
        return

    user_id = update.effective_user.id if update.effective_user else "?"
    log.info("/print %r triggered by %s in chat %s", order_id, user_id, chat_id)

    # 1. Lookup with spelling-variant tolerance.
    order = _resolve_order_id(order_id)
    if not order:
        await _safe_send_message(bot, chat_id, f"⚠️ Order {order_id} not found in CRM.")
        return

    canonical_id = order.get("order_id") or order_id

    # 2. Status gate.
    status = (order.get("order_status") or "").strip()
    if status != "Processed":
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} status is \"{status or '(blank)'}\" — only \"Processed\" orders can be printed.",
        )
        return

    client = get_filex_client()

    # 3. Already-labeled branch: re-fetch existing label, no placement.
    filex_status = (order.get("filex_status") or "").strip()
    if filex_status:
        tn = (order.get("tracking_number") or "").strip()
        if not tn:
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {canonical_id} has FILEX STATUS but no tracking number. Manual investigation needed.",
            )
            return
        try:
            pdf_bytes = client.get_label_pdf([tn])
        except Exception as e:
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {canonical_id} label fetch failed: {e}",
            )
            log.error("/print %s label fetch failed", canonical_id, exc_info=True)
            return
        today = datetime.now().strftime("%Y-%m-%d")
        await _safe_send_document(
            bot, chat_id,
            document=pdf_bytes,
            filename=f"{canonical_id.replace(' ', '_')}_{today}.pdf",
            caption=f"ℹ️ {canonical_id} already has a Filex label. Status: {filex_status}. Tracking: {tn}.",
        )
        return

    # 4. Fresh placement branch.
    try:
        payload = build_payload(order)
    except ValidationError as e:
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} cannot be placed: {e}. Fix the order in CRM and retry.",
        )
        return

    page_ids_by_ref = {payload["ShipperRef"]: [order["page_id"]]}
    _lock_pages(page_ids_by_ref)
    try:
        result = client.place_orders([payload])
    except Exception as e:
        _unlock_pages(page_ids_by_ref)
        await _safe_send_message(bot, chat_id, f"⚠️ {canonical_id} Filex submission failed: {e}")
        log.error("/print %s placebulk failed", canonical_id, exc_info=True)
        return

    tracking_pairs = result.get("trackingnos", [])
    written = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)
    if not written:
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} placed but Filex returned no tracking number — verify in Filex portal.",
        )
        return

    tn = written[0]
    try:
        pdf_bytes = client.get_label_pdf([tn])
    except Exception as e:
        await _safe_send_message(
            bot, chat_id,
            f"✅ {canonical_id} placed (tracking: {tn}), but label fetch failed: {e}",
        )
        log.error("/print %s label fetch failed after placement", canonical_id, exc_info=True)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    await _safe_send_document(
        bot, chat_id,
        document=pdf_bytes,
        filename=f"{canonical_id.replace(' ', '_')}_{today}.pdf",
        caption=f"✅ {canonical_id} placed. Tracking: {tn}.",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_bridge():
    """Run both the Notion poller and Telegram bot concurrently."""
    log.info("=" * 60)
    log.info("  Notion ↔ Telegram Order Bridge")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)
    log.info("")

    # Build the Telegram application
    app = Application.builder().token(tg.TELEGRAM_BOT_TOKEN).build()

    # Register command handlers (pass notion_client module)
    handlers = tg.create_command_handlers(notion)
    for handler in handlers:
        app.add_handler(handler)

    # Filex /print command (fulfillment group only — guarded inside the handler)
    app.add_handler(CommandHandler("print", cmd_print))
    app.add_handler(CommandHandler("pvt", cmd_pvt))

    # Initialize the application and get bot
    await app.initialize()
    bot = app.bot

    # Test connections
    me = await bot.get_me()
    log.info(f"✓ Telegram bot: @{me.username}")

    notion_ok = notion.test_connection()
    if not notion_ok:
        log.error("❌ Notion connection failed. Check your NOTION_API_KEY and NOTION_DATABASE_ID.")
        return

    # Show tracking info message
    log.info("  Native Notion Checkbox tracking is ENABLED (replaces local JSON)")

    log.info("")
    log.info("🚀 Bridge is running! Listening for orders...")
    log.info(f"   Polling Notion every {POLL_INTERVAL_SECONDS}s for Confirmed orders")
    log.info("   Listening for #cost, #note, #reset, #status commands and /print in Telegram")
    log.info("   Press Ctrl+C to stop")
    log.info("")

    # Start the bot's updater (for receiving messages)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Run the Notion poller as a concurrent task
    poller_task = asyncio.create_task(notion_poller_loop(bot))

    # Start health check server (for uptime monitoring)
    health_task = asyncio.create_task(start_health_server())

    try:
        # Keep running until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
    finally:
        poller_task.cancel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Bridge stopped.")


async def run_test():
    """Test connections only."""
    log.info("Testing connections...")
    log.info("")

    # Test Notion
    notion_ok = notion.test_connection()
    log.info("")

    # Test Telegram
    telegram_ok = await tg.test_connection()
    log.info("")

    if notion_ok and telegram_ok:
        log.info("✅ All connections OK — ready to run!")
    else:
        log.error("❌ Some connections failed. Check your .env settings.")


async def run_poll_once():
    """Poll once and exit (useful for testing)."""
    log.info("Polling Notion once...")

    bot = Bot(token=tg.TELEGRAM_BOT_TOKEN)

    count = await poll_notion_once(bot)
    log.info(f"Done. Processed {count} order(s).")


def main():
    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    parser = argparse.ArgumentParser(
        description="Notion ↔ Telegram Order Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python execution/order_bridge.py             # run the bridge
  python execution/order_bridge.py --test      # test connections
  python execution/order_bridge.py --poll-once # poll once and exit
        """,
    )
    parser.add_argument("--test", action="store_true", help="Test connections only")
    parser.add_argument("--poll-once", action="store_true", help="Poll Notion once and exit")
    args = parser.parse_args()

    try:
        if args.test:
            asyncio.run(run_test())
        elif args.poll_once:
            asyncio.run(run_poll_once())
        else:
            asyncio.run(run_bridge())
    except KeyboardInterrupt:
        log.info("Bridge stopped by user (Ctrl+C).")


if __name__ == "__main__":
    import error_reporter
    error_reporter.install("order-bridge", host="gcp-vm")
    main()
