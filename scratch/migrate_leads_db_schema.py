"""
One-off migration for the Leads DB:
  1. Rename "Last Contact Date" -> "Last Agent Reply".
  2. Add "Last Customer Message" (date).

Safe to re-run. Will skip renames and additions that are already in place.
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
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def main():
    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY missing in .env")
        sys.exit(1)

    url = f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}"
    resp = httpx.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    props = resp.json().get("properties", {})
    print(f"Current columns: {sorted(props.keys())}")

    patch_payload = {"properties": {}}

    # 1) Rename if the old name exists
    if "Last Contact Date" in props and "Last Agent Reply" not in props:
        patch_payload["properties"]["Last Contact Date"] = {"name": "Last Agent Reply"}
        print("  Will rename: Last Contact Date -> Last Agent Reply")
    elif "Last Agent Reply" in props:
        print("  Already renamed: Last Agent Reply exists ✓")
    else:
        print("  (no Last Contact Date to rename)")

    # 2) Add the new customer-message field if missing
    if "Last Customer Message" not in props:
        patch_payload["properties"]["Last Customer Message"] = {"date": {}}
        print("  Will add: Last Customer Message (date)")
    else:
        print("  Already exists: Last Customer Message ✓")

    if not patch_payload["properties"]:
        print("\nNothing to do.")
        return

    resp = httpx.patch(url, headers=HEADERS, json=patch_payload, timeout=15)
    if resp.status_code != 200:
        print(f"\nERROR: {resp.status_code} {resp.text}")
        sys.exit(1)

    print("\nSchema updated successfully.")

    # Verify
    resp = httpx.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    new_props = resp.json().get("properties", {})
    print(f"New columns: {sorted(new_props.keys())}")


if __name__ == "__main__":
    main()
