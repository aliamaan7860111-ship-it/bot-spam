#!/usr/bin/env python3
"""Re-send the combined Filex label PDF to the Telegram fulfillment channel.

Use this when `/print all` placed every order (labels created, tracking written
to Notion) but the final combined-PDF Telegram message failed to send.

It is READ-ONLY against Filex and Notion: it never places orders or writes
back — it only fetches the airway-bill PDF for orders that already have a
tracking number and posts it to the channel.

Scope (pick one; default = last 24h):
  (no args)            orders dispatched in the last 24 hours
  --hours N            orders dispatched in the last N hours
  --since YYYY-MM-DD   orders dispatched on/after that date (UTC midnight)
  --ids ID1,ID2,...    specific order IDs (looked up individually)
  --all                every order with ORDER STATUS=Processed that has tracking

Options:
  --dry-run            fetch + save the PDF locally but DO NOT send to Telegram

Examples (on the VM, from the execution dir):
  python3 resend_labels.py
  python3 resend_labels.py --since 2026-06-06
  python3 resend_labels.py --ids "AM3700,Di1845,VX 21"
  python3 resend_labels.py --all --dry-run
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import requests
import notion_client as nc
from filex_client import FilexClient

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")
TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
FULFILLMENT_GROUP_ID = os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID")


def select_orders(args) -> list[dict]:
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
        out = []
        for oid in ids:
            o = nc.find_order_by_id(oid)
            if o:
                out.append(o)
            else:
                print(f"  ! {oid}: not found in Notion")
        return out
    if args.all:
        return nc.query_filex_processed()
    if args.since:
        cutoff = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    print(f"  scope: dispatched on/after {cutoff}")
    return nc.query_filex_active_since(cutoff)


def main():
    ap = argparse.ArgumentParser(description="Re-send combined Filex label PDF to Telegram.")
    ap.add_argument("--hours", type=int, default=24, help="orders dispatched in the last N hours (default 24)")
    ap.add_argument("--since", help="orders dispatched on/after YYYY-MM-DD (UTC)")
    ap.add_argument("--ids", help="comma-separated order IDs")
    ap.add_argument("--all", action="store_true", help="every Processed order with tracking")
    ap.add_argument("--dry-run", action="store_true", help="save PDF locally, do not send")
    args = ap.parse_args()

    orders = select_orders(args)

    # Dedupe by tracking number (merged shipments share one).
    seen, unique_tns, rows_by_tn = set(), [], {}
    for o in orders:
        tn = (o.get("tracking_number") or "").strip()
        if not tn:
            print(f"  ! {o.get('order_id')} has no tracking — excluded")
            continue
        rows_by_tn.setdefault(tn, []).append(o.get("order_id"))
        if tn not in seen:
            seen.add(tn)
            unique_tns.append(tn)

    total_orders = sum(len(v) for v in rows_by_tn.values())
    merged = {tn: ids for tn, ids in rows_by_tn.items() if len(ids) > 1}
    print(f"Orders with tracking: {total_orders} | shipments: {len(unique_tns)} | merged: {len(merged)}")
    for tn, ids in merged.items():
        print(f"  merged {tn}: {' + '.join(ids)}")

    if not unique_tns:
        print("Nothing to send.")
        return 1

    client = FilexClient(FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE)
    print("Fetching combined PDF from Filex ...")
    pdf = client.get_label_pdf(unique_tns)
    today = datetime.now().strftime("%Y-%m-%d")
    out = HERE.parent / f"filex_labels_{today}_{len(unique_tns)}.pdf"
    out.write_bytes(pdf)
    print(f"  saved {len(pdf)} bytes -> {out}")

    cap = (f"\U0001F4E6 Filex labels — {today}\n"
           f"{total_orders} order(s) across {len(unique_tns)} shipment(s).")
    if merged:
        cap += "\n\nMerged shipments:\n" + "\n".join(f"• {' + '.join(ids)}" for ids in merged.values())
    cap = cap[:1024]

    if args.dry_run:
        print("\n[dry-run] PDF saved; not sent.")
        return 0
    if not (TELEGRAM_BOT_TOKEN and FULFILLMENT_GROUP_ID):
        print("\n! TELEGRAM_BOT_TOKEN / TELEGRAM_FULFILLMENT_GROUP_ID not set — not sent.")
        return 1

    print(f"Sending to Telegram chat {FULFILLMENT_GROUP_ID} ...")
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
        data={"chat_id": FULFILLMENT_GROUP_ID, "caption": cap},
        files={"document": (f"filex_labels_{today}_{len(unique_tns)}.pdf", pdf, "application/pdf")},
        timeout=120,
    )
    body = resp.json()
    if resp.status_code == 200 and body.get("ok"):
        print(f"  ✓ sent. message_id={body['result']['message_id']}")
        return 0
    print(f"  ✗ Telegram send failed: HTTP {resp.status_code} {body}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
