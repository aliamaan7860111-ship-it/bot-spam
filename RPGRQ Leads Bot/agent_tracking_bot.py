import os
import sys
import time
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import json
import httpx
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "RPGRQ Leads Bot"))
import agent_notion_client as notion

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("leads_bot")

WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")

# The 5 Brands mapping 
PHONE_BRANDS = {
    "1031340813395459": "ELARA",
    "1045332455333591": "AMARA",
    "1073890042476443": "VIREX UAE",
    "1138942462625909": "LUNE",
    "1002123586328400": "Dialo UAE"
}

API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"
POLL_INTERVAL_SECONDS = 15

# Known Human Agents
AGENTS = ["Ushda", "Amaan", "Nauman", "Ibrahim Taha"]

# The main labels we track from WhatChimp → Notion Status (multi-select)
TRACKED_LABELS = {"Confirmation", "Dry", "Follow-up", "Cancelled", "Closed", "Support"}

# Ensure we don't query same unchanged conversation multiple times
_last_processed_time = {}
_last_processed_labels = {}  # Cache labels to avoid redundant Notion updates
IS_FIRST_RUN = True

async def get_subscriber_list(client: httpx.AsyncClient, phone_id: str):
    """Fetch recent subscribers sorted by last message."""
    url = f"{API_BASE}/subscriber/list"
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_id,
        "limit": 20,
        "offset": 0,
        "orderBy": 1  # 1 = most recent message first
    }
    try:
        resp = await client.post(url, data=payload)
        data = resp.json()
        if str(data.get("status")) == "1" and data.get("message"):
            return data["message"]
        return []
    except Exception as e:
        log.error(f"Failed to fetch subscriber list for {phone_id}: {e}")
        return []

async def get_conversation(client: httpx.AsyncClient, phone_id: str, phone_number: str):
    """Fetch recent conversation events for a customer."""
    url = f"{API_BASE}/get/conversation"
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_id,
        "phone_number": phone_number,
        "limit": 20,
        "offset": 0
    }
    try:
        resp = await client.post(url, data=payload)
        data = resp.json()
        if str(data.get("status")) == "1" and data.get("message"):
            msg_data = data["message"]
            # WhatChimp PHP returns data in wildly inconsistent formats:
            #   - A JSON string that needs parsing
            #   - A list of message dicts (normal)
            #   - A single message dict
            #   - A dict with numeric string keys {"0": {...}, "1": {...}}
            if isinstance(msg_data, str):
                msg_data = json.loads(msg_data)
            if isinstance(msg_data, dict):
                # Check if it's a dict with numeric keys (PHP array quirk)
                if all(k.isdigit() for k in msg_data.keys()):
                    return list(msg_data.values())
                else:
                    return [msg_data]
            return msg_data
        return []
    except Exception as e:
        return []

