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
# The CRM "Source (Store)" model = exactly 5 labels:
#   Amara / Rimal / Dialo (individual numbers)
#   Customer Care        (shared number — stores: Orlento, Velix, Lune)
#   Shopping Assistance  (shared number — stores: Elara, Diwan, Pelvini, Viresta)
# Every pnid a customer might be on (new + old) resolves to one of these 5.
BRAND_BY_PHONE_ID = {
    # --- 2026-09 Saudi portfolio: nine stores on their OWN numbers -------------
    # Each number serves exactly one store, so leads resolve to that brand.
    "1253320094539905": "Amara",               # Amara's Room
    "1199932539879999": "Amara's Watches",
    "1397321910124444": "Rimal",
    "1327104710486442": "Orlento",
    "1268960389639645": "Saqr",
    "1234704216403689": "Lune",
    "1306342719226934": "Velix",
    "1354836361042768": "Wrist Gallery",
    "1260047933866516": "Viresta",
    # --- legacy numbers (kept: customers may still be on them) ----------------
    # A SHARED number cannot identify the store, so these must keep the grouped
    # label. Do not "upgrade" them to a per-brand label.
    "1309764938876096": "Amara",               # Amara, previous number
    "1045332455333591": "Amara",               # Amara, oldest number
    "1223004617567784": "Rimal",               # Rimal, previous number
    "1304894276030064": "Dialo",               # Dialo new
    "1002123586328400": "Dialo",               # Dialo old
    "1148388868368542": "Customer Care",       # shared: Orlento/Velix/Lune — ambiguous
    "1138942462625909": "Customer Care",       # old Lune number folds in
    "1238071629387272": "Shopping Assistance", # shared: Elara/Diwan/Pelvini (+Viresta before it split)
    "1031340813395459": "Shopping Assistance", # old Elara number folds in
}

BRAND_ALIASES = {
    "amara": "Amara", "amaras room": "Amara", "amara's room": "Amara",
    "amaras room dubai": "Amara", "amara's room dubai": "Amara",
    "rimal": "Rimal", "rimal uae": "Rimal",
    "dialo": "Dialo", "dialo uae": "Dialo",
    # Stores that now have their OWN number resolve to their own label. bot_id is
    # checked BEFORE bot_name, so a lead arriving on an old shared bot still lands
    # on the grouped label via WHATCHIMP_BOT_ID_TO_BRAND below.
    "lune": "Lune", "lune collection": "Lune",
    "orlento": "Orlento", "orlento uae": "Orlento",
    "velix": "Velix", "velix uae": "Velix",
    "viresta": "Viresta", "viresta uae": "Viresta",
    "amaras watches": "Amara's Watches", "amara's watches": "Amara's Watches",
    "saqr": "Saqr", "saqr uae": "Saqr",
    "wrist gallery": "Wrist Gallery", "wrist gallery uae": "Wrist Gallery",
    # Grouped labels — still correct for the shared numbers behind them.
    "customer care": "Customer Care",
    # Shopping Assistance group (Elara / Diwan / Pelvini still share one number)
    "shopping assistance": "Shopping Assistance",
    "elara": "Shopping Assistance", "elara uae": "Shopping Assistance",
    "diwan": "Shopping Assistance", "pelvini": "Shopping Assistance",
}

# Primary source of truth: whatsapp_bot_id (stable, unique per WhatChimp bot).
# bot_id wins over bot_name — important for the old Elara bot, which is literally
# NAMED "Customer Care" in WhatChimp but belongs to the Shopping Assistance group.
# New-number bot_ids are TBD (capture from first inbound and add here).
WHATCHIMP_BOT_ID_TO_BRAND = {
    "382073": "Dialo",               # old Dialo
    "382036": "Amara",               # old Amara
    "352261": "Shopping Assistance", # old Elara bot (named "Customer Care" in WC)
    "382778": "Customer Care",       # old Lune bot (Customer Care group)
}


# Canonical source labels the CRM expects. Anything outside this set that shows
# up as a resolved source means a new bot name/id we haven't mapped yet.
CANONICAL_SOURCES = {
    # Per-brand (own number since the 2026-09 Saudi portfolio migration)
    "Amara", "Amara's Watches", "Rimal", "Orlento", "Saqr",
    "Lune", "Velix", "Wrist Gallery", "Viresta", "Dialo",
    # Still grouped: Elara / Diwan / Pelvini share one number, and legacy shared
    # numbers can't be resolved to a single store.
    "Customer Care", "Shopping Assistance",
}

