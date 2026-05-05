"""
order_bridge.py
===============
Main bridge script: connects Notion CRM to Telegram for order management.

Runs two concurrent tasks:
    1. Notion Poller — checks for newly confirmed orders every 30s,
       sends them to the Telegram sourcing group
    2. Telegram Bot — listens for #ready, #cost, #note commands
       in the sourcing group

Uses a local JSON file (.tmp/notified_orders.json) to track which orders
have already been sent — no Notion database modifications required.

Usage:
    python execution/order_bridge.py             # run the bridge
    python execution/order_bridge.py --test      # test connections only
    python execution/order_bridge.py --poll-once  # poll once and exit

Requires in .env:
    NOTION_API_KEY, NOTION_DATABASE_ID,
    TELEGRAM_BOT_TOKEN, TELEGRAM_SOURCING_GROUP_ID, TELEGRAM_FULFILLMENT_GROUP_ID
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


async def _safe_send_document(bot, chat_id: int, document: bytes, filename: str) -> bool:
    """Send a Telegram document, log timeouts/errors instead of crashing. Returns True on success."""
    try:
        await bot.send_document(chat_id, document=document, filename=filename)
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
    "LU": "Lune Collection"
}

def get_brand_from_order_id(order_id: str) -> str:
    """Extracts the brand name from the order ID prefix."""
    if not isinstance(order_id, str):
        return "Elara"
    prefix = order_id[:2]
    return BRAND_MAP.get(prefix, "Elara")


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
# Tracking happens natively via Notion checkboxes now
# (SOURCING NOTIFIED, FULFILLMENT NOTIFIED) — local JSON is deprecated.
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
    Poll Notion for confirmed orders, send to Telegram sourcing group.
    Returns number of orders processed.
    """
    sourcing_group = tg.TELEGRAM_SOURCING_GROUP_ID
    if not sourcing_group:
        log.error("TELEGRAM_SOURCING_GROUP_ID is not set")
        return 0

    # Query Notion for confirmed orders
    orders = notion.query_confirmed_orders()

    if not orders:
        return 0

    # Filter out already-notified orders AND orders currently being sent
    new_orders = [
        o for o in orders
        if not o.get("sourcing_notified", False)
        and o.get("order_id", "") not in _sending_in_progress
    ]

    if not new_orders:
        return 0

    log.info(f"Found {len(new_orders)} new confirmed order(s)")

    processed = 0
    for order in new_orders:
        order_id = order.get("order_id", "?")

        # Lock immediately — prevents duplicate if next poll fires fast
        _sending_in_progress.add(order_id)
        
        # Update Notion status and tracking FIRST — eliminates race condition window
        notion.mark_sourcing_notified(order["page_id"])
        notion.update_order_status(order["page_id"], "SOURCING")

        log.info(f"📤 Sending order {order_id} to sourcing group...")

        # Send to Telegram
        send_result = await tg.send_order_to_group(
            bot, sourcing_group, order,
            header="📦 NEW ORDER FOR SOURCING"
        )
        success = send_result["success"]

        if success:
            log.info(f"  ✓ Order {order_id} sent, status → SOURCING")

            # Save file_ids returned from Stage 1 send. 3 retries with backoff.
            file_ids = send_result.get("file_ids", [])
            if file_ids:
                ok = await notion_write_with_retry(
                    notion.update_telegram_file_ids,
                    order["page_id"], file_ids,
                    description=f"file_ids write for {order_id}",
                )
                if ok:
                    await notion_write_with_retry(
                        notion.mark_file_ids_saved,
                        order["page_id"], True,
                        description=f"FILE IDS SAVED checkbox for {order_id}",
                    )
                else:
                    # Couldn't save file_ids; warn in the chat. Stage 2 will use
                    # GraphQL fallback automatically when FILE IDS SAVED is false.
                    try:
                        await bot.send_message(
                            chat_id=sourcing_group,
                            text=f"⚠️ {order_id}: file IDs not saved after 3 retries, manual fulfillment may be needed",
                        )
                    except Exception as e:
                        log.error(f"  Failed to post file_id warning: {e}")

            # Image count vs items count mismatch warning.
            delivered = send_result.get("image_count", 0)
            expected = send_result.get("expected_count", 0)
            if expected > 0 and delivered < expected:
                missing = expected - delivered
                try:
                    await bot.send_message(
                        chat_id=sourcing_group,
                        text=f"⚠️ {order_id}: {delivered} of {expected} images sent, {missing} missing",
                    )
                except Exception as e:
                    log.error(f"  Failed to post image-count warning: {e}")

            processed += 1
        else:
            # Revert Notion status and tracking so it retries on next cycle
            notion.update_order_status(order["page_id"], "CONFIRMED | PROCESSING")
            notion.unmark_notified(order["page_id"])
            log.error(f"  ✗ Failed to send order {order_id} to Telegram — will retry")

        # Release the in-memory lock
        _sending_in_progress.discard(order_id)

        # Rate limit: wait between orders to avoid Telegram API limits
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

                    for target in all_orders_to_update:
                        page_id = target["page_id"]
                        target_ref = target.get("order_id") or shipper_ref
                        try:
                            if new_status:
                                nc.set_filex_status(page_id, new_status)
                            nc.set_filex_notes(page_id, notes)
                            if incoming_dt:
                                nc.set_last_update(page_id, incoming_dt.isoformat())
                            log.info(
                                f"  ✓ Notion updated for {target_ref}: "
                                f"FILEX STATUS → {new_status!r}"
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

    # 1. Query eligible orders from Notion
    eligible = nc.query_filex_eligible()
    if not eligible:
        await _safe_send_message(bot, chat_id, "No orders eligible for /print all right now.")
        return

    # 2. Group eligible orders by normalized phone — same customer = merged shipment
    from collections import defaultdict
    from whatchimp_client import clean_phone_number

    grouped: dict[str, list[dict]] = defaultdict(list)
    orphans: list[dict] = []  # orders missing phone — handled individually
    for order in eligible:
        raw_phone = order.get("phone", "") or ""
        if not raw_phone:
            orphans.append(order)
            continue
        normalized = clean_phone_number(raw_phone)
        if not normalized:
            orphans.append(order)
            continue
        grouped[normalized].append(order)

    # 3. Build payloads — one per group (merged when group has >1 order)
    payloads: list[dict] = []
    page_ids_by_ref: dict[str, list[str]] = {}  # ref -> list of page_ids (multiple if merged)

    def _register(ref: str, page_ids: list[str], payload: dict) -> None:
        payloads.append(payload)
        page_ids_by_ref[ref] = page_ids

    for phone, group in grouped.items():
        if len(group) == 1:
            order = group[0]
            try:
                payload = build_payload(order)
            except ValidationError as e:
                await _safe_send_message(
                    bot,
                    chat_id,
                    f"⚠️ Skipped {order.get('order_id', '?')}: {e}",
                )
                continue
            _register(payload["ShipperRef"], [order["page_id"]], payload)
        else:
            try:
                merged_payload = build_merged_payload(group)
            except ValidationError as e:
                order_ids = [o.get("order_id", "?") for o in group]
                await _safe_send_message(
                    bot,
                    chat_id,
                    f"⚠️ Skipped merged group {order_ids}: {e}",
                )
                continue
            _register(
                merged_payload["ShipperRef"],
                [o["page_id"] for o in group],
                merged_payload,
            )
            log.info(
                f"  ↳ Merging {len(group)} orders for phone {phone}: "
                f"{[o.get('order_id') for o in group]}"
            )

    # Phone-less orders are processed individually (no merging possible)
    for order in orphans:
        try:
            payload = build_payload(order)
        except ValidationError as e:
            await _safe_send_message(
                bot,
                chat_id,
                f"⚠️ Skipped {order.get('order_id', '?')}: {e}",
            )
            continue
        _register(payload["ShipperRef"], [order["page_id"]], payload)

    if not payloads:
        await _safe_send_message(bot, chat_id, "No valid orders after validation.")
        return

    # 4. Lock orders BEFORE the API call (prevents double-submit on retry)
    for ref, page_ids in page_ids_by_ref.items():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, True)

    # 5. Place orders via Filex
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        # Revert locks on failure so a retry can re-submit
        for ref, page_ids in page_ids_by_ref.items():
            for page_id in page_ids:
                nc.mark_filex_submitted(page_id, False)
        await _safe_send_message(bot, chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    # 6. Update Notion with tracking info — fan out across all linked rows for merged shipments
    now_iso = datetime.now(timezone.utc).isoformat()
    tracking_pairs = result.get("trackingnos", [])
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

    # 6. Fetch combined PDF
    tracking_numbers = [e["tracking_no"] for e in tracking_pairs if e.get("tracking_no")]
    try:
        pdf_bytes = client.get_label_pdf(tracking_numbers)
    except Exception as e:
        await _safe_send_message(
            bot,
            chat_id,
            f"✅ Placed {len(tracking_numbers)} orders, but label PDF fetch failed: {e}\n"
            f"Retry the print manually.",
        )
        log.error("filex label pdf fetch failed", exc_info=True)
        return

    # 7. Send PDF as document attachment
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"filex_labels_{today}_{len(tracking_numbers)}.pdf"
    await _safe_send_document(
        bot,
        chat_id,
        document=pdf_bytes,
        filename=filename,
    )
    log.info(
        "/print all completed: %d orders, PDF %d bytes",
        len(tracking_numbers),
        len(pdf_bytes),
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
    app.add_handler(CommandHandler("print", cmd_print_all))

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
    log.info("   Listening for #ready, #cost, #note, #reset, #status commands in Telegram")
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
    main()
