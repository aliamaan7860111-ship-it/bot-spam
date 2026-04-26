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
from datetime import datetime

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

from telegram import Bot
from telegram.ext import Application

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
            if "/whatchimp_webhook" in path:
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
