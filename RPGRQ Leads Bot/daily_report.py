"""
Daily Agent Performance Report
Runs at 11:20 PM PKT every day.
Queries the main Leads DB, calculates per-agent stats,
and creates one page per agent in the Daily Report DB.
"""

import os
import sys
import logging
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# ── Path Setup ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "RPGRQ Leads Bot"))
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("daily_report")

# ── Config ──
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
LEADS_DB_ID     = "344c320eba59800a90a6e804a575d272"
REPORT_DB_ID    = "345c320eba59814590abed11ff301bcf"
ROSTER_DB_ID    = "346c320eba598175969dd15472249081"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION  = "2022-06-28"
PKT = timezone(timedelta(hours=5))



def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _query_leads_db(filter_payload: dict) -> list:
    """Query the Leads DB with pagination support."""
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {"filter": filter_payload, "page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
                    headers=_headers(),
                    json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                all_results.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")
        except Exception as e:
            log.error(f"Notion query failed: {e}")
            break

    return all_results


def fetch_active_agent_names() -> list[str]:
    """Read the Agent Roster DB and return active agent names, alphabetical."""
    url = f"{NOTION_API_BASE}/databases/{ROSTER_DB_ID}/query"
    payload = {"filter": {"property": "Active", "checkbox": {"equals": True}}}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, headers=_headers(), json=payload)
            resp.raise_for_status()
            rows = resp.json().get("results", [])
    except Exception as e:
        log.error(f"Roster fetch failed: {e}")
        return []

    names = []
    for row in rows:
        title_arr = row.get("properties", {}).get("Name", {}).get("title", [])
        name = title_arr[0].get("plain_text", "").strip() if title_arr else ""
        if name:
            names.append(name)
    names.sort()
    return names


def get_today_range_iso() -> tuple[str, str]:
    """Return (start_of_today, end_of_today) as ISO strings in PKT."""
    now_pkt = datetime.now(PKT)
    start = now_pkt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now_pkt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start.isoformat(), end.isoformat()


def count_responded_today(agent: str, day_start: str, day_end: str) -> int:
    """Tickets where the agent participated AND a reply happened today."""
    filter_payload = {
        "and": [
            {"property": "Agent Assigned", "multi_select": {"contains": agent}},
            {"property": "Last Agent Reply", "date": {"on_or_after": day_start}},
            {"property": "Last Agent Reply", "date": {"on_or_before": day_end}},
        ]
    }
    results = _query_leads_db(filter_payload)
    return len(results)


def count_closed_today(agent: str, day_start: str, day_end: str) -> int:
    """Tickets where Closed Date falls within today AND this agent is the latest responder (Agent Assigned[0])."""
    filter_payload = {
        "and": [
            {"property": "Closed Date", "date": {"on_or_after": day_start}},
            {"property": "Closed Date", "date": {"on_or_before": day_end}},
        ]
    }
    results = _query_leads_db(filter_payload)
    count = 0
    for row in results:
        arr = row.get("properties", {}).get("Agent Assigned", {}).get("multi_select", [])
        names = [o.get("name", "") for o in arr if o.get("name")]
        if names and names[0] == agent:
            count += 1
    return count


def count_pending(agent: str) -> int:
    """Pending tickets where this agent is anywhere in the multi-select."""
    filter_payload = {
        "and": [
            {"property": "Agent Assigned", "multi_select": {"contains": agent}},
            {"property": "Outcome", "select": {"equals": "Pending"}},
        ]
    }
    results = _query_leads_db(filter_payload)
    return len(results)


def create_report_page(agent: str, responded: int, closed: int, pending: int) -> bool:
    """Create a single agent report page in the Daily Report DB.
    Conv Rate % is a Notion formula field — auto-calculated from Total Closed / Total Responded."""
    now_pkt = datetime.now(PKT)
    date_str = now_pkt.strftime("%Y-%m-%d")
    # Title: Agent - Date (user will re-arrange as they wish)
    title = f"{agent} - {date_str}"

    properties = {
        "Name": {"title": [{"text": {"content": title}}]},
        "Sales Agent": {"select": {"name": agent}},
        "Total Responded": {"number": responded},
        "Total Closed": {"number": closed},
        "Pending": {"number": pending},
        "Date": {"date": {"start": date_str}},
    }

    payload = {
        "parent": {"database_id": REPORT_DB_ID},
        "properties": properties
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{NOTION_API_BASE}/pages",
                headers=_headers(),
                json=payload
            )
            resp.raise_for_status()
            log.info(f"✅ Report created: {title}")
            return True
    except Exception as e:
        log.error(f"❌ Failed to create report for {agent}: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"   Response: {e.response.text}")
        return False


def generate_daily_report():
    """Main report generation logic."""
    day_start, day_end = get_today_range_iso()
    now_pkt = datetime.now(PKT)
    log.info(f"📊 Generating Daily Report for {now_pkt.strftime('%Y-%m-%d')}")
    log.info(f"   Day range: {day_start} → {day_end}")

    agents = fetch_active_agent_names()
    if not agents:
        log.error("No active agents in Roster DB — aborting report.")
        return
    log.info(f"   Active agents: {agents}")

    for agent in agents:
        responded = count_responded_today(agent, day_start, day_end)
        closed = count_closed_today(agent, day_start, day_end)
        pending = count_pending(agent)

        log.info(f"   {agent}: Responded={responded}, Closed={closed}, Pending={pending}")
        create_report_page(agent, responded, closed, pending)

    log.info("📊 Daily report complete.")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  Daily Report — on-demand run")
    log.info(f"  Started: {datetime.now(PKT).strftime('%Y-%m-%d %H:%M:%S PKT')}")
    log.info("=" * 60)
    generate_daily_report()
