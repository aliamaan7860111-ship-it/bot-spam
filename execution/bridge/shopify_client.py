"""
Shopify Admin API helpers — create + complete draft orders with a 10% discount,
fetch order details. Per-brand config via BrandConfig.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import BrandConfig

log = logging.getLogger(__name__)
SHOPIFY_API_VERSION = "2026-04"


def _base(brand: BrandConfig) -> str:
    return f"https://{brand.shopify_domain}/admin/api/{SHOPIFY_API_VERSION}"


def _headers(brand: BrandConfig) -> dict[str, str]:
    return {
        "X-Shopify-Access-Token": brand.shopify_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_draft_order_payload(
    *,
    line_items: list[dict],
    shipping_address: dict | None,
    billing_address: dict | None,
    customer_email: str | None,
    customer_phone: str | None,
    discount_percent: float = 10.0,
    note: str = "Abandoned checkout recovery (10% off applied)",
) -> dict:
    """Compose the body for POST /draft_orders.json.

    line_items expects each entry to have either `variant_id` + `quantity` (a
    real Shopify variant) or `title` + `price` + `quantity` (a custom line —
    used as fallback when variant_id isn't available).
    """
    draft_line_items = []
    for li in line_items:
        if li.get("variant_id"):
            draft_line_items.append({
                "variant_id": int(li["variant_id"]),
                "quantity": int(li.get("quantity", 1)),
            })
        else:
            # custom line item fallback
            draft_line_items.append({
                "title": li.get("title") or li.get("name") or "Item",
                "price": str(li.get("price") or "0.00"),
                "quantity": int(li.get("quantity", 1)),
            })

    body: dict[str, Any] = {
        "draft_order": {
            "line_items": draft_line_items,
            "applied_discount": {
                "description": "Abandoned checkout recovery discount",
                "value_type": "percentage",
                "value": str(discount_percent),
                "title": f"{int(discount_percent)}% Recovery Discount",
            },
            "note": note,
            "tags": "abandoned-recovery,grq-ac",
            "use_customer_default_address": False,
        }
    }

    if shipping_address:
        body["draft_order"]["shipping_address"] = shipping_address
    if billing_address:
        body["draft_order"]["billing_address"] = billing_address
    if customer_email:
        body["draft_order"]["email"] = customer_email
    if customer_phone:
        body["draft_order"]["phone"] = customer_phone

    return body


async def create_draft_order(
    client: httpx.AsyncClient,
    brand: BrandConfig,
    payload: dict,
) -> dict:
    """POST /draft_orders.json — returns the created draft order dict."""
    resp = await client.post(
        f"{_base(brand)}/draft_orders.json",
        headers=_headers(brand),
        json=payload,
        timeout=20.0,
    )
    if resp.status_code not in (200, 201):
        log.error("[%s] draft_order create failed %d: %s", brand.slug, resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()["draft_order"]


async def complete_draft_order(
    client: httpx.AsyncClient,
    brand: BrandConfig,
    draft_id: int,
    payment_pending: bool = True,
) -> dict:
    """PUT /draft_orders/{id}/complete.json — completes the draft.

    `payment_pending=True` means the resulting Order is created with financial
    status `pending` (Cash on Delivery flow — payment collected on delivery).
    """
    resp = await client.put(
        f"{_base(brand)}/draft_orders/{draft_id}/complete.json",
        headers=_headers(brand),
        params={"payment_pending": "true" if payment_pending else "false"},
        timeout=20.0,
    )
    if resp.status_code not in (200, 201):
        log.error("[%s] draft_order complete failed %d: %s", brand.slug, resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()["draft_order"]


async def fetch_order(
    client: httpx.AsyncClient,
    brand: BrandConfig,
    order_id: int,
) -> dict:
    """GET /orders/{id}.json — needed to read the order's customer-facing name
    (e.g. '#1234') and final totals after the draft is completed.
    """
    resp = await client.get(
        f"{_base(brand)}/orders/{order_id}.json",
        headers=_headers(brand),
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()["order"]
