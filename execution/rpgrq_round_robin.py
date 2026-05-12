"""
Round-robin rotation for RPGRQ Leads Bot.

Strategy:
  1. Read the active roster (cached).
  2. Derive the "anchor" agent from the most recently created Notion ticket
     that has Agent Assigned in the active roster. If none, start at index 0.
  3. For each assignment in a batch, advance an in-memory pointer so two events
     in the same second don't both read the same anchor from Notion.
  4. Response-speed math lives here too, keyed on the assigned agent's shift.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx

from execution import rpgrq_notion as notion

log = logging.getLogger("rpgrq.rr")

PKT = timezone(timedelta(hours=5))


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


class RoundRobin:
    """
    Stateful round-robin allocator. One instance per process.

    Usage:
        rr = RoundRobin()
        agent = await rr.next_agent(client)    # dict {name, team_member_id, shift_start, shift_end}
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._pointer_name: Optional[str] = None  # last agent we handed out
        self._pointer_initialized: bool = False

    async def next_agent(self, client: httpx.AsyncClient) -> Optional[dict]:
        """
        Return the next agent in rotation based on shift-aware pool selection.
        Thread/task-safe via internal asyncio lock.
        """
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
            if self._pointer_name and self._pointer_name in names:
                idx = (names.index(self._pointer_name) + 1) % len(pool)
            elif self._pointer_name is None:
                idx = 0
            else:
                # Pointer points outside the current pool: pick the smallest name
                # alphabetically greater than the pointer; else wrap to pool[0].
                greater = [n for n in names if n > self._pointer_name]
                idx = names.index(min(greater)) if greater else 0

            chosen = pool[idx]
            self._pointer_name = chosen["name"]
            log.info(
                f"RR pool@{now_hour:02d}:00 {day_name} = {names} -> {chosen['name']}"
            )
            return chosen

    async def refresh_pointer_from_notion(self, client: httpx.AsyncClient):
        """Force re-sync with Notion. Useful on long idle periods."""
        async with self._lock:
            self._pointer_initialized = False


# ────────────────────────────────────────────────────────────
# Response-speed math
# ────────────────────────────────────────────────────────────

def calculate_response_speed(
    created_at_iso: str,
    replied_at_iso: str,
    shift_start_hour: int,
    shift_end_hour: int,
) -> str:
    """
    Returns 'Fast' / 'Medium' / 'Slow'.
    Only counts business-hours minutes within the assigned agent's shift,
    measured in PKT. Same-day shifts only (shift_start < shift_end).
    """
    try:
        created = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00")).astimezone(PKT)
        replied = datetime.fromisoformat(replied_at_iso.replace("Z", "+00:00")).astimezone(PKT)
    except Exception as e:
        log.warning(f"Bad timestamps for speed calc: {e}")
        return "Medium"

    if replied <= created:
        return "Fast"

    if shift_start_hour is None or shift_end_hour is None:
        shift_start_hour, shift_end_hour = 12, 20  # fallback
    if shift_end_hour <= shift_start_hour:
        log.warning(f"Wraparound shift ({shift_start_hour}->{shift_end_hour}) unsupported; falling back to 12-20")
        shift_start_hour, shift_end_hour = 12, 20

    total_seconds = 0.0
    current = created
    while current < replied:
        day_shift_start = current.replace(hour=shift_start_hour, minute=0, second=0, microsecond=0)
        day_shift_end   = current.replace(hour=shift_end_hour,   minute=0, second=0, microsecond=0)

        if current < day_shift_start:
            current = day_shift_start
        elif current >= day_shift_end:
            # jump to next day's shift start
            current = (current + timedelta(days=1)).replace(
                hour=shift_start_hour, minute=0, second=0, microsecond=0
            )
        else:
            chunk_end = min(replied, day_shift_end)
            total_seconds += (chunk_end - current).total_seconds()
            current = chunk_end

    minutes = total_seconds / 60.0
    if minutes <= 5.0:
        return "Fast"
    if minutes <= 15.0:
        return "Medium"
    return "Slow"
