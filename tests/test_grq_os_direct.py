"""Labels, courier statuses and payments reach GRQ OS without Vercel (2026-10-26)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import grq_os_ingest as gi  # noqa: E402
import grq_os_work as w  # noqa: E402


class Labels(unittest.TestCase):
    def call(self, payload, returns=None):
        with mock.patch.object(w.db, "enabled", return_value=True), \
             mock.patch.object(w.db, "rpc", return_value=returns) as rpc:
            handled, body = w._try_direct("labels", payload)
        self.assertTrue(handled)
        return rpc.call_args.args, body

    def test_board_keeps_the_routes_default_and_envelope(self):
        (fn, args), body = self.call({"action": "board"}, returns={"to_place": []})
        self.assertEqual((fn, args), ("orders_for_labelling", {"p_limit": 200}))
        self.assertEqual(body, {"to_place": []})

    def test_zero_is_a_limit_not_a_missing_value(self):
        (_, args), _ = self.call({"action": "board", "limit": 0})
        self.assertEqual(args, {"p_limit": 0})

    def test_in_flight_and_stuck(self):
        (fn, args), body = self.call({"action": "in_flight", "within_days": 21, "courier": "tjr"}, returns=[{"x": 1}])
        self.assertEqual((fn, args), ("parcels_in_flight", {"p_within_days": 21, "p_courier": "tjr", "p_limit": 500}))
        self.assertEqual(body, {"orders": [{"x": 1}]})
        (fn, args), _ = self.call({"action": "stuck"})
        self.assertEqual((fn, args), ("parcels_stuck_at_label", {"p_hours": 24, "p_courier": "filex", "p_limit": 200}))

    def test_begin_end_labels(self):
        (fn, args), body = self.call({"action": "begin", "order_ids": ["a"]}, returns="run-1")
        self.assertEqual((fn, args, body), ("begin_label_run", {"p_orders": ["a"], "p_worker": "print-all"}, {"run_id": "run-1"}))
        (fn, args), body = self.call({"action": "end", "run_id": "run-1"}, returns=3)
        self.assertEqual((fn, body), ("end_label_run", {"released": 3}))
        (fn, args), body = self.call({"action": "labels", "labels": [{"tracking_no": "T", "order_ids": ["a"]}]}, returns=1)
        self.assertEqual((fn, args, body), ("record_filex_labels", {"p": [{"tracking_no": "T", "order_ids": ["a"]}]}, {"written": 1}))

    def test_supersede_leaves_out_what_was_not_given(self):
        (fn, args), body = self.call({"action": "supersede", "order_id": "o", "courier": "tjr"})
        self.assertEqual((fn, args, body), ("supersede_courier", {"p_order": "o", "p_courier": "tjr"}, {"ok": True}))

    def test_a_database_refusal_is_a_refusal(self):
        with mock.patch.object(w.db, "enabled", return_value=True), \
             mock.patch.object(w.db, "rpc", side_effect=w.db.Failed("400 nope")):
            self.assertEqual(w._try_direct("labels", {"action": "one", "order_code": "X"}), (True, None))

    def test_without_the_database_it_goes_to_vercel_as_before(self):
        with mock.patch.object(w.db, "enabled", return_value=False):
            self.assertEqual(w._try_direct("labels", {"action": "board"}), (False, None))


class CourierAndPayment(unittest.TestCase):
    def test_courier_goes_to_postgres_when_enabled(self):
        with mock.patch.object(gi.db, "enabled", return_value=True), \
             mock.patch.object(gi.db, "rpc", return_value=True) as rpc, \
             mock.patch.object(gi, "_post") as post:
            p = {"order_code": "AM 1", "courier": "filex", "status": "Assigned to Driver"}
            self.assertTrue(gi.courier(p))
        rpc.assert_called_once_with("ingest_courier_status", {"p": p})
        post.assert_not_called()

    def test_unknown_order_and_refusals_are_false_and_never_raise(self):
        with mock.patch.object(gi.db, "enabled", return_value=True), \
             mock.patch.object(gi.db, "rpc", return_value=False):
            self.assertFalse(gi.courier({"order_code": "X", "courier": "tjr", "status": "delivered"}))
        with mock.patch.object(gi.db, "enabled", return_value=True), \
             mock.patch.object(gi.db, "rpc", side_effect=gi.db.Failed("unmapped courier status: x")):
            self.assertFalse(gi.courier({"order_code": "X", "courier": "tjr", "status": "x"}))

    def test_courier_falls_back_to_the_endpoint_without_the_database(self):
        with mock.patch.object(gi.db, "enabled", return_value=False), \
             mock.patch.object(gi, "_post", return_value=True) as post:
            self.assertTrue(gi.courier({"order_code": "X"}))
        post.assert_called_once_with("/api/ingest/courier", {"order_code": "X"})

    def test_payment_passes_what_the_route_passed(self):
        with mock.patch.object(gi.db, "enabled", return_value=True), \
             mock.patch.object(gi.db, "rpc", return_value={"found": True, "already": False}) as rpc:
            self.assertTrue(gi.payment({"order_code": " AM 5 ", "reference": "cs_1", "amount": 99}))
        rpc.assert_called_once_with("ingest_payment", {"p": {
            "order_code": "AM 5", "reference": "cs_1", "amount": 99, "method": "stripe", "at": None}})

    def test_payment_unknown_order_is_false(self):
        with mock.patch.object(gi.db, "enabled", return_value=True), \
             mock.patch.object(gi.db, "rpc", return_value={"found": False}):
            self.assertFalse(gi.payment({"order_code": "AM 5"}))


if __name__ == "__main__":
    unittest.main()