# Deliberately NOT tracked (Virex is not a store). Leads matching these are
# acknowledged (200) and dropped — no ticket, no assignment, no error alert.
IGNORED_BOT_IDS = {"381990"}                # old Virex WhatChimp bot
IGNORED_PHONE_IDS = {"1073890042476443"}    # Virex number
IGNORED_NAME_SUBSTRINGS = ("virex",)


def is_ignored(bot_id: str, bot_name: str, phone_id: str = "") -> bool:
    """True for sources we intentionally don't track (e.g. Virex)."""
    if bot_id and str(bot_id).strip() in IGNORED_BOT_IDS:
        return True
    if phone_id and str(phone_id).strip() in IGNORED_PHONE_IDS:
        return True
    name = (bot_name or "").strip().lower()
    return any(s in name for s in IGNORED_NAME_SUBSTRINGS)


def bot_id_to_brand(bot_id: str) -> Optional[str]:
    if not bot_id:
        return None
    return WHATCHIMP_BOT_ID_TO_BRAND.get(str(bot_id).strip())


def resolve_source(bot_id: str, bot_name: str, phone_id: str = "") -> str:
    """
    Best-effort CRM 'Source (Store)' label. NEVER returns None — a lead must
    never be dropped for want of a clean brand. Resolution order:
      1. whatsapp_bot_id  -> brand           (stable, unique per bot)
      2. phone_number_id  -> brand           (authoritative: 1:1 per store)
      3. whatsapp_bot_name -> alias          (clean label, but names can be stale)
      4. bot_id treated as phone_id          (legacy payload shape)
      5. raw bot_name                        (preserved so we can map it later)
      6. "Unknown"                           (last resort — still captured)

    pnid is checked BEFORE bot_name as of the 2026-09 migration: every new store
    has its own number, so BRAND_BY_PHONE_ID identifies the store exactly, whereas
    a bot's display name may be generic or reused. For legacy SHARED numbers the
    pnid returns the same grouped label the alias would, so nothing regresses.
    """
    return (
        bot_id_to_brand(bot_id)
        or (phone_id_to_brand(phone_id) if phone_id else None)
        or normalize_brand(bot_name)
        or phone_id_to_brand(bot_id)
        or (bot_name.strip() if bot_name and bot_name.strip() else None)
        or "Unknown"
    )


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


def brand_to_phone_ids(brand: str) -> list:
    """
    ALL phone_number_ids for a brand (new listed first, then old). During the
    number migration a brand has two live numbers; a given customer sits on
    exactly one. Callers try each until the subscriber/conversation is found,
    so assignment + reply-tracking work regardless of which number they use.
    """
    return [pid for pid, b in BRAND_BY_PHONE_ID.items() if b == brand]


def phone_id_candidates(brand: str, payload_phone_id: str = "") -> list:
    """Ordered, de-duped pnid candidates: the payload's own pnid first (most
    accurate), then every pnid mapped to the brand."""
    out = []
    for pid in ([payload_phone_id] if payload_phone_id else []) + brand_to_phone_ids(brand):
        if pid and pid not in out:
            out.append(pid)
    return out


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
        # Non-fatal: the Notion round-robin assignment already happened; this is
        # only the WhatChimp-side chat routing. Common during migration when a
        # customer is on an old number whose pnid we don't have. Warn, don't alert.
        log.warning(f"assign-to-team-member non-success (non-fatal): {data}")
        return False
    except Exception as e:
        log.warning(f"assign-to-team-member({phone_number}, {team_member_id}) failed (non-fatal): {e}")
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


async def get_latest_message_any(
    client: httpx.AsyncClient,
    phone_ids: list,
    phone_number: str,
):
    """Try each candidate pnid; return (pnid, latest_message) for the first that
    has any conversation history. Handles a customer sitting on the old number
    of a brand that also has a new number. Returns (None, None) if none match."""
    for pid in phone_ids:
        msg = await get_latest_message(client, pid, phone_number)
        if msg:
            return pid, msg
    return None, None


async def assign_to_team_member_any(
    client: httpx.AsyncClient,
    phone_ids: list,
    phone_number: str,
    team_member_id: int,
) -> Optional[str]:
    """Try to assign on each candidate pnid; return the pnid that succeeded (the
    number the subscriber actually lives on), or None if every attempt failed."""
    for pid in phone_ids:
        if await assign_to_team_member(client, pid, phone_number, team_member_id):
            return pid
    return None
