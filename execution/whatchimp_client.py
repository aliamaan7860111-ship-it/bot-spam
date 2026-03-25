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

def create_or_update_subscriber(phone_number: str, name: str) -> bool:
    """
    Ensures a subscriber exists in WhatChimp with the correct name.
    """
    if not WHATCHIMP_API_TOKEN or not WHATCHIMP_PHONE_NUMBER_ID:
        return False

    cleaned_phone = clean_phone_number(phone_number)
    
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phoneNumberID": WHATCHIMP_PHONE_NUMBER_ID,
        "name": name,
        "phoneNumber": cleaned_phone
    }

    try:
        log.info(f"Syncing subscriber {cleaned_phone} (Name: {name})...")
        resp = requests.post(f"{API_BASE}/subscriber/create", data=payload, timeout=15)
        data = resp.json()
        
        if str(data.get("status")) == "1":
            log.info(f"✓ Subscriber synced.")
            return True
        else:
            # If create fails, try Update specifically
            update_payload = {
                "apiToken": WHATCHIMP_API_TOKEN,
                "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
                "phone_number": cleaned_phone,
                "first_name": name
            }
            resp_upd = requests.post(f"{API_BASE}/subscriber/update", data=update_payload, timeout=15)
            status = str(resp_upd.json().get("status")) == "1"
            if status:
                log.info(f"✓ Subscriber updated via fallback.")
            return status
                
    except Exception as e:
        log.error(f"Subscriber sync failed: {e}")
        return False

def assign_order_id_to_subscriber(phone_number: str, order_id: str) -> bool:
    """
    Assigns the order_id as a custom field to the subscriber in WhatChimp.
    This allows the downstream flow (Confirm 123) to use {{order_id}} in its webhook.
    """
    cleaned_phone = clean_phone_number(phone_number)
    # Format A: Form-encoded with stringified JSON (Based on research)
    payload_form = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "phone_number": cleaned_phone,
        "custom_fields": json.dumps({"order_id": order_id})
    }

    # Format B: Regular JSON object (fallback)
    payload_json = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "phone_number": cleaned_phone,
        "custom_fields": {
            "order_id": order_id
        }
    }

    try:
        log.info(f"Assigning order_id '{order_id}' to {cleaned_phone} in WhatChimp...")
        
        # Try Format A first (Form-encoded with stringified JSON)
        resp = requests.post(
            f"{API_BASE}/subscriber/chat/assign-custom-fields", 
            data=payload_form,
            timeout=15
        )
        result = resp.json()
        
        if str(result.get("status")) == "1":
            log.info(f"  ✓ order_id assigned (Method A).")
            return True
        
        # Try Format B (Pure JSON) if Method A failed
        log.warning(f"  ! Method A failed ({result.get('message')}), trying Method B...")
        resp = requests.post(
            f"{API_BASE}/subscriber/chat/assign-custom-fields", 
            json=payload_json,
            timeout=15
        )
        result = resp.json()
        if str(result.get("status")) == "1":
            log.info(f"  ✓ order_id assigned (Method B).")
            return True

        log.error(f"  ✗ All assign methods failed: {result.get('message', result)}")
        return False
    except Exception as e:
        log.error(f"Assign custom field failed: {e}")
        return False

def send_template_message(phone_number: str, customer_name: str, order_id: str, total: str) -> bool:
    """
    Sends the WhatsApp confirmation template via the direct API (reliable delivery)
    while manually mapping the buttons to their respective flows.
    """
    if not WHATCHIMP_API_TOKEN or not WHATCHIMP_PHONE_NUMBER_ID:
        log.error("Missing WhatChimp credentials (token or phone_id) in .env")
        return False

    # 1. Sync the name first so placeholders work
    create_or_update_subscriber(phone_number, customer_name)
    
    # 2. Assign order_id as a custom field so {{order_id}} works in the flow
    assign_order_id_to_subscriber(phone_number, order_id)

    cleaned_phone = clean_phone_number(phone_number)
    
    # 3. Prepare payload with manual button routing
    # Button order in prettybyshd_order_confirmation: [Reschedule, Process Order]
    button_values = '["YES_START_CHAT_WITH_BOT","Nop_RZOKKksN72n"]'
    
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "template_id": "340363",
        "phone_number": cleaned_phone,
        "template_quick_reply_button_values": button_values
    }

    try:
        log.info(f"Sending Template 340363 with manual button routing to {cleaned_phone}...")
        resp = requests.post(f"{API_BASE}/send/template", data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if str(data.get("status")) == "1":
            log.info(f"✓ WhatChimp Template delivered with button routing.")
            return True
        else:
            log.error(f"WhatChimp Template Error: {data.get('message', data)}")
            return False

    except Exception as e:
        log.error(f"WhatChimp Template request failed: {e}")
        return False

def trigger_bot_flow(phone_number: str, flow_id: str = None) -> bool:
    """
    Triggers a specific WhatChimp bot flow for a phone number.
    """
    if not flow_id:
        flow_id = WHATCHIMP_BOT_FLOW_ID
        
    if not WHATCHIMP_API_TOKEN or not WHATCHIMP_PHONE_NUMBER_ID or not flow_id:
        log.error("Missing WhatChimp credentials (token, phone_id, or flow_id) in .env")
        return False
    
    create_or_update_subscriber(phone_number, "Customer")
        
    cleaned_phone = clean_phone_number(phone_number)
    
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": WHATCHIMP_PHONE_NUMBER_ID,
        "bot_flow_unique_id": flow_id,
        "phone_number": cleaned_phone
    }

    try:
        log.info(f"Triggering WhatChimp flow {flow_id} for {cleaned_phone}...")
        resp = requests.post(f"{API_BASE}/trigger-bot", data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if str(data.get("status")) == "1":
            log.info(f"✓ WhatChimp flow {flow_id} triggered successfully.")
            return True
        else:
            log.error(f"WhatChimp API Error: {data.get('message', data)}")
            return False
            
    except Exception as e:
        log.error(f"WhatChimp request failed: {e}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test-assign":
        test_phone = sys.argv[2] if len(sys.argv) > 2 else "971555614190"
        test_id = sys.argv[3] if len(sys.argv) > 3 else "TEST_ID_123"
        success = assign_order_id_to_subscriber(test_phone, test_id)
        print(f"Assign Result: {success}")
