"""
Nightly Filex reconciliation.

Two jobs in one run:
  1. Stuck-order alert: orders sitting at 'Label Created' for >24h get
     reported to the fulfillment Telegram group.
  2. Status reconciliation: for every active (non-RTO, dispatched within
     14 days) order, query Filex's ShipmentLastStatus and update Notion
     if the status has changed. Catches webhooks missed during downtime.

Run via systemd timer (filex-reconcile.timer) at 23:00 daily.
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
import requests

load_dotenv()

import notion_client as nc
import cutover
import filex_status_mapper
import grq_os_ingest
import grq_os_work as grqw
from filex_client import FilexClient

# Where the list of parcels to ask Filex about comes from. Once Notion stops
# being written, its list of active shipments stops growing - and a poller
# whose work list has quietly stopped growing looks exactly like a poller
# with nothing left to do.
FILEX_FROM_GRQ_OS = os.getenv("FILEX_FROM_GRQ_OS", "").strip().lower() in ("1", "true", "yes", "on")

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
FULFILLMENT_GROUP_ID = os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("filex_reconcile")


def _as_notion_shape(row: dict) -> dict:
    """
    A GRQ OS parcel in the shape this module has always spoken.

    The two disagree on more than names. GRQ OS calls the courier's word
    `delivery_status` and uses `order_id` for its own uuid; Notion calls them
    `filex_status` and `order_id`-the-code. Reading the wrong key does not
    fail, it compares against None - so every parcel looks like it just
    changed status, on every pass, forever.

    `page_id` is deliberately absent. There is no Notion page behind these,
    and every Notion write downstream checks for one.
    """
    return {
        "order_id":        row.get("order_code"),
        "grq_os_order_id": row.get("order_id"),
        "tracking_number": row.get("tracking_number"),
        "filex_status":    row.get("delivery_status"),
        "order_status":    row.get("order_status"),
        "customer_name":   row.get("customer"),
        "phone":           row.get("phone"),
        "page_id":         None,
    }


def _tell_grq_os(order: dict, raw_status: str, mapped: str, tracking_no: str, event_iso: str | None) -> None:
    """
    Send the same courier status to GRQ OS.

    GRQ OS does its own promotion: `ingest_courier` maps the courier's word
    through `status_options` and drags the ORDER STATUS along for the three
    that mean something to the team - Shipped, Delivered, Return to Origin -
    while refusing to walk back a decision a person made by hand. So only the
    raw status goes over; the mapping is not duplicated here, where the two
    copies would drift.

    Fire and forget, like every other GRQ OS write on this box: a problem
    there must never stop the Filex reconciliation, which is the job that
    keeps Notion honest.
    """
    try:
        grq_os_ingest.courier({
            "order_code": order.get("order_id"),
            "courier": "filex",
            "status": raw_status or mapped,
            "awb": tracking_no,
            "at": event_iso,
        })
    except Exception as e:
        log.warning("GRQ OS courier update skipped for %s: %s", order.get("order_id"), e)


def send_telegram(text: str) -> None:
    """Plain HTTP POST to Telegram Bot API. Avoids importing the full bot."""
    if not (TELEGRAM_BOT_TOKEN and FULFILLMENT_GROUP_ID):
        log.warning("Telegram not configured; skipping alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": FULFILLMENT_GROUP_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=20)


def alert_stuck_orders():
    """Find orders stuck at 'Label Created' for 24h+ and alert."""
    stuck = ([_as_notion_shape(r) for r in grqw.parcels_stuck(hours=24)]
             if FILEX_FROM_GRQ_OS else nc.query_filex_stuck(hours=24))
    if not stuck:
        log.info("No stuck orders.")
        return
    lines = [f"⚠️ *{len(stuck)} order(s) stuck at 'Label Created' for 24h+:*"]
    for order in stuck:
        lines.append(
            f"- `{order['order_id']}` (`{order.get('tracking_number') or '?'}`) "
            f"— {order.get('customer_name') or '?'}"
        )
    lines.append("\nInvestigate with Filex / fulfillment.")
    send_telegram("\n".join(lines))
    log.info("Sent stuck-order alert for %d orders.", len(stuck))


def reconcile_active_orders(cutoff_iso: str | None = None):
    """Poll Filex for status drift and update Notion when found."""
    if FILEX_FROM_GRQ_OS:
        # GRQ OS bounds this itself: active parcels only, behind the
        # automation line, so the eleven thousand imported tracking numbers
        # are never handed to a courier API.
        active = [_as_notion_shape(r) for r in grqw.parcels_in_flight(within_days=14)]
    elif cutoff_iso:
        active = nc.query_filex_active_since(cutoff_iso)
    else:
        active = nc.query_filex_active(within_days=14)
    if not active:
        log.info("No active orders to reconcile.")
        return

    client = FilexClient(FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE)

    # Build tracking_no -> page lookup
    by_tn = {o["tracking_number"]: o for o in active if o.get("tracking_number")}
    if not by_tn:
        log.info("No tracking numbers in active orders.")
        return

    # Batch in groups of 50
    tracking_numbers = list(by_tn.keys())
    for i in range(0, len(tracking_numbers), 50):
        chunk = tracking_numbers[i : i + 50]
        try:
            results = client.get_status(chunk)
        except Exception as e:
            log.error("get_status batch failed: %s", e)
            continue
        for r in results:
            tn = r["tracking_No"]
            order = by_tn.get(tn)
            if not order:
                continue
            mapped = filex_status_mapper.map_status(
                r.get("trackingStatus", ""), order.get("filex_status"),
            )
            if mapped != order.get("filex_status"):
                log.info(
                    "Reconcile drift: %s %s -> %s",
                    order["order_id"], order.get("filex_status"), mapped,
                )
                if cutover.write_notion() and order.get("page_id"):
                    nc.set_filex_status(order["page_id"], mapped)
                _tell_grq_os(order, r.get("trackingStatus", ""), mapped, tn, r.get("eventTime"))
                # Also promote to the main ORDER STATUS for Shipped/Delivered/RTO.
                # Pass current ORDER STATUS so we don't stomp downstream manual moves
                # (e.g. ops marked the order as ↩️ RETURNED after verifying the return).
                promoted = filex_status_mapper.order_status_from_filex(
                    mapped, order.get("order_status"),
                )
                if promoted:
                    # GRQ OS promotes the order status itself, inside
                    # `ingest_courier`, and refuses to walk back a decision a
                    # person made by hand. This branch is Notion only.
                    if cutover.write_notion() and order.get("page_id"):
                        nc.update_order_status(order["page_id"], promoted)
                    log.info(
                        "  ↳ ORDER STATUS promoted to %r for %s",
                        promoted, order["order_id"],
                    )
                    # The out-for-delivery WhatsApp used to be fired here, the
                    # instant we promoted to SHIPPED, and the comment said the
                    # double-send was prevented by "the success-set checkbox".
                    # That checkbox was Notion's. Notion is retired, so this
                    # path sent the customer a message and recorded it nowhere:
                    # the order dict has no GRQ OS id and no page worth
                    # writing to, so `_mark_sent` did nothing and the poller
                    # was free to send it again.
                    #
                    # `grq-ofd` polls GRQ OS every 30 seconds and now takes a
                    # real claim before sending. One sender, one latch; the
                    # only cost is up to half a minute.
                event_iso = r.get("eventTime")
                if event_iso:
                    # Filex eventTime is naive PKT; tag as +05:00 so stored UTC matches reality.
                    if "T" in event_iso and "+" not in event_iso and "Z" not in event_iso:
                        event_iso = event_iso + "+05:00"
                    if cutover.write_notion() and order.get("page_id"):
                        nc.set_last_update(order["page_id"], event_iso)


def main():
    parser = argparse.ArgumentParser(description="Filex reconciliation runner.")
    parser.add_argument("--status-only", action="store_true",
                        help="Skip stuck-order alerts; only reconcile statuses (used by polling timer).")
    parser.add_argument("--cutoff-iso", default=None,
                        help="ISO timestamp; only poll orders dispatched at or after this. "
                             "Default: 14 days back.")
    args = parser.parse_args()

    log.info("=== Filex reconcile starting (status_only=%s, cutoff=%s) ===",
             args.status_only, args.cutoff_iso)

    if not args.status_only:
        try:
            alert_stuck_orders()
        except Exception:
            log.exception("alert_stuck_orders crashed")

    try:
        reconcile_active_orders(cutoff_iso=args.cutoff_iso)
    except Exception:
        log.exception("reconcile_active_orders crashed")

    log.info("=== Filex reconcile run done ===")


if __name__ == "__main__":
    import error_reporter
    error_reporter.install("filex-poll", host="gcp-vm")
    main()
