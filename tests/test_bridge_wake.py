"""POST /wake makes the fulfilment poller check at once; a burst is one check."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import order_bridge as ob  # noqa: E402


class Wake(unittest.TestCase):
    def setUp(self):
        ob._wake = asyncio.Event()
        ob._last_wake = 0.0

    def test_a_wake_sets_the_event(self):
        self.assertTrue(ob.wake_poller())
        self.assertTrue(ob._wake.is_set())

    def test_a_burst_of_wakes_is_one_check(self):
        self.assertTrue(ob.wake_poller())
        self.assertFalse(ob.wake_poller())
        self.assertFalse(ob.wake_poller())

    def test_after_the_gap_it_wakes_again(self):
        self.assertTrue(ob.wake_poller())
        ob._last_wake = time.monotonic() - ob.WAKE_MIN_GAP - 0.1
        self.assertTrue(ob.wake_poller())

    def test_before_the_poller_starts_a_wake_is_ignored(self):
        ob._wake = None
        self.assertFalse(ob.wake_poller())


if __name__ == "__main__":
    unittest.main()
