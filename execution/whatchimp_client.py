import os
import logging
import requests
import json
from dotenv import load_dotenv
from pathlib import Path

# Load env variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Use a specific logger that will show up in the main logs
log = logging.getLogger("whatchimp_client")
log.setLevel(logging.INFO)

# Config from .env
WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")
WHATCHIMP_PHONE_NUMBER_ID = os.getenv("WHATCHIMP_PHONE_NUMBER_ID", "1031340813395459")
WHATCHIMP_TEMPLATE_ID = "340859" # 🏆 Verified Template ID from screenshot

API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

def clean_phone_number(phone: str) -> str:
    """
    Cleans a phone number for the WhatsApp API.
    """
    if not phone: return ""
    cleaned = ''.join(c for c in str(phone) if c.isdigit())
    if cleaned.startswith("05") and len(cleaned) == 10:
        cleaned = "971" + cleaned[1:]
    return cleaned

def create_or_update_subscriber(phone_number: str, name: str, order_id: str = "", brand: str = "") -> bool:
    """
    Ensures a subscriber exists in WhatChimp and correctly assigns custom fields.
    This follows the 'assign-custom-fields' blueprint from the WhatChimp SKILL.md.
    """
    cleaned_phone = clean_phone_number(phone_number)
    
    # 1. Ensure subscriber exists (uses camelCase)
    create_url = f"{API_BASE}/subscriber/create"
    create_payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phoneNumberID": WHATCHIMP_PHONE_NUMBER_ID,
        "name": name,
        "phoneNumber": cleaned_phone
    }
    
    try:
        # We don't strictly check status here because 'already exists' returns status 0
        requests.post(create_url, data=create_payload, timeout=10)
        
        # 2. Assign Custom Fields (uses snake_case and JSON string for custom_fields)
        assign_url = f"{API_BASE}/subscriber/chat/assign-custom-fields"
        custom_fields = {
            "order_id": order_id,
            "brand_name": brand
        }
        assign_payload = {
            "apiToken": WHATCHIMP_API_TOKEN,
            "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
            "phone_number": cleaned_phone,
            "custom_fields": json.dumps(custom_fields)
        }
        
        resp = requests.post(assign_url, data=assign_payload, timeout=10)
        data = resp.json()
        if str(data.get("status")) == "1":
            log.info(f"  ✓ Subscriber metadata synced: {custom_fields}")
            return True
        else:
            log.warning(f"  ⚠️ Custom field sync warning: {data.get('message')}")
            return False
    except Exception as e:
        log.error(f"  ✗ Subscriber sync failed: {str(e)}")
        return False

def send_template_message(phone_number: str, customer_name: str, order_id: str, total: str, brand_name: str = "PrettyByShd") -> bool:
    """
    Sends the WhatsApp confirmation using the DEFINITIVE 'templateVariable' format.
    Also ensures the subscriber has the latest Order ID and Brand for Flow variables.
    """
    if not WHATCHIMP_API_TOKEN or not WHATCHIMP_PHONE_NUMBER_ID:
        log.error("Missing WhatChimp credentials (token or phone_id) in .env")
        return False

    cleaned_phone = clean_phone_number(phone_number)
    
    # Update subscriber profile first so #order_id# is available in the flow
    create_or_update_subscriber(phone_number, customer_name, order_id, brand_name)
    
    # URL exactly as per official documentation
    url = f"{API_BASE}/send/template"
    
    # Payload using the custom 'templateVariable' labels identified in your screenshot
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "template_id": WHATCHIMP_TEMPLATE_ID,
        "phone_number": cleaned_phone,
        
        # dynamic variable mapping for template 340859
        "templateVariable-brand-2": brand_name,
        "templateVariable-id-3": order_id,
        "templateVariable-name-1": customer_name, # Standard pattern for first variable
        
        # Button values as a JSON string (no spaces after comma for WhatChimp API compatibility)
        "template_quick_reply_button_values": json.dumps(["YES_START_CHAT_WITH_HUMAN", "Nop_RZOKKksN72n"], separators=(',', ':'))
    }

    try:
        log.info(f"🚀 [Verified Send] Template {WHATCHIMP_TEMPLATE_ID} to {cleaned_phone} (Brand: {brand_name})...")
        # Use data=payload for application/x-www-form-urlencoded (standard curl -d behavior)
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        
        if str(data.get("status")) == "1":
            log.info(f"✅ SUCCESS: Template delivered instantly!")
            return True
        else:
            log.error(f"❌ API Rejected: {data.get('message', data)}")
            return False
    except Exception as e:
        log.error(f"Request failed: {e}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("WhatChimp DEFINITIVE Client Ready.")
