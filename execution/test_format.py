import asyncio
import httpx
from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(Path(__file__).resolve().parent / ".." / ".env")

import telegram_client as tg

async def run():
    order = {
        "order_id": "TEST-123",
        "customer_name": None,
        "phone": None,
        "item_qty": "1x Test Item",
        "total_aed": "150",
        "full_address": None,
        "line_items": [
            {"variant": None, "quantity": 1}
        ]
    }
    
    msg = tg.format_order_message(order, "📦 TEST HEADER")
    print(msg)
    
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    CHAT_ID = os.getenv("TELEGRAM_SOURCING_GROUP_ID")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    
    try:
        print("Sending to Telegram API...")
        r = httpx.post(url, json=payload, timeout=20)
        print(f"TELEGRAM API RESPONSE: {r.status_code}")
        print(r.text)
    except Exception as e:
        print(f"Network error to Telegram: {e}")

if __name__ == "__main__":
    asyncio.run(run())
