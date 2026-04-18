"""Archive empty placeholder rows from the Roster DB (no Name set)."""
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
ROSTER_DB_ID = "346c320eba598175969dd15472249081"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def main():
    # Query all rows
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}/query"
    resp = httpx.post(url, headers=HEADERS, json={}, timeout=15)
    resp.raise_for_status()
    rows = resp.json().get("results", [])

    archived = 0
    for row in rows:
        title_arr = row.get("properties", {}).get("Name", {}).get("title", [])
        name = title_arr[0].get("plain_text", "") if title_arr else ""
        if not name.strip():
            pid = row["id"]
            patch_url = f"{NOTION_API_BASE}/pages/{pid}"
            r = httpx.patch(patch_url, headers=HEADERS, json={"archived": True}, timeout=15)
            if r.status_code == 200:
                archived += 1
                print(f"  Archived empty row {pid}")
            else:
                print(f"  Failed to archive {pid}: {r.status_code} {r.text}")

    print(f"\nDone. Archived {archived} empty rows.")


if __name__ == "__main__":
    main()
