"""
add_order_bridge_v2_fields.py
=============================
Adds two Notion properties to the orders data source for the skip-sourcing rollout:
    - ALBUMS SENT            (number)   - per-album send checkpoint counter
    - FULFILLMENT MESSAGE ID (number)   - Telegram message_id of the caption-bearing msg

Idempotent: re-running is safe; existing properties of the right type are kept.

Prints a list of legacy properties for the operator to manually hide/delete in Notion
after the new flow ships. The script does NOT delete properties to avoid data loss.
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

FIELDS = {
    "ALBUMS SENT":            ("number",  {"number": {}}),
    "FULFILLMENT MESSAGE ID": ("number",  {"number": {}}),
}

LEGACY_TO_HIDE = [
    "SOURCING NOTIFIED",
    "FULFILLMENT NOTIFIED",
    "TELEGRAM FILE IDS",
    "FILE IDS SAVED",
]

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

        if to_add:
            r3 = client.patch(
                f"https://api.notion.com/v1/data_sources/{ds_id}",
                headers=headers,
                json={"properties": to_add},
            )
            if r3.status_code >= 400:
                print("PATCH failed:", r3.status_code, r3.text)
                r3.raise_for_status()

            new_props = r3.json().get("properties", {})
            for name in to_add:
                actual_type = new_props.get(name, {}).get("type")
                if actual_type == FIELDS[name][0]:
                    print(f"OK: added '{name}' ({actual_type}).")
                else:
                    print(f"WARN: '{name}' missing or wrong type after PATCH (got {actual_type!r}).")
        else:
            print("All new properties already in place. Nothing to add.")

        print()
        print("Manual cleanup steps after the rollout (operator must do these in Notion UI):")
        for legacy in LEGACY_TO_HIDE:
            if legacy in existing:
                print(f"  - Hide or delete column: '{legacy}'")
            else:
                print(f"  - '{legacy}' is already absent from the schema.")
        print("  - Remove 'SOURCING' option from the ORDER STATUS select dropdown.")
        print("  - Hide 'ALBUMS SENT' and 'FULFILLMENT MESSAGE ID' from operator-facing views.")


if __name__ == "__main__":
    main()
