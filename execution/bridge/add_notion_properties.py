"""
One-shot: add the four new properties to the existing Abandoned Checkout
Recovery Notion DB. Idempotent — re-running just patches missing ones.

Adds:
- Order Number (rich_text)
- Order Total (number)
- Discount Applied (checkbox)
- Raw Checkout Data (rich_text)

Also extends the Status select with: Customer Completed Order,
Order Placement Failed (the existing options stay).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DB_ID = os.environ["RECOVERY_NOTION_DATABASE_ID"]
TOKEN = os.environ["NOTION_API_KEY"]

EXTRA_STATUS_OPTIONS = [
    {"name": "Customer Completed Order", "color": "yellow"},
    {"name": "Order Placement Failed", "color": "red"},
]


def main() -> int:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    # First read the current DB to get existing Status options (so we extend, not replace).
    r = requests.get(f"https://api.notion.com/v1/databases/{DB_ID}", headers=headers, timeout=20)
    r.raise_for_status()
    db = r.json()
    existing_status = (db.get("properties", {}).get("Status") or {}).get("select", {}).get("options", [])
    have_names = {opt["name"] for opt in existing_status}
    merged_status = list(existing_status) + [
        opt for opt in EXTRA_STATUS_OPTIONS if opt["name"] not in have_names
    ]

    props_patch = {
        "Order Number": {"rich_text": {}},
        "Order Total": {"number": {"format": "number"}},
        "Discount Applied": {"checkbox": {}},
        "Raw Checkout Data": {"rich_text": {}},
        "Order URL": {"url": {}},
        "Status": {"select": {"options": merged_status}},
    }

    r2 = requests.patch(
        f"https://api.notion.com/v1/databases/{DB_ID}",
        headers=headers,
        json={"properties": props_patch},
        timeout=20,
    )
    if r2.status_code != 200:
        print(f"ERROR {r2.status_code}: {r2.text[:600]}", file=sys.stderr)
        return 1
    print("OK: properties added/updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
