"""Pull ad spend from Triple Whale into Nexa (the GRQ OS Supabase database).

Directive: directives/nexa_ad_pull.md

Modes:
  hourly   today and the previous 3 days (Dubai), because platforms restate
  nightly  the previous 30 days
  initial  from each store's data_from (2026-10-01) to today, once

Stores come from the database (ad_sources, active only), never from code.
Each store is pulled and written on its own: one failing never stops the rest.
A store's window is REPLACED in the database, so an ad that a platform drops
from a day disappears here too.

Env: TRIPLEWHALE_API_KEY, NEXA_SUPABASE_URL, NEXA_SUPABASE_SERVICE_KEY
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from zoneinfo import ZoneInfo

import httpx

TW_SQL = "https://api.triplewhale.com/api/v2/orcabase/api/sql"
DUBAI = ZoneInfo("Asia/Dubai")
WINDOW_DAYS = {"hourly": 3, "nightly": 30}
ATTEMPTS = 3

# Summed per hour-free key in SQL to keep the reply small; aggregate() still
# merges rows whose names changed during a day.
QUERY = """SELECT event_date, channel, account_id, campaign_id, campaign_name,
  adset_id, adset_name, ad_id, ad_name, currency,
  SUM(spend) AS spend, SUM(impressions) AS impressions, SUM(clicks) AS clicks,
  SUM(conversions) AS conversions, SUM(conversion_value) AS conversion_value
