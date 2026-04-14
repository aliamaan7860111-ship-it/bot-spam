"""
WhatsApp Sales Bot — Webhook Server v2
Multi-store aware, with message dedup, rate limiting, and escalation reset.
Batches rapid messages (4s window) so multiple quick messages = one response.
"""

import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

from brain import process_message
from whatchimp_client import send_text
from supabase_client import (
    save_message, create_customer, check_message_exists
)
from config import get_store_config, rate_limiter, get_logger

log = get_logger("webhook")

# ── Message batching ───────────────────────────────────────
BATCH_DELAY = int(os.getenv("BATCH_DELAY", "4"))
pending_timers: dict[str, asyncio.Task] = {}
pending_context: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Sales bot v2 started — listening for webhooks")
    yield
    for task in pending_timers.values():
        task.cancel()
    log.info("Sales bot shutting down")


app = FastAPI(title="WhatsApp Sales Bot v2", lifespan=lifespan)


async def process_after_delay(phone: str):
    """Wait BATCH_DELAY seconds, then process all accumulated messages for this phone."""
    try:
        await asyncio.sleep(BATCH_DELAY)
    except asyncio.CancelledError:
        return

    try:
        ctx = pending_context.pop(phone, {})
        pending_timers.pop(phone, None)
        store_config = ctx.get("store_config", {})
        phone_number_id = store_config.get("phone_number_id")

        log.info(f"Batch timer expired for {phone} — processing (store={store_config.get('store', '?')})")

        result = await process_message(
            phone=phone,
            message_text=ctx.get("last_message_text", ""),
            first_name=ctx.get("first_name", ""),
            message_type=ctx.get("last_message_type", "text"),
            image_url=ctx.get("last_image_url"),
            store_config=store_config,
        )

        if result["reply"] is None:
            log.info(f"Human takeover active for {phone}, bot silent")
            return

        if result["reply"]:
            resp = await send_text(phone, result["reply"], phone_number_id=phone_number_id)
            log.info(f"Sent reply to {phone}: {result['reply'][:80]}...")

    except Exception as e:
        log.error(f"Processing error for {phone}: {e}", exc_info=True)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Receives incoming WhatsApp messages from WhatChimp.
    Routes to correct store based on phone_number_id.
    Saves message immediately, delays brain processing by BATCH_DELAY seconds.
    """
    try:
        body = await request.json()

        phone = body.get("chat_id", "")
        user_message = body.get("user_message", "")
        first_name = body.get("first_name", "")
        wa_message_id = body.get("message_id", "")
        incoming_phone_number_id = body.get("phone_number_id", "")

        if not phone:
            return {"status": "skipped", "reason": "no phone"}

        # ── Multi-store routing ──────────────────────────
        store_config = get_store_config(incoming_phone_number_id)
        if not store_config:
            log.warning(f"Unknown phone_number_id: {incoming_phone_number_id}")
            return {"status": "skipped", "reason": "unknown store"}

        # ── Rate limiting ────────────────────────────────
        if not rate_limiter.is_allowed(phone):
            log.warning(f"Rate limit exceeded for {phone}")
            # Send once, then drop
            return {"status": "rate_limited"}

        # ── Message deduplication ────────────────────────
        if wa_message_id and check_message_exists(wa_message_id):
            log.info(f"Duplicate message {wa_message_id} for {phone}, skipping")
            return {"status": "skipped", "reason": "duplicate"}

        # ── Parse message ────────────────────────────────
        message_type = "text"
        message_text = ""
        image_url = None

        if isinstance(user_message, dict):
            message_type = user_message.get("type", "text")
            image_url = user_message.get("url")
            caption = user_message.get("caption", "")
            message_text = caption if caption else f"[Sent {message_type}]"
        elif isinstance(user_message, str):
            message_text = user_message
        else:
            return {"status": "skipped", "reason": "unexpected format"}

        if not message_text and not image_url:
            return {"status": "skipped", "reason": "empty message"}

        log.info(f"Webhook: phone={phone}, store={store_config['store']}, type={message_type}, msg={message_text[:50]}")

        # ── Ensure customer exists ───────────────────────
        create_customer(phone, name=first_name if first_name else None,
                       store=store_config["store"])

        # ── Save message to DB ───────────────────────────
        if message_type == "image" and image_url:
            save_message(phone, "customer", f"[Sent an image]",
                        message_type="image", image_url=image_url,
                        wa_message_id=wa_message_id)
        else:
            save_message(phone, "customer", message_text, message_type="text",
                        wa_message_id=wa_message_id)

        # ── Update pending context ───────────────────────
        pending_context[phone] = {
            "first_name": first_name,
            "last_message_text": message_text,
            "last_message_type": message_type,
            "last_image_url": image_url,
            "store_config": store_config,
        }

        # ── Reset batch timer ────────────────────────────
        if phone in pending_timers:
            pending_timers[phone].cancel()

        pending_timers[phone] = asyncio.create_task(process_after_delay(phone))

        return {"status": "ok", "action": "message_queued", "store": store_config["store"]}

    except Exception as e:
        log.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/health")
async def health():
    """Health check with dependency status."""
    checks = {"service": "whatsapp-sales-bot-v2"}

    # Check Supabase
    try:
        from supabase_client import supabase
        supabase.table("customers").select("phone").limit(1).execute()
        checks["supabase"] = True
    except Exception:
        checks["supabase"] = False

    # Check Claude API key
    checks["claude_key_set"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    checks["whatchimp_key_set"] = bool(os.getenv("WHATCHIMP_API_TOKEN"))

    all_ok = checks.get("supabase", False) and checks["claude_key_set"]
    checks["status"] = "healthy" if all_ok else "degraded"

    return checks


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
