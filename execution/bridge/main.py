"""
grq-ac — Abandoned Checkout Recovery bridge.

Receives Shopify webhooks for 6 brands, writes to Notion, schedules a 30-min
delayed WhatChimp template send, suppresses on orders/create.

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
from config import (
    BRAND_SLUGS,
    BrandConfig,
    bridge_base_url,
    load_all_brands,
    recovery_delay_seconds,
)
from shopify_verify import verify_shopify_hmac
from whatchimp_sender import send_recovery_template

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
    """The actual WhatChimp send fired by the scheduler after the delay."""
    assert HTTP is not None
    brand = BRANDS[brand_slug]
    log.info("[%s] scheduled send firing for page=%s phone=%s", brand_slug, page_id, phone)
    result = await send_recovery_template(HTTP, brand=brand, phone=phone)

    if result["status"] == "ok":
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
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=delay)).isoformat()

    # Pull rows whose abandoned_at <= now (i.e. all New rows; we'll filter by age in code)
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
    """Process a checkouts/create or checkouts/update event."""
    assert HTTP is not None

    checkout_id = str(payload.get("id") or payload.get("token") or "")
    if not checkout_id:
        log.warning("[%s] checkout event with no id/token, ignoring", cfg.slug)
        return {"status": "ignored", "reason": "no checkout id"}

    phone = _extract_phone(payload)
    if not phone:
        log.info("[%s] checkout %s has no phone, ignoring", cfg.slug, checkout_id)
        return {"status": "ignored", "reason": "no phone"}

    customer_name = _extract_name(payload)
    email = payload.get("email") or (payload.get("customer") or {}).get("email")
    cart_value = _extract_total(payload)
    cart_items = _extract_cart_items_text(payload)
    checkout_url = payload.get("abandoned_checkout_url")
    abandoned_at = payload.get("updated_at") or payload.get("created_at") or _now_iso()

    page_id = await nw.upsert_checkout(
        HTTP,
        customer_name=customer_name,
        phone=phone,
        email=email,
        brand=cfg.slug,
        status="New" if fresh else "New",  # don't overwrite later
        cart_value=cart_value,
        cart_items=cart_items,
        checkout_id=checkout_id,
        checkout_url=checkout_url,
        abandoned_at=abandoned_at,
    )

    if fresh:
        run_at = datetime.now(timezone.utc) + timedelta(seconds=recovery_delay_seconds())
        _schedule_send(cfg.slug, page_id, phone, run_at)

    log.info(
        "[%s] %s checkout=%s phone=%s page=%s",
        cfg.slug, "created" if fresh else "updated", checkout_id, phone, page_id,
    )
    return {"status": "ok", "page_id": page_id}


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
