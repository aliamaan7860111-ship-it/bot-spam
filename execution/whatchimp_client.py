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

WHATCHIMP_API_URL = "https://app.whatchimp.com/api/v1/whatsapp/send"

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
    
    # Assume UAE (971) if it starts with 05 (Since the stores operate in UAE often)
    if cleaned.startswith("05") and len(cleaned) == 10:
        cleaned = "971" + cleaned[1:]
        
    return cleaned

def send_whatsapp_message(phone_number: str, message: str) -> bool:
    """
    Sends a standard WhatsApp text message via WhatChimp API.
    """
    if not WHATCHIMP_API_TOKEN:
        log.error("WHATCHIMP_API_TOKEN is missing from .env")
        return False
        
    if not WHATCHIMP_PHONE_NUMBER_ID:
        log.error("WHATCHIMP_PHONE_NUMBER_ID is missing from .env")
        return False

    cleaned_phone = clean_phone_number(phone_number)
    
    if not cleaned_phone:
        log.error("Invalid or empty phone number provided.")
        return False

    # The API documentation specifies these keys and required string formatting
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "phone_number": cleaned_phone,
        "message": message
    }

    try:
        log.info(f"Sending WhatsApp message to {cleaned_phone} via WhatChimp...")
        with httpx.Client(timeout=15) as client:
            resp = client.post(WHATCHIMP_API_URL, data=payload)
            resp.raise_for_status()
            
            # {"status":"1", "wa_message_id":"...", "message":"Message sent successfully."}
            data = resp.json()
            
            if str(data.get("status")) == "1":
                log.info(f"✓ WhatChimp message sent to {cleaned_phone}.")
                return True
            else:
                log.error(f"WhatChimp API returned error: {data.get('message', data)}")
                return False
                
    except httpx.HTTPStatusError as e:
        log.error(f"WhatChimp HTTP error: {e.response.status_code} — {e.response.text}")
        return False
    except Exception as e:
        log.error(f"WhatChimp request failed: {e}")
        return False

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    # Simple CLI for testing: python execution/whatchimp_client.py --test-message 9715...
    if len(sys.argv) > 1 and sys.argv[1] == "--test-message":
        test_phone = sys.argv[2] if len(sys.argv) > 2 else "971501234567"
        test_msg = "Hello! Your Pretty by Shahid order is confirmed."
        success = send_whatsapp_message(test_phone, test_msg)
        print(f"Result: {'Success' if success else 'Failed'}")
