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
) -> dict[str, Any]:
    return {
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
