import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_client import (
    resume_range,
    sort_orders_for_label_replies,
    legacy_label_caption,
)


class TestResumeRange(unittest.TestCase):
    def test_fresh_order_one_album(self):
        self.assertEqual(resume_range(0, 1), [(0, True)])

    def test_fresh_order_two_albums(self):
        self.assertEqual(resume_range(0, 2), [(0, False), (1, True)])

    def test_resume_after_first_album(self):
        self.assertEqual(resume_range(1, 2), [(1, True)])

    def test_already_complete(self):
        self.assertEqual(resume_range(2, 2), [])

    def test_overshoot_complete(self):
        self.assertEqual(resume_range(5, 2), [])

    def test_zero_total(self):
        self.assertEqual(resume_range(0, 0), [])

    def test_three_albums_resume_middle(self):
        self.assertEqual(resume_range(1, 3), [(1, False), (2, True)])


class TestSortOrdersForLabelReplies(unittest.TestCase):
    def test_chronological_by_message_id(self):
        orders = [
            {"order_id": "AM3", "fulfillment_message_id": 300},
            {"order_id": "AM1", "fulfillment_message_id": 100},
            {"order_id": "AM2", "fulfillment_message_id": 200},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2", "AM3"])

    def test_legacy_orders_go_last_sorted_by_order_id(self):
        orders = [
            {"order_id": "AM5", "fulfillment_message_id": 500},
            {"order_id": "AM7", "fulfillment_message_id": None},
            {"order_id": "AM6", "fulfillment_message_id": None},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM5", "AM6", "AM7"])

    def test_only_legacy(self):
        orders = [
            {"order_id": "AM2", "fulfillment_message_id": None},
            {"order_id": "AM1", "fulfillment_message_id": None},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2"])

    def test_only_anchored(self):
        orders = [
            {"order_id": "AM2", "fulfillment_message_id": 200},
            {"order_id": "AM1", "fulfillment_message_id": 100},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2"])

    def test_empty_input(self):
        self.assertEqual(sort_orders_for_label_replies([]), [])


class TestLegacyLabelCaption(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            legacy_label_caption("AM1234"),
            "📄 AM1234 — legacy order, no thread anchor",
        )

    def test_strips_whitespace(self):
        self.assertEqual(
            legacy_label_caption("  AM1234  "),
            "📄 AM1234 — legacy order, no thread anchor",
        )


if __name__ == "__main__":
    unittest.main()
