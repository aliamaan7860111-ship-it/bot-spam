import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import notion_client as _nc
from order_bridge import _resolve_order_id


class TestResolveOrderId(unittest.TestCase):
    def test_exact_match_first(self):
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=lambda x: {"order_id": x} if x == "AM3030" else None):
            order = _resolve_order_id("AM3030")
            self.assertEqual(order["order_id"], "AM3030")

    def test_inserts_space_for_wa127(self):
        # Notion has 'WA 127' but user typed 'WA127'.
        def fake(x):
            return {"order_id": "WA 127"} if x == "WA 127" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            order = _resolve_order_id("WA127")
            self.assertEqual(order["order_id"], "WA 127")

    def test_strips_space_for_wa127(self):
        # Notion has 'WA127' but user typed 'WA 127'.
        def fake(x):
            return {"order_id": "WA127"} if x == "WA127" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            order = _resolve_order_id("WA 127")
            self.assertEqual(order["order_id"], "WA127")

    def test_unknown_id_returns_none(self):
        with patch.object(_nc, "find_order_by_shipper_ref", return_value=None):
            self.assertIsNone(_resolve_order_id("ZZ9999"))

    def test_preserves_case_sensitivity(self):
        # Notion is case-sensitive; lowercase variant should not match an uppercase row.
        def fake(x):
            return {"order_id": "AM3030"} if x == "AM3030" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            self.assertIsNone(_resolve_order_id("am3030"))


if __name__ == "__main__":
    unittest.main()
