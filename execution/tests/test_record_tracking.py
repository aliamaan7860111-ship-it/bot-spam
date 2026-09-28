"""_record_tracking must hand off to the right writer, not to itself.

On 2026-09-28 the Notion branch of _record_tracking ended with

    return _record_tracking(page_ids_by_ref, tracking_pairs, orders_by_ref)

instead of _write_tracking_to_notion(...). One word, and it recurses until the
interpreter gives up.

What made it expensive is WHERE it sits. The caller runs it after
client.place_orders() has already succeeded, and outside the try that guards
placement - so the labels are bought and paid for, then every tracking number
for the batch is lost, the orders stay locked, and nothing is written to the log
or said in the group. The failure is indistinguishable from "the command did
nothing", which is exactly how it was reported.

These tests fail loudly on that slip: a recursive call raises RecursionError
long before any assertion is reached.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import order_bridge as ob


PAIRS = [
    {"barcode": "AM1234", "tracking_no": "48661000001"},
    {"barcode": "AM 620+AM5159", "tracking_no": "48661000002"},
]
BY_REF = {"AM1234": ["page-a"], "AM 620+AM5159": ["page-b", "page-c"]}


class TestRecordTrackingNotionBranch(unittest.TestCase):
    def test_delegates_to_the_notion_writer(self):
        calls = []

        def fake_writer(page_ids_by_ref, tracking_pairs, orders_by_ref=None):
            calls.append((page_ids_by_ref, tracking_pairs, orders_by_ref))
            return [e["tracking_no"] for e in tracking_pairs]

        with mock.patch.object(ob, "LABELS_FROM_GRQ_OS", False), \
             mock.patch.object(ob, "_write_tracking_to_notion", fake_writer):
            written = ob._record_tracking(BY_REF, PAIRS, None)

        self.assertEqual(len(calls), 1, "the Notion writer should be called exactly once")
        self.assertEqual(written, ["48661000001", "48661000002"])

    def test_passes_its_arguments_through_untouched(self):
        """A merged shipment covers several pages; losing that mapping would
        write one tracking number to one order and strand the rest."""
        seen = {}

        def fake_writer(page_ids_by_ref, tracking_pairs, orders_by_ref=None):
            seen["by_ref"] = page_ids_by_ref
            seen["pairs"] = tracking_pairs
            return []

        with mock.patch.object(ob, "LABELS_FROM_GRQ_OS", False), \
             mock.patch.object(ob, "_write_tracking_to_notion", fake_writer):
            ob._record_tracking(BY_REF, PAIRS, None)

        self.assertIs(seen["by_ref"], BY_REF)
        self.assertIs(seen["pairs"], PAIRS)
        self.assertEqual(seen["by_ref"]["AM 620+AM5159"], ["page-b", "page-c"])

    def test_does_not_call_itself(self):
        """The regression itself: with the writer stubbed out, a self-call is
        the only way execution can re-enter _record_tracking."""
        depth = {"n": 0}
        real = ob._record_tracking

        def counting(*args, **kwargs):
            depth["n"] += 1
            return real(*args, **kwargs)

        with mock.patch.object(ob, "LABELS_FROM_GRQ_OS", False), \
             mock.patch.object(ob, "_write_tracking_to_notion", lambda *a, **k: []), \
             mock.patch.object(ob, "_record_tracking", counting):
            ob._record_tracking(BY_REF, PAIRS, None)

        self.assertEqual(depth["n"], 0, "_record_tracking re-entered itself")


class TestRecordTrackingGrqOsBranch(unittest.TestCase):
    """The other branch still has to behave; the fix must not disturb it."""

    def test_records_batch_and_releases_the_lock(self):
        recorded, unlocked = {}, {}

        with mock.patch.object(ob, "LABELS_FROM_GRQ_OS", True), \
             mock.patch.object(ob, "grqw") as grqw, \
             mock.patch.object(ob, "_unlock_pages", lambda p: unlocked.setdefault("done", p)):
            grqw.record_labels.side_effect = lambda b: recorded.setdefault("batch", b) or True
            written = ob._record_tracking(BY_REF, PAIRS, None)

        self.assertEqual(written, ["48661000001", "48661000002"])
        self.assertEqual(len(recorded["batch"]), 2)
        self.assertEqual(recorded["batch"][1]["order_ids"], ["page-b", "page-c"])
        self.assertIn("done", unlocked)

    def test_skips_entries_filex_returned_no_tracking_for(self):
        pairs = PAIRS + [{"barcode": "AM9999", "tracking_no": None}]
        by_ref = dict(BY_REF, AM9999=["page-d"])

        with mock.patch.object(ob, "LABELS_FROM_GRQ_OS", True), \
             mock.patch.object(ob, "grqw") as grqw, \
             mock.patch.object(ob, "_unlock_pages", lambda p: None):
            grqw.record_labels.return_value = True
            written = ob._record_tracking(by_ref, pairs, None)

        self.assertEqual(written, ["48661000001", "48661000002"])


if __name__ == "__main__":
    unittest.main()
