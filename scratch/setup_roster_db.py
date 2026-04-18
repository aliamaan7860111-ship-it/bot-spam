"""
One-off setup script for the RPGRQ Agent Roster DB.

Goals:
  1. Verify we can reach the DB with the .env NOTION_API_KEY.
  2. Inspect current schema.
  3. Ensure required columns exist (add missing ones via PATCH).
  4. Ensure the 3 active agents are present as rows.
  5. Print a final mapping report so the user can verify.

Schema (target):
  - Name               (title)
  - Team Member ID     (number)
  - Active             (checkbox)
  - Shift Start Hour   (number)
  - Shift End Hour     (number)

Agents (target rows):
  - Amaan   / 278343 / Active=True / 12 / 20
  - Nauman  / 278340 / Active=True / 12 / 20
  - Ushda   / 269976 / Active=True / 12 / 20
"""

import os
import sys
import httpx
from pathlib import Path
from dotenv import load_dotenv

# Force UTF-8 stdout on Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "").strip()
ROSTER_DB_ID = "346c320eba598175969dd15472249081"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

AGENTS = [
    {"name": "Amaan",  "team_member_id": 278343, "active": True, "shift_start": 12, "shift_end": 20},
    {"name": "Nauman", "team_member_id": 278340, "active": True, "shift_start": 12, "shift_end": 20},
    {"name": "Ushda",  "team_member_id": 269976, "active": True, "shift_start": 12, "shift_end": 20},
]

# Required columns keyed by their display name
REQUIRED_COLUMNS = {
    "Name":             {"title": {}},
    "Team Member ID":   {"number": {"format": "number"}},
    "Active":           {"checkbox": {}},
    "Shift Start Hour": {"number": {"format": "number"}},
    "Shift End Hour":   {"number": {"format": "number"}},
}


def get_db_schema():
    """Retrieve the database object to see its current properties."""
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}"
    resp = httpx.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ensure_columns(current_properties: dict) -> bool:
    """Patch the DB to add any missing columns. Returns True if changes were made."""
    missing = {}
    for col_name, col_spec in REQUIRED_COLUMNS.items():
        if col_name not in current_properties:
            missing[col_name] = col_spec

    if not missing:
        print("  All required columns already exist ✓")
        return False

    print(f"  Adding missing columns: {list(missing.keys())}")
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}"
    payload = {"properties": missing}
    resp = httpx.patch(url, headers=HEADERS, json=payload, timeout=15)
    if resp.status_code != 200:
        print(f"  ERROR adding columns: {resp.status_code} {resp.text}")
        return False
    print("  Columns added ✓")
    return True


def find_existing_row_by_name(name: str) -> dict | None:
    """Query the DB for a row with matching Name."""
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}/query"
    payload = {
        "filter": {"property": "Name", "title": {"equals": name}}
    }
    resp = httpx.post(url, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


def create_or_update_agent(agent: dict):
    """Ensure a row exists for this agent with the correct values."""
    existing = find_existing_row_by_name(agent["name"])
    props = {
        "Name":             {"title": [{"text": {"content": agent["name"]}}]},
        "Team Member ID":   {"number": agent["team_member_id"]},
        "Active":           {"checkbox": agent["active"]},
        "Shift Start Hour": {"number": agent["shift_start"]},
        "Shift End Hour":   {"number": agent["shift_end"]},
    }

    if existing:
        page_id = existing["id"]
        url = f"{NOTION_API_BASE}/pages/{page_id}"
        resp = httpx.patch(url, headers=HEADERS, json={"properties": props}, timeout=15)
        if resp.status_code == 200:
            print(f"  Updated existing row for {agent['name']} ✓")
        else:
            print(f"  ERROR updating {agent['name']}: {resp.status_code} {resp.text}")
    else:
        url = f"{NOTION_API_BASE}/pages"
        payload = {
            "parent": {"database_id": ROSTER_DB_ID},
            "properties": props,
        }
        resp = httpx.post(url, headers=HEADERS, json=payload, timeout=15)
        if resp.status_code == 200:
            print(f"  Created row for {agent['name']} ✓")
        else:
            print(f"  ERROR creating {agent['name']}: {resp.status_code} {resp.text}")


def _read_prop(row_props: dict, col_name: str, col_type: str):
    prop = row_props.get(col_name, {})
    if col_type == "title":
        arr = prop.get("title", [])
        return arr[0].get("plain_text", "") if arr else ""
    if col_type == "number":
        return prop.get("number")
    if col_type == "checkbox":
        return prop.get("checkbox")
    return None


def final_report():
    """Query the DB and print a clean mapping so the user can verify."""
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}/query"
    resp = httpx.post(url, headers=HEADERS, json={}, timeout=15)
    resp.raise_for_status()
    rows = resp.json().get("results", [])

    print("\n" + "=" * 60)
    print(" FINAL ROSTER STATE")
    print("=" * 60)
    print(f" {'Name':<10} {'ID':>8} {'Active':>8} {'Shift Start':>12} {'Shift End':>10}")
    print(" " + "-" * 58)
    for row in rows:
        p = row.get("properties", {})
        name = _read_prop(p, "Name", "title")
        tmid = _read_prop(p, "Team Member ID", "number")
        active = _read_prop(p, "Active", "checkbox")
        start = _read_prop(p, "Shift Start Hour", "number")
        end = _read_prop(p, "Shift End Hour", "number")
        print(f" {name:<10} {tmid!s:>8} {active!s:>8} {start!s:>12} {end!s:>10}")
    print("=" * 60)


def main():
    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY is not set in .env")
        sys.exit(1)

    print(f"Roster DB: {ROSTER_DB_ID}")
    print("\n[1/4] Fetching current DB schema...")
    try:
        db = get_db_schema()
    except httpx.HTTPStatusError as e:
        print(f"  ERROR: {e.response.status_code} {e.response.text}")
        print("\nLikely cause: the Notion integration (associated with your API key)")
        print("does not have access to this DB. Open the DB in Notion, click '...',")
        print("'Connections', and add the integration.")
        sys.exit(1)

    props = db.get("properties", {})
    print(f"  Current columns: {list(props.keys())}")

    print("\n[2/4] Ensuring required columns exist...")
    ensure_columns(props)

    print("\n[3/4] Ensuring each agent has a row...")
    for agent in AGENTS:
        create_or_update_agent(agent)

    print("\n[4/4] Verifying final state...")
    final_report()

    print("\nDone. Please confirm the table above looks correct.")


if __name__ == "__main__":
    main()
