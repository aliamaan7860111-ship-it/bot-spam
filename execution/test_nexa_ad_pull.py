"""Tests for nexa_ad_pull. Run: cd execution && python -m unittest test_nexa_ad_pull -v"""
import datetime as dt
import unittest

import nexa_ad_pull as p


def tw(day="2026-10-05", ad="ad1", spend="1.5", **kw):
    row = {"event_date": day, "channel": "facebook-ads", "account_id": "acc", "campaign_id": "c1",
           "campaign_name": "Camp ", "adset_id": "s1", "adset_name": "Set", "ad_id": ad, "ad_name": "Ad",
           "currency": "AED", "spend": spend, "impressions": "10", "clicks": "2",
           "conversions": "1", "conversion_value": "100"}
    row.update(kw)
    return row


class Aggregate(unittest.TestCase):
    def test_rows_for_the_same_ad_and_day_are_summed(self):
        out = p.aggregate([tw(spend="1.5"), tw(spend="2.25")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["spend"], 3.75)
        self.assertEqual(out[0]["impressions"], 20)
        self.assertEqual(out[0]["platform_value"], 200.0)

    def test_different_days_stay_apart(self):
        out = p.aggregate([tw(day="2026-10-05"), tw(day="2026-10-06")])
        self.assertEqual(sorted(r["day"] for r in out), ["2026-10-05", "2026-10-06"])

    def test_names_are_stripped(self):
        self.assertEqual(p.aggregate([tw()])[0]["campaign_name"], "Camp")

    def test_missing_ad_id_falls_back_to_adset_then_campaign(self):
        self.assertEqual(p.aggregate([tw(ad_id="None")])[0]["ad_key"], "adset:s1")
        self.assertEqual(p.aggregate([tw(ad_id=None, adset_id="")])[0]["ad_key"], "campaign:c1")

    def test_non_aed_spend_is_refused(self):
        with self.assertRaises(p.PullError):
            p.aggregate([tw(currency="USD", spend="5")])

    def test_non_aed_row_with_no_spend_is_ignored_safely(self):
        self.assertEqual(p.aggregate([tw(currency="USD", spend="0")])[0]["spend"], 0.0)


class Window(unittest.TestCase):
    floor = dt.date(2026, 10, 1)

    def test_hourly_is_today_and_three_days_back(self):
        self.assertEqual(p.window("hourly", dt.date(2026, 10, 7), self.floor), (dt.date(2026, 10, 4), dt.date(2026, 10, 7)))

    def test_nightly_never_goes_before_the_floor(self):
        self.assertEqual(p.window("nightly", dt.date(2026, 10, 7), self.floor), (self.floor, dt.date(2026, 10, 7)))

    def test_initial_starts_at_the_floor(self):
        self.assertEqual(p.window("initial", dt.date(2026, 10, 20), self.floor), (self.floor, dt.date(2026, 10, 20)))


class FakeDb:
    def __init__(self, fail_replace=False):
        self.runs, self.replaced, self.fail_replace = [], [], fail_replace

    def replace(self, brand_id, start, end, rows):
        if self.fail_replace:
            raise RuntimeError("db down")
        self.replaced.append((brand_id, start, end, rows))
        return {"rows": len(rows), "spend": sum(r["spend"] for r in rows)}

    def record(self, run):
        self.runs.append(run)


SOURCE = {"brand_id": "b1", "shop_domain": "x.myshopify.com", "data_from": "2026-10-01"}
TODAY = dt.date(2026, 10, 7)


class PullStore(unittest.TestCase):
    def test_success_replaces_and_records_ok(self):
        db = FakeDb()
        res = p.pull_store(db, lambda shop, s, e: [tw()], SOURCE, "hourly", TODAY, sleep=lambda s: None)
        self.assertTrue(res["ok"])
        self.assertEqual(db.replaced[0][1:3], (dt.date(2026, 10, 4), TODAY))
        self.assertEqual(db.runs[-1]["status"], "ok")

    def test_transient_errors_are_retried(self):
        calls, sleeps = [], []

        def flaky(shop, s, e):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("reset")
            return [tw()]

        res = p.pull_store(FakeDb(), flaky, SOURCE, "hourly", TODAY, sleep=sleeps.append)
        self.assertTrue(res["ok"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_pull_error_is_not_retried_and_is_recorded(self):
        calls, db = [], FakeDb()

        def denied(shop, s, e):
            calls.append(1)
            raise p.PullError("Triple Whale access lost for x.myshopify.com (403)")

        res = p.pull_store(db, denied, SOURCE, "hourly", TODAY, sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(db.runs[-1]["status"], "failed")
        self.assertIn("403", db.runs[-1]["error"])

    def test_giving_up_after_three_attempts(self):
        db = FakeDb(fail_replace=True)
        res = p.pull_store(db, lambda shop, s, e: [tw()], SOURCE, "hourly", TODAY, sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertIn("db down", res["error"])
        self.assertEqual(db.runs[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
