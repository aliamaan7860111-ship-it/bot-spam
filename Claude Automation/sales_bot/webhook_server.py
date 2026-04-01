"""
WhatsApp Sales Bot — Webhook Server
Receives incoming messages from WhatChimp and routes them through the brain.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

from brain import process_message
from whatchimp_client import send_text, send_image

# ── Logging ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sales_bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Sales bot started — listening for webhooks")
    yield
    log.info("Sales bot shutting down")


app = FastAPI(title="WhatsApp Sales Bot", lifespan=lifespan)


@app.post("/webhook")
async def handle_webhook(request: Request):
    """
    Receives incoming WhatsApp messages from WhatChimp webhook.

    WhatChimp payload format:
    {
        "chat_id": "971555614190",           # customer phone
        "user_message": "hi" | {"type": "image", "url": "...", "caption": ""},
        "first_name": "Customer Name",
        "subscriber_id": "971555614190-352261",
        "wa_message_id": "wamid.XXX",
        "whatsapp_bot_id": 352261,
        "label_names": "Label1,Label2",
        "custom_fields": {...}
    }
    """
    try:
        body = await request.json()
        log.info(f"Webhook received: {body.get('chat_id', 'unknown')}")

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
            # Image, voice, or other media
            message_type = user_message.get("type", "text")
            image_url = user_message.get("url")
            caption = user_message.get("caption", "")
            message_text = caption if caption else f"[Sent {message_type}]"
        elif isinstance(user_message, str):
            message_text = user_message
        else:
            log.warning(f"Unexpected user_message format: {type(user_message)}")
            return {"status": "skipped", "reason": "unexpected format"}

        # Skip empty messages
        if not message_text and not image_url:
            return {"status": "skipped", "reason": "empty message"}

        log.info(f"Processing: phone={phone}, type={message_type}, msg={message_text[:50]}")

        # ── Process through brain ──────────────────────────
        result = await process_message(
            phone=phone,
            message_text=message_text,
            first_name=first_name,
            message_type=message_type,
            image_url=image_url,
        )

        # If human_takeover is active, don't send anything
        if result["reply"] is None:
            log.info(f"Human takeover active for {phone}, bot silent")
            return {"status": "ok", "action": "human_takeover"}

        # ── Send response via WhatChimp ────────────────────

        # Send product images first (if any)
        for img_url in result.get("images", []):
            try:
                await send_image(phone, img_url)
                log.info(f"Sent image to {phone}")
            except Exception as e:
                log.error(f"Failed to send image to {phone}: {e}")

        # Send text reply
        if result["reply"]:
            resp = await send_text(phone, result["reply"])
            log.info(f"Sent reply to {phone}: {result['reply'][:50]}... | WhatChimp: {resp}")

        return {"status": "ok", "reply_sent": True}

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
