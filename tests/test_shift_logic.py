"""Unit tests for the pure shift/pool-selection logic.

These tests run against module-level functions only -- no Notion or WhatChimp
API calls. They're the only meaningful tests we add for v3 because everything
else is integration-bound to live APIs.
"""

import sys
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
