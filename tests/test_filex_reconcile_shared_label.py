"""Every order on a shared Filex label is reconciled, not just the last one (VL 238 / VL 239, 2026-10-29)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import filex_reconcile as fr  # noqa: E402


def row(code, tn, status="Label Created"):
    return {"order_id": code, "tracking_number": tn, "filex_status": status, "order_status": "Processed", "page_id": None}


class SharedLabel(unittest.TestCase):
    def run_with(self, rows, readings):
        client = mock.Mock()
        client.get_status.return_value = readings
        with mock.patch.object(fr, "FILEX_FROM_GRQ_OS", True), \
             mock.patch.object(fr.grqw, "parcels_in_flight", return_value=rows), \
             mock.patch.object(fr, "_as_notion_shape", side_effect=lambda r: r), \
             mock.patch.object(fr, "FilexClient", return_value=client), \
             mock.patch.object(fr.cutover, "write_notion", return_value=False), \
             mock.patch.object(fr, "_tell_grq_os") as tell:
            fr.reconcile_active_orders()
        return [c.args[0]["order_id"] for c in tell.call_args_list], client

    def test_both_orders_on_one_label_move_together(self):
        told, client = self.run_with(
            [row("VL 238", "48661048109"), row("VL 239", "48661048109"), row("LU1860", "48661048107")],
            [{"tracking_No": "48661048109", "trackingStatus": "Assigned to Driver", "eventTime": None},
             {"tracking_No": "48661048107", "trackingStatus": "To be Picked Up", "eventTime": None}],
        )
        self.assertEqual(sorted(told), ["VL 238", "VL 239"])
        # One Filex question per tracking number, not per order.
        asked = client.get_status.call_args.args[0]
        self.assertEqual(sorted(asked), ["48661048107", "48661048109"])

    def test_an_order_alone_on_its_label_still_moves(self):
        told, _ = self.run_with([row("AM 1", "111")], [{"tracking_No": "111", "trackingStatus": "Assigned to Driver"}])
        self.assertEqual(told, ["AM 1"])


if __name__ == "__main__":
    unittest.main()
