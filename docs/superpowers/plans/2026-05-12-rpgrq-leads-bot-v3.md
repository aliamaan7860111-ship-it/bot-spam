# RPGRQ Leads Bot v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the live RPGRQ Leads Bot v2 with shift-aware round-robin, multi-select Agent Assigned, Closed Date stamping, Status field cleanup, and stamping reliability hardening.

**Architecture:** Same 3-layer architecture (directive → orchestration → execution scripts). All v3 changes are additive or behavioral on existing files in [execution/](execution/) and [RPGRQ Leads Bot/](RPGRQ Leads Bot/). One-off migrations + backfills live in [scratch/](scratch/). Production code calls Notion HTTP API directly (no MCP — production has no LLM in the loop).

**Tech Stack:** Python 3.10+ asyncio, httpx, dotenv, raw Notion HTTP API (api.notion.com/v1, version `2025-09-03` for execution code per existing convention, `2022-06-28` acceptable for scratch scripts), WhatChimp REST API, systemd on GCP Ubuntu VM (port 8082).

**Spec:** [docs/superpowers/specs/2026-05-12-rpgrq-leads-bot-v3-design.md](docs/superpowers/specs/2026-05-12-rpgrq-leads-bot-v3-design.md)

---

## File Structure

**New files:**
- `scratch/migrate_leads_db_v3.py` — one-off schema migration (multi-select conversion, new Closed Date and Off Days fields).
- `scratch/backfill_stamps.py` — one-off backfill for orphaned Last Agent Reply / Actioned At fields.
- `tests/test_shift_logic.py` — unit tests for pure shift/pool-selection logic (no API).

**Modified files:**
- `execution/rpgrq_notion.py` — multi-select Agent Assigned helpers, Closed Date stamping, Off Days in roster fetch, drop Status=Waiting default.
- `execution/rpgrq_round_robin.py` — shift-aware pool selection, half-open interval, off-days filter, next-to-start fallback.
- `execution/rpgrq_webhook_server.py` — wire shift-aware pool, rewrite reassignment for multi-select, add retry logic, Closed Date stamping in label sync, improved diagnostic logs.
- `RPGRQ Leads Bot/daily_report.py` — multi-select `contains` filters, Closed Date query, drop cron entry path.
- `directives/rpgrq_leads_bot.md` — document v3 rules.
- `C:\Users\PMLS\.claude\projects\c--Users-PMLS-Desktop-Automation\memory\project_rpgrq_leads_bot.md` — update locked decisions.

---

## Phase 1 — Schema migration

### Task 1: Write `scratch/migrate_leads_db_v3.py`

**Files:**
- Create: `scratch/migrate_leads_db_v3.py`

- [ ] **Step 1: Create the migration script**

Write the file:

