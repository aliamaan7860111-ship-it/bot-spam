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

import json as _json

import notion_writer as nw
import shopify_client as sc
from config import (
    BRAND_SLUGS,
    BrandConfig,
    bridge_base_url,
    load_all_brands,
    recovery_delay_seconds,
)
from shopify_verify import verify_shopify_hmac
from whatchimp_sender import send_recovery_template, upsert_subscriber

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

    - already '+'-prefixed  -> kept as-is (assume correct international format)
    - '00'-prefixed         -> '00' replaced with '+'
    - local '0'-prefixed    -> leading 0 dropped, default country code prepended
    - bare local digits     -> default country code prepended

    default_cc is +971 (UAE) since all 6 stores are UAE-based. A foreign number
    MUST be entered in international ('+') form — normalization can't infer it.
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

    Re-reads the phone + customer name from Notion at fire time so any
    correction made after the job was scheduled (via a later checkouts/update)
    is honoured. Then:
      1. Upserts the subscriber with their first name so #User-Name#
         substitutes correctly in the template + downstream flow messages.
      2. Sends the recovery template with the brand's button postback IDs,
         so the Quick Reply buttons fire the right bot flows when tapped.
    """
    assert HTTP is not None
    brand = BRANDS[brand_slug]
    try:
        current_phone, customer_name = await nw.get_phone_and_name(HTTP, page_id)
    except Exception:
        current_phone, customer_name = None, None
    phone = normalize_phone(current_phone or phone) or phone
    first_name = (customer_name or "").strip().split(" ")[0] or None

    # 1. Make sure the subscriber exists with their first name set.
    if first_name and brand.whatchimp_phone_number_id:
        try:
            up = await upsert_subscriber(HTTP, phone, brand, first_name)
            log.info("[%s] upsert_subscriber phone=%s name=%s -> %s",
                     brand_slug, phone, first_name, up.get("status"))
        except Exception:
            log.exception("[%s] upsert_subscriber failed (non-fatal)", brand_slug)

    log.info("[%s] scheduled send firing for page=%s phone=%s name=%s",
             brand_slug, page_id, phone, first_name)
    result = await send_recovery_template(
        HTTP, brand=brand, phone=phone,
        quick_reply_postback_ids=brand.postback_ids,
    )

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

    # Cache the pieces we'll need to rebuild a draft order if the customer
    # eventually taps Complete Order — line items + addresses. Stored as a
    # compact JSON blob in the row's Raw Checkout Data property.
    raw_blob = _json.dumps({
        "line_items": [
            {
                "variant_id": li.get("variant_id"),
                "quantity": li.get("quantity", 1),
                "title": li.get("title") or li.get("name"),
                "price": li.get("price"),
            }
            for li in (payload.get("line_items") or [])
        ],
        "shipping_address": payload.get("shipping_address"),
        "billing_address": payload.get("billing_address"),
        "email": email,
        "phone": phone,
    }, default=str)[:1900]  # keep under Notion rich_text limit

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
        raw_checkout_data=raw_blob,
    )

    if was_created:
        # Schedule the recovery send 30 min after abandonment, floored 60s out
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


def _extract_caller_phone(body: dict) -> str | None:
    """WhatChimp flows can send the subscriber's phone under various keys.
    Check the ones we've seen + likely fallbacks.
    """
    for key in ("phone", "phone_number", "subscriber_phone", "wa_id", "PhoneNumber"):
        v = body.get(key)
        if v:
            return normalize_phone(str(v))
    # Fallback: try nested 'subscriber'
    sub = body.get("subscriber") or {}
    for key in ("phone", "phone_number", "wa_id"):
        v = sub.get(key)
        if v:
            return normalize_phone(str(v))
    return None


def _confirm_response(
    *, status: str = "error", reason: str = "", order_id: str = "", total: str = "",
    discount_applied: bool = False,
) -> dict:
    """All confirm-order responses share a stable shape so the WhatChimp HTTP
    API mapping UI sees the same keys (order_id, total, discount_applied)
    on every call — including verify and error paths."""
    return {
        "status": status,
        "reason": reason,
        "order_id": order_id,
        "total": total,
        "discount_applied": discount_applied,
    }


@app.post("/webhooks/whatchimp/{brand}/confirm-order")
async def whatchimp_confirm_order(brand: str, request: Request):
    """Called by a WhatChimp bot flow when the customer taps Complete Order.

    Looks up their abandoned-checkout row, recreates the cart as a draft order
    with 10% off, completes it as COD (financial status pending), and returns
    JSON the WhatChimp HTTP API node can capture into custom fields so the
    follow-up Text message renders with the real order number + total.

    The response shape is stable across success/error so the WhatChimp UI's
    mapping dropdown always sees `order_id` and `total` as available fields.
    """
    cfg = _brand_or_404(brand)
    assert HTTP is not None
    raw_bytes = await request.body()
    try:
        body = _json.loads(raw_bytes) if raw_bytes else {}
    except Exception:
        body = {}
    # Always log the incoming body so we can debug WhatChimp's payload shape
    # — particularly useful for figuring out which key the phone arrives under.
    log.info("[%s] confirm-order incoming body: %r", brand, raw_bytes[:1000])

    phone = _extract_caller_phone(body)
    if not phone:
        log.warning("[%s] confirm-order no phone in body keys=%s", brand, list(body.keys()))
        return _confirm_response(reason="no phone in request body")

    row = await nw.find_recent_recovery_sent_by_phone(HTTP, phone=phone, brand=brand)
    if not row:
        log.warning("[%s] confirm-order: no recent Recovery Sent row for %s", brand, phone)
        return _confirm_response(reason="no matching recovery row")

    page_id = row["id"]
    # Record the click immediately so a Shopify failure leaves a visible state.
    await nw.patch_recovery_outcome(HTTP, page_id, status="Customer Completed Order")

    raw_text = nw.extract_text(row, "Raw Checkout Data")
    if not raw_text:
        log.error("[%s] confirm-order: row %s has no Raw Checkout Data", brand, page_id)
        await nw.patch_recovery_outcome(HTTP, page_id, status="Order Placement Failed")
        return _confirm_response(reason="missing cached checkout data")

    try:
        cached = _json.loads(raw_text)
    except Exception:
        log.exception("[%s] failed to parse Raw Checkout Data on %s", brand, page_id)
        await nw.patch_recovery_outcome(HTTP, page_id, status="Order Placement Failed")
        return _confirm_response(reason="checkout data parse error")

    line_items = cached.get("line_items") or []
    if not line_items:
        await nw.patch_recovery_outcome(HTTP, page_id, status="Order Placement Failed")
        return _confirm_response(reason="no line items")

    shipping_address = cached.get("shipping_address")
    billing_address = cached.get("billing_address") or shipping_address
    customer_email = cached.get("email")
    customer_phone = cached.get("phone") or phone

    payload = sc.build_draft_order_payload(
        line_items=line_items,
        shipping_address=shipping_address,
        billing_address=billing_address,
        customer_email=customer_email,
        customer_phone=customer_phone,
        discount_percent=10.0,
    )

    try:
        draft = await sc.create_draft_order(HTTP, cfg, payload)
        completed = await sc.complete_draft_order(HTTP, cfg, draft["id"], payment_pending=True)
        order_id_num = completed.get("order_id")
        if not order_id_num:
            raise RuntimeError("draft_order completion returned no order_id")
        order = await sc.fetch_order(HTTP, cfg, order_id_num)
    except Exception:
        log.exception("[%s] confirm-order: Shopify call failed for page=%s", brand, page_id)
        await nw.patch_recovery_outcome(HTTP, page_id, status="Order Placement Failed")
        return _confirm_response(reason="shopify order placement failed")

    order_name = order.get("name", f"#{order_id_num}")  # like '#1234'
    try:
        order_total_value = float(order.get("total_price") or 0.0)
    except (TypeError, ValueError):
        order_total_value = 0.0
    currency = order.get("currency") or "AED"
    order_total_display = f"{currency} {order_total_value:.2f}"

    await nw.patch_recovery_outcome(
        HTTP,
        page_id,
        status="Recovered",
        order_number=order_name,
        order_total=order_total_value,
        discount_applied=True,
    )

    log.info("[%s] confirm-order OK: page=%s order=%s total=%s", brand, page_id, order_name, order_total_display)
    return _confirm_response(
        status="ok", order_id=order_name, total=order_total_display, discount_applied=True,
    )


@app.post("/webhooks/whatchimp/{brand}/talk-with-agent")
async def whatchimp_talk_with_agent(brand: str, request: Request):
    """Called by the Talk with Agent bot flow. For now we just record the
    handoff in Notion — actual agent assignment via WhatChimp's
    assign-to-team-member API can be wired in once the agent pool / shift
    logic is moved into the bridge.
    """
    cfg = _brand_or_404(brand)
    assert HTTP is not None
    try:
        body = await request.json()
    except Exception:
        body = {}

    phone = _extract_caller_phone(body)
    if not phone:
        return {"status": "error", "reason": "no phone in body"}

    row = await nw.find_recent_recovery_sent_by_phone(HTTP, phone=phone, brand=brand)
    if not row:
        return {"status": "error", "reason": "no matching recovery row"}

    await nw.patch_recovery_outcome(HTTP, row["id"], status="Talked to Agent")
    log.info("[%s] talk-with-agent: page=%s phone=%s flagged for human handoff", brand, row["id"], phone)
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "grq-ac", "base_url": bridge_base_url()}
