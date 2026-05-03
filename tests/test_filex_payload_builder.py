import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_payload_builder import build_payload, ValidationError

class TestBuildPayload(unittest.TestCase):
    def _full_order(self):
        return {
            "page_id": "page-id-123",
            "order_id": "AM2985",
            "customer_name": "Mariska Hulley",
            "phone": "0547737735",
            "full_address": "Apt 916 Binghatti Views Silicon Oasis Dubai",
            "total": 295.00,
            "item_qty": "YSL Bag | Icare Maxi  1",
            "internal_note": "",
        }

    def test_valid_order_builds_payload(self):
        order = self._full_order()
        result = build_payload(order)
        self.assertEqual(result["RecipientName"], "Mariska Hulley")
        self.assertEqual(result["TotalCOG"], "295.00")
        self.assertEqual(result["MobileNumber"], "0547737735")
        self.assertEqual(result["ShipperRef"], "AM2985")
        self.assertEqual(result["City"], "Dubai")
        self.assertEqual(result["AddressCountry"], "United Arab Emirates")
        self.assertEqual(result["NumberOfPieces"], "1")

    def test_phone_normalized_from_plus971(self):
        order = self._full_order()
        order["phone"] = "+971547737735"
        result = build_payload(order)
        self.assertEqual(result["MobileNumber"], "0547737735")

    def test_total_zero_allowed_for_exchange(self):
        order = self._full_order()
        order["total"] = 0
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "0.00")

    def test_missing_phone_raises(self):
        order = self._full_order()
        order["phone"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("phone", str(ctx.exception).lower())

    def test_missing_name_raises(self):
        order = self._full_order()
        order["customer_name"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("name", str(ctx.exception).lower())

    def test_no_city_raises(self):
        order = self._full_order()
        order["full_address"] = "Random building street villa"
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("city", str(ctx.exception).lower())

    def test_multiline_item_summed_into_pieces(self):
        order = self._full_order()
        order["item_qty"] = "Hermes Birkin  1\nBvlgari Set  1"
        result = build_payload(order)
        self.assertEqual(result["NumberOfPieces"], "2")

if __name__ == "__main__":
    unittest.main()
