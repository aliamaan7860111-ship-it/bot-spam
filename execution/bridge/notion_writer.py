"""
Notion DB writer for the Abandoned Checkout Recovery database.

All writes are idempotent on `Shopify Checkout ID` — we always search before
creating, and update in place on subsequent checkouts/update events.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from config import notion_api_key, recovery_db_id

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {notion_api_key()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _rt(text: str | None) -> dict:
    if not text:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": str(text)[:2000]}}]}


def _title(text: str | None) -> dict:
    if not text:
        return {"title": []}
    return {"title": [{"type": "text", "text": {"content": str(text)[:200]}}]}


def _date(iso: str | None) -> dict:
    if not iso:
        return {"date": None}
    return {"date": {"start": iso}}


def _select(name: str | None) -> dict:
    if not name:
        return {"select": None}
    return {"select": {"name": name}}


def _number(n) -> dict:
    if n is None:
        return {"number": None}
    return {"number": float(n)}


def _phone(n: str | None) -> dict:
    return {"phone_number": n or None}


def _email(e: str | None) -> dict:
    return {"email": e or None}


def _url(u: str | None) -> dict:
    return {"url": u or None}


async def find_row_by_checkout_id(client: httpx.AsyncClient, checkout_id: str) -> str | None:
    """Return Notion page ID if a row with this Shopify Checkout ID exists."""
    resp = await client.post(
        f"{NOTION_BASE}/databases/{recovery_db_id()}/query",
        headers=_headers(),
        json={
            "filter": {
                "property": "Shopify Checkout ID",
                "rich_text": {"equals": checkout_id},
            },
            "page_size": 1,
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


def build_properties(
    *,
    customer_name: str | None,
    phone: str | None,
    email: str | None,
    brand: str,
    status: str,
    cart_value: float | None,
    cart_items: str | None,
    checkout_id: str,
    checkout_url: str | None,
    abandoned_at: str | None,
    raw_checkout_data: str | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Customer Name": _title(customer_name or "Unknown"),
        "Phone": _phone(phone),
        "Email": _email(email),
        "Brand": _select(brand.capitalize()),
        "Status": _select(status),
        "Cart Value": _number(cart_value),
        "Cart Items": _rt(cart_items),
        "Shopify Checkout ID": _rt(checkout_id),
        "Shopify Checkout URL": _url(checkout_url),
        "Abandoned At": _date(abandoned_at),
    }
    if raw_checkout_data is not None:
        props["Raw Checkout Data"] = _rt(raw_checkout_data)
    return props


async def upsert_checkout(
    client: httpx.AsyncClient,
    *,
    customer_name: str | None,
    phone: str | None,
    email: str | None,
    brand: str,
    status: str,
    cart_value: float | None,
    cart_items: str | None,
    checkout_id: str,
    checkout_url: str | None,
    abandoned_at: str | None,
    raw_checkout_data: str | None = None,
) -> tuple[str, bool]:
    """Create or update a row keyed by Shopify Checkout ID.

    Returns (page_id, was_created). `was_created` is True only when this call
    inserted a brand-new row — the caller uses it to decide whether to schedule
    the recovery send, so scheduling happens exactly once per checkout
    regardless of whether the first event we saw was create or update.
    """
    existing = await find_row_by_checkout_id(client, checkout_id)
    props = build_properties(
        customer_name=customer_name,
        phone=phone,
        email=email,
        brand=brand,
        status=status,
        cart_value=cart_value,
        cart_items=cart_items,
        checkout_id=checkout_id,
        checkout_url=checkout_url,
        abandoned_at=abandoned_at,
        raw_checkout_data=raw_checkout_data,
    )

    if existing:
        # Don't overwrite status on update — only the original insert sets it.
        # Subsequent updates refresh data fields but leave Status alone.
        props.pop("Status", None)
        resp = await client.patch(
            f"{NOTION_BASE}/pages/{existing}",
            headers=_headers(),
            json={"properties": props},
        )
        resp.raise_for_status()
        return existing, False

    resp = await client.post(
        f"{NOTION_BASE}/pages",
        headers=_headers(),
        json={
            "parent": {"database_id": recovery_db_id()},
            "properties": props,
        },
    )
    resp.raise_for_status()
    return resp.json()["id"], True


async def find_recent_recovery_sent_by_phone(
    client: httpx.AsyncClient, *, phone: str, brand: str | None = None,
) -> dict | None:
    """Find the most recent row for this phone (optionally constrained to one
    brand) that has had a recovery template sent. Returns the row dict or None.

    `brand=None` is the right call when the WhatChimp WABA serves multiple
    brands (e.g. Virex serves both virex + pelvini): the URL brand is just the
    bot flow's hardcoded path, but the actual cart could be on any brand sharing
    that WABA. Callers should use the matched row's Brand property for
    downstream Shopify operations.
    """
    and_filters: list[dict] = [
        {"property": "Phone", "phone_number": {"equals": phone}},
        {
            "or": [
                {"property": "Status", "select": {"equals": "Recovery Sent"}},
                {"property": "Status", "select": {"equals": "Recovery Pending"}},
                {"property": "Status", "select": {"equals": "Customer Completed Order"}},
            ]
        },
    ]
    if brand:
        and_filters.insert(1, {"property": "Brand", "select": {"equals": brand.capitalize()}})

    resp = await client.post(
        f"{NOTION_BASE}/databases/{recovery_db_id()}/query",
        headers=_headers(),
        json={
            "filter": {"and": and_filters},
            "sorts": [{"property": "Abandoned At", "direction": "descending"}],
            "page_size": 1,
        },
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


def extract_text(row: dict, prop: str) -> str:
    return "".join(t.get("plain_text", "") for t in (row.get("properties", {}).get(prop) or {}).get("rich_text", []))


def extract_select(row: dict, prop: str) -> str:
    return ((row.get("properties", {}).get(prop) or {}).get("select") or {}).get("name", "")


def extract_phone(row: dict, prop: str = "Phone") -> str:
    return (row.get("properties", {}).get(prop) or {}).get("phone_number") or ""


async def patch_recovery_outcome(
    client: httpx.AsyncClient,
    page_id: str,
    *,
    status: str,
    order_number: str | None = None,
    order_total: float | None = None,
    discount_applied: bool | None = None,
    assigned_agent: str | None = None,
) -> None:
    """Patch the row after the bridge has acted on a customer button-click —
    Order Number filled if we placed the order, Assigned Agent if we routed
    to a human, etc.
    """
    props: dict[str, Any] = {"Status": _select(status)}
    if order_number is not None:
        props["Order Number"] = _rt(order_number)
    if order_total is not None:
        props["Order Total"] = _number(order_total)
    if discount_applied is not None:
        props["Discount Applied"] = {"checkbox": bool(discount_applied)}
    if assigned_agent is not None:
        props["Assigned Agent"] = _rt(assigned_agent)
    resp = await client.patch(
        f"{NOTION_BASE}/pages/{page_id}",
        headers=_headers(),
        json={"properties": props},
    )
    resp.raise_for_status()


async def get_phone(client: httpx.AsyncClient, page_id: str) -> str | None:
    """Re-read the current Phone value from a row — used at send time so a
    phone correction made via checkouts/update is picked up."""
    resp = await client.get(f"{NOTION_BASE}/pages/{page_id}", headers=_headers())
    resp.raise_for_status()
    props = resp.json().get("properties", {})
    return (props.get("Phone") or {}).get("phone_number")


async def get_phone_and_name(
    client: httpx.AsyncClient, page_id: str
) -> tuple[str | None, str | None]:
    """Single fetch returning (phone, customer_name) — saves one round trip
    over calling get_phone() and querying separately for the title."""
    resp = await client.get(f"{NOTION_BASE}/pages/{page_id}", headers=_headers())
    resp.raise_for_status()
    props = resp.json().get("properties", {})
    phone = (props.get("Phone") or {}).get("phone_number")
    title_parts = (props.get("Customer Name") or {}).get("title", [])
    name = "".join(t.get("plain_text", "") for t in title_parts).strip()
    return phone, (name or None)


async def patch_status(client: httpx.AsyncClient, page_id: str, status: str, **extra) -> None:
    """Patch a row's Status (and optionally other timestamps/fields)."""
    props: dict[str, Any] = {"Status": _select(status)}
    if "recovery_sent_at" in extra:
        props["Recovery Sent At"] = _date(extra["recovery_sent_at"])
    if "button_clicked" in extra:
        props["Button Clicked"] = _select(extra["button_clicked"])
    resp = await client.patch(
        f"{NOTION_BASE}/pages/{page_id}",
        headers=_headers(),
        json={"properties": props},
    )
    resp.raise_for_status()


async def find_rows_pending_recovery(
    client: httpx.AsyncClient, before_iso: str
) -> list[dict]:
    """Find rows with Status=New and abandoned_at older than the threshold.
    Used on service restart to backfill missed scheduled sends.
    """
    resp = await client.post(
        f"{NOTION_BASE}/databases/{recovery_db_id()}/query",
        headers=_headers(),
        json={
            "filter": {
                "and": [
                    {"property": "Status", "select": {"equals": "New"}},
                    {"property": "Abandoned At", "date": {"on_or_before": before_iso}},
                ]
            },
            "page_size": 100,
        },
    )
    resp.raise_for_status()
    return resp.json().get("results", [])
