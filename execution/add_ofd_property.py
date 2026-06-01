"""
One-shot: add the 'Out For Delivery Sent' checkbox property to the main CRM
Notion database. Idempotent — re-running is a no-op patch if it already exists.
Run once before deploying the OFD notifier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_ID = os.environ["NOTION_DATABASE_ID"]
TOKEN = os.environ["NOTION_API_KEY"]


def main() -> int:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    props_patch = {
        "Out For Delivery Sent": {"checkbox": {}},
    }
    r = requests.patch(
        f"https://api.notion.com/v1/databases/{DB_ID}",
        headers=headers,
        json={"properties": props_patch},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"ERROR {r.status_code}: {r.text[:600]}", file=sys.stderr)
        return 1
    print("OK: 'Out For Delivery Sent' checkbox added/confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
