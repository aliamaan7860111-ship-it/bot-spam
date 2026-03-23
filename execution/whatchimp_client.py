import os
import logging
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Load env variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("whatchimp_client")

# Config from .env
WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")
WHATCHIMP_PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID", "")
WHATCHIMP_BOT_FLOW_ID = os.getenv("WHATCHIMP_BOT_FLOW_ID", "")

API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

def clean_phone_number(phone: str) -> str:
    """
    Cleans a phone number for the WhatsApp API.
    Must start with country code and only contain numeric characters.
    Example: '+971 50 123 4567' -> '971501234567'
    """
    if not phone:
        return ""
    
    # Remove all non-numeric characters (including +, spaces, dashes)
    cleaned = ''.join(c for c in str(phone) if c.isdigit())
    
    # Assume UAE (971) if it starts with 05
    if cleaned.startswith("05") and len(cleaned) == 10:
        cleaned = "971" + cleaned[1:]
        
    return cleaned

def trigger_bot_flow(phone_number: str) -> bool:
    """
    Kicks off a specific Bot Flow in WhatChimp.
    Uses WHATCHIMP_BOT_FLOW_ID from .env.
    """
    if not WHATCHIMP_API_TOKEN or not WHATCHIMP_PHONE_NUMBER_ID or not WHATCHIMP_BOT_FLOW_ID:
        log.error("Missing WhatChimp credentials (token, phone_id, or flow_id) in .env")
        return False

    cleaned_phone = clean_phone_number(phone_number)
    
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "bot_flow_unique_id": WHATCHIMP_BOT_FLOW_ID,
        "phone_number": cleaned_phone
    }

    try:
        log.info(f"Triggering WhatChimp Flow {WHATCHIMP_BOT_FLOW_ID} for {cleaned_phone}...")
        with httpx.Client(timeout=15) as client:
            resp = client.post(f"{API_BASE}/trigger-bot", data=payload)
            resp.raise_for_status()
            data = resp.json()
            
            if str(data.get("status")) == "1":
                log.info(f"✓ WhatChimp Flow triggered for {cleaned_phone}.")
                return True
            else:
                log.error(f"WhatChimp Flow Error: {data.get('message', data)}")
                return False
                
    except Exception as e:
        log.error(f"WhatChimp Flow request failed: {e}")
        return False

def assign_label(phone_number: str, label_ids: str) -> bool:
    """
    Manually assign a label to a subscriber via WhatChimp API.
    label_ids can be a comma-separated string like "1,4".
    """
    cleaned_phone = clean_phone_number(phone_number)
    
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "phone_number": cleaned_phone,
        "label_ids": label_ids
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(f"{API_BASE}/subscriber/chat/assign-labels", data=payload)
            resp.raise_for_status()
            return str(resp.json().get("status")) == "1"
    except Exception as e:
        log.error(f"Assign label failed: {e}")
        return False

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test-flow":
        test_phone = sys.argv[2] if len(sys.argv) > 2 else "971501234567"
        success = trigger_bot_flow(test_phone)
        print(f"Flow Trigger Result: {'Success' if success else 'Failed'}")
