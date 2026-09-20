"""
notion_mirror.py
================
Notion -> GRQ OS. One way, forever, until the teams move across.

The sales and processing teams work in Notion and will for weeks yet. Until
they move, GRQ OS has to agree with it order for order, or every difference
between the two is ambiguous: a bug in the new system, or just a record it
never heard about?

Nothing goes the other way. There is no conflict resolution in here and there
should not be - while Notion is the source of truth, an edit made in GRQ OS is
expected to be overwritten.

How it talks to GRQ OS
----------------------
Through the signed ingest endpoint, like every other automation on this box:

    GET  /api/ingest/mirror   -> the watermark and how far behind we are
    POST /api/ingest/mirror   -> a page of Notion results, applied

The VM has the shared ingest secret and no Supabase credentials. Handing it a
service-role key so it could write the database directly would put the
strongest credential we own on a machine that does not otherwise need it, to
save one hop.

Two things worth knowing
------------------------
1. The mirror writes nothing when nothing differs. That is what makes it safe
   to re-read a few minutes behind the watermark on every pass, which in turn
   is what stops an edit being lost when it lands between fetching a page and
   moving the mark.

2. Notion stops at ten thousand rows. A data source query paginates a hundred
   at a time and then, at exactly 10,000, says `has_more: false` - the same
   answer it gives when there is genuinely nothing left. A single poll never
   comes close, but after a long outage it could, so the cap is detected and
   the pass simply stops early: the watermark has advanced as far as it did
   read, and the next tick carries on from there.

Usage
-----
    python execution/notion_mirror.py            # poll forever (60s)
    python execution/notion_mirror.py --once     # one pass, then exit
    python execution/notion_mirror.py --health   # say how far apart they are

Requires in .env: NOTION_API_KEY, GRQ_OS_URL, GRQ_OS_INGEST_SECRET.
Optional: NOTION_ORDERS_DATA_SOURCE_ID, MIRROR_POLL_SECONDS, MIRROR_OVERLAP_MINUTES.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("notion_mirror")

NOTION_KEY = os.getenv("NOTION_API_KEY", "").strip()
DATA_SOURCE = os.getenv("NOTION_ORDERS_DATA_SOURCE_ID", "2ebc320e-ba59-80b9-b23e-000beb542ac8").strip()
NOTION_VERSION = "2025-09-03"

GRQ_OS_URL = os.getenv("GRQ_OS_URL", "").strip().rstrip("/")
INGEST_SECRET = os.getenv("GRQ_OS_INGEST_SECRET", "").strip()
BYPASS = os.getenv("GRQ_OS_BYPASS", "").strip()

POLL_SECONDS = int(os.getenv("MIRROR_POLL_SECONDS", "60"))
OVERLAP_MINUTES = int(os.getenv("MIRROR_OVERLAP_MINUTES", "5"))
BATCH = 100
NOTION_PAGE_CAP = 10_000


# ---------------------------------------------------------------------------
# Reading a Notion page
# ---------------------------------------------------------------------------

def _text(prop: dict | None) -> str:
    if not prop:
        return ""
    runs = prop.get("rich_text") or prop.get("title") or []
    return "".join(r.get("plain_text", "") for r in runs).strip()


def _select(prop: dict | None):
    return (prop or {}).get("select", {}).get("name") if (prop or {}).get("select") else None


def _number(prop: dict | None):
    return (prop or {}).get("number")


def _checkbox(prop: dict | None) -> bool:
    return bool((prop or {}).get("checkbox"))


def _date(prop: dict | None):
    d = (prop or {}).get("date")
    return d.get("start") if d else None


def to_payload(page: dict) -> dict:
    """One Notion page, in the shape mirror_from_notion wants."""
    q = page.get("properties", {})
    return {
        "notion_page_id": page["id"],
        "last_edited": page["last_edited_time"],
        "order_code": _text(q.get("ORDER ID")),
        "order_status": _select(q.get("ORDER STATUS")),
        "filex_status": _select(q.get("FILEX STATUS")),
        "total": _number(q.get("TOTAL")),
        "tracking_number": _text(q.get("Tracking Number")) or None,
        "internal_note": _text(q.get("INTERNAL NOTE")) or None,
        "cancellation_reason": _select(q.get("Cancellation Reason")),
        "private_driver": _checkbox(q.get("Private Driver")),
        "ofd_sent": _checkbox(q.get("Out For Delivery Sent")),
        "albums_sent": _number(q.get("ALBUMS SENT")),
        "dispatched_at": _date(q.get("Dispatched At")),
    }


# ---------------------------------------------------------------------------
# Talking to Notion
# ---------------------------------------------------------------------------

def notion_query(client: httpx.Client, body: dict) -> dict:
    """Query the orders data source, riding out rate limits and dropped sockets."""
    for attempt in range(6):
        try:
            res = client.post(
                f"https://api.notion.com/v1/data_sources/{DATA_SOURCE}/query",
                headers={
                    "Authorization": f"Bearer {NOTION_KEY}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=45.0,
            )
        except Exception as e:
            if attempt == 5:
                raise
            log.warning("Notion request failed (%s), retrying", e)
            time.sleep(2 * (attempt + 1))
            continue

        if res.status_code == 200:
            return res.json()
        # Notion allows three requests a second and says 429 when you exceed it.
        if res.status_code == 429 or res.status_code >= 500:
            wait = float(res.headers.get("retry-after", 1)) * (attempt + 1)
            time.sleep(wait)
            continue
        raise RuntimeError(f"Notion {res.status_code}: {res.text[:200]}")
    raise RuntimeError("Notion kept refusing after six attempts")


def pages_since(client: httpx.Client, since: str):
    """Every page edited at or after `since`, oldest edit first."""
    cursor = None
    seen = 0
    while True:
        body = {
            "page_size": 100,
            "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
            "filter": {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": since}},
        }
        if cursor:
            body["start_cursor"] = cursor
        j = notion_query(client, body)
        for page in j.get("results", []):
            yield page
            seen += 1
        if not j.get("has_more"):
            if seen >= NOTION_PAGE_CAP:
                # Not the end, just as far as Notion will go. The watermark has
                # moved with what we did read, so the next tick continues.
                log.warning(
                    "hit Notion's %d-row cap; the rest will be picked up next pass",
                    NOTION_PAGE_CAP,
                )
            return
        cursor = j["next_cursor"]


# ---------------------------------------------------------------------------
# Talking to GRQ OS
# ---------------------------------------------------------------------------

def _sign(raw: str) -> str:
    """HMAC over the exact bytes sent. Arabic customer names 401 otherwise."""
    return hmac.new(INGEST_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _headers(raw: str) -> dict:
    h = {"Content-Type": "application/json", "x-grq-signature": _sign(raw)}
    if BYPASS:
        h["x-vercel-protection-bypass"] = BYPASS
    return h


def grq_health(client: httpx.Client) -> dict | None:
    try:
        res = client.get(f"{GRQ_OS_URL}/api/ingest/mirror", headers=_headers(""), timeout=30.0)
        if res.status_code != 200:
            log.error("mirror health: %s %s", res.status_code, res.text[:200])
            return None
        return res.json()
    except Exception as e:
        log.error("mirror health failed: %s", e)
        return None


def send_batch(client: httpx.Client, batch: list[dict]) -> dict | None:
    raw = json.dumps({"pages": batch}, ensure_ascii=False)
    try:
        res = client.post(f"{GRQ_OS_URL}/api/ingest/mirror", content=raw.encode("utf-8"),
                          headers=_headers(raw), timeout=120.0)
    except Exception as e:
        log.error("batch of %d failed to send: %s", len(batch), e)
        return None
    if res.status_code != 200:
        log.error("batch of %d rejected: %s %s", len(batch), res.status_code, res.text[:200])
        return None
    return res.json()


# ---------------------------------------------------------------------------
# A pass
# ---------------------------------------------------------------------------

def run_once(client: httpx.Client) -> None:
    health = grq_health(client)
    if not health:
        return
    mark = health.get("watermark") or "1970-01-01T00:00:00Z"
    since = (
        datetime.fromisoformat(str(mark).replace("Z", "+00:00")) - timedelta(minutes=OVERLAP_MINUTES)
    ).astimezone(timezone.utc).isoformat()

    updated = unchanged = failed = 0
    missing: list[str] = []
    batch: list[dict] = []
    seen = 0

    def flush() -> None:
        nonlocal updated, unchanged, failed, batch
        if not batch:
            return
        r = send_batch(client, batch)
        if r is None:
            failed += len(batch)
        else:
            updated += r.get("updated", 0)
            unchanged += r.get("unchanged", 0)
            missing.extend(r.get("missing") or [])
            for f in r.get("failed") or []:
                failed += 1
                log.error("  %s: %s", f.get("order_code"), f.get("error"))
        batch = []

    for page in pages_since(client, since):
        batch.append(to_payload(page))
        seen += 1
        if len(batch) >= BATCH:
            flush()
    flush()

    if seen:
        log.info(
            "%d pages · %d updated · %d already agreed%s%s",
            seen, updated, unchanged,
            f" · {len(missing)} not in GRQ OS" if missing else "",
            f" · {failed} errored" if failed else "",
        )
    if missing:
        # Deliberately not created here. An order Notion has and GRQ OS does
        # not is a gap to go and look at; `npm run rescue` is the looking.
        log.warning("not in GRQ OS: %s", ", ".join(missing[:15]))


def main() -> None:
    ap = argparse.ArgumentParser(description="Mirror Notion order status into GRQ OS")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--health", action="store_true", help="print how far apart the two are")
    args = ap.parse_args()

    missing_config = [
        name for name, value in
        (("NOTION_API_KEY", NOTION_KEY), ("GRQ_OS_URL", GRQ_OS_URL), ("GRQ_OS_INGEST_SECRET", INGEST_SECRET))
        if not value
    ]
    if missing_config:
        # Inert rather than half-configured, the same contract as grq_os_ingest.
        log.error("not configured (%s); nothing to do", ", ".join(missing_config))
        return

    with httpx.Client() as client:
        if args.health:
            h = grq_health(client)
            if h:
                for k in sorted(h):
                    log.info("  %-18s %s", k, h[k])
            return

        if args.once:
            run_once(client)
            return

        log.info("mirror poller started — every %ds, %dm overlap", POLL_SECONDS, OVERLAP_MINUTES)
        while True:
            try:
                run_once(client)
            except Exception as e:
                # A mirror that dies on one bad pass stops being a mirror.
                log.error("pass failed: %s", e, exc_info=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    import error_reporter
    error_reporter.install("notion-mirror", host="gcp-vm")
    main()
