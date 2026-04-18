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
        Return the next agent in rotation, or None if no active agents.
        Thread/task-safe via internal asyncio lock.
        """
        async with self._lock:
            roster = await notion.get_active_roster(client)
            if not roster:
                log.error("No active agents in roster")
                return None

            names = [a["name"] for a in roster]

            # Initialize the pointer from Notion on first call.
            if not self._pointer_initialized:
                last = await notion.get_last_assigned_agent(client, names)
                if last and last in names:
                    self._pointer_name = last
                    log.info(f"RR pointer initialized from Notion: last was {last}")
                else:
                    self._pointer_name = None  # start from index 0
                    log.info("RR pointer initialized fresh (no prior assignment found)")
                self._pointer_initialized = True

            # Compute next index.
            if self._pointer_name in names:
                idx = (names.index(self._pointer_name) + 1) % len(roster)
            else:
                # Pointer points to someone no longer active (or fresh start).
                idx = 0

            chosen = roster[idx]
            self._pointer_name = chosen["name"]
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
