"""
Notion client for the RPGRQ Leads Bot.

Two databases:
  - Leads DB:   344c320eba59800a90a6e804a575d272   (CRM tickets)
  - Roster DB:  346c320eba598175969dd15472249081   (agents, IDs, shifts)

Ticket identity = (Phone Number, Source (Store)). One ticket per brand per phone.

All functions are async and use a shared httpx.AsyncClient.
"""

import os
import logging
import time
from pathlib import Path
from typing import Optional
import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("rpgrq.notion")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
LEADS_DB_ID    = "344c320eba59800a90a6e804a575d272"
ROSTER_DB_ID   = "346c320eba598175969dd15472249081"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION  = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# ── Roster cache ──
_ROSTER_CACHE: list[dict] = []
_ROSTER_CACHE_AT: float = 0.0
ROSTER_TTL_SECONDS = 60


# ────────────────────────────────────────────────────────────
# Roster DB
# ────────────────────────────────────────────────────────────

async def get_active_roster(client: httpx.AsyncClient, force_refresh: bool = False) -> list[dict]:
    """
    Return a list of active agents sorted alphabetically by Name.
    Each dict: {name, team_member_id, shift_start, shift_end}.
    Cached for ROSTER_TTL_SECONDS.
    """
    global _ROSTER_CACHE, _ROSTER_CACHE_AT
    now = time.time()
    if not force_refresh and (now - _ROSTER_CACHE_AT) < ROSTER_TTL_SECONDS and _ROSTER_CACHE:
        return _ROSTER_CACHE

    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}/query"
    payload = {"filter": {"property": "Active", "checkbox": {"equals": True}}}
    try:
        resp = await client.post(url, headers=HEADERS, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Roster fetch failed: {e}")
        return _ROSTER_CACHE  # return stale cache rather than fail

    rows = resp.json().get("results", [])
    roster = []
    for row in rows:
        p = row.get("properties", {})
        title_arr = p.get("Name", {}).get("title", [])
        name = title_arr[0].get("plain_text", "").strip() if title_arr else ""
        if not name:
            continue
        roster.append({
            "name": name,
            "team_member_id": p.get("Team Member ID", {}).get("number"),
            "user_id": p.get("WhatChimp User ID", {}).get("number"),
            "shift_start": p.get("Shift Start Hour", {}).get("number"),
            "shift_end": p.get("Shift End Hour", {}).get("number"),
        })
    roster.sort(key=lambda a: a["name"])
    _ROSTER_CACHE = roster
    _ROSTER_CACHE_AT = now
    log.info(f"Roster refreshed: {[a['name'] for a in roster]}")
    return roster


# ────────────────────────────────────────────────────────────
# Leads DB — queries
# ────────────────────────────────────────────────────────────

async def find_ticket(client: httpx.AsyncClient, phone: str, brand: str) -> Optional[dict]:
    """Look up a ticket by (phone, brand). Returns the Notion page or None."""
    payload = {
        "filter": {
            "and": [
                {"property": "Phone Number", "rich_text": {"equals": phone.strip()}},
                {"property": "Source (Store)", "select": {"equals": brand}},
            ]
        }
    }
    try:
        resp = await client.post(
            f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
            headers=HEADERS, json=payload, timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception as e:
        log.error(f"find_ticket({phone}, {brand}) failed: {e}")
        return None


async def get_last_assigned_agent(client: httpx.AsyncClient, valid_agent_names: list[str]) -> Optional[str]:
    """
    Return the Agent Assigned name from the most recently created ticket
    where Agent Assigned is one of valid_agent_names. Used for round-robin derivation.
    """
    if not valid_agent_names:
        return None

    payload = {
        "filter": {
            "or": [
                {"property": "Agent Assigned", "select": {"equals": n}}
                for n in valid_agent_names
            ]
        },
        "sorts": [{"property": "Created At", "direction": "descending"}],
        "page_size": 1,
    }
    try:
        resp = await client.post(
            f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
            headers=HEADERS, json=payload, timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        prop = results[0].get("properties", {}).get("Agent Assigned", {}).get("select")
        return prop.get("name") if prop else None
    except Exception as e:
        log.error(f"get_last_assigned_agent failed: {e}")
        return None


# ────────────────────────────────────────────────────────────
# Leads DB — writes
# ────────────────────────────────────────────────────────────

async def create_ticket(
    client: httpx.AsyncClient,
    phone: str,
    brand: str,
    agent_name: str,
    created_at_iso: str,
) -> Optional[str]:
    """Create a fresh ticket. Returns the new page id or None."""
    properties = {
        "Name":                   {"title": [{"text": {"content": phone}}]},
        "Phone Number":           {"rich_text": [{"text": {"content": phone}}]},
        "Source (Store)":         {"select": {"name": brand}},
        "Status":                 {"multi_select": [{"name": "Waiting"}]},
        "Outcome":                {"select": {"name": "Pending"}},
        "Created At":             {"date": {"start": created_at_iso}},
        "Last Customer Message":  {"date": {"start": created_at_iso}},
        "Agent Assigned":         {"select": {"name": agent_name}},
    }
    payload = {"parent": {"database_id": LEADS_DB_ID}, "properties": properties}
    try:
        resp = await client.post(
            f"{NOTION_API_BASE}/pages", headers=HEADERS, json=payload, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as e:
        log.error(f"create_ticket({phone}, {brand}) failed: {e}")
        return None


async def reopen_ticket(
    client: httpx.AsyncClient,
    page_id: str,
    agent_name: str,
    reopened_at_iso: str,
) -> bool:
    """
    Revive a Closed ticket:
      - overwrite Created At with new message time
      - Status -> [Active]
      - Outcome -> Pending
      - Agent Assigned -> fresh round-robin
      - clear Actioned At / Response Speed so the new response-speed clock can run
    """
    properties = {
        "Created At":            {"date": {"start": reopened_at_iso}},
        "Last Customer Message": {"date": {"start": reopened_at_iso}},
        "Status":                {"multi_select": [{"name": "Active"}]},
        "Outcome":               {"select": {"name": "Pending"}},
        "Agent Assigned":        {"select": {"name": agent_name}},
        "Actioned At":           {"date": None},
        "Response Speed":        {"select": None},
    }
    return await _update_page(client, page_id, properties)


async def set_pending(client: httpx.AsyncClient, page_id: str) -> bool:
    """Ping-pong: customer replied, flip Outcome back to Pending."""
    return await _update_page(
        client, page_id, {"Outcome": {"select": {"name": "Pending"}}}
    )


async def stamp_agent_reply(
    client: httpx.AsyncClient,
    page_id: str,
    reply_time_iso: str,
    is_first_reply: bool,
    response_speed: Optional[str],
) -> bool:
    """
    A real agent replied. Stamp:
      - Last Agent Reply (always)
      - Outcome = No Response (always)
      - Actioned At + Response Speed (only on first reply)
    Never changes Agent Assigned.
    """
    properties = {
        "Last Agent Reply": {"date": {"start": reply_time_iso}},
        "Outcome":          {"select": {"name": "No Response"}},
    }
    if is_first_reply:
        properties["Actioned At"] = {"date": {"start": reply_time_iso}}
        if response_speed:
            properties["Response Speed"] = {"select": {"name": response_speed}}
    return await _update_page(client, page_id, properties)


async def stamp_customer_message(
    client: httpx.AsyncClient,
    page_id: str,
    message_time_iso: str,
) -> bool:
    """Stamp Last Customer Message on every inbound event."""
    return await _update_page(
        client, page_id, {"Last Customer Message": {"date": {"start": message_time_iso}}}
    )


async def update_status_labels(
    client: httpx.AsyncClient,
    page_id: str,
    labels: list[str],
) -> bool:
    """Overwrite the Status multi-select with the given label list."""
    multi_select = [{"name": l} for l in labels]
    return await _update_page(
        client, page_id, {"Status": {"multi_select": multi_select}}
    )


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

async def _update_page(client: httpx.AsyncClient, page_id: str, properties: dict) -> bool:
    try:
        resp = await client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=HEADERS,
            json={"properties": properties},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"_update_page({page_id}) failed: {e}")
        return False


def ticket_has_closed(ticket: dict) -> bool:
    """True if the ticket's Status multi-select contains 'Closed'."""
    status_arr = ticket.get("properties", {}).get("Status", {}).get("multi_select", [])
    return any(s.get("name") == "Closed" for s in status_arr)


def ticket_actioned_at(ticket: dict) -> Optional[str]:
    """Return the Actioned At ISO string, or None if unset."""
    prop = ticket.get("properties", {}).get("Actioned At", {}).get("date")
    return prop.get("start") if prop else None


def ticket_created_at(ticket: dict) -> Optional[str]:
    prop = ticket.get("properties", {}).get("Created At", {}).get("date")
    return prop.get("start") if prop else None


def ticket_status_names(ticket: dict) -> list[str]:
    status_arr = ticket.get("properties", {}).get("Status", {}).get("multi_select", [])
    return [s.get("name", "") for s in status_arr]


def ticket_agent_assigned(ticket: dict) -> Optional[str]:
    prop = ticket.get("properties", {}).get("Agent Assigned", {}).get("select")
    return prop.get("name") if prop else None
