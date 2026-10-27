"""Copy TJR's per-parcel driver cost and driver expenses into Nexa (GRQ OS).

Directive: directives/nexa_tjr_cost_pull.md

Reads the TJR database (view parcel_costs, table driver_expenses) with TJR's
service key and hands both lists to GRQ OS nexa_replace_tjr_costs, which
swaps every TJR row in one transaction. Hourly on the VM.

A TJR read that returns no parcels aborts: an outage must never wipe Nexa's
TJR costs.

Env: NEXA_SUPABASE_URL, NEXA_SUPABASE_SERVICE_KEY (GRQ OS);
     TJR credentials from TJR_ENV_FILE (default /home/bilal/tjr-logistics/.env):
     SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import dotenv_values

PARCELS_SINCE = "2026-09-20"   # TJR created_at; Nexa's floor is 2026-10-01 by placed date
EXPENSES_SINCE = "2026-10-01"
PAGE = 1000


class EmptyRead(RuntimeError):
    """TJR returned nothing; replacing would wipe Nexa's TJR costs."""


def _ref(v) -> str | None:
    s = (v or "").strip()
    return s or None


def to_parcels(rows: list[dict]) -> list[dict]:
    return [{
        "source_ref": r["order_id"],
        "order_ref": _ref(r.get("order_ref")),
        "tracking_id": r.get("tracking_id"),
        "status": r.get("status"),
        "driver_key": r.get("driver_id"),
        "final_day": r.get("final_day"),
        "status_commission": float(r.get("status_commission") or 0),
        "attempt_commission": float(r.get("attempt_commission") or 0),
    } for r in rows]


def to_expenses(rows: list[dict]) -> list[dict]:
    return [{
        "source_ref": r["id"],
        "driver_key": r["driver_id"],
        "day": r["expense_date"],
        "kind": r.get("category") or "other",
        "amount": float(r.get("amount") or 0),
    } for r in rows if r.get("driver_id")]


def sync(read_parcels, read_expenses, db) -> dict:
    parcels = to_parcels(read_parcels())
    if not parcels:
        raise EmptyRead("TJR returned no parcels")
    return db.replace(parcels, to_expenses(read_expenses()))


class Rest:
    def __init__(self, client: httpx.Client, url: str, key: str):
        self.client, self.url = client, url.rstrip("/")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}"}

    def read_all(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        while True:
            r = self.client.get(f"{self.url}/rest/v1/{path}", params=params, timeout=60,
                                headers={**self.h, "Range": f"{len(out)}-{len(out) + PAGE - 1}"})
            r.raise_for_status()
            page = r.json()
            out.extend(page)
            if len(page) < PAGE:
                return out


class Nexa(Rest):
    def replace(self, parcels: list[dict], expenses: list[dict]) -> dict:
        r = self.client.post(f"{self.url}/rest/v1/rpc/nexa_replace_tjr_costs", headers=self.h, timeout=120,
                             json={"p_parcels": parcels, "p_expenses": expenses})
        r.raise_for_status()
        return r.json()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tjr-env", default=os.getenv("TJR_ENV_FILE", "/home/bilal/tjr-logistics/.env"))
    args = ap.parse_args(argv)
    tjr_env = dotenv_values(args.tjr_env)
    with httpx.Client() as client:
        tjr = Rest(client, tjr_env["SUPABASE_URL"], tjr_env["SUPABASE_SERVICE_ROLE_KEY"])
        nexa = Nexa(client, os.environ["NEXA_SUPABASE_URL"], os.environ["NEXA_SUPABASE_SERVICE_KEY"])
        out = sync(
            lambda: tjr.read_all("parcel_costs", {
                "select": "order_id,order_ref,tracking_id,status,driver_id,final_day,status_commission,attempt_commission",
                "created_at": f"gte.{PARCELS_SINCE}", "order": "created_at.asc"}),
            lambda: tjr.read_all("driver_expenses", {
                "select": "id,driver_id,expense_date,category,amount",
                "expense_date": f"gte.{EXPENSES_SINCE}", "order": "expense_date.asc"}),
            nexa,
        )
    unmatched = out.get("unmatched") or []
    print(f"tjr costs: {out['parcels']} parcels, {out['matched']} matched to orders, "
          f"{out['expenses']} expenses; {len(unmatched)} finished since 1 Oct with no order: {', '.join(unmatched[:20])}")
    return 0


if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import error_reporter
        error_reporter.install("nexa-tjr-cost-pull", host="gcp-vm")
    except ImportError:  # on the laptop, where error_reporter is not installed
        pass
    sys.exit(main())
