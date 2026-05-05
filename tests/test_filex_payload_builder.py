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

    def test_total_aed_key_also_accepted(self):
        order = self._full_order()
        order.pop("total")
        order["total_aed"] = 295.00
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "295.00")

    def test_total_as_string_with_aed_prefix(self):
        order = self._full_order()
        order["total"] = "AED299.00"
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "299.00")

    def test_total_as_string_with_aed_space(self):
        order = self._full_order()
        order["total"] = "AED 299.00"
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "299.00")

    def test_total_as_plain_string(self):
        order = self._full_order()
        order["total"] = "210"
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "210.00")

    def test_total_as_string_with_comma(self):
        order = self._full_order()
        order["total"] = "AED1,299.00"
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "1299.00")

    def test_total_aed_string_takes_precedence_over_total(self):
        order = self._full_order()
        order["total"] = 999  # ignored
        order["total_aed"] = "AED 295.00"
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "295.00")

    def test_total_garbage_string_raises(self):
        order = self._full_order()
        order["total"] = "not a number"
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("total", str(ctx.exception).lower())


from filex_payload_builder import parse_total


class TestParseTotal(unittest.TestCase):
    def test_int_passes_through(self):
        self.assertEqual(parse_total(300), 300.0)

    def test_float_passes_through(self):
        self.assertEqual(parse_total(300.99), 300.99)

    def test_zero_int_allowed(self):
        self.assertEqual(parse_total(0), 0.0)

    def test_zero_float_allowed(self):
        self.assertEqual(parse_total(0.0), 0.0)

    def test_negative_int_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(-5)

    def test_plain_string_number(self):
        self.assertEqual(parse_total("300"), 300.0)

    def test_string_with_decimal(self):
        self.assertEqual(parse_total("300.99"), 300.99)

    def test_aed_prefix_with_space(self):
        self.assertEqual(parse_total("AED 300.00"), 300.0)

    def test_aed_prefix_no_space(self):
        self.assertEqual(parse_total("AED1,111.99"), 1111.99)

    def test_inline_note_after_amount(self):
        # The bug from the AM3088 incident: was returning 1.00 instead of 1949.39.
        self.assertEqual(
            parse_total("AED1,949.39\n\nnote: send all good quality and same"),
            1949.39,
        )

    def test_million_thousand_separators(self):
        self.assertEqual(parse_total("AED 1,234,567.89"), 1234567.89)

    def test_trailing_slash_dash(self):
        self.assertEqual(parse_total("300/-"), 300.0)

    def test_aed_only_no_number_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total("AED")

    def test_empty_string_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total("")

    def test_none_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(None)

    def test_unsupported_type_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(["300"])


class TestBuildMergedPayload(unittest.TestCase):
    def _order(self, order_id, total, item):
        return {
            "page_id": f"pg-{order_id}",
            "order_id": order_id,
            "customer_name": "Salah Alhammadi",
            "phone": "0509678579",
            "full_address": "A 165/13 Fujairah",
            "total": total,
            "item_qty": item,
            "internal_note": "",
        }

    def test_two_orders_merge_correctly(self):
        from filex_payload_builder import build_merged_payload
        orders = [
            self._order("AM3013", 150.00, "LV Ring  1"),
            self._order("Di1665", 518.99, "RM Watch  1"),
        ]
        result = build_merged_payload(orders)
        self.assertEqual(result["ShipperRef"], "AM3013+Di1665")
        self.assertEqual(result["TotalCOG"], "668.99")
        self.assertEqual(result["NumberOfPieces"], "2")
        self.assertEqual(result["RecipientName"], "Salah Alhammadi")
        self.assertIn("LV Ring", result["Desc1"])
        self.assertIn("RM Watch", result["Desc1"])
        self.assertIn("Merged", result["Remarks"])

    def test_single_order_falls_through_to_normal_build(self):
        from filex_payload_builder import build_merged_payload, build_payload
        order = self._order("AM3013", 150.00, "LV Ring  1")
        merged = build_merged_payload([order])
        normal = build_payload(order)
        self.assertEqual(merged, normal)

    def test_three_orders_with_string_totals(self):
        from filex_payload_builder import build_merged_payload
        orders = [
            self._order("A", "AED100.00", "Item A  1"),
            self._order("B", "AED200.00", "Item B  1"),
            self._order("C", 300.00, "Item C  1"),
        ]
        result = build_merged_payload(orders)
        self.assertEqual(result["TotalCOG"], "600.00")
        self.assertEqual(result["NumberOfPieces"], "3")
        self.assertEqual(result["ShipperRef"], "A+B+C")


if __name__ == "__main__":
    unittest.main()
