"""
notion_catchup.py
=================
Tell Notion what it missed while it was switched off.

While `NOTION_RETIRED=1` every order lands in GRQ OS and nowhere else. That
is the point. But it means the gap in Notion is exactly as wide as the time
it was off, and switching Notion back on without filling that gap leaves two
books that disagree permanently - Notion's automations would never see those
orders, and the mirror would never correct them, because the mirror only
updates rows Notion already has.

So this runs on the way back on, before the mirror starts.

The hard part is not writing the rows. It is writing them so that Notion's
automations leave them alone. An order written as a plain new page is an
order the confirmation bot will message, the bridge will album, and /print
all will label - all for a second time. Every "already done" mark therefore
crosses with it:

    Confirmation Sent        the template already went
    ALBUMS SENT              how many albums fulfilment already has
    FULFILLMENT MESSAGE ID   which thread they went to
    Filex Submitted          it is already with a courier
    FILEX STATUS / Tracking  what that courier said
    Private Driver / Label   TJR has it, do not print another
    Out For Delivery Sent    the customer already knows
    Paid via Stripe          the money is in

Those are the same six marks the mirror carries the other way, for the same
reason, and getting one wrong costs a real message to a real customer.

    python3 execution/notion_catchup.py --since 2026-09-28T03:04:00Z
    python3 execution/notion_catchup.py --dry-run

Safe to run twice: an order that has been written is linked back to its page
id in GRQ OS and drops out of the list.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import grq_os_work as grq
import notion_client as nc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notion_catchup")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_API_KEY', '')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _text(value) -> dict:
    return {"rich_text": [{"text": {"content": str(value)[:2000]}}]}


def build_properties(o: dict) -> dict:
    """
    One GRQ OS order as a Notion page.

    Only fields with a value are written. Notion rejects a select whose
    option name is empty, and an empty rich_text is noise that makes a
    backfilled row look different from a webhook one.
    """
    props: dict = {
        nc.FIELD_ORDER_ID: {"title": [{"text": {"content": o["order_code"]}}]},
        nc.FIELD_PLATFORM_SOURCE: {"select": {"name": "Shopify" if o.get("channel") == "shopify" else "Manual"}},
    }

    for field, key in (
        (nc.FIELD_CUSTOMER_NAME, "customer"),
        (nc.FIELD_PHONE, "phone"),
        (nc.FIELD_EMAIL, "email"),
        (nc.FIELD_FULL_ADDRESS, "full_address"),
        (nc.FIELD_ITEM_QTY, "item_qty"),
        (nc.FIELD_ORDER_SOURCE_URL, "source_url"),
        (nc.FIELD_INTERNAL_NOTE, "internal_note"),
    ):
        if o.get(key):
            props[field] = _text(o[key])

    if o.get("source_ip"):
        props["IP ADDRESS"] = _text(o["source_ip"])

    # A total nobody has established is left blank rather than written as 0.
    # Zero is a price; blank is an admission.
    if o.get("total_known") and o.get("total") is not None:
        props[nc.FIELD_TOTAL] = {"number": float(o["total"])}

    if o.get("order_status"):
        props[nc.FIELD_ORDER_STATUS] = {"select": {"name": o["order_status"]}}
    if o.get("payment_method"):
        props[nc.FIELD_PAYMENT] = {"select": {"name": o["payment_method"]}}

    # ---- the marks --------------------------------------------------------
    if o.get("confirmation_sent"):
        props[nc.FIELD_WHATSAPP_SENT] = {"checkbox": True}
    if o.get("ofd_sent"):
        props[nc.FIELD_OUT_FOR_DELIVERY_SENT] = {"checkbox": True}
    if o.get("private_driver"):
        props[nc.FIELD_PRIVATE_DRIVER] = {"checkbox": True}
    if o.get("private_label_created"):
        props[nc.FIELD_PRIVATE_LABEL_CREATED] = {"checkbox": True}
    if o.get("filex_submitted"):
        props[nc.FIELD_FILEX_SUBMITTED] = {"checkbox": True}
    if o.get("paid_via_stripe"):
        props[nc.FIELD_PAID_VIA_STRIPE] = {"checkbox": True}
    if o.get("filex_status"):
        props[nc.FIELD_FILEX_STATUS] = {"select": {"name": o["filex_status"]}}
    if o.get("tracking_number"):
        props[nc.FIELD_TRACKING_NUMBER] = _text(o["tracking_number"])
    if (o.get("albums_sent") or 0) > 0:
        props[nc.FIELD_ALBUMS_SENT] = {"number": int(o["albums_sent"])}
    if o.get("fulfilment_message_id"):
        props[nc.FIELD_FULFILLMENT_MESSAGE_ID] = _text(o["fulfilment_message_id"])
    if o.get("dispatched_at"):
        props[nc.FIELD_DISPATCHED_AT] = {"date": {"start": o["dispatched_at"]}}

    return props


def create_page(client: httpx.Client, data_source_id: str, props: dict) -> str | None:
    res = client.post(
        f"{NOTION_API_BASE}/pages",
        headers=_headers(),
        json={"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": props},
        timeout=30.0,
    )
    if res.status_code != 200:
        log.error("Notion refused the page: %s %s", res.status_code, res.text[:300])
        return None
    return res.json().get("id")


def main() -> int:
    ap = argparse.ArgumentParser(description="Write orders GRQ OS has and Notion does not.")
    ap.add_argument("--since", default=None, help="ISO timestamp; default is everything unlinked")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true", help="list them and write nothing")
    args = ap.parse_args()

    if not os.getenv("NOTION_API_KEY"):
        log.error("NOTION_API_KEY is not set")
        return 1
    if not grq.configured():
        log.error("GRQ_OS_URL / GRQ_OS_INGEST_SECRET are not set")
        return 1

    body = grq._post("notion", {"action": "missing", "since": args.since, "limit": args.limit})
    if body is None:
        log.error("GRQ OS would not hand over the list")
        return 1
    orders = body.get("orders") or []

    if not orders:
        log.info("Notion is not missing anything.")
        return 0

    log.info("Notion is missing %d order(s).", len(orders))
    for o in orders:
        marks = [k for k in ("confirmation_sent", "ofd_sent", "private_driver",
                             "private_label_created", "filex_submitted", "paid_via_stripe")
                 if o.get(k)]
        log.info("  %-12s %-22s %s", o["order_code"], o.get("order_status") or "-",
                 ("already: " + ", ".join(marks)) if marks else "nothing done yet")

    if args.dry_run:
        log.info("Dry run; nothing written.")
        return 0

    # Private by name, but it is the only resolver and the 2025-09-03 API
    # needs a data source rather than a database id.
    data_source_id = nc._get_data_source_id()
    written, failed = 0, 0
    with httpx.Client() as client:
        for o in orders:
            page_id = create_page(client, data_source_id, build_properties(o))
            if not page_id:
                failed += 1
                continue
            # Link it back before counting it: an order written to Notion and
            # not linked here comes back on the next run and is written twice.
            if not grq.notion_link(o["order_code"], page_id):
                log.error("  %s written to Notion but GRQ OS would not link it — "
                          "re-running would duplicate it", o["order_code"])
                failed += 1
                continue
            written += 1
            log.info("  wrote %s -> %s", o["order_code"], page_id)

    log.info("Catch-up done: %d written, %d failed.", written, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
