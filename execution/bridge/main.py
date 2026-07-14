"""
grq-ac — Abandoned Checkout Recovery bridge.

Receives Shopify checkout webhooks for 6 brands, writes to Notion, and schedules
a delayed WhatChimp recovery template (name + discounted checkout link). Suppresses
the send if the customer completes the order first. Recovery is link-based: the
template carries the checkout URL with a one-time discount code, so the customer
completes on Shopify directly — the bridge never builds an order.

Run with:
    uvicorn main:app --host 127.0.0.1 --port 8084

Caddy on the VM reverse-proxies https://grqholdings.duckdns.org/* to this.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request

import notion_writer as nw
import sent_log
from config import (
    BrandConfig,
    bridge_base_url,
    load_all_brands,
    recovery_delay_seconds,
)
from shopify_verify import verify_shopify_hmac
from whatchimp_sender import assign_custom_fields, send_recovery_template, upsert_subscriber

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("grq-ac")

BRANDS: dict[str, BrandConfig] = {}
HTTP: httpx.AsyncClient | None = None
SCHEDULER: AsyncIOScheduler | None = None


def _brand_or_404(slug: str) -> BrandConfig:
    b = BRANDS.get(slug)
    if not b:
        raise HTTPException(404, f"Unknown brand: {slug}")
    return b


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_phone(payload: dict) -> str | None:
    # Shopify checkout payload has phone in a few places — try them in order
    return (
        payload.get("phone")
        or (payload.get("customer") or {}).get("phone")
        or (payload.get("billing_address") or {}).get("phone")
        or (payload.get("shipping_address") or {}).get("phone")
    )


def normalize_phone(raw: str | None, default_cc: str = "+971") -> str | None:
    """Coerce a phone number to E.164.

    - already '+'-prefixed   -> kept as-is
    - '00'-prefixed          -> '00' replaced with '+'
    - leading '0' local      -> strip 0, prepend default country code
    - bare digits, >=11 long -> already includes a country code (WhatsApp Chat
      IDs are this shape, e.g. 923372461000 or 971503512023) -> just prepend '+'
    - bare digits, <11 long  -> assume local, prepend default_cc

    default_cc is +971 (UAE) since all 6 stores are UAE-based. The >=11-digit
    branch makes us tolerant of WhatChimp's #LEAD_USER_CHAT_ID# which delivers
    phones in international form without the '+' prefix.
    """
    if not raw:
        return None
    s = "".join(c for c in raw.strip() if c.isdigit() or c == "+")
    if not s:
        return None
    if s.startswith("+"):
        return s
    if s.startswith("00"):
        return "+" + s[2:]
    if s.startswith("0"):
        return default_cc + s[1:]
    if len(s) >= 11:
        return "+" + s
    return default_cc + s


def _extract_name(payload: dict) -> str | None:
    cust = payload.get("customer") or {}
    name = " ".join(
        x for x in [cust.get("first_name"), cust.get("last_name")] if x
    ).strip()
    if name:
        return name
    addr = payload.get("billing_address") or payload.get("shipping_address") or {}
    name = " ".join(
        x for x in [addr.get("first_name"), addr.get("last_name")] if x
    ).strip()
    return name or None


def _extract_cart_items_text(payload: dict) -> str:
    lines = payload.get("line_items") or []
    return "; ".join(
        f"{i.get('quantity', 1)}× {i.get('title') or i.get('name') or 'item'}"
        for i in lines
    )[:1900]


def _extract_total(payload: dict) -> float | None:
    for key in ("total_price", "subtotal_price", "total_line_items_price"):
        v = payload.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


async def _do_send(brand_slug: str, page_id: str, phone: str) -> None:
    """The actual WhatChimp send fired by the scheduler after the delay.

    Re-reads the phone + customer name + checkout url from Notion at fire time so
    any correction made after the job was scheduled (via a later checkouts/update)
    is honoured. Then:
      1. Upserts the subscriber with their first name so {{1}} substitutes.
      2. Sets the `url` custom field so {{2}} renders the discounted checkout link.
      3. Sends the recovery offer template.
    """
    assert HTTP is not None
    brand = BRANDS[brand_slug]
    try:
        current_phone, customer_name, db_checkout_url = await nw.get_phone_name_and_url(HTTP, page_id)
    except Exception:
        current_phone, customer_name, db_checkout_url = None, None, None
    phone = normalize_phone(current_phone or phone) or phone
    first_name = (customer_name or "").strip().split(" ")[0] or None

    # DEDUP GUARD: never message the same number twice from the same WhatsApp
    # sender within the de-dup window. Protects against scheduler re-fires,
    # retries, and Notion-write failures that leave a row un-marked. Durable
    # across restarts (SQLite on disk).
    sender_id = brand.whatchimp_phone_number_id
    if sender_id and sent_log.already_sent_recently(phone, sender_id):
        log.info("[%s] DEDUP skip: %s already messaged from sender %s within window",
                 brand_slug, phone, sender_id)
        try:
            await nw.patch_status(HTTP, page_id, "Recovery Sent", recovery_sent_at=_now_iso())
        except Exception:
            log.exception("[%s] dedup-skip patch_status failed (non-fatal)", brand_slug)
        return

    discount_code = brand.checkout_discount_code
    final_checkout_url = db_checkout_url or ""
    if final_checkout_url and discount_code:
        sep = "&" if "?" in final_checkout_url else "?"
        final_checkout_url += f"{sep}discount={discount_code}"

    # 1. Make sure the subscriber exists with their first name set (fills {{1}}).
    if first_name and brand.whatchimp_phone_number_id:
        try:
            up = await upsert_subscriber(HTTP, phone, brand, first_name)
            log.info("[%s] upsert_subscriber phone=%s name=%s -> %s",
                     brand_slug, phone, first_name, up.get("status"))
        except Exception:
            log.exception("[%s] upsert_subscriber failed (non-fatal)", brand_slug)

    # 2. Set the `url` custom field so {{2}} = #!url!# renders the discounted link.
    if final_checkout_url and brand.whatchimp_phone_number_id:
        try:
            c_resp = await assign_custom_fields(HTTP, phone=phone, brand=brand, fields={"url": final_checkout_url})
            log.info("[%s] assign_custom_fields url to %s -> %s", brand_slug, phone, c_resp.get("status"))
        except Exception:
            log.exception("[%s] assign_custom_fields failed", brand_slug)

    log.info("[%s] scheduled send firing for page=%s phone=%s name=%s",
             brand_slug, page_id, phone, first_name)
    result = await send_recovery_template(
        HTTP, brand=brand, phone=phone,
        first_name=first_name,
        checkout_url=final_checkout_url,
    )

    if result["status"] == "ok":
        # Record in the durable de-dup log FIRST — before the Notion write — so
        # even if patch_status fails, this number can never be re-sent later.
        if sender_id:
            sent_log.record_sent(phone, sender_id, result.get("message_id"))
        await nw.patch_status(
            HTTP, page_id, "Recovery Sent", recovery_sent_at=_now_iso()
        )
        log.info("[%s] recovery sent | wa_message_id=%s", brand_slug, result.get("message_id"))
    elif result["status"] == "stubbed":
        await nw.patch_status(HTTP, page_id, "Recovery Pending")
        log.info("[%s] stubbed (template not configured) | page=%s", brand_slug, page_id)
    else:
        log.error("[%s] send failed: %s", brand_slug, result)


def _schedule_send(brand_slug: str, page_id: str, phone: str, run_at: datetime) -> None:
    assert SCHEDULER is not None
    job_id = f"recov_{brand_slug}_{page_id}"
    # Replace existing job if one is already scheduled for this checkout
    SCHEDULER.add_job(
        _do_send,
        "date",
        run_date=run_at,
        args=[brand_slug, page_id, phone],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    log.info("[%s] scheduled send at %s | page=%s", brand_slug, run_at.isoformat(), page_id)


async def _backfill_pending() -> None:
    """On startup, find Notion rows with Status=New that have passed their delay
    and either fire them immediately or reschedule for whatever time remains.
    """
    assert HTTP is not None
    delay = recovery_delay_seconds()

    rows = await nw.find_rows_pending_recovery(HTTP, before_iso=_now_iso())
    log.info("backfill: %d rows with Status=New", len(rows))

    for row in rows:
        props = row.get("properties", {})
        page_id = row["id"]
        abandoned = (props.get("Abandoned At") or {}).get("date") or {}
        abandoned_at_str = abandoned.get("start")
        phone_obj = props.get("Phone") or {}
        phone = phone_obj.get("phone_number")
        brand_obj = props.get("Brand") or {}
        brand_name = (brand_obj.get("select") or {}).get("name", "").lower()

        if brand_name not in BRANDS:
            log.warning("backfill: skip row %s — unknown brand %s", page_id, brand_name)
            continue
        if not phone:
            log.warning("backfill: skip row %s — no phone", page_id)
            continue
        if not abandoned_at_str:
            log.warning("backfill: skip row %s — no abandoned_at", page_id)
            continue

        abandoned_dt = datetime.fromisoformat(abandoned_at_str.replace("Z", "+00:00"))
        send_at = abandoned_dt + timedelta(seconds=delay)
        now = datetime.now(timezone.utc)

        if send_at <= now:
            # Fire now (asyncio task so we don't block startup)
            log.info("backfill: firing overdue send for %s (was due %s)", page_id, send_at)
            asyncio.create_task(_do_send(brand_name, page_id, phone))
        else:
            _schedule_send(brand_name, page_id, phone, send_at)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BRANDS, HTTP, SCHEDULER
    BRANDS = load_all_brands()
    log.info("loaded brands: %s", list(BRANDS.keys()))
    for slug, cfg in BRANDS.items():
        log.info(
            "  %s | shopify_ready=%s whatchimp_ready=%s",
            slug, cfg.shopify_ready, cfg.whatchimp_ready,
        )
    HTTP = httpx.AsyncClient(timeout=20.0)
    SCHEDULER = AsyncIOScheduler(timezone="UTC")
    SCHEDULER.start()

    try:
        await _backfill_pending()
    except Exception:
        log.exception("backfill failed (non-fatal)")

    yield

    SCHEDULER.shutdown(wait=False)
    await HTTP.aclose()


app = FastAPI(title="grq-ac", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "brands": {
            slug: {
                "shopify_ready": cfg.shopify_ready,
                "whatchimp_ready": cfg.whatchimp_ready,
            }
            for slug, cfg in BRANDS.items()
        },
        "scheduled_jobs": len(SCHEDULER.get_jobs()) if SCHEDULER else 0,
    }


@app.post("/webhooks/{brand}/checkout-created")
async def shopify_checkout_created(
    brand: str,
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    cfg = _brand_or_404(brand)
    raw = await request.body()
    if not verify_shopify_hmac(cfg.shopify_api_secret, raw, x_shopify_hmac_sha256):
        raise HTTPException(401, "HMAC verification failed")

    payload = await request.json()
    return await _handle_checkout(cfg, payload, fresh=True)


@app.post("/webhooks/{brand}/checkout-updated")
async def shopify_checkout_updated(
    brand: str,
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    cfg = _brand_or_404(brand)
    raw = await request.body()
    if not verify_shopify_hmac(cfg.shopify_api_secret, raw, x_shopify_hmac_sha256):
        raise HTTPException(401, "HMAC verification failed")

    payload = await request.json()
    return await _handle_checkout(cfg, payload, fresh=False)


@app.post("/webhooks/{brand}/order-created")
async def shopify_order_created(
    brand: str,
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    cfg = _brand_or_404(brand)
    raw = await request.body()
    if not verify_shopify_hmac(cfg.shopify_api_secret, raw, x_shopify_hmac_sha256):
        raise HTTPException(401, "HMAC verification failed")

    payload = await request.json()
    return await _handle_order(cfg, payload)


async def _handle_checkout(cfg: BrandConfig, payload: dict, *, fresh: bool) -> dict:
    """Process a checkouts/create or checkouts/update event.

    `fresh` only reflects which Shopify endpoint fired. Scheduling is driven by
    whether the Notion row was newly created (was_created) — so a checkout whose
    first event we ever see is an `update` still gets a recovery scheduled.
    """
    assert HTTP is not None

    checkout_id = str(payload.get("id") or payload.get("token") or "")
    if not checkout_id:
        log.warning("[%s] checkout event with no id/token, ignoring", cfg.slug)
        return {"status": "ignored", "reason": "no checkout id"}

    phone = normalize_phone(_extract_phone(payload))
    if not phone:
        log.info("[%s] checkout %s has no phone, ignoring", cfg.slug, checkout_id)
        return {"status": "ignored", "reason": "no phone"}

    customer_name = _extract_name(payload)
    email = payload.get("email") or (payload.get("customer") or {}).get("email")
    cart_value = _extract_total(payload)
    cart_items = _extract_cart_items_text(payload)
    checkout_url = payload.get("abandoned_checkout_url")
    abandoned_at = payload.get("created_at") or payload.get("updated_at") or _now_iso()

    # One active row per phone+brand: if this customer already has an active
    # recovery cycle for this brand, update THAT row with the latest cart data
    # instead of creating a parallel one keyed by a fresh checkout_id. Keeps the
    # database clean — no duplicate rows per customer per brand.
    active_row = None
    try:
        active_row = await nw.find_active_recovery_row(HTTP, phone=phone, brand=cfg.slug)
    except Exception:
        log.exception("[%s] active-row lookup failed (non-fatal, will fall through to upsert)", cfg.slug)

    if active_row:
        page_id = active_row["id"]
        was_created = False
        await nw.patch_active_row_update(
            HTTP, page_id,
            checkout_id=checkout_id,
            checkout_url=checkout_url,
            cart_value=cart_value,
            cart_items=cart_items,
        )
        log.info("[%s] consolidated checkout=%s into existing active row=%s for phone=%s",
                 cfg.slug, checkout_id, page_id, phone)
    else:
        page_id, was_created = await nw.upsert_checkout(
            HTTP,
            customer_name=customer_name,
            phone=phone,
            email=email,
            brand=cfg.slug,
            status="New",
            cart_value=cart_value,
            cart_items=cart_items,
            checkout_id=checkout_id,
            checkout_url=checkout_url,
            abandoned_at=abandoned_at,
        )

    if was_created:
        # Schedule the recovery send `delay` after abandonment, floored 60s out
        # so pre-existing carts (first seen via `update`) fire shortly rather
        # than instantly.
        try:
            abandoned_dt = datetime.fromisoformat(abandoned_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            abandoned_dt = datetime.now(timezone.utc)
        run_at = abandoned_dt + timedelta(seconds=recovery_delay_seconds())
        floor = datetime.now(timezone.utc) + timedelta(seconds=60)
        if run_at < floor:
            run_at = floor
        _schedule_send(cfg.slug, page_id, phone, run_at)

    log.info(
        "[%s] %s checkout=%s phone=%s page=%s created=%s",
        cfg.slug, "create" if fresh else "update", checkout_id, phone, page_id, was_created,
    )
    return {"status": "ok", "page_id": page_id, "was_created": was_created}


async def _handle_order(cfg: BrandConfig, payload: dict) -> dict:
    """Suppress any pending recovery for this checkout — order was placed."""
    assert HTTP is not None
    checkout_id = str(
        payload.get("checkout_id") or payload.get("checkout_token") or ""
    )
    if not checkout_id:
        log.info("[%s] order has no checkout reference, nothing to suppress", cfg.slug)
        return {"status": "no_checkout_id"}

    page_id = await nw.find_row_by_checkout_id(HTTP, checkout_id)
    if not page_id:
        log.info("[%s] order checkout=%s — no pending row, ignoring", cfg.slug, checkout_id)
        return {"status": "no_pending_row"}

    job_id = f"recov_{cfg.slug}_{page_id}"
    if SCHEDULER and SCHEDULER.get_job(job_id):
        SCHEDULER.remove_job(job_id)
        log.info("[%s] cancelled scheduled send for page=%s", cfg.slug, page_id)

    await nw.patch_status(HTTP, page_id, "Order Placed")
    return {"status": "suppressed", "page_id": page_id}


@app.get("/")
async def root():
    return {"service": "grq-ac", "base_url": bridge_base_url()}
