"""
Creates the Abandoned Checkout Recovery Notion database under the Execution Desk page.

Run once. Outputs the new database ID — paste into .env as
RECOVERY_NOTION_DATABASE_ID=<id>.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PARENT_PAGE_ID = "2dec320e-ba59-8121-8898-f2dc6d7a9641"  # Execution Desk
NOTION_VERSION = "2022-06-28"
DB_TITLE = "Abandoned Checkout Recovery"

BRANDS = ["Amara", "Pelvini", "Elara", "Lune", "Virex", "Dialo"]

STATUSES = [
    ("New", "default"),
    ("Recovery Sent", "yellow"),
    ("Recovered", "green"),
    ("Talked to Agent", "blue"),
    ("Order Placed", "green"),
    ("Lost", "gray"),
    ("Suppressed", "gray"),
]

BUTTON_CLICKED = [
    ("Complete Order", "green"),
    ("Talk with Agent", "blue"),
    ("None", "gray"),
]


def select_options(pairs):
    return [{"name": name, "color": color} for name, color in pairs]


def build_properties() -> dict:
    return {
        "Customer Name": {"title": {}},
        "Phone": {"phone_number": {}},
        "Email": {"email": {}},
        "Brand": {
            "select": {"options": [{"name": b, "color": "default"} for b in BRANDS]}
        },
        "Status": {"select": {"options": select_options(STATUSES)}},
        "Cart Value": {"number": {"format": "number"}},
        "Cart Items": {"rich_text": {}},
        "Shopify Checkout ID": {"rich_text": {}},
        "Shopify Checkout URL": {"url": {}},
        "Abandoned At": {"date": {}},
        "Template Name": {"rich_text": {}},
        "Discount Code": {"rich_text": {}},
        "Recovery Sent At": {"date": {}},
        "Button Clicked": {"select": {"options": select_options(BUTTON_CLICKED)}},
        "Recovered Name": {"rich_text": {}},
        "Recovered Address": {"rich_text": {}},
        "Recovered Phone": {"phone_number": {}},
        "Assigned Agent": {"rich_text": {}},
        "Notes": {"rich_text": {}},
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    token = os.getenv("NOTION_API_KEY")
    if not token:
        print("ERROR: NOTION_API_KEY missing from .env", file=sys.stderr)
        return 1

    payload = {
        "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": DB_TITLE}}],
        "properties": build_properties(),
    }

    resp = requests.post(
        "https://api.notion.com/v1/databases",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"ERROR: Notion API returned {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        return 1

    db = resp.json()
    db_id = db["id"]
    db_url = db.get("url", "")

    print("OK Database created")
    print(f"   ID:  {db_id}")
    print(f"   URL: {db_url}")
    print()
    print("Add to .env:")
    print(f"RECOVERY_NOTION_DATABASE_ID={db_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
