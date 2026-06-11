import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# repo root too — rpgrq_webhook_server imports itself as `from execution import ...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

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


import rescue_server as rs


class TestClassifyEvent(unittest.TestCase):
    BASE = {
        "chat_id": "9715551234",
        "whatsapp_bot_id": "381990",
        "whatsapp_bot_name": "Virex UAE",
        "wa_message_id": "wamid.test1",
    }

    def test_incoming_text_is_real_inbound(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "where is my order?"})
        self.assertEqual(ev["kind"], "real_inbound")
        self.assertEqual(ev["bot_id"], "381990")
        self.assertEqual(ev["phone"], "9715551234")

    def test_incoming_button_label_is_button_tap(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "Connect with Agent"})
        self.assertEqual(ev["kind"], "button_tap")

    def test_button_reply_prefix_is_button_tap(self):
        # live-captured format 2026-06-11: '#Button Reply#<button title>'
        ev = rs.classify_event("in", {**self.BASE, "message": "#Button Reply#Connect with Agent"})
        self.assertEqual(ev["kind"], "button_tap")
        ev = rs.classify_event("in", {**self.BASE, "message": "#Button Reply#Not Interested"})
        self.assertEqual(ev["kind"], "button_tap")

    def test_other_flows_button_taps_stay_real_inbound(self):
        # a tap on some other flow's button (e.g. AC recovery) is a genuine interaction
        ev = rs.classify_event("in", {**self.BASE, "message": "#Button Reply#Complete Order"})
        self.assertEqual(ev["kind"], "real_inbound")

    def test_button_match_is_case_insensitive(self):
        ev = rs.classify_event("in", {**self.BASE, "message": "not interested"})
        self.assertEqual(ev["kind"], "button_tap")

    def test_extra_button_texts_from_env(self):
        # postback hashes captured live get added via RESCUE_EXTRA_BUTTON_TEXTS
        old = rs.BUTTON_TEXTS
        rs.BUTTON_TEXTS = rs.BUTTON_TEXTS | {"q9d4f8y6gzrkw_4"}
        try:
            ev = rs.classify_event("in", {**self.BASE, "message": "q9D4f8Y6gzRKW_4"})
            self.assertEqual(ev["kind"], "button_tap")
        finally:
            rs.BUTTON_TEXTS = old

    def test_text_extracted_from_alternate_keys(self):
        # exact field name is unconfirmed until live capture — accept common variants
        for key in ("message", "message_text", "text", "user_message", "msg"):
            ev = rs.classify_event("in", {**self.BASE, key: "hello"})
            self.assertEqual(ev["kind"], "real_inbound", f"key={key}")

    def test_incoming_without_text_is_real_inbound(self):
        # media-only message: still a real customer message
        ev = rs.classify_event("in", dict(self.BASE))
        self.assertEqual(ev["kind"], "real_inbound")

    def test_outgoing_is_outbound(self):
        ev = rs.classify_event("out", {**self.BASE, "message": "hi, agent here"})
        self.assertEqual(ev["kind"], "outbound")

    def test_missing_phone_or_bot_id_returns_none(self):
        self.assertIsNone(rs.classify_event("in", {"whatsapp_bot_id": "381990"}))
        self.assertIsNone(rs.classify_event("in", {"chat_id": "9715551234"}))


import asyncio


class TestRunTick(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        self.calls = []
        # restore module config after each test
        self._old_config = rs.RESCUE_CONFIG
        rs.RESCUE_CONFIG = {
            "381990": {"brand": "VIREX UAE", "phone_number_id": "1073890042476443",
                       "bot_flow_unique_id": "flow_virex_rescue", "enabled": True},
            "382073": {"brand": "DIALO UAE", "phone_number_id": "1002123586328400",
                       "bot_flow_unique_id": "", "enabled": False},
        }

    def tearDown(self):
        rs.RESCUE_CONFIG = self._old_config

    async def fake_trigger_ok(self, phone, phone_number_id, flow_id):
        self.calls.append((phone, phone_number_id, flow_id))
        return True

    async def fake_trigger_fail(self, phone, phone_number_id, flow_id):
        self.calls.append((phone, phone_number_id, flow_id))
        return False

    def test_fires_for_eligible_enabled_brand_and_marks_rescued(self):
        store.record_real_inbound(self.conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        now = 1000.0 + MIN_AGE + 60
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=now))
        self.assertEqual(fired, 1)
        self.assertEqual(self.calls, [("9715551234", "1073890042476443", "flow_virex_rescue")])
        # second tick: rescued_at set → no re-fire
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=now + 60))
        self.assertEqual(fired, 0)
        self.assertEqual(len(self.calls), 1)

    def test_disabled_brand_is_skipped(self):
        store.record_real_inbound(self.conn, "382073", "9715551234", "DIALO UAE", ts=1000.0)
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=1000.0 + MIN_AGE + 60))
        self.assertEqual(fired, 0)
        self.assertEqual(self.calls, [])

    def test_unknown_bot_id_is_skipped(self):
        store.record_real_inbound(self.conn, "999999", "9715551234", "???", ts=1000.0)
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=1000.0 + MIN_AGE + 60))
        self.assertEqual(fired, 0)

    def test_config_resolves_by_phone_number_id_fallback(self):
        # some bots' payloads carry phone_number_id in the bot-id field (LUNE case)
        store.record_real_inbound(self.conn, "1073890042476443", "9715551234", "VIREX UAE", ts=1000.0)
        fired = asyncio.run(rs.run_tick(self.conn, self.fake_trigger_ok, now=1000.0 + MIN_AGE + 60))
        self.assertEqual(fired, 1)
        self.assertEqual(self.calls, [("9715551234", "1073890042476443", "flow_virex_rescue")])

    def test_failed_trigger_bumps_attempts_and_caps_at_max(self):
        store.record_real_inbound(self.conn, "381990", "9715551234", "VIREX UAE", ts=1000.0)
        now = 1000.0 + MIN_AGE + 60
        for _ in range(store.MAX_ATTEMPTS + 2):  # more ticks than the cap
            asyncio.run(rs.run_tick(self.conn, self.fake_trigger_fail, now=now))
        # called exactly MAX_ATTEMPTS times, then silenced
        self.assertEqual(len(self.calls), store.MAX_ATTEMPTS)


class TestRescueTee(unittest.TestCase):
    def test_tee_swallows_connection_errors(self):
        # The tee must NEVER raise into the leads pipeline — even with rescue down.
        from execution import rpgrq_webhook_server as rws

        class ExplodingClient:
            async def post(self, *a, **kw):
                raise OSError("connection refused")

        # must not raise
        asyncio.run(rws.tee_to_rescue(ExplodingClient(), "in", {"chat_id": "x"}))

    def test_tee_disabled_when_url_empty(self):
        from execution import rpgrq_webhook_server as rws

        class MustNotBeCalled:
            async def post(self, *a, **kw):
                raise AssertionError("tee should be disabled")

        old = rws.RESCUE_EVENTS_URL
        rws.RESCUE_EVENTS_URL = ""
        try:
            asyncio.run(rws.tee_to_rescue(MustNotBeCalled(), "in", {"chat_id": "x"}))
        finally:
            rws.RESCUE_EVENTS_URL = old


if __name__ == "__main__":
    unittest.main()
