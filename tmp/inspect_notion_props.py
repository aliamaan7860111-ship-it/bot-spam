import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

def inspect_db():
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}"
    resp = httpx.get(url, headers=headers)
    db = resp.json()
    props = db.get("properties", {})
    status_prop = props.get("ORDER STATUS", {})
    if status_prop:
        options = status_prop.get("select", {}).get("options", [])
        print("ORDER STATUS options:")
        for opt in options:
            print(f" - {opt['name']}")
    else:
        print("ORDER STATUS property not found")

if __name__ == "__main__":
    inspect_db()
