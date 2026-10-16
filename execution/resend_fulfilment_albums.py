"""
resend_fulfilment_albums.py
===========================
Post fulfilment albums to the Telegram group again, one order at a time,
without touching the orders.

For when the group's messages are lost (deleted by accident, a chat
migrated) and the packers need them back. Each album is built exactly the
way the order bridge builds it - the same GRQ OS payload (the
`fulfilment_payload` function returns what `claim_for_fulfilment` does),
the same Shopify photos and sizes, the same merged-order combining, the
same caption - and sent with the same sender.

What it does NOT do: claim the order, stamp it sent, move its status, or
count albums. In GRQ OS nothing changes; only the group gets messages.

Usage (on the VM, from /home/bilal/automation):
    venv/bin/python execution/resend_fulfilment_albums.py --codes "AM5459,LU1829" --dry-run
    venv/bin/python execution/resend_fulfilment_albums.py --codes-file codes.txt --gap 4
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
load_dotenv(PROJECT_ROOT / ".env")

import grq_os_db as db  # noqa: E402
import grq_os_fulfilment as grq  # noqa: E402
import telegram_client as tg  # noqa: E402
from telegram import Bot  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("resend_albums")


async def run(codes: list[str], gap: float, dry: bool) -> int:
    if not db.enabled():
        log.error("GRQ OS direct access is not configured (grq_os_db)")
        return 2
    rows = db.rpc("fulfilment_payload", {"p_codes": codes}) or []
    found = [r.get("order_code") for r in rows]
    missing = [c for c in codes if c not in found]
    log.info("%d order(s) asked for, %d found%s", len(codes), len(rows),
             f", not found: {', '.join(missing)}" if missing else "")

    group = tg.TELEGRAM_FULFILLMENT_GROUP_ID
    if not dry and (not group or not tg.TELEGRAM_BOT_TOKEN):
        log.error("TELEGRAM_FULFILLMENT_GROUP_ID / TELEGRAM_BOT_TOKEN not set")
        return 2
    bot = None if dry else Bot(token=tg.TELEGRAM_BOT_TOKEN)

    sent = failed = 0
    for i, raw in enumerate(rows, 1):
        order = grq.to_bridge_order(raw)
        grq.combine_merged(order, tg.fetch_order_images_graphql)
        code = order["order_id"]
        if dry:
            log.info("[%d/%d] %s - would send (%s, %d item line(s))", i, len(rows), code,
                     "Shopify page" if order.get("order_source_url") else "GRQ OS photos",
                     len(order.get("own_items") or []) + sum(len(p.get("items") or []) for p in order.get("merged_parts") or []))
            continue
        try:
            res = await tg.send_order_to_group(bot, group, order, header="✅ NEW ORDER FOR FULFILLMENT")
        except Exception as e:  # one order failing must not stop the rest
            log.error("[%d/%d] %s - failed: %s", i, len(rows), code, e)
            failed += 1
        else:
            if res.get("success"):
                sent += 1
                log.info("[%d/%d] %s - sent (%s of %s photo(s))", i, len(rows), code,
                         res.get("image_count", 0), res.get("expected_count", "?"))
            else:
                failed += 1
                log.error("[%d/%d] %s - the sender reported a failure", i, len(rows), code)
        await asyncio.sleep(gap)  # Telegram limits how fast one group can take albums

    log.info("done: %d sent, %d failed, %d not found", sent, failed, len(missing))
    return 0 if failed == 0 and not missing else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", help="comma-separated order codes, in sending order")
    ap.add_argument("--codes-file", help="a file with one order code per line, in sending order")
    ap.add_argument("--gap", type=float, default=4.0, help="seconds between orders (default 4)")
    ap.add_argument("--dry-run", action="store_true", help="build every album, send nothing")
    a = ap.parse_args()
    codes: list[str] = []
    if a.codes:
        codes += [c.strip() for c in a.codes.split(",") if c.strip()]
    if a.codes_file:
        codes += [line.strip() for line in Path(a.codes_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not codes:
        ap.error("give --codes or --codes-file")
    sys.exit(asyncio.run(run(codes, a.gap, a.dry_run)))


if __name__ == "__main__":
    main()