def calculate_response_speed(created_at_str: str, actioned_at_str: str = None) -> str:
    """Calculates response speed based on true Business Hours Elapsed."""
    if not created_at_str:
        return "Medium"
    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        
        # If we have the exact agent action time from WhatChimp, use it. Otherwise use now.
        if actioned_at_str:
            actioned_at = datetime.fromisoformat(actioned_at_str.replace("Z", "+00:00"))
        else:
            actioned_at = datetime.now(timezone.utc)
            
        # Pakistan is UTC+5, shift is 12:00 to 22:00
        pkt_tz = timezone(timedelta(hours=5))
        
        start_dt = created_at.astimezone(pkt_tz)
        end_dt = actioned_at.astimezone(pkt_tz)

        if end_dt <= start_dt:
            return "Fast"

        total_seconds = 0.0
        current = start_dt

        while current < end_dt:
            shift_start = current.replace(hour=12, minute=0, second=0, microsecond=0)
            shift_end = current.replace(hour=22, minute=0, second=0, microsecond=0)

            if current < shift_start:
                current = shift_start
            elif current >= shift_end:
                current = (current + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
            else:
                chunk_end = min(end_dt, shift_end)
                total_seconds += (chunk_end - current).total_seconds()
                current = chunk_end

        diff_mins = total_seconds / 60.0
        
        if diff_mins <= 5.0: return "Fast"
        elif diff_mins <= 15.0: return "Medium"
        else: return "Slow"
        
    except Exception as e:
        log.error(f"Error calculating response speed: {e}")
        return "Medium"

def process_chat(brand: str, phone_number: str, conversation: list, subscriber_info: dict, is_first_run: bool = False):
    if not conversation:
        return
        
    # PHP APIs sometimes return flat dict if only 1 item exists instead of list
    if isinstance(conversation, dict):
        conversation = [conversation]
        
    # Filter out anything that isn't a dictionary just to be perfectly type-safe
    valid_msgs = [msg for msg in conversation if isinstance(msg, dict)]
    if not valid_msgs:
        return
        
    # Sort messages so [0] is the latest by conversation_time
    conversation = sorted(valid_msgs, key=lambda msg: msg.get("conversation_time", ""), reverse=True)
    
    # Build a composite fingerprint of the conversation state
    # This catches ANY change - new customer msg, new agent msg, anything
    latest_time = conversation[0].get("conversation_time", "")
    dedup_key = f"{phone_number}_{brand}"
    if _last_processed_time.get(dedup_key) == latest_time:
        return
    _last_processed_time[dedup_key] = latest_time
    
    if is_first_run:
        return

    # ── Scan ALL recent messages to find the latest "user" and latest "agent" message ──
    latest_user_msg = None
    latest_agent_msg = None
    latest_agent_name = None
    
    for msg in conversation:
        sender = msg.get("sender")
        agent_name = msg.get("agent_name")
        
        if sender == "user" and not latest_user_msg:
            latest_user_msg = msg
        
        if sender == "bot" and agent_name in AGENTS and not latest_agent_msg:
            latest_agent_msg = msg
            latest_agent_name = agent_name
        
        # Stop early once we found both
        if latest_user_msg and latest_agent_msg:
            break
    
    existing_ticket = notion.find_chat_by_phone(phone_number)
    
    # ── INBOUND RULE: If there's a customer message and no ticket yet → create one ──
    if latest_user_msg and not existing_ticket:
        customer_time = latest_user_msg.get("conversation_time", "")
        log.info(f"🆕 [NEW] Customer {phone_number} messaged {brand}. Creating ticket...")
        notion.create_chat(phone_number, brand, created_at=customer_time)
        existing_ticket = notion.find_chat_by_phone(phone_number)
    
    # ── PING-PONG RULE: If the MOST RECENT message is from the customer → flip to Pending ──
    if latest_user_msg and existing_ticket:
        # Check if the customer's message is newer than the agent's
        user_time = latest_user_msg.get("conversation_time", "")
        agent_time = latest_agent_msg.get("conversation_time", "") if latest_agent_msg else ""
        
        if user_time > agent_time:
            outcome_prop = existing_ticket.get("properties", {}).get("Outcome", {}).get("select")
            current_outcome = outcome_prop.get("name") if outcome_prop else ""
            if current_outcome != "Pending":
                log.info(f"🔄 [PING-PONG] Customer {phone_number} replied back on {brand}.")
                notion.update_chat_to_pending(existing_ticket["id"])
            return
    
    # ── AGENT RULE: If there's a human agent message → lock agent & update ──
    if latest_agent_msg and existing_ticket:
        ticket_id = existing_ticket["id"]
        props = existing_ticket.get("properties", {})
        
        assigned_prop = props.get("Agent Assigned", {}).get("select")
        assigned_agent = assigned_prop.get("name") if assigned_prop else "Unassigned"
        
        actioned_at_prop = props.get("Actioned At", {}).get("date")
        is_first_reply = True if not actioned_at_prop else False
        
        # Option A Logic - First-To-Claim
        if assigned_agent == "Unassigned" or assigned_agent == latest_agent_name:
            outcome_prop = props.get("Outcome", {}).get("select")
            current_outcome = outcome_prop.get("name") if outcome_prop else ""
            
            if current_outcome != "No response" or assigned_agent == "Unassigned":
                log.info(f"👨‍💻 [AGENT LOCK] {latest_agent_name} actioned {phone_number} on {brand}.")
                
                response_speed = None
                if is_first_reply:
                    created_at_prop = props.get("Created At", {}).get("date", {})
                    created_at_val = created_at_prop.get("start") if created_at_prop else None
                    agent_time = latest_agent_msg.get("conversation_time", "")
                    response_speed = calculate_response_speed(created_at_val, agent_time)
                
                agent_time = latest_agent_msg.get("conversation_time", "")
                notion.update_chat_to_agent(ticket_id, latest_agent_name, is_first_reply, response_speed, actioned_at=agent_time)
        return

def sync_labels(phone_number: str, brand: str, subscriber_info: dict, is_first_run: bool = False):
    """Sync WhatChimp labels → Notion Status (multi-select)."""
    if is_first_run:
        return
    
    # Parse the comma-separated label_names string from WhatChimp
    raw_labels = subscriber_info.get("label_names") or ""
    if isinstance(raw_labels, str):
        current_labels = [l.strip() for l in raw_labels.split(",") if l.strip()]
    else:
        current_labels = []
    
    # Filter to only our tracked labels
    matched_labels = [l for l in current_labels if l in TRACKED_LABELS]
    
    if not matched_labels:
        return
    
    # Dedup — skip if labels haven't changed since last poll
    dedup_key = f"{phone_number}_{brand}_labels"
    label_fingerprint = ",".join(sorted(matched_labels))
    if _last_processed_labels.get(dedup_key) == label_fingerprint:
        return
    _last_processed_labels[dedup_key] = label_fingerprint
    
    # Find the existing ticket in Notion
    existing_ticket = notion.find_chat_by_phone(phone_number)
    if not existing_ticket:
        return
    
    # Compare with current Notion Status values
    status_prop = existing_ticket.get("properties", {}).get("Status", {}).get("multi_select", [])
    current_notion_labels = sorted([s.get("name", "") for s in status_prop])
    
    if sorted(matched_labels) != current_notion_labels:
        log.info(f"🏷️ [LABEL SYNC] {phone_number} on {brand}: {matched_labels}")
        notion.update_chat_status(existing_ticket["id"], matched_labels)

async def poll_all_channels():
    global IS_FIRST_RUN
    async with httpx.AsyncClient(timeout=90.0) as client:
        # Loop through all 5 brands
        for phone_id, brand_name in PHONE_BRANDS.items():
            try:
                subscribers = await get_subscriber_list(client, phone_id)
                # Check top 20 active subscribers per brand
                for sub in subscribers[:20]:
                    clean_phone = sub.get("chat_id")
                    if not clean_phone: continue
                    
                    # Sync labels first (lightweight — no conversation fetch needed)
                    sync_labels(clean_phone, brand_name, sub, IS_FIRST_RUN)
                    
                    # Then check conversation for message-based rules
                    conversation = await get_conversation(client, phone_id, clean_phone)
                    process_chat(brand_name, clean_phone, conversation, sub, IS_FIRST_RUN)
                    
                    await asyncio.sleep(0.3)
            except Exception as e:
                import traceback
                log.error(f"Error polling brand {brand_name}: {e}\n{traceback.format_exc()}")
                
    if IS_FIRST_RUN:
        log.info("🌟 Initial memory warmup complete. The bot is now exclusively listening for NEW messages.")
        IS_FIRST_RUN = False

async def main_loop():
    log.info("=" * 60)
    log.info("  RPGRQ Leads & Agent Tracking Bot")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Polling {len(PHONE_BRANDS)} channels every {POLL_INTERVAL_SECONDS}s")
    log.info("=" * 60)

    while True:
        try:
            await poll_all_channels()
        except Exception as e:
            log.error(f"Poller crashed: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("Tracking bot stopped by user.")
