"""
WhatChimp client for the RPGRQ Leads Bot.

Only two endpoints used:
  - POST /subscriber/chat/assign-to-team-member   (round-robin assignment)
  - POST /get/conversation                        (fetch last message to read agent_name)
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional
import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("rpgrq.whatchimp")

WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "").strip()
API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

# ────────────────────────────────────────────────────────────
# Brand / phone_number_id mapping
# ────────────────────────────────────────────────────────────
# Canonical brand names are ALL CAPS to match existing Notion options.
# Webhook payloads send whatsapp_bot_name in TitleCase (e.g. "Virex UAE"),
# so we normalize via this alias map.
BRAND_BY_PHONE_ID = {
    "1031340813395459": "ELARA",
    "1045332455333591": "AMARA",
    "1073890042476443": "VIREX UAE",
    "1138942462625909": "LUNE",
    "1002123586328400": "DIALO UAE",
}

BRAND_ALIASES = {
    "elara": "ELARA",
    "elara uae": "ELARA",
    "amara": "AMARA",
    "amaras room": "AMARA",
    "amara's room": "AMARA",
    "virex uae": "VIREX UAE",
    "virex": "VIREX UAE",
    "lune": "LUNE",
    "lune collection": "LUNE",
    "dialo uae": "DIALO UAE",
    "dialo": "DIALO UAE",
}


def normalize_brand(raw: str) -> Optional[str]:
    """Map a webhook whatsapp_bot_name to a canonical brand, or None if unknown."""
    if not raw:
        return None
    key = raw.strip().lower()
    return BRAND_ALIASES.get(key)


def phone_id_to_brand(phone_id: str) -> Optional[str]:
    return BRAND_BY_PHONE_ID.get(phone_id)


def brand_to_phone_id(brand: str) -> Optional[str]:
    for pid, b in BRAND_BY_PHONE_ID.items():
        if b == brand:
            return pid
    return None


# ────────────────────────────────────────────────────────────
# API calls
# ────────────────────────────────────────────────────────────

async def assign_to_team_member(
    client: httpx.AsyncClient,
    phone_number_id: str,
    phone_number: str,
    team_member_id: int,
) -> bool:
    """Assign a chat to a team member. Returns True on success."""
    url = f"{API_BASE}/subscriber/chat/assign-to-team-member"
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_number_id,
        "phone_number": phone_number,
        "team_member_id": team_member_id,
    }
    try:
        resp = await client.post(url, data=payload, timeout=15)
        data = resp.json()
        if str(data.get("status")) == "1":
            return True
        log.error(f"assign-to-team-member non-success: {data}")
        return False
    except Exception as e:
        log.error(f"assign-to-team-member({phone_number}, {team_member_id}) failed: {e}")
        return False


async def get_latest_message(
    client: httpx.AsyncClient,
    phone_number_id: str,
    phone_number: str,
) -> Optional[dict]:
    """
    Fetch the most recent message from /get/conversation.
    Returns the message dict (with sender, agent_name, conversation_time)
    or None on failure/empty.
    """
    url = f"{API_BASE}/get/conversation"
    payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_number_id,
        "phone_number": phone_number,
        "limit": 5,
        "offset": 0,
    }
    try:
        resp = await client.post(url, data=payload, timeout=20)
        data = resp.json()
    except Exception as e:
        log.error(f"get/conversation({phone_number}) failed: {e}")
        return None

    if str(data.get("status")) != "1":
        return None

    msg_data = data.get("message")
    if msg_data is None:
        return None

    # WhatChimp's PHP API returns this in several shapes; normalize.
    if isinstance(msg_data, str):
        try:
            msg_data = json.loads(msg_data)
        except Exception:
            return None

    if isinstance(msg_data, dict):
        if all(isinstance(k, str) and k.isdigit() for k in msg_data.keys()):
            msgs = list(msg_data.values())
        else:
            msgs = [msg_data]
    elif isinstance(msg_data, list):
        msgs = msg_data
    else:
        return None

    valid = [m for m in msgs if isinstance(m, dict)]
    if not valid:
        return None

    # Sort newest first
    valid.sort(key=lambda m: m.get("conversation_time", ""), reverse=True)
    return valid[0]