FROM ads_table
WHERE event_date BETWEEN @startDate AND @endDate
GROUP BY event_date, channel, account_id, campaign_id, campaign_name,
  adset_id, adset_name, ad_id, ad_name, currency"""

NAME_FIELDS = ("account_id", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name")


class PullError(Exception):
    """A failure retrying will not fix (access lost, bad currency, bad reply)."""


def today_dubai(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(DUBAI)).astimezone(DUBAI).date()


def window(mode: str, today: dt.date, data_from: dt.date) -> tuple[dt.date, dt.date]:
    start = data_from if mode == "initial" else today - dt.timedelta(days=WINDOW_DAYS[mode])
    return max(start, data_from), today


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "None", "null") else s


def _num(v) -> float:
    s = _text(v)
    try:
        return float(s) if s is not None else 0.0
    except ValueError:
        return 0.0


def ad_key(row: dict) -> str:
    for field, prefix in (("ad_id", ""), ("adset_id", "adset:"), ("campaign_id", "campaign:")):
        v = _text(row.get(field))
        if v:
            return prefix + v
    return "account:" + (_text(row.get("account_id")) or "unknown")


def aggregate(rows: list[dict]) -> list[dict]:
    """One row per (day, channel, ad). Names: last non-empty value seen."""
    out: dict[tuple, dict] = {}
    for r in rows:
        currency = _text(r.get("currency")) or "AED"
        if currency != "AED" and _num(r.get("spend")) != 0:
            raise PullError(f"spend reported in {currency}, not AED; conversion is not verified, refusing")
        key = (str(r["event_date"])[:10], _text(r.get("channel")) or "unknown", ad_key(r))
        a = out.get(key)
        if a is None:
            a = out[key] = {"day": key[0], "channel": key[1], "ad_key": key[2],
                            **{f: None for f in NAME_FIELDS},
                            "spend": 0.0, "impressions": 0, "clicks": 0,
                            "platform_purchases": 0.0, "platform_value": 0.0}
        for f in NAME_FIELDS:
            v = _text(r.get(f))
            if v:
                a[f] = v
        a["spend"] += _num(r.get("spend"))
        a["impressions"] += int(_num(r.get("impressions")))
        a["clicks"] += int(_num(r.get("clicks")))
        a["platform_purchases"] += _num(r.get("conversions"))
        a["platform_value"] += _num(r.get("conversion_value"))
    for a in out.values():
        a["spend"] = round(a["spend"], 2)
        a["platform_value"] = round(a["platform_value"], 2)
        a["platform_purchases"] = round(a["platform_purchases"], 4)
    return list(out.values())


def fetch_tw(client: httpx.Client, api_key: str, shop: str, start: dt.date, end: dt.date) -> list[dict]:
    r = client.post(TW_SQL, headers={"x-api-key": api_key}, timeout=120, json={
        "shopId": shop, "query": QUERY,
        "period": {"startDate": start.isoformat(), "endDate": end.isoformat()}})
    if r.status_code in (401, 403):
        raise PullError(f"Triple Whale access lost for {shop} ({r.status_code})")
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise PullError(f"unexpected Triple Whale reply for {shop}: {str(data)[:200]}")
    return data


class Db:
    """The three calls the job makes to Supabase, over PostgREST."""

    def __init__(self, client: httpx.Client, url: str, key: str):
        self.client, self.url = client, url.rstrip("/")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def sources(self) -> list[dict]:
        r = self.client.get(f"{self.url}/rest/v1/ad_sources", headers=self.h, timeout=30, params={
            "select": "brand_id,shop_domain,data_from,brands(code)",
            "active": "eq.true", "provider": "eq.triple_whale"})
        r.raise_for_status()
        return r.json()

    def replace(self, brand_id: str, start: dt.date, end: dt.date, rows: list[dict]) -> dict:
        r = self.client.post(f"{self.url}/rest/v1/rpc/nexa_replace_ad_spend", headers=self.h, timeout=120, json={
            "p_brand": brand_id, "p_from": start.isoformat(), "p_to": end.isoformat(), "p_rows": rows})
        if r.status_code >= 400:
            raise RuntimeError(f"nexa_replace_ad_spend {r.status_code}: {r.text[:300]}")
        return r.json()

    def record(self, run: dict) -> None:
        r = self.client.post(f"{self.url}/rest/v1/ad_pull_runs", timeout=30, json=run,
                             headers={**self.h, "Prefer": "return=minimal"})
        r.raise_for_status()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def pull_store(db, fetch, source: dict, mode: str, today: dt.date, sleep=time.sleep) -> dict:
    start, end = window(mode, today, dt.date.fromisoformat(source["data_from"]))
    run = {"brand_id": source["brand_id"], "started_at": _now(),
           "window_from": start.isoformat(), "window_to": end.isoformat()}
    error = "unknown"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            rows = aggregate(fetch(source["shop_domain"], start, end))
            res = db.replace(source["brand_id"], start, end, rows)
            db.record({**run, "finished_at": _now(), "status": "ok",
                       "rows": res["rows"], "spend_total": res["spend"]})
            return {"ok": True, "rows": res["rows"], "spend": res["spend"]}
        except PullError as e:
            error = str(e)
            break
        except Exception as e:  # transient: network, 5xx, database hiccup
            error = f"{type(e).__name__}: {e}"
            if attempt < ATTEMPTS:
                sleep(5 * attempt)
    db.record({**run, "finished_at": _now(), "status": "failed", "rows": 0, "error": error[:1000]})
    return {"ok": False, "error": error}


def main(argv=None, report=None) -> int:
    ap = argparse.ArgumentParser(description="Pull ad spend from Triple Whale into Nexa.")
    ap.add_argument("--mode", choices=["hourly", "nightly", "initial"], default="hourly")
    args = ap.parse_args(argv)

    tw_key = os.environ["TRIPLEWHALE_API_KEY"]
    url, key = os.environ["NEXA_SUPABASE_URL"], os.environ["NEXA_SUPABASE_SERVICE_KEY"]
    failures = []
    with httpx.Client() as client:
        db = Db(client, url, key)
        today = today_dubai()
        for src in db.sources():
            code = (src.get("brands") or {}).get("code") or src["brand_id"]
            try:
                res = pull_store(db, lambda shop, s, e: fetch_tw(client, tw_key, shop, s, e), src, args.mode, today)
            except Exception as e:  # recording the failure itself failed
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(f"{dt.datetime.now(DUBAI):%Y-%m-%d %H:%M} {args.mode} {code}: {res}", flush=True)
            if not res["ok"]:
                failures.append(f"{code}: {res['error']}")
    if failures and report:
        report("Nexa ad pull failed for " + "; ".join(failures),
               error_type="NexaAdPullFailed", context={"mode": args.mode, "failures": failures})
    return 1 if failures else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import error_reporter
        error_reporter.install("nexa-ad-pull", host="gcp-vm")
        _report = error_reporter.report
    except ImportError:  # running on the laptop, where error_reporter is not installed
        _report = None
    sys.exit(main(report=_report))
