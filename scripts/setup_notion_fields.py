"""
One-time script to add Filex-related properties to the Notion CRM database.
Idempotent: safe to re-run; only adds fields that don't already exist.

Note: Notion API version 2025-09-03 introduced multi-source databases. Properties
now live on data sources, not the database. This script:
  1. GETs the database to discover its data sources.
  2. Picks the data source named "ALL ORDERS" (falls back to the sole data source
     if there's only one; errors out if multiple and none match).
  3. Reads its properties, computes which Filex fields are missing.
  4. PATCHes only the missing ones onto that data source.
"""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2025-09-03"
PRIMARY_DATA_SOURCE_NAME = "ALL ORDERS"

if not NOTION_API_KEY or not DATABASE_ID:
    sys.exit("Missing NOTION_API_KEY or NOTION_DATABASE_ID in .env")

NEW_PROPERTIES = {
    "Tracking Number":   {"rich_text": {}},
    "Tracking Link":     {"url": {}},
    "FILEX STATUS":      {"select": {"options": [
        {"name": "Label Created", "color": "yellow"},
        {"name": "Handed Off", "color": "blue"},
        {"name": "Shipped", "color": "blue"},
        {"name": "Delivered", "color": "green"},
        {"name": "Cancelled", "color": "red"},
        {"name": "Receiver Cancelled With Money", "color": "orange"},
        {"name": "Received in CSA", "color": "orange"},
        {"name": "Return to Origin", "color": "red"},
        {"name": "LC", "color": "default"},
        {"name": "SFT", "color": "default"},
        {"name": "MNA", "color": "default"},
        {"name": "FD", "color": "default"},
        {"name": "LC-OPS", "color": "default"},
        {"name": "SFT-OPS", "color": "default"},
        {"name": "MNA-OPS", "color": "default"},
        {"name": "FD-OPS", "color": "default"},
        {"name": "In OPS", "color": "default"},
    ]}},
    "FILEX NOTES":       {"rich_text": {}},
    "Filex Submitted":   {"checkbox": {}},
    "Dispatched At":     {"date": {}},
    "Last Update":       {"date": {}},
}

def main():
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=15) as client:
        # Step 1: Get the database to find its data sources.
        r = client.get(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}",
            headers=headers,
        )
        r.raise_for_status()
        db = r.json()

        # Notion 2025-09-03+: properties live on data_sources, not the database.
        data_sources = db.get("data_sources")
        if not data_sources:
            sys.exit(
                "No data_sources returned for this database. "
                "Expected Notion API 2025-09-03+ multi-source response."
            )

        # Step 2: Pick the data source by name. Fall back to the only one if
        # there's just a single source; otherwise error with the available names.
        ds = next((d for d in data_sources if d.get("name") == PRIMARY_DATA_SOURCE_NAME), None)
        if ds is None:
            if len(data_sources) == 1:
                ds = data_sources[0]
            else:
                sys.exit(
                    f"Multiple data sources and none named '{PRIMARY_DATA_SOURCE_NAME}'. "
                    f"Available: {[d.get('name') for d in data_sources]}"
                )

        ds_id = ds["id"]
        ds_name = ds.get("name", "<unnamed>")
        print(f"Database has {len(data_sources)} data source(s). Using: '{ds_name}' ({ds_id})")

        # Fetch its properties.
        r = client.get(
            f"https://api.notion.com/v1/data_sources/{ds_id}",
            headers=headers,
        )
        r.raise_for_status()
        existing = set(r.json().get("properties", {}).keys())
        patch_url = f"https://api.notion.com/v1/data_sources/{ds_id}"

        print(f"Existing properties: {len(existing)}")

        # Add only new ones.
        additions = {name: spec for name, spec in NEW_PROPERTIES.items() if name not in existing}
        if not additions:
            print("All Filex fields already present. Nothing to do.")
            return

        print(f"Adding: {list(additions.keys())}")
        r = client.patch(
            patch_url,
            headers=headers,
            json={"properties": additions},
        )
        if r.status_code >= 400:
            print(f"ERROR {r.status_code}: {r.text}")
            r.raise_for_status()
        print("Done.")

if __name__ == "__main__":
    main()
