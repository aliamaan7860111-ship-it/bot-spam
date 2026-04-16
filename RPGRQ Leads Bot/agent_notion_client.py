import os
import logging
from datetime import datetime, timezone
import httpx
from dotenv import load_dotenv
from pathlib import Path

# Load config
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("leads_bot.notion")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
AGENT_DB_ID = "344c320eba59800a90a6e804a575d272"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def find_chat_by_phone(phone_number: str) -> dict | None:
    """Lookup an existing chat by Phone Number."""
    payload = {
        "filter": {
            "property": "Phone Number",
            "rich_text": {"equals": phone_number.strip()}
        }
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{NOTION_API_BASE}/databases/{AGENT_DB_ID}/query",
                headers=_headers(),
                json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0]
            return None
    except Exception as e:
        log.error(f"Notion query failed for phone {phone_number}: {e}")
        return None

def create_chat(phone_number: str, brand: str, created_at: str = None) -> str | None:
    """
    Creates a NEW unassigned chat in Notion.
    Status = Waiting, Outcome = Pending.
    If created_at is provided (from WhatChimp), uses that instead of now.
    """
    iso_time = created_at if created_at else datetime.now(timezone.utc).isoformat()
    
    properties = {
        "Name": {"title": [{"text": {"content": phone_number}}]},
        "Phone Number": {"rich_text": [{"text": {"content": phone_number}}]},
        "Source (Store)": {"select": {"name": brand}},
        "Status": {"select": {"name": "Waiting"}},
        "Outcome": {"select": {"name": "Pending"}},
        "Created At": {"date": {"start": iso_time}},
        "Agent Assigned": {"select": {"name": "Unassigned"}},
    }
    
    payload = {
        "parent": {"database_id": AGENT_DB_ID},
        "properties": properties
    }
    
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{NOTION_API_BASE}/pages",
                headers=_headers(),
                json=payload
            )
            resp.raise_for_status()
            return resp.json()["id"]
    except Exception as e:
        log.error(f"Notion page creation failed: {e}")
        return None

def update_chat_to_agent(page_id: str, agent_name: str, is_first_reply: bool, response_speed: str = None, actioned_at: str = None) -> bool:
    """
    Lock to agent. Sets Outcome to 'No response', updates Actioned At and Speed if first reply.
    If actioned_at is provided (from WhatChimp), uses that instead of now.
    """
    iso_time = actioned_at if actioned_at else datetime.now(timezone.utc).isoformat()
    properties = {
        "Agent Assigned": {"select": {"name": agent_name}},
        "Outcome": {"select": {"name": "No response"}},
        "Last Contact Date": {"date": {"start": iso_time}},
        "Status": {"select": {"name": "Active"}}
    }
    
    if is_first_reply:
        properties["Actioned At"] = {"date": {"start": iso_time}}
        if response_speed:
            properties["Response Speed"] = {"select": {"name": response_speed}}
            
    return _update_page(page_id, properties)

def update_chat_to_pending(page_id: str) -> bool:
    """Customer replied - set back to pending (Ping Pong)."""
    properties = {
        "Outcome": {"select": {"name": "Pending"}}
    }
    return _update_page(page_id, properties)

def update_chat_status(page_id: str, status: str) -> bool:
    """Directly update status if label changed in WhatChimp."""
    properties = {
        "Status": {"select": {"name": status}}
    }
    return _update_page(page_id, properties)

def _update_page(page_id: str, properties: dict) -> bool:
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=_headers(),
                json={"properties": properties}
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        log.error(f"Notion page update failed for {page_id}: {e}")
        return False
