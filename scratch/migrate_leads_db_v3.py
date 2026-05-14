"""
One-off schema migration for RPGRQ Leads Bot v3.

Changes:
  Leads DB (344c320eba59800a90a6e804a575d272):
    - Convert "Agent Assigned" from select -> multi_select, preserving values.
    - Add "Closed Date" (date).
  Roster DB (346c320eba598175969dd15472249081):
    - Add "Off Days" (multi_select with 7 day-name options).

Safe to re-run. Detects already-applied changes and skips.
"""

import os
import sys
import httpx
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
LEADS_DB_ID = "344c320eba59800a90a6e804a575d272"
ROSTER_DB_ID = "346c320eba598175969dd15472249081"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def get_db(db_id: str) -> dict:
    r = httpx.get(f"{NOTION_API_BASE}/databases/{db_id}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def patch_db(db_id: str, payload: dict) -> dict:
    r = httpx.patch(f"{NOTION_API_BASE}/databases/{db_id}", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def migrate_agent_assigned_to_multiselect():
    """Convert Leads DB 'Agent Assigned' from select to multi_select, preserving rows."""
    db = get_db(LEADS_DB_ID)
    props = db.get("properties", {})
    aa = props.get("Agent Assigned", {})
    aa_type = aa.get("type")

    if aa_type == "multi_select":
        print("  Agent Assigned: already multi_select OK")
        return

    if aa_type != "select":
        raise RuntimeError(f"Unexpected Agent Assigned type: {aa_type!r}")

    # Read existing select options to preserve as multi_select options.
    existing_options = aa.get("select", {}).get("options", [])
    option_names = [o.get("name") for o in existing_options if o.get("name")]
    print(f"  Agent Assigned: converting select -> multi_select with options {option_names}")

    # Step 1: enumerate all rows and capture (page_id, current_agent_name).
    rows = []
    has_more = True
    cursor = None
    while has_more:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query", headers=HEADERS, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        for row in data.get("results", []):
            sel = row.get("properties", {}).get("Agent Assigned", {}).get("select")
            name = sel.get("name") if sel else None
            rows.append((row["id"], name))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
    print(f"  Found {len(rows)} rows to migrate")

    # Step 2: change the property definition to multi_select.
    patch_db(LEADS_DB_ID, {
        "properties": {
            "Agent Assigned": {
                "multi_select": {
                    "options": [{"name": n} for n in option_names]
                }
            }
        }
    })
    print("  Agent Assigned: column converted to multi_select OK")

    # Step 3: rewrite each row's value as a 1-item array (or empty if was null).
    for page_id, name in rows:
        ms_value = [{"name": name}] if name else []
        r = httpx.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=HEADERS,
            json={"properties": {"Agent Assigned": {"multi_select": ms_value}}},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  WARN: failed to rewrite {page_id}: {r.status_code} {r.text}")
    print(f"  Agent Assigned: {len(rows)} rows rewritten as multi_select arrays OK")


def ensure_closed_date():
    db = get_db(LEADS_DB_ID)
    if "Closed Date" in db.get("properties", {}):
        print("  Closed Date: already exists OK")
        return
    patch_db(LEADS_DB_ID, {"properties": {"Closed Date": {"date": {}}}})
    print("  Closed Date: added OK")


def ensure_off_days():
    db = get_db(ROSTER_DB_ID)
    if "Off Days" in db.get("properties", {}):
        print("  Off Days: already exists OK")
        return
    patch_db(ROSTER_DB_ID, {
        "properties": {
            "Off Days": {
                "multi_select": {
                    "options": [{"name": d} for d in DAY_NAMES]
                }
            }
        }
    })
    print("  Off Days: added with 7 day-name options OK")


def main():
    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY missing in .env")
        sys.exit(1)
    print("== RPGRQ v3 schema migration ==")
    print("\n[1/3] Migrating Leads DB Agent Assigned to multi_select...")
    migrate_agent_assigned_to_multiselect()
    print("\n[2/3] Ensuring Closed Date field exists...")
    ensure_closed_date()
    print("\n[3/3] Ensuring Off Days field exists on Roster DB...")
    ensure_off_days()
    print("\nDone.")


if __name__ == "__main__":
    main()
