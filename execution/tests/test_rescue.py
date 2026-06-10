import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rescue_store as store

H = 3600.0
MIN_AGE = 23 * H          # fire from 23h00m...
MAX_AGE = 23 * H + 45 * 60  # ...until 23h45m


def fresh_conn():
    return store.connect(":memory:")


class TestStoreEligibility(unittest.TestCase):
    def test_unanswered_conversation_becomes_eligible_at_23h(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        # 1 second before the 23h mark: not eligible
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE - 1, MIN_AGE, MAX_AGE), [])
        # at the 23h mark: eligible
        rows = store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "9715551234")
        self.assertEqual(rows[0]["bot_id"], "381990")

    def test_missed_firing_window_is_skipped(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        # past 23h45m (e.g. service was down): never fire — window might be closed
        self.assertEqual(store.eligible(conn, 1000.0 + MAX_AGE + 1, MIN_AGE, MAX_AGE), [])

    def test_answered_conversation_not_eligible(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "381990", "9715551234", "VIREX UAE", ts=2000.0)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE), [])

    def test_new_inbound_after_answer_re_arms(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "381990", "9715551234", "VIREX UAE", ts=2000.0)
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=3000.0)
        rows = store.eligible(conn, 3000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)

    def test_rescued_conversation_not_eligible_again(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 60, MIN_AGE, MAX_AGE), [])

    def test_real_inbound_clears_rescued_flag_and_attempts(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=2000.0)
        store.bump_attempts(conn, "381990", "9715551234", ts=2000.0)
        # customer sends a real message → fully re-armed
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=5000.0)
        rows = store.eligible(conn, 5000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 0)

    def test_button_tap_does_not_re_arm(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.mark_rescued(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        store.record_button_tap(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0 + MIN_AGE + 60)
        # tap recorded, but conversation stays rescued/ineligible forever after
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 120, MIN_AGE, MAX_AGE), [])
        self.assertEqual(
            store.eligible(conn, 1000.0 + MIN_AGE + 60 + MIN_AGE, MIN_AGE, MAX_AGE), []
        )

    def test_attempts_cap_blocks_eligibility(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        for _ in range(store.MAX_ATTEMPTS):
            store.bump_attempts(conn, "381990", "9715551234", ts=1000.0 + MIN_AGE)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE + 60, MIN_AGE, MAX_AGE), [])

    def test_outbound_only_conversation_never_eligible(self):
        # e.g. an OFD template sent to someone who never wrote in
        conn = fresh_conn()
        store.record_outbound(conn, "381990", "9715559999", "VIREX UAE", ts=1000.0)
        self.assertEqual(store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE), [])

    def test_conversations_are_keyed_per_bot(self):
        conn = fresh_conn()
        store.record_real_inbound(conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        store.record_outbound(conn, "382073", "9715551234", "DIALO UAE", ts=2000.0)
        # the Dialo reply must not mark the Virex conversation answered
        rows = store.eligible(conn, 1000.0 + MIN_AGE, MIN_AGE, MAX_AGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bot_id"], "381990")


if __name__ == "__main__":
    unittest.main()