```python
"""
One-off schema migration for RPGRQ Leads Bot v3.

Changes:
  Leads DB (344c320eba59800a90a6e804a575d272):
    - Convert "Agent Assigned" from select -> multi_select, preserving values.
    - Add "Closed Date" (date).
  Roster DB (346c320eba598175969dd15472249081):
    - Add "Off Days" (multi_select with 7 day-name options).

Safe to re-run. Detects already-applied changes and skips.
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
ROSTER_DB_ID = "346c320eba598175969dd15472249081"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def get_db(db_id: str) -> dict:
    r = httpx.get(f"{NOTION_API_BASE}/databases/{db_id}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def patch_db(db_id: str, payload: dict) -> dict:
    r = httpx.patch(f"{NOTION_API_BASE}/databases/{db_id}", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def migrate_agent_assigned_to_multiselect():
    """Convert Leads DB 'Agent Assigned' from select to multi_select, preserving rows."""
    db = get_db(LEADS_DB_ID)
    props = db.get("properties", {})
    aa = props.get("Agent Assigned", {})
    aa_type = aa.get("type")

    if aa_type == "multi_select":
        print("  Agent Assigned: already multi_select ✓")
        return

    if aa_type != "select":
        raise RuntimeError(f"Unexpected Agent Assigned type: {aa_type!r}")

    # Read existing select options to preserve as multi_select options.
    existing_options = aa.get("select", {}).get("options", [])
    option_names = [o.get("name") for o in existing_options if o.get("name")]
    print(f"  Agent Assigned: converting select -> multi_select with options {option_names}")

    # Step 1: enumerate all rows and capture (page_id, current_agent_name).
    rows = []
    has_more = True
    cursor = None
    while has_more:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query", headers=HEADERS, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        for row in data.get("results", []):
            sel = row.get("properties", {}).get("Agent Assigned", {}).get("select")
            name = sel.get("name") if sel else None
            rows.append((row["id"], name))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
    print(f"  Found {len(rows)} rows to migrate")

    # Step 2: change the property definition to multi_select.
    patch_db(LEADS_DB_ID, {
        "properties": {
            "Agent Assigned": {
                "multi_select": {
                    "options": [{"name": n} for n in option_names]
                }
            }
        }
    })
    print("  Agent Assigned: column converted to multi_select ✓")

    # Step 3: rewrite each row's value as a 1-item array (or empty if was null).
    for page_id, name in rows:
        ms_value = [{"name": name}] if name else []
        r = httpx.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=HEADERS,
            json={"properties": {"Agent Assigned": {"multi_select": ms_value}}},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"  WARN: failed to rewrite {page_id}: {r.status_code} {r.text}")
    print(f"  Agent Assigned: {len(rows)} rows rewritten as multi_select arrays ✓")


def ensure_closed_date():
    db = get_db(LEADS_DB_ID)
    if "Closed Date" in db.get("properties", {}):
        print("  Closed Date: already exists ✓")
        return
    patch_db(LEADS_DB_ID, {"properties": {"Closed Date": {"date": {}}}})
    print("  Closed Date: added ✓")


def ensure_off_days():
    db = get_db(ROSTER_DB_ID)
    if "Off Days" in db.get("properties", {}):
        print("  Off Days: already exists ✓")
        return
    patch_db(ROSTER_DB_ID, {
        "properties": {
            "Off Days": {
                "multi_select": {
                    "options": [{"name": d} for d in DAY_NAMES]
                }
            }
        }
    })
    print("  Off Days: added with 7 day-name options ✓")


def main():
    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY missing in .env")
        sys.exit(1)
    print("== RPGRQ v3 schema migration ==")
    print("\n[1/3] Migrating Leads DB Agent Assigned to multi_select...")
    migrate_agent_assigned_to_multiselect()
    print("\n[2/3] Ensuring Closed Date field exists...")
    ensure_closed_date()
    print("\n[3/3] Ensuring Off Days field exists on Roster DB...")
    ensure_off_days()
    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit the script (don't run yet)**

```bash
git add scratch/migrate_leads_db_v3.py
git commit -m "feat(rpgrq): v3 schema migration script (not yet run)"
```

### Task 2: Run the migration

- [ ] **Step 1: Dry-run inspect — read current DB schema first**

```bash
cd "c:/Users/PMLS/Desktop/Automation"
python -c "
import sys, os, httpx
sys.path.insert(0, '.')
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('.env'))
h = {'Authorization': f\"Bearer {os.getenv('NOTION_API_KEY').strip()}\", 'Notion-Version': '2022-06-28'}
for db_id, label in [('344c320eba59800a90a6e804a575d272', 'Leads'), ('346c320eba598175969dd15472249081', 'Roster')]:
    r = httpx.get(f'https://api.notion.com/v1/databases/{db_id}', headers=h, timeout=15)
    props = r.json().get('properties', {})
    print(f'{label}: {sorted(props.keys())}')
    if 'Agent Assigned' in props:
        print(f'  Agent Assigned type: {props[\"Agent Assigned\"][\"type\"]}')
"
```

Expected output shows current `Agent Assigned` type is `select` and lists current property names.

- [ ] **Step 2: Run the migration script**

```bash
python scratch/migrate_leads_db_v3.py
```

Expected output: progress through `[1/3]`, `[2/3]`, `[3/3]`, "Done." Confirm row-rewrite count matches the live ticket count.

- [ ] **Step 3: Verify schema post-migration**

Run the same inspect snippet from Step 1. Confirm:
- Leads DB now contains `Closed Date`.
- Leads DB `Agent Assigned` type is `multi_select`.
- Roster DB now contains `Off Days`.

- [ ] **Step 4: Spot-check a row**

```bash
python -c "
import os, httpx
from dotenv import load_dotenv
load_dotenv()
h = {'Authorization': f\"Bearer {os.getenv('NOTION_API_KEY').strip()}\", 'Notion-Version': '2022-06-28', 'Content-Type': 'application/json'}
r = httpx.post('https://api.notion.com/v1/databases/344c320eba59800a90a6e804a575d272/query', headers=h, json={'page_size': 3}, timeout=15)
for row in r.json().get('results', []):
    aa = row.get('properties', {}).get('Agent Assigned', {}).get('multi_select', [])
    print([o.get('name') for o in aa])
"
```

Expected: each row prints a 1-item array like `['Nauman']` or empty `[]` for previously unassigned tickets.

- [ ] **Step 5: User populates Off Days in the Notion UI**

Skip this step in the code plan — it's manual. After migration the user opens the Roster DB and fills `Off Days` for each agent. Tasks below assume this is done OR can be deferred (empty Off Days = always available regardless of day).

---

## Phase 2 — Notion client helpers

### Task 3: Extend roster fetch to include `Off Days`

**Files:**
- Modify: `execution/rpgrq_notion.py` (the `get_active_roster` function)

- [ ] **Step 1: Update get_active_roster to read Off Days**

In `execution/rpgrq_notion.py`, find the existing `roster.append({...})` block inside `get_active_roster` and replace it with:

```python
        off_days_arr = p.get("Off Days", {}).get("multi_select", [])
        off_days = [o.get("name", "") for o in off_days_arr if o.get("name")]
        roster.append({
            "name": name,
            "team_member_id": p.get("Team Member ID", {}).get("number"),
            "user_id": p.get("WhatChimp User ID", {}).get("number"),
            "shift_start": p.get("Shift Start Hour", {}).get("number"),
            "shift_end": p.get("Shift End Hour", {}).get("number"),
            "off_days": off_days,
        })
```

- [ ] **Step 2: Verify imports still work**

```bash
cd "c:/Users/PMLS/Desktop/Automation"
python -c "
import sys
sys.path.insert(0, '.')
import asyncio, httpx
from execution import rpgrq_notion as n
async def main():
    async with httpx.AsyncClient() as c:
        roster = await n.get_active_roster(c, force_refresh=True)
        for a in roster:
            print(a)
asyncio.run(main())
"
```

Expected: each row prints with `off_days` key present (possibly `[]` if Off Days not yet populated in Notion).

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_notion.py
git commit -m "feat(rpgrq): include Off Days in active roster fetch"
```

### Task 4: Add multi-select `Agent Assigned` helpers

**Files:**
- Modify: `execution/rpgrq_notion.py`

- [ ] **Step 1: Replace `reassign_agent` with `update_agent_assigned_list`**

Find the existing `reassign_agent` function. Replace it with:

```python
async def update_agent_assigned_list(
    client: httpx.AsyncClient,
    page_id: str,
    ordered_agents: list[str],
) -> bool:
    """Write Agent Assigned as a multi-select array. Position [0] = latest replier."""
    ms_value = [{"name": n} for n in ordered_agents]
    return await _update_page(
        client, page_id, {"Agent Assigned": {"multi_select": ms_value}}
    )
```

- [ ] **Step 2: Add `ticket_agent_assigned_list` helper**

Find the existing `ticket_agent_assigned` helper. Below it, add:

```python
def ticket_agent_assigned_list(ticket: dict) -> list[str]:
    """Return the Agent Assigned multi-select array as a list of names. Position [0] = latest."""
    arr = ticket.get("properties", {}).get("Agent Assigned", {}).get("multi_select", [])
    return [o.get("name", "") for o in arr if o.get("name")]
```

- [ ] **Step 3: Replace `ticket_agent_assigned` to return position [0]**

Replace the body of `ticket_agent_assigned`:

```python
def ticket_agent_assigned(ticket: dict) -> Optional[str]:
    """Return position [0] of the Agent Assigned multi-select (the latest replier), or None."""
    arr = ticket_agent_assigned_list(ticket)
    return arr[0] if arr else None
```

- [ ] **Step 4: Verify imports still work**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_notion as n
assert hasattr(n, 'update_agent_assigned_list')
assert hasattr(n, 'ticket_agent_assigned_list')
assert hasattr(n, 'ticket_agent_assigned')
assert not hasattr(n, 'reassign_agent'), 'reassign_agent should be removed'
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add execution/rpgrq_notion.py
git commit -m "feat(rpgrq): multi-select Agent Assigned helpers"
```

### Task 5: Add Closed Date stamping helper

**Files:**
- Modify: `execution/rpgrq_notion.py`

- [ ] **Step 1: Add `stamp_closed_date` function**

Below `stamp_customer_message`, add:

```python
async def stamp_closed_date(
    client: httpx.AsyncClient,
    page_id: str,
    closed_at_iso: str,
) -> bool:
    """Stamp Closed Date when the Closed label transitions from absent to present."""
    return await _update_page(
        client, page_id, {"Closed Date": {"date": {"start": closed_at_iso}}}
    )
```

- [ ] **Step 2: Verify**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_notion as n
assert hasattr(n, 'stamp_closed_date')
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_notion.py
git commit -m "feat(rpgrq): add stamp_closed_date helper"
```

### Task 6: Update `create_ticket` for multi-select + drop Status default

**Files:**
- Modify: `execution/rpgrq_notion.py`

- [ ] **Step 1: Replace the `properties` block in `create_ticket`**

Find the properties dict inside `create_ticket`. Replace it with:

```python
    properties = {
        "Name":                   {"title": [{"text": {"content": phone}}]},
        "Phone Number":           {"rich_text": [{"text": {"content": phone}}]},
        "Source (Store)":         {"select": {"name": brand}},
        "Outcome":                {"select": {"name": "Pending"}},
        "Created At":             {"date": {"start": created_at_iso}},
        "Last Customer Message":  {"date": {"start": created_at_iso}},
        "Agent Assigned":         {"multi_select": [{"name": agent_name}]},
    }
```

Key changes:
- Drop the `"Status": {"multi_select": [{"name": "Waiting"}]}` line.
- `Agent Assigned` is now multi-select with 1-item array.

- [ ] **Step 2: Verify imports**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_notion
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_notion.py
git commit -m "feat(rpgrq): create_ticket uses multi-select Agent Assigned; drop Status=Waiting"
```

---

## Phase 3 — Round-robin rewrite

### Task 7: Write unit tests for shift-pool selection

**Files:**
- Create: `tests/test_shift_logic.py`

- [ ] **Step 1: Create test file**

```python
"""Unit tests for the pure shift/pool-selection logic.

These tests run against module-level functions only — no Notion or WhatChimp
API calls. They're the only meaningful tests we add for v3 because everything
else is integration-bound to live APIs.
"""

import sys
from datetime import datetime
from pathlib import Path

# Allow importing from execution/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from execution.rpgrq_round_robin import select_pool


def _agent(name, start, end, off=None):
    return {
        "name": name,
        "team_member_id": 1,
        "user_id": 1,
        "shift_start": start,
        "shift_end": end,
        "off_days": off or [],
    }


ROSTER = [
    _agent("A1", 9, 18),
    _agent("A2", 13, 22),
    _agent("A3", 15, 24),
]


def at(hour: int, day: str = "Monday"):
    """Return a (now_hour, day_name) tuple."""
    return hour, day


def test_only_a1_at_9am():
    pool = select_pool(ROSTER, *at(9))
    assert [a["name"] for a in pool] == ["A1"]


def test_a1_a2_at_1pm():
    pool = select_pool(ROSTER, *at(13))
    assert [a["name"] for a in pool] == ["A1", "A2"]


def test_all_three_at_3pm():
    pool = select_pool(ROSTER, *at(15))
    assert [a["name"] for a in pool] == ["A1", "A2", "A3"]


def test_a2_a3_at_6pm_a1_off():
    pool = select_pool(ROSTER, *at(18))
    assert [a["name"] for a in pool] == ["A2", "A3"]


def test_only_a3_at_10pm():
    pool = select_pool(ROSTER, *at(22))
    assert [a["name"] for a in pool] == ["A3"]


def test_off_hours_falls_back_to_next_to_start():
    """At 1am, nobody is on shift. Next to start today is A1 at 9am."""
    pool = select_pool(ROSTER, *at(1))
    assert [a["name"] for a in pool] == ["A1"]


def test_a1_off_today_falls_back_to_a2():
    """If A1 has Monday off, off-hours next-to-start = A2 (13:00)."""
    roster = [
        _agent("A1", 9, 18, off=["Monday"]),
        _agent("A2", 13, 22),
        _agent("A3", 15, 24),
    ]
    pool = select_pool(roster, *at(1, "Monday"))
    assert [a["name"] for a in pool] == ["A2"]


def test_off_day_excluded_during_shift_window():
    """If A1 has Monday off, at 10am on Monday pool excludes A1; nobody else is on shift -> next-to-start."""
    roster = [
        _agent("A1", 9, 18, off=["Monday"]),
        _agent("A2", 13, 22),
        _agent("A3", 15, 24),
    ]
    pool = select_pool(roster, *at(10, "Monday"))
    assert [a["name"] for a in pool] == ["A2"]


def test_everyone_off_today_last_resort():
    """All three off on Sunday -> pool = all active agents regardless."""
    roster = [
        _agent("A1", 9, 18, off=["Sunday"]),
        _agent("A2", 13, 22, off=["Sunday"]),
        _agent("A3", 15, 24, off=["Sunday"]),
    ]
    pool = select_pool(roster, *at(15, "Sunday"))
    assert sorted([a["name"] for a in pool]) == ["A1", "A2", "A3"]


def test_shift_boundary_half_open_end():
    """At exactly 18:00, A1's shift is over (half-open [start, end))."""
    pool_at_18 = select_pool(ROSTER, *at(18))
    assert "A1" not in [a["name"] for a in pool_at_18]


def test_shift_boundary_half_open_start():
    """At exactly 9:00, A1's shift just started."""
    pool_at_9 = select_pool(ROSTER, *at(9))
    assert "A1" in [a["name"] for a in pool_at_9]
```

- [ ] **Step 2: Run the tests — expect ImportError or failures (function doesn't exist yet)**

```bash
python -m pytest tests/test_shift_logic.py -v 2>&1 | head -10
```

Expected: ImportError on `select_pool` (we haven't added it yet).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_shift_logic.py
git commit -m "test(rpgrq): unit tests for shift-aware pool selection (failing)"
```

### Task 8: Implement `select_pool` in `rpgrq_round_robin.py`

**Files:**
- Modify: `execution/rpgrq_round_robin.py`

- [ ] **Step 1: Add `select_pool` function (above the `RoundRobin` class)**

Add this function above `class RoundRobin:`:

```python
def select_pool(roster: list[dict], now_hour: int, day_name: str) -> list[dict]:
    """
    Select the pool of agents eligible for new lead assignment.

    roster: list of dicts each with keys name, team_member_id, shift_start, shift_end, off_days.
    now_hour: current hour in PKT (0-23).
    day_name: current weekday name in PKT (e.g. "Monday").

    Pool rules (in priority order):
      1. on_shift_now = available_today members where shift_start <= now_hour < shift_end.
         If non-empty, that's the pool.
      2. Otherwise: the agent in available_today whose shift_start is the smallest value
         strictly greater than now_hour. If multiple tie, alphabetical by name.
         If no agent has shift_start > now_hour (it's past everyone's start today),
         fall through to step 3.
      3. Last resort (everyone off today, or nobody starts later today): pool = all roster,
         ignoring shifts AND off days.
    """
    available_today = [a for a in roster if day_name not in (a.get("off_days") or [])]

    on_shift_now = [
        a for a in available_today
        if a.get("shift_start") is not None
        and a.get("shift_end") is not None
        and a["shift_start"] <= now_hour < a["shift_end"]
    ]
    if on_shift_now:
        return sorted(on_shift_now, key=lambda a: a["name"])

    # Find next-to-start today.
    later_today = [
        a for a in available_today
        if a.get("shift_start") is not None and a["shift_start"] > now_hour
    ]
    if later_today:
        earliest_start = min(a["shift_start"] for a in later_today)
        winners = sorted(
            [a for a in later_today if a["shift_start"] == earliest_start],
            key=lambda a: a["name"],
        )
        return [winners[0]]

    # Last resort: everyone is off today OR it's past everyone's start time.
    return sorted(roster, key=lambda a: a["name"])
```

- [ ] **Step 2: Run the tests — expect them to pass**

```bash
python -m pytest tests/test_shift_logic.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_round_robin.py
git commit -m "feat(rpgrq): implement shift-aware select_pool function"
```

### Task 9: Update `RoundRobin.next_agent` to use the shift pool

**Files:**
- Modify: `execution/rpgrq_round_robin.py`

- [ ] **Step 1: Replace `next_agent` body**

Find the `async def next_agent(self, client: httpx.AsyncClient)` method and replace its body with:

```python
    async def next_agent(self, client: httpx.AsyncClient) -> Optional[dict]:
        async with self._lock:
            roster = await notion.get_active_roster(client)
            if not roster:
                log.error("No active agents in roster")
                return None

            now_pkt = datetime.now(PKT)
            now_hour = now_pkt.hour
            day_name = now_pkt.strftime("%A")

            pool = select_pool(roster, now_hour, day_name)
            if not pool:
                log.error("Pool selection returned empty list — should never happen")
                return None

            names = [a["name"] for a in pool]

            # Pointer initialization from Notion uses the FULL roster, not just current pool.
            if not self._pointer_initialized:
                all_names = [a["name"] for a in sorted(roster, key=lambda a: a["name"])]
                last = await notion.get_last_assigned_agent(client, all_names)
                if last and last in all_names:
                    self._pointer_name = last
                    log.info(f"RR pointer initialized from Notion: last was {last}")
                else:
                    self._pointer_name = None
                    log.info("RR pointer initialized fresh (no prior assignment found)")
                self._pointer_initialized = True

            # Pick the next name in the pool that comes after the pointer (alphabetical).
            # If the pointer name isn't in the pool, take the first pool member alphabetically
            # whose name is greater than the pointer; else wrap to pool[0].
            if self._pointer_name and self._pointer_name in names:
                idx = (names.index(self._pointer_name) + 1) % len(pool)
            elif self._pointer_name is None:
                idx = 0
            else:
                greater = [n for n in names if n > self._pointer_name]
                idx = names.index(min(greater)) if greater else 0

            chosen = pool[idx]
            self._pointer_name = chosen["name"]
            log.info(
                f"RR pool@{now_hour:02d}:00 {day_name} = {names} -> {chosen['name']}"
            )
            return chosen
```

Also add to the imports at the top of `rpgrq_round_robin.py` (if not already there):

```python
from typing import Optional
```

- [ ] **Step 2: Import verification**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution.rpgrq_round_robin import RoundRobin, select_pool, calculate_response_speed
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_round_robin.py
git commit -m "feat(rpgrq): RoundRobin.next_agent uses shift-aware pool selection"
```

### Task 10: Make `get_last_assigned_agent` work with multi-select

**Files:**
- Modify: `execution/rpgrq_notion.py`

- [ ] **Step 1: Update the query payload**

Find `get_last_assigned_agent`. Replace its filter to use `multi_select.contains`:

```python
async def get_last_assigned_agent(client: httpx.AsyncClient, valid_agent_names: list[str]) -> Optional[str]:
    if not valid_agent_names:
        return None
    payload = {
        "filter": {
            "or": [
                {"property": "Agent Assigned", "multi_select": {"contains": n}}
                for n in valid_agent_names
            ]
        },
        "sorts": [{"property": "Created At", "direction": "descending"}],
        "page_size": 1,
    }
    try:
        resp = await client.post(
            f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
            headers=HEADERS, json=payload, timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        arr = results[0].get("properties", {}).get("Agent Assigned", {}).get("multi_select", [])
        return arr[0].get("name") if arr else None
    except Exception as e:
        log.error(f"get_last_assigned_agent failed: {e}")
        return None
```

- [ ] **Step 2: Verify against live DB**

```bash
python -c "
import sys, asyncio, httpx
sys.path.insert(0, '.')
from execution import rpgrq_notion as n
async def main():
    async with httpx.AsyncClient() as c:
        last = await n.get_last_assigned_agent(c, ['Amaan', 'Nauman', 'Ushda'])
        print('Last assigned:', last)
asyncio.run(main())
"
```

Expected: prints a real agent name (not error).

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_notion.py
git commit -m "fix(rpgrq): get_last_assigned_agent uses multi_select.contains filter"
```

---

## Phase 4 — Webhook handlers

### Task 11: Update `handle_incoming` for new schema

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Refactor `handle_incoming`'s new-ticket branch**

The `handle_incoming` function's "no ticket -> create" branch already calls `rr.next_agent(client)`. After v3 round-robin changes, that's now shift-aware automatically. No structural change needed in `handle_incoming` for this — but verify:

- The `if agent` branch reads `agent["team_member_id"]` and `agent["name"]` — still valid (those keys haven't changed).
- The `notion.create_ticket(client, phone, brand, agent_name=...)` call still passes a string name — still valid (Task 6 already updated create_ticket to wrap it as a 1-item multi-select array).

No code changes in this task. Move to step 2.

- [ ] **Step 2: Confirm import still works**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

- [ ] **Step 3: No-op commit (skip if no changes made)**

Skip — proceed to Task 12.

### Task 12: Rewrite reassignment for multi-select in `handle_outgoing`

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Replace the reassignment block**

Find the block in `handle_outgoing` that currently calls `notion.reassign_agent(...)`. Replace it with the multi-select prepend logic:

```python
    # Update Agent Assigned multi-select: prepend the replier to position [0],
    # removing them from elsewhere in the list if present.
    if replier:
        current_list = notion.ticket_agent_assigned_list(ticket)
        new_list = [replier["name"]] + [n for n in current_list if n != replier["name"]]
        if new_list != current_list:
            ok = await notion.update_agent_assigned_list(client, ticket["id"], new_list)
            if ok:
                log.info(
                    f"🔀 reassigned {brand} / {phone}: "
                    f"{current_list} -> {new_list}"
                )
                # WhatChimp side-assign only when position [0] actually changed.
                prev_owner = current_list[0] if current_list else None
                if prev_owner != replier["name"]:
                    pid = wc.brand_to_phone_id(brand)
                    if pid and replier.get("team_member_id"):
                        await wc.assign_to_team_member(
                            client, pid, phone, int(replier["team_member_id"])
                        )
        current_assigned = replier["name"]
    else:
        current_assigned = notion.ticket_agent_assigned(ticket) or ""
```

(The variable `current_assigned` is used later in the function for the shift-hour lookup; preserve that usage.)

- [ ] **Step 2: Verify imports**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_webhook_server.py
git commit -m "feat(rpgrq): handle_outgoing rewrites Agent Assigned as multi-select array"
```

### Task 13: Add retry logic for find_ticket race

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Wrap the first `find_ticket` call in `handle_outgoing` with a 2s retry**

Find this in `handle_outgoing`:

```python
    ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        log.debug(f"outgoing: no ticket for {phone}/{brand}; ignoring")
        return
```

Replace with:

```python
    ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        # Race: outgoing webhook fired before the incoming webhook finished
        # creating the ticket. Sleep 2s and retry once.
        await asyncio.sleep(2.0)
        ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        log.info(f"outgoing: ignored — no ticket for {phone}/{brand} after retry")
        return
```

- [ ] **Step 2: Verify imports + asyncio is imported**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_webhook_server.py
git commit -m "fix(rpgrq): retry find_ticket once on outgoing race condition"
```

### Task 14: Add retry logic for stale conversation lookups

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Wrap the `get_latest_message` call with a wa_message_id check**

Find this in `handle_outgoing`:

```python
    latest = await wc.get_latest_message(client, pid, phone)
    if not latest:
        log.info(f"outgoing: {phone}/{brand} no conversation history returned")
        return
```

Replace with:

```python
    async def _fetch_latest():
        return await wc.get_latest_message(client, pid, phone)

    latest = await _fetch_latest()
    # If the webhook's wa_message_id doesn't match the conversation's latest,
    # WhatChimp may not have propagated yet. Sleep 1s and retry once.
    webhook_msg_id = wa_message_id or ""
    if latest and webhook_msg_id and latest.get("wa_message_id") != webhook_msg_id:
        log.info(
            f"outgoing: stale conversation lookup for {phone}/{brand} "
            f"(latest={latest.get('wa_message_id')!r} webhook={webhook_msg_id!r}); retrying"
        )
        await asyncio.sleep(1.0)
        latest = await _fetch_latest()
    if not latest:
        log.info(f"outgoing: ignored — no conversation history for {phone}/{brand}")
        return
```

- [ ] **Step 2: Verify imports**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_webhook_server.py
git commit -m "fix(rpgrq): retry conversation lookup when wa_message_id mismatches"
```

### Task 15: Add Closed Date stamping to `sync_labels_side_effect`

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Rewrite `sync_labels_side_effect` to detect Closed transitions**

Find the existing function `async def sync_labels_side_effect`. Replace its body with:

```python
async def sync_labels_side_effect(
    client: httpx.AsyncClient,
    ticket_id: str,
    label_names_raw: str,
    ticket: Optional[dict] = None,
):
    """Sync mapped WhatChimp labels to Notion Status. Stamp Closed Date on transition."""
    new_labels = pick_active_labels(label_names_raw)
    if not new_labels:
        # Empty incoming labels: don't clear Status (could erase manual labels).
        return
    if not label_cache.changed(ticket_id, new_labels):
        return

    # Read existing Status to detect Closed transition.
    prev_labels: list[str] = []
    if ticket is not None:
        status_arr = ticket.get("properties", {}).get("Status", {}).get("multi_select", [])
        prev_labels = [s.get("name", "") for s in status_arr if s.get("name")]

    ok = await notion.update_status_labels(client, ticket_id, new_labels)
    if ok:
        log.info(f"🏷️  labels synced for {ticket_id[-6:]}: {new_labels}")
        # Closed transition: stamp Closed Date.
        if "Closed" in new_labels and "Closed" not in prev_labels:
            await notion.stamp_closed_date(client, ticket_id, utc_iso_now())
            log.info(f"🔒 closed-date stamped for {ticket_id[-6:]}")
```

- [ ] **Step 2: Pass `ticket` to all callsites of `sync_labels_side_effect`**

Find the four (or so) callsites in `handle_incoming` and `handle_outgoing`. Each call currently looks like:

```python
    await sync_labels_side_effect(client, ticket["id"], label_names_raw)
```

(Or `await sync_labels_side_effect(client, new_id, label_names_raw)` for the new-ticket path.)

Update each to pass the ticket dict:

- New ticket path: `await sync_labels_side_effect(client, new_id, label_names_raw, ticket=None)` — no prior labels.
- Ping-pong path: `await sync_labels_side_effect(client, ticket["id"], label_names_raw, ticket=ticket)`.
- Outgoing path: `await sync_labels_side_effect(client, ticket["id"], label_names_raw, ticket=ticket)`.

For the new-ticket path, after `notion.create_ticket(...)` we don't yet have the row dict back — pass `ticket=None`. The Closed-transition check will see `prev_labels = []`, so if the customer message arrived with `Closed` already in `label_names`, we'd correctly stamp Closed Date. Acceptable.

- [ ] **Step 3: Verify imports + Optional is imported in webhook_server**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

If `Optional` isn't imported, add `from typing import Optional` at the top.

- [ ] **Step 4: Commit**

```bash
git add execution/rpgrq_webhook_server.py
git commit -m "feat(rpgrq): stamp Closed Date on Closed-label transition"
```

### Task 16: Improve diagnostic logging for outgoing-ignored cases

**Files:**
- Modify: `execution/rpgrq_webhook_server.py`

- [ ] **Step 1: Audit and tag every `return` in handle_outgoing with a reason**

Search `handle_outgoing` for every place we return early. Each must log with the prefix `outgoing: ignored —`. Make sure each branch logs the reason. Examples to confirm or add:

- Already logged: `sender != "bot"`, `agent_name empty`, `agent_name not numeric`, `no ticket after retry`, `no conversation history`.
- Add if missing: when the conversation-retry mismatch persists, log a final ignore line before returning.

Also bump `log.debug(...)` lines that signal common drops (e.g., "couldn't fetch latest message") to `log.info(...)` so they show up by default — these are the gaps we need visibility on.

- [ ] **Step 2: Verify imports**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from execution import rpgrq_webhook_server
print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add execution/rpgrq_webhook_server.py
git commit -m "chore(rpgrq): make outgoing-ignored reasons visible at INFO level"
```

---

## Phase 5 — Daily report rewrite

### Task 17: Update `count_responded_today` to use multi-select contains

**Files:**
- Modify: `RPGRQ Leads Bot/daily_report.py`

- [ ] **Step 1: Replace the filter**

Find `count_responded_today`. Replace the filter_payload:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add "RPGRQ Leads Bot/daily_report.py"
git commit -m "fix(rpgrq): count_responded_today uses multi_select.contains"
```

### Task 18: Rewrite `count_closed_today` using Closed Date

**Files:**
- Modify: `RPGRQ Leads Bot/daily_report.py`

- [ ] **Step 1: Replace the function body**

Replace `count_closed_today` with:

```python
def count_closed_today(agent: str, day_start: str, day_end: str) -> int:
    """Tickets where Closed Date falls within today AND this agent is the latest responder."""
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
```

- [ ] **Step 2: Commit**

```bash
git add "RPGRQ Leads Bot/daily_report.py"
git commit -m "fix(rpgrq): count_closed_today queries Closed Date + filters by Agent Assigned[0]"
```

### Task 19: Update `count_pending` to use multi-select contains

**Files:**
- Modify: `RPGRQ Leads Bot/daily_report.py`

- [ ] **Step 1: Replace the filter**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add "RPGRQ Leads Bot/daily_report.py"
git commit -m "fix(rpgrq): count_pending uses multi_select.contains"
```

### Task 20: Drop scheduled-mode entry path from daily_report.py

**Files:**
- Modify: `RPGRQ Leads Bot/daily_report.py`

- [ ] **Step 1: Remove `wait_for_report_time` function and rewrite `__main__`**

Find and delete the entire `wait_for_report_time` function. Replace the `if __name__ == "__main__":` block with:

```python
if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  Daily Report — on-demand run")
    log.info(f"  Started: {datetime.now(PKT).strftime('%Y-%m-%d %H:%M:%S PKT')}")
    log.info("=" * 60)
    generate_daily_report()
```

Also remove the now-unused `REPORT_HOUR` and `REPORT_MINUTE` constants, and the `import time` import if unused.

- [ ] **Step 2: Verify**

```bash
python "RPGRQ Leads Bot/daily_report.py" 2>&1 | head -20
```

Expected: prints header, runs report, exits cleanly. No more "Next report scheduled" line.

- [ ] **Step 3: Commit**

```bash
git add "RPGRQ Leads Bot/daily_report.py"
git commit -m "refactor(rpgrq): daily_report is on-demand only; remove cron-mode entry path"
```

---

## Phase 6 — Backfill + docs + deploy

### Task 21: Write `scratch/backfill_stamps.py`

**Files:**
- Create: `scratch/backfill_stamps.py`

- [ ] **Step 1: Create the backfill script**

```python
"""
One-off backfill for orphaned RPGRQ Leads tickets.

Scans Leads DB for tickets where Outcome != Pending but Last Agent Reply is null,
then walks /get/conversation for each, identifies the latest message with a
non-empty numeric agent_name, and stamps Last Agent Reply (and Actioned At if
also missing) based on that message's conversation_time.

Safe to re-run. Idempotent.
"""

import os
import sys
import asyncio
import httpx
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from execution import rpgrq_notion as notion
from execution import rpgrq_whatchimp as wc

LEADS_DB_ID = "344c320eba59800a90a6e804a575d272"
NOTION_API_BASE = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {os.getenv('NOTION_API_KEY', '').strip()}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


async def find_orphans(client: httpx.AsyncClient) -> list[dict]:
    """Outcome != Pending but Last Agent Reply is null."""
    payload = {
        "filter": {
            "and": [
                {"property": "Outcome", "select": {"does_not_equal": "Pending"}},
                {"property": "Last Agent Reply", "date": {"is_empty": True}},
            ]
        },
        "page_size": 100,
    }
    out = []
    has_more = True
    cursor = None
    while has_more:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        r = await client.post(
            f"{NOTION_API_BASE}/databases/{LEADS_DB_ID}/query",
            headers=HEADERS, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
    return out


async def main():
    fixed = 0
    skipped = 0
    async with httpx.AsyncClient(timeout=30) as client:
        orphans = await find_orphans(client)
        print(f"Found {len(orphans)} orphan tickets to inspect")
        for ticket in orphans:
            props = ticket.get("properties", {})
            phone_rt = props.get("Phone Number", {}).get("rich_text", [])
            phone = phone_rt[0].get("plain_text", "") if phone_rt else ""
            brand_sel = props.get("Source (Store)", {}).get("select")
            brand = brand_sel.get("name") if brand_sel else None
            if not phone or not brand:
                skipped += 1
                continue
            pid = wc.brand_to_phone_id(brand)
            if not pid:
                skipped += 1
                continue
            latest = await wc.get_latest_message(client, pid, phone)
            if not latest:
                skipped += 1
                continue
            agent_name = latest.get("agent_name")
            if not isinstance(agent_name, str) or not agent_name.strip().isdigit():
                skipped += 1
                continue
            sender = latest.get("sender")
            if sender != "bot":
                skipped += 1
                continue
            reply_time = latest.get("conversation_time")
            if not reply_time:
                skipped += 1
                continue
            actioned_missing = notion.ticket_actioned_at(ticket) is None
            ok = await notion.stamp_agent_reply(
                client, ticket["id"], reply_time, is_first_reply=actioned_missing, response_speed=None
            )
            if ok:
                fixed += 1
                print(f"  fixed {phone}/{brand} -> reply_time={reply_time}")
    print(f"\nDone. Fixed {fixed}. Skipped {skipped}.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Don't run yet — commit first**

```bash
git add scratch/backfill_stamps.py
git commit -m "feat(rpgrq): backfill script for orphaned Last Agent Reply / Actioned At"
```

- [ ] **Step 3: Run after deploy (see Task 24)**

### Task 22: Update directive

**Files:**
- Modify: `directives/rpgrq_leads_bot.md`

- [ ] **Step 1: Update the directive to describe v3 rules**

Replace the relevant sections of `directives/rpgrq_leads_bot.md`:

- Under "Core rules → Rule 1: Inbound", add a sub-bullet describing the shift-aware pool selection (steps 1-3 of the algorithm in the spec).
- Under "Core rules → Rule 3: Stamp on agent reply", document the multi-select Agent Assigned prepend semantics.
- Add a new "Rule 4 (revised): Label sync — Closed Date" section noting the auto-stamp on Closed transition.
- Under "Locked business rules", swap the "Dynamic reassignment" bullet for one mentioning the multi-select format.

(Don't rewrite from scratch — keep all existing structure and only edit the relevant parts.)

- [ ] **Step 2: Commit**

```bash
git add directives/rpgrq_leads_bot.md
git commit -m "docs: directive updated for RPGRQ Leads Bot v3"
```

### Task 23: Update memory entry

**Files:**
- Modify: `C:\Users\PMLS\.claude\projects\c--Users-PMLS-Desktop-Automation\memory\project_rpgrq_leads_bot.md`

- [ ] **Step 1: Append v3 bullets under "Locked design decisions"**

Add:

```markdown
- Shift-aware round-robin (v3): only on-shift agents get new leads. Off-hours falls back to next-to-start agent. Off Days (day-of-week) excludes the agent. If everyone is off today, last-resort = all active agents.
- `Agent Assigned` is now a multi-select (v3). Position [0] = latest replier; each new replier prepended. Single-select before v3.
- `Closed Date` (v3): stamped on every transition where Status gains the `Closed` label (re-stamped on each add).
- `Status` field no longer auto-set to `Waiting` (v3). Pure mirror of mapped WhatChimp labels.
- Daily report (v3): Responded today counts each multi-select participant who replied today (slight cross-day over-count, documented limitation). Closed today counts only `Agent Assigned[0]` on tickets with `Closed Date` today. Pending counts each multi-select participant.
```

- [ ] **Step 2: Commit**

```bash
# (memory lives outside the repo — no git commit needed)
echo "memory updated"
```

### Task 24: Deploy and verify

**Files:** none — operational steps.

- [ ] **Step 1: Push to production**

```bash
GIT_TERMINAL_PROMPT=0 git push production main
```

- [ ] **Step 2: SSH to VM and pull**

```bash
ssh bilal@34.30.125.177 'cd ~/automation && git pull production main'
```

- [ ] **Step 3: Restart the webhook service**

```bash
ssh bilal@34.30.125.177 'sudo systemctl restart rpgrq-webhook && sleep 2 && sudo systemctl status rpgrq-webhook --no-pager | head -20'
```

Expected: `Active: active (running)`.

- [ ] **Step 4: Health check**

```bash
curl -s http://34.30.125.177:8082/health
```

Expected: `rpgrq up`.

- [ ] **Step 5: Tail logs and watch for one real event**

```bash
ssh bilal@34.30.125.177 'tail -f ~/automation/rpgrq_webhook.log' &
TAIL_PID=$!
sleep 60   # wait for a real customer event to flow through
kill $TAIL_PID 2>/dev/null
```

Look for clean `🆕 new ticket ...` and/or `🔀 reassigned ...` lines without errors.

- [ ] **Step 6: Run backfill on VM**

```bash
ssh bilal@34.30.125.177 'cd ~/automation && python3 scratch/backfill_stamps.py'
```

Note the "Fixed N. Skipped M." line. Should be the count of historical orphans repaired.

- [ ] **Step 7: Run on-demand daily report**

```bash
ssh bilal@34.30.125.177 'cd ~/automation && python3 "RPGRQ Leads Bot/daily_report.py"'
```

Expected: prints per-agent counts and creates one report page in the Daily Report DB.

- [ ] **Step 8: Spot-check Notion**

Open the Leads DB. Confirm:
- A few recent tickets have `Agent Assigned` showing as multi-select chips (not a single select).
- Any closed ticket from earlier has a `Closed Date` value (only on tickets closed AFTER deploy — historical ones stay blank).
- New tickets created post-deploy have empty Status.

Open the Daily Report DB. Confirm the new row has correct numbers (or at least sensible numbers vs. yesterday's manual count).

- [ ] **Step 9: Tag the release**

```bash
git tag v3.0.0
GIT_TERMINAL_PROMPT=0 git push production v3.0.0
```

---

## Self-Review

**1. Spec coverage:**
- ✅ Multi-select Agent Assigned (Tasks 4, 6, 12)
- ✅ Closed Date field + stamping (Tasks 1, 5, 15)
- ✅ Off Days field (Task 1, Task 3 reads it)
- ✅ Shift-aware round-robin (Tasks 7, 8, 9)
- ✅ Half-open interval (Task 7 test, Task 8 implementation)
- ✅ Off-hours next-to-start fallback (Task 7 test, Task 8)
- ✅ Everyone-off-today last-resort (Task 7 test, Task 8)
- ✅ Status field cleanup (Task 6 — drop Status=Waiting)
- ✅ Stamping reliability retries (Tasks 13, 14)
- ✅ Diagnostic logging (Task 16)
- ✅ Closed Date stamping (Task 15)
- ✅ Daily report rewrite — Responded (Task 17), Closed (Task 18), Pending (Task 19), drop cron (Task 20)
- ✅ Backfill script (Task 21)
- ✅ Migration plan including risk fallback (Task 1 — but the in-place select→multi_select conversion may need the fallback documented in the spec)
- ✅ Directive + memory updates (Tasks 22, 23)
- ✅ Deploy sequence (Task 24)

**2. Placeholder scan:** No `TBD`, `TODO`, "fill in", "add error handling" etc. anywhere. ✓

**3. Type consistency:**
- `update_agent_assigned_list(client, page_id, ordered_agents: list[str])` — same signature referenced in Task 12.
- `ticket_agent_assigned_list(ticket)` returns `list[str]` — used in Task 12 (`notion.ticket_agent_assigned_list(ticket)`).
- `select_pool(roster, now_hour: int, day_name: str)` — used in Task 9 (`select_pool(roster, now_hour, day_name)`).
- `stamp_closed_date(client, page_id, closed_at_iso)` — used in Task 15.
All match. ✓

**4. Schema migration risk:** Task 1 attempts in-place select→multi_select conversion. The spec lists a fallback (create new column, copy, archive old) — but I don't include that fallback in the migration script. If Notion rejects the in-place conversion, the engineer would need to fall back manually. Documented but not pre-built. Acceptable for v3 since this is run interactively with the user watching the output.
