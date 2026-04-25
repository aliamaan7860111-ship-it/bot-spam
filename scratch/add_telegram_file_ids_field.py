"""
add_telegram_file_ids_field.py
==============================
One-shot setup script. Adds two properties to the orders Notion database
so the order bridge bot can save and reuse Telegram file_ids:

  1. TELEGRAM FILE IDS  (rich_text)  — comma-separated file_ids
  2. FILE IDS SAVED     (checkbox)   — true once file_ids are persisted

Run once before deploying the file_id reuse feature. Idempotent: safe to
re-run; existing properties of the right type are left alone.

Stage 1 logic (in the bot):
    - Send images to sourcing -> mark SOURCING NOTIFIED = true (prevents re-send)
    - Save file_ids: 3 in-process retries with backoff (1s, 2s, 4s)
    - On success -> set FILE IDS SAVED = true
    - On all retries failing -> leave FILE IDS SAVED = false, post warning

Stage 2 logic (in the bot):
    - If FILE IDS SAVED = true -> reuse file_ids (fast path)
    - If FILE IDS SAVED = false -> GraphQL re-fetch + post warning in fulfillment

Operator-facing: neither column needs to be visible. Hide both in operator views.
"""

import os
import sys
import httpx
from dotenv import load_dotenv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2025-09-03"

# Properties to ensure exist on the orders data source.
# Maps field name -> (notion property type, schema body)
FIELDS = {
    "TELEGRAM FILE IDS": ("rich_text", {"rich_text": {}}),
    "FILE IDS SAVED":    ("checkbox",  {"checkbox": {}}),
}

if not NOTION_API_KEY or not NOTION_DATABASE_ID:
    sys.exit("Missing NOTION_API_KEY or NOTION_DATABASE_ID in .env")

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def main():
    with httpx.Client(timeout=15) as client:
        r = client.get(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
            headers=headers,
        )
        r.raise_for_status()
        data_sources = r.json().get("data_sources", [])
        if not data_sources:
            sys.exit("No data sources found for this database.")
        ds_id = data_sources[0]["id"]
        print(f"Data source: {ds_id} ({data_sources[0].get('name', '?')})")

        r2 = client.get(
            f"https://api.notion.com/v1/data_sources/{ds_id}",
            headers=headers,
        )
        r2.raise_for_status()
        existing = r2.json().get("properties", {})

        to_add = {}
        for name, (expected_type, schema) in FIELDS.items():
            if name in existing:
                current_type = existing[name].get("type")
                if current_type == expected_type:
                    print(f"OK: '{name}' already exists ({expected_type}).")
                else:
                    sys.exit(
                        f"'{name}' exists but has type={current_type!r}, "
                        f"expected {expected_type!r}. Resolve manually."
                    )
            else:
                to_add[name] = schema

        if not to_add:
            print("All properties already in place. Nothing to do.")
            return

        r3 = client.patch(
            f"https://api.notion.com/v1/data_sources/{ds_id}",
            headers=headers,
            json={"properties": to_add},
        )
        if r3.status_code >= 400:
            print("PATCH failed:", r3.status_code, r3.text)
            r3.raise_for_status()

        new_props = r3.json().get("properties", {})
        for name, (expected_type, _) in FIELDS.items():
            if name in to_add:
                actual_type = new_props.get(name, {}).get("type")
                if actual_type == expected_type:
                    print(f"OK: added '{name}' ({actual_type}).")
                else:
                    print(f"WARN: '{name}' missing or wrong type after PATCH (got {actual_type!r}).")


if __name__ == "__main__":
    main()
