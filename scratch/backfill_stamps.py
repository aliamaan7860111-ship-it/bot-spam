"""
One-off backfill for orphaned RPGRQ Leads tickets.

Scans Leads DB for tickets where Outcome != Pending but Last Agent Reply is null,
then walks /get/conversation for each, identifies the latest message with a
non-empty numeric agent_name, and stamps Last Agent Reply (and Actioned At if
also missing) based on that message's conversation_time.

Safe to re-run. Idempotent.
"""

import os
import sys
import asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from execution import rpgrq_notion as notion
from execution import rpgrq_whatchimp as wc

LEADS_DB_ID = "344c320eba59800a90a6e804a575d272"
NOTION_API_BASE = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {os.getenv('NOTION_API_KEY', '').strip()}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


async def find_orphans(client: httpx.AsyncClient) -> list[dict]:
    """Outcome != Pending but Last Agent Reply is null."""
    payload = {
        "filter": {
            "and": [
                {"property": "Outcome", "select": {"does_not_equal": "Pending"}},
                {"property": "Last Agent Reply", "date": {"is_empty": True}},
            ]
        },
        "page_size": 100,
    }
    out = []
    has_more = True
    cursor = None
    while has_more:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = await client.post(
            f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
            headers=HEADERS, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
    return out


async def main():
    fixed = 0
    skipped = 0
    async with httpx.AsyncClient(timeout=30) as client:
        orphans = await find_orphans(client)
        print(f"Found {len(orphans)} orphan tickets to inspect")
        for ticket in orphans:
            props = ticket.get("properties", {})
            phone_rt = props.get("Phone Number", {}).get("rich_text", [])
            phone = phone_rt[0].get("plain_text", "") if phone_rt else ""
            brand_sel = props.get("Source (Store)", {}).get("select")
            brand = brand_sel.get("name") if brand_sel else None
            if not phone or not brand:
                skipped += 1
                continue
            pid = wc.brand_to_phone_id(brand)
            if not pid:
                skipped += 1
                continue
            latest = await wc.get_latest_message(client, pid, phone)
            if not latest:
                skipped += 1
                continue
            agent_name = latest.get("agent_name")
            if not isinstance(agent_name, str) or not agent_name.strip().isdigit():
                skipped += 1
                continue
            sender = latest.get("sender")
            if sender != "bot":
                skipped += 1
                continue
            reply_time = latest.get("conversation_time")
            if not reply_time:
                skipped += 1
                continue
            actioned_missing = notion.ticket_actioned_at(ticket) is None
            ok = await notion.stamp_agent_reply(
                client, ticket["id"], reply_time, is_first_reply=actioned_missing, response_speed=None
            )
            if ok:
                fixed += 1
                print(f"  fixed {phone}/{brand} -> reply_time={reply_time}")
    print(f"\nDone. Fixed {fixed}. Skipped {skipped}.")


if __name__ == "__main__":
    asyncio.run(main())
