"""Tests for nexa_tjr_cost_pull. Run: cd execution && python -m unittest test_nexa_tjr_cost_pull -v"""
import unittest

import nexa_tjr_cost_pull as p


def parcel(**kw):
    row = {"order_id": "t1", "order_ref": "AM 591", "tracking_id": "TJR-1", "status": "delivered",
           "driver_id": "d1", "final_day": "2026-10-21", "status_commission": "8.00", "attempt_commission": "0.00"}
    row.update(kw)
    return row


class Shape(unittest.TestCase):
    def test_a_parcel_becomes_a_charge_row(self):
        self.assertEqual(p.to_parcels([parcel()]), [{
            "source_ref": "t1", "order_ref": "AM 591", "tracking_id": "TJR-1", "status": "delivered",
            "driver_key": "d1", "final_day": "2026-10-21", "status_commission": 8.0, "attempt_commission": 0.0}])

    def test_order_ref_is_trimmed_and_blank_is_none(self):
        self.assertEqual(p.to_parcels([parcel(order_ref="  AM 591 ")])[0]["order_ref"], "AM 591")
        self.assertIsNone(p.to_parcels([parcel(order_ref="  ")])[0]["order_ref"])

    def test_an_expense_becomes_an_expense_row(self):
        self.assertEqual(p.to_expenses([{"id": "e1", "driver_id": "d1", "expense_date": "2026-10-21", "category": "petrol", "amount": "50"}]),
                         [{"source_ref": "e1", "driver_key": "d1", "day": "2026-10-21", "kind": "petrol", "amount": 50.0}])

    def test_an_expense_with_no_driver_is_dropped(self):
        self.assertEqual(p.to_expenses([{"id": "e1", "driver_id": None, "expense_date": "2026-10-21", "category": "x", "amount": "5"}]), [])


class Guard(unittest.TestCase):
    def test_nothing_from_tjr_never_replaces(self):
        class DB:
            called = False
            def replace(self, parcels, expenses): DB.called = True
        with self.assertRaises(p.EmptyRead):
            p.sync(lambda: [], lambda: [], DB())
        self.assertFalse(DB.called)

    def test_sync_hands_both_lists_over(self):
        seen = {}
        class DB:
            def replace(self, parcels, expenses):
                seen.update(parcels=parcels, expenses=expenses)
                return {"parcels": 1, "matched": 1, "expenses": 0, "unmatched": []}
        out = p.sync(lambda: [parcel()], lambda: [], DB())
        self.assertEqual(out["matched"], 1)
        self.assertEqual(len(seen["parcels"]), 1)


if __name__ == "__main__":
    unittest.main()
