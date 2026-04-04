"""
WhatsApp Sales Bot — Webhook Server
Receives incoming messages from WhatChimp and routes them through the brain.
Batches rapid messages (4s window) so multiple quick messages = one response.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

from brain import process_message
from whatchimp_client import send_text, send_image
from supabase_client import save_message, create_customer

# ── Logging ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sales_bot")

# ── Message batching ───────────────────────────────────────
# Tracks pending timers per phone number. When a message arrives,
# we save it to DB immediately but delay processing by BATCH_DELAY seconds.
# If another message arrives before the timer fires, the timer resets.
# When the timer finally fires, the brain loads ALL recent messages from DB.

BATCH_DELAY = 4  # seconds to wait for more messages before processing
pending_timers: dict[str, asyncio.Task] = {}
# Store the latest webhook data per phone so the processing task has context
pending_context: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Sales bot started — listening for webhooks")
    yield
    # Cancel all pending timers on shutdown
    for task in pending_timers.values():
        task.cancel()
    log.info("Sales bot shutting down")


app = FastAPI(title="WhatsApp Sales Bot", lifespan=lifespan)


async def process_after_delay(phone: str):
    """Wait BATCH_DELAY seconds, then process all accumulated messages for this phone."""
    try:
        await asyncio.sleep(BATCH_DELAY)
    except asyncio.CancelledError:
        # Timer was reset because a new message arrived — do nothing
        return

    # Timer expired — process now
    try:
        ctx = pending_context.pop(phone, {})
        pending_timers.pop(phone, None)

        first_name = ctx.get("first_name", "")

        log.info(f"Batch timer expired for {phone} — processing now")

        # The brain loads conversation history from DB, which includes
        # ALL messages saved during the batching window
        result = await process_message(
            phone=phone,
            message_text=ctx.get("last_message_text", ""),
            first_name=first_name,
            message_type=ctx.get("last_message_type", "text"),
            image_url=ctx.get("last_image_url"),
        )

        # If human_takeover is active, don't send anything
        if result["reply"] is None:
            log.info(f"Human takeover active for {phone}, bot silent")
            return

        # TODO: Re-enable when WhatChimp image endpoint is confirmed
        # Image sending disabled — /whatsapp/send only supports text,
        # sending image URLs as plain text looks unprofessional.
        # for img_url in result.get("images", []):
        #     try:
        #         await send_image(phone, img_url)
        #         log.info(f"Sent image to {phone}")
        #     except Exception as e:
        #         log.error(f"Failed to send image to {phone}: {e}")

        # Send text reply
        if result["reply"]:
            resp = await send_text(phone, result["reply"])
            log.info(f"Sent reply to {phone}: {result['reply'][:80]}...")

    except Exception as e:
        log.error(f"Processing error for {phone}: {e}", exc_info=True)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Receives incoming WhatsApp messages from WhatChimp webhook.
    Saves message immediately, but delays brain processing by BATCH_DELAY seconds
    to batch rapid consecutive messages into one response.
    """
    try:
        body = await request.json()

        # Extract fields from WhatChimp payload
        phone = body.get("chat_id", "")
        user_message = body.get("user_message", "")
        first_name = body.get("first_name", "")

        if not phone:
            log.warning("No chat_id in webhook payload, skipping")
            return {"status": "skipped", "reason": "no phone"}

        # Determine message type and content
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
            log.warning(f"Unexpected user_message format: {type(user_message)}")
            return {"status": "skipped", "reason": "unexpected format"}

        if not message_text and not image_url:
            return {"status": "skipped", "reason": "empty message"}

        log.info(f"Webhook: phone={phone}, type={message_type}, msg={message_text[:50]}")

        # ── Ensure customer exists before saving message ───
        create_customer(phone, name=first_name if first_name else None)

        # ── Save message to DB immediately ─────────────────
        # (brain will load these from conversation history when it processes)
        if message_type == "image" and image_url:
            save_message(phone, "customer", f"[Sent an image]",
                        message_type="image", image_url=image_url)
        else:
            save_message(phone, "customer", message_text, message_type="text")

        # ── Update pending context for this phone ──────────
        pending_context[phone] = {
            "first_name": first_name,
            "last_message_text": message_text,
            "last_message_type": message_type,
            "last_image_url": image_url,
        }

        # ── Reset the batch timer ──────────────────────────
        # Cancel existing timer if there is one
        if phone in pending_timers:
            pending_timers[phone].cancel()
            log.info(f"Timer reset for {phone} — batching messages")

        # Start a new timer
        pending_timers[phone] = asyncio.create_task(process_after_delay(phone))

        return {"status": "ok", "action": "message_queued"}

    except Exception as e:
        log.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "whatsapp-sales-bot"}


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
