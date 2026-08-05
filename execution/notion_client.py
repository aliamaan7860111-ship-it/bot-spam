"""
notion_client.py
================
Reusable Notion API wrapper for querying and updating the orders database.
Uses httpx for HTTP requests — zero dependency beyond stdlib + httpx.

All field names match the user's exact Notion database schema.
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
from pathlib import Path

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("order_bridge.notion")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"

# Cached data source ID (fetched once at first use)
_DATA_SOURCE_ID: str | None = None

# Only process orders created after this date (ISO format)
# Set in .env as ORDER_CUTOFF_DATE, or defaults to Feb 24 2026 5:19PM PKT
ORDER_CUTOFF_DATE = os.getenv("ORDER_CUTOFF_DATE", "2026-02-24T17:19:00+05:00")


def _headers() -> dict:
    """Standard Notion API headers."""
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _get_data_source_id() -> str:
    """Fetch and cache the data_source_id for the configured database.
    Required by Notion API 2025-09-03 — queries now target data sources, not databases."""
    global _DATA_SOURCE_ID
    if _DATA_SOURCE_ID:
        return _DATA_SOURCE_ID

    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}",
            headers=_headers(),
        )
        resp.raise_for_status()
        db = resp.json()

    data_sources = db.get("data_sources", [])
    if not data_sources:
        raise RuntimeError(
            f"No data sources found for database {NOTION_DATABASE_ID}. "
            "Check that the integration has access to this database."
        )
    _DATA_SOURCE_ID = data_sources[0]["id"]
    log.info(f"Resolved data_source_id: {_DATA_SOURCE_ID} (name: {data_sources[0].get('name', '?')})")
    return _DATA_SOURCE_ID


# ---------------------------------------------------------------------------
# Field name constants (match user's exact Notion property names)
# ---------------------------------------------------------------------------

FIELD_ORDER_ID = "ORDER ID"
FIELD_CREATED = "CREATED"
FIELD_CUSTOMER_NAME = "CUSTOMER NAME"
FIELD_ITEM_QTY = "ITEM | QTY"
FIELD_FULL_ADDRESS = "FULL ADDRESS"
FIELD_PHONE = "PHONE"
FIELD_TOTAL = "TOTAL "
FIELD_COST = "COST"
FIELD_INTERNAL_NOTE = "INTERNAL NOTE"
FIELD_ORDER_STATUS = "ORDER STATUS"
FIELD_PAYMENT = "PAYMENT"
FIELD_CUSTOMER_TYPE = "CUSTOMER TYPE"
FIELD_ORDER_SOURCE_URL = "ORDER SOURCE URL"
FIELD_IMAGE_URL = "IMAGE URL"
FIELD_EMAIL = "EMAIL"
FIELD_PLATFORM_SOURCE = "PLATFORM SOURCE"
FIELD_ALBUMS_SENT = "ALBUMS SENT"
FIELD_FULFILLMENT_MESSAGE_ID = "FULFILLMENT MESSAGE ID"
FIELD_WHATSAPP_SENT = "Confirmation Sent"
STATUS_CONFIRMATION_SENT = "Confirmation Sent"

# Filex-related fields (added in Task 1 setup_notion_fields.py)
FIELD_TRACKING_NUMBER = "Tracking Number"
FIELD_TRACKING_LINK = "Tracking Link"
FIELD_FILEX_STATUS = "FILEX STATUS"
FIELD_FILEX_NOTES = "FILEX NOTES"
FIELD_FILEX_SUBMITTED = "Filex Submitted"
FIELD_DISPATCHED_AT = "Dispatched At"
FIELD_LAST_UPDATE = "Last Update"
FIELD_OUT_FOR_DELIVERY_SENT = "Out For Delivery Sent"

# TJR Logistics routing flags (own-driver fulfilment, separate from Filex)
FIELD_PRIVATE_DRIVER = "Private Driver"
FIELD_PRIVATE_LABEL_CREATED = "Private Label Created"

# Main ORDER STATUS value an order reaches when shipped. MUST stay identical to
# filex_status_mapper.ORDER_STATUS_FROM_FILEX["Shipped"] (guarded by a unit test).
STATUS_SHIPPED = "\U0001F69A SHIPPED"  # 🚚 SHIPPED


# ---------------------------------------------------------------------------
# Helpers to extract property values
# ---------------------------------------------------------------------------

def _get_title(props: dict, field: str) -> str:
    """Extract text from a title property."""
    try:
        parts = props[field]["title"]
        return "".join(p.get("plain_text", "") for p in parts)
    except (KeyError, TypeError):
        return ""


def _get_rich_text(props: dict, field: str) -> str:
    """Extract text from a rich_text property."""
    try:
        parts = props[field]["rich_text"]
        return "".join(p.get("plain_text", "") for p in parts)
    except (KeyError, TypeError):
        return ""


def _get_number(props: dict, field: str) -> float | None:
    """Extract value from a number property."""
    try:
        return props[field]["number"]
    except (KeyError, TypeError):
        return None


def _get_number_or_default(props: dict, field: str, default: int = 0) -> int:
    """Extract an int from a number property; return default if unset or non-numeric."""
    try:
        n = props[field]["number"]
    except (KeyError, TypeError):
        return default
    if n is None:
        return default
    try:
        return int(n)
    except (TypeError, ValueError):
        return default


def _get_select(props: dict, field: str) -> str:
    """Extract value from a select property."""
    try:
        sel = props[field]["select"]
        return sel["name"] if sel else ""
    except (KeyError, TypeError):
        return ""


def _get_url(props: dict, field: str) -> str:
    """Extract value from a url property."""
    try:
        return props[field]["url"] or ""
    except (KeyError, TypeError):
        return ""


def _get_files(props: dict, field: str) -> list[str]:
    """Extract URLs from a files & media property (external or uploaded)."""
    try:
        files = props[field]["files"]
    except (KeyError, TypeError):
        return []
    urls = []
    for f in files or []:
        ftype = f.get("type")
        if ftype == "external":
            url = f.get("external", {}).get("url")
        elif ftype == "file":
            url = f.get("file", {}).get("url")
        else:
            url = None
        if url:
            urls.append(url)
    return urls


def _get_checkbox(props: dict, field: str) -> bool:
    """Extract value from a checkbox property."""
    try:
        return props[field]["checkbox"]
    except (KeyError, TypeError):
        return False


def _get_date(props: dict, field: str) -> str:
    """Extract value from a date property."""
    try:
        d = props[field]["date"]
        return d["start"] if d else ""
    except (KeyError, TypeError):
        return ""


def _get_created_time(props: dict, field: str) -> str:
    """Extract value from a created_time property (Notion's built-in timestamp).
    Needed because FIELD_CREATED can be a created_time column, which stores its
    value under ["created_time"] rather than ["date"] — _get_date returns '' for it,
    which silently fails the confirmation freshness guard."""
    try:
        return props[field].get("created_time", "") or ""
    except (KeyError, TypeError, AttributeError):
        return ""


def _get_formula(props: dict, field: str):
    """Extract value from a formula property (can return number or string)."""
    try:
        formula = props[field]["formula"]
        if formula.get("type") == "number":
            return formula.get("number")
        elif formula.get("type") == "string":
            return formula.get("string", "")
        elif formula.get("type") == "boolean":
            return formula.get("boolean")
        return None
    except (KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Parse a Notion page into a clean order dict
# ---------------------------------------------------------------------------

def parse_order(page: dict) -> dict:
    """
    Convert a Notion page object into a flat order dictionary.
    """
    props = page["properties"]

    # IMAGE URL is a Files & Media property. Used as fallback to GraphQL.
    image_urls = _get_files(props, FIELD_IMAGE_URL)

    return {
        "page_id": page["id"],
        "order_id": _get_title(props, FIELD_ORDER_ID),
        "created": _get_date(props, FIELD_CREATED) or _get_created_time(props, FIELD_CREATED) or _get_rich_text(props, FIELD_CREATED),
        "customer_name": _get_rich_text(props, FIELD_CUSTOMER_NAME),
        "item_qty": _get_rich_text(props, FIELD_ITEM_QTY),
        "full_address": _get_rich_text(props, FIELD_FULL_ADDRESS),
        "phone": _get_rich_text(props, FIELD_PHONE),
        "total_aed": _get_number(props, FIELD_TOTAL) or _get_rich_text(props, FIELD_TOTAL) or _get_formula(props, FIELD_TOTAL),
        "cost": _get_number(props, FIELD_COST) or _get_rich_text(props, FIELD_COST) or _get_formula(props, FIELD_COST),
        "internal_note": _get_rich_text(props, FIELD_INTERNAL_NOTE),
        "order_status": _get_select(props, FIELD_ORDER_STATUS),
        "payment": _get_select(props, FIELD_PAYMENT) or _get_rich_text(props, FIELD_PAYMENT),
        "customer_type": _get_select(props, FIELD_CUSTOMER_TYPE) or _get_rich_text(props, FIELD_CUSTOMER_TYPE),
        "order_source_url": _get_rich_text(props, FIELD_ORDER_SOURCE_URL) or _get_url(props, FIELD_ORDER_SOURCE_URL),
        "image_urls": image_urls,
        "email": _get_rich_text(props, FIELD_EMAIL) or _get_url(props, FIELD_EMAIL),
        "platform_source": _get_select(props, FIELD_PLATFORM_SOURCE) or _get_rich_text(props, FIELD_PLATFORM_SOURCE),
        "whatsapp_sent": _get_checkbox(props, FIELD_WHATSAPP_SENT),
        "albums_sent": _get_number_or_default(props, FIELD_ALBUMS_SENT, default=0),
        "fulfillment_message_id": _get_number_or_default(props, FIELD_FULFILLMENT_MESSAGE_ID, default=0) or None,
        "filex_status": _get_select(props, FIELD_FILEX_STATUS),
        "last_update": _get_date(props, FIELD_LAST_UPDATE),
        "tracking_number": _get_rich_text(props, FIELD_TRACKING_NUMBER),
        "filex_submitted": _get_checkbox(props, FIELD_FILEX_SUBMITTED),
        "out_for_delivery_sent": _get_checkbox(props, FIELD_OUT_FOR_DELIVERY_SENT),
    }


# ---------------------------------------------------------------------------
# Query Operations
# ---------------------------------------------------------------------------

def is_order_fresh(created_iso, max_age_hours: int, now: datetime = None) -> bool:
    """True if an order was created within `max_age_hours`.

    Guards the confirmation bot against sending stale confirmations: an order
    that has been failing/backlogged longer than this simply ages out and is
    never confirmed late. Unknown/unparseable created dates return False
    (safer: never blast a stale confirmation).
    """
    if not created_iso:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(str(created_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created) <= timedelta(hours=max_age_hours)


def is_organic_order(order_id) -> bool:
    """True for 'organic' orders that must NOT get an auto WhatsApp confirmation.

    Identified by a space between the brand initials and the number
    (e.g. 'LU 231'), unlike normal orders ('LU1227').
    """
    if not order_id:
        return False
    return bool(re.match(r"^[A-Za-z]+ +\d", str(order_id).strip()))


def query_new_orders(cutoff_date: str = None) -> list[dict]:
    """
    Query Notion for orders where:
    1. Status is 'Confirmed' (or matching FIELD_ORDER_STATUS logic)
    2. CREATED date is after the cutoff.
    3. SOURCING NOTIFIED is False or WHATSAPP SENT is False.
    """
    # Use the provided cutoff or fall back to the global one
    active_cutoff = cutoff_date or ORDER_CUTOFF_DATE
    
    # Simple query for all recent potentially unprocessed orders
    payload = {
        "filter": {
            "and": [
                {
                    "property": FIELD_CREATED,
                    "date": {
                        "on_or_after": active_cutoff
                    }
                }
            ]
        },
        "sorts": [
            {
                "property": FIELD_CREATED,
                "direction": "ascending"
            }
        ]
    }

    orders = []
    has_more = True
    start_cursor = None

    try:
        with httpx.Client(timeout=30) as client:
            while has_more:
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = client.post(
                    f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    try:
                        orders.append(parse_order(page))
                    except Exception as e:
                        log.warning(f"Failed to parse order page {page.get('id', '?')}: {e}")

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

        return orders

    except httpx.HTTPStatusError as e:
        log.error(f"Notion API error: {e.response.status_code} — {e.response.text}")
        return orders
    except Exception as e:
        log.error(f"Notion query failed: {e}")
        return orders


def query_confirmed_orders() -> list[dict]:
    """
    Find all orders where ORDER STATUS = 'CONFIRMED | PROCESSING'
    and created after the cutoff date.
    Duplicate prevention is handled by local tracking in order_bridge.py.
    Returns a list of parsed order dicts.
    """
    payload = {
        "filter": {
            "and": [
                {
                    "property": FIELD_ORDER_STATUS,
                    "select": {"equals": "CONFIRMED | PROCESSING"},
                },
                {
                    "timestamp": "created_time",
                    "created_time": {"on_or_after": ORDER_CUTOFF_DATE},
                },
            ]
        },
    }

    orders = []
    has_more = True
    start_cursor = None

    try:
        with httpx.Client(timeout=30) as client:
            while has_more:
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = client.post(
                    f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    try:
                        orders.append(parse_order(page))
                    except Exception as e:
                        log.warning(f"Failed to parse order page {page.get('id', '?')}: {e}")

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

        return orders

    except httpx.HTTPStatusError as e:
        log.error(f"Notion API error: {e.response.status_code} — {e.response.text}")
        return orders
    except Exception as e:
        log.error(f"Notion query failed: {e}")
        return orders


def find_order_by_id(order_id: str) -> dict | None:
    """
    Look up a specific order by ORDER ID.
    Returns parsed order dict or None.
    """
    payload = {
        "filter": {
            "property": FIELD_ORDER_ID,
            "title": {"equals": order_id.strip()},
        },
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            # Try case-insensitive / fuzzy: search with contains
            payload["filter"] = {
                "property": FIELD_ORDER_ID,
                "title": {"contains": order_id.strip()},
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            results = data.get("results", [])

        if results:
            return parse_order(results[0])
        return None

    except Exception as e:
        log.error(f"Notion lookup failed for order '{order_id}': {e}")
        return None


# ---------------------------------------------------------------------------
# Update Operations
# ---------------------------------------------------------------------------

def _update_page(page_id: str, properties: dict) -> bool:
    """Update a Notion page's properties."""
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=_headers(),
                json={"properties": properties},
            )
            resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        log.error(f"Notion update failed: {e.response.status_code} — {e.response.text}")
        return False
    except Exception as e:
        log.error(f"Notion update failed: {e}")
        return False


def update_order_status(page_id: str, status: str) -> bool:
    """Change the ORDER STATUS select field."""
    return _update_page(page_id, {
        FIELD_ORDER_STATUS: {"select": {"name": status}},
    })


def set_sourcing_cost(page_id: str, cost: float) -> bool:
    """Set the COST number field."""
    return _update_page(page_id, {
        FIELD_COST: {"number": cost},
    })


def update_internal_note(page_id: str, note: str) -> bool:
    """Update the INTERNAL NOTE text field."""
    return _update_page(page_id, {
        FIELD_INTERNAL_NOTE: {"rich_text": [{"text": {"content": note}}]},
    })


def mark_order_processed(page_id: str) -> bool:
    """Update status to Processed."""
    return _update_page(page_id, {
        FIELD_ORDER_STATUS: {"select": {"name": "Processed"}},
    })

def mark_order_confirmation_sent(page_id: str) -> bool:
    """Update checkbox AND status to Confirmation Sent."""
    return _update_page(page_id, {
        FIELD_WHATSAPP_SENT: {"checkbox": True},
        FIELD_ORDER_STATUS: {"select": {"name": STATUS_CONFIRMATION_SENT}},
    })


def mark_order_new(page_id: str) -> bool:
    """Reset order status back to NEW and UNCHECK confirmation sent."""
    return _update_page(page_id, {
        FIELD_ORDER_STATUS: {"select": {"name": "NEW"}},
        FIELD_WHATSAPP_SENT: {"checkbox": False},
    })


def update_albums_sent(page_id: str, count: int) -> bool:
    """Set the ALBUMS SENT number on the order page."""
    return _update_page(page_id, {
        FIELD_ALBUMS_SENT: {"number": int(count)},
    })


def update_fulfillment_message_id(page_id: str, message_id: int) -> bool:
    """Set the FULFILLMENT MESSAGE ID number on the order page."""
    return _update_page(page_id, {
        FIELD_FULFILLMENT_MESSAGE_ID: {"number": int(message_id)},
    })


def query_recent_orders(hours: int = 24) -> list[dict]:
    """
    Find all orders created within the last `hours` hours,
    regardless of their current status.
    Returns a list of parsed order dicts.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    payload = {
        "filter": {
            "timestamp": "created_time",
            "created_time": {"on_or_after": cutoff_iso},
        },
    }

    orders = []
    has_more = True
    start_cursor = None

    try:
        with httpx.Client(timeout=30) as client:
            while has_more:
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = client.post(
                    f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    try:
                        orders.append(parse_order(page))
                    except Exception as e:
                        log.warning(f"Failed to parse order page {page.get('id', '?')}: {e}")

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

        return orders

    except httpx.HTTPStatusError as e:
        log.error(f"Notion API error: {e.response.status_code} — {e.response.text}")
        return orders
    except Exception as e:
        log.error(f"Notion query failed: {e}")
        return orders


# ---------------------------------------------------------------------------
# Connectivity Test
# ---------------------------------------------------------------------------

def test_connection() -> bool:
    """Test that the Notion API key and database ID are valid."""
    if not NOTION_API_KEY:
        log.error("NOTION_API_KEY is not set in .env")
        return False
    if not NOTION_DATABASE_ID:
        log.error("NOTION_DATABASE_ID is not set in .env")
        return False

    try:
        # Fetch database to get title and data sources
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}",
                headers=_headers(),
            )
            resp.raise_for_status()
            db = resp.json()
            title_parts = db.get("title", [])
            title = "".join(p.get("plain_text", "") for p in title_parts)
            log.info(f"✓ Connected to Notion database: {title}")

            data_sources = db.get("data_sources", [])
            log.info(f"  Found {len(data_sources)} data source(s)")

            # Fetch properties from the data source endpoint
            ds_id = _get_data_source_id()
            resp = client.get(
                f"{NOTION_API_BASE}/data_sources/{ds_id}",
                headers=_headers(),
            )
            resp.raise_for_status()
            ds = resp.json()
            props = ds.get("properties", {})
            log.info(f"  Found {len(props)} properties: {', '.join(props.keys())}")
            return True
    except Exception as e:
        log.error(f"Notion connection failed: {e}")
        return False




def mark_whatsapp_sent(page_id: str) -> bool:
    """Check the WHATSAPP SENT box and update ORDER STATUS in Notion."""
    return _update_page(page_id, {
        FIELD_WHATSAPP_SENT: {"checkbox": True},
        FIELD_ORDER_STATUS: {"select": {"name": STATUS_CONFIRMATION_SENT}}
    })


# ---------------------------------------------------------------------------
# Filex setters
# ---------------------------------------------------------------------------

def set_tracking_info(page_id: str, tracking_number: str, tracking_link: str) -> bool:
    """Write Tracking Number + Tracking Link in one PATCH."""
    return _update_page(page_id, {
        FIELD_TRACKING_NUMBER: {"rich_text": [{"text": {"content": tracking_number}}]},
        FIELD_TRACKING_LINK: {"url": tracking_link},
    })


def set_filex_status(page_id: str, status: str) -> bool:
    """Update FILEX STATUS select. Notion auto-creates new options on write."""
    return _update_page(page_id, {
        FIELD_FILEX_STATUS: {"select": {"name": status}},
    })


def set_filex_notes(page_id: str, notes: str) -> bool:
    """Update FILEX NOTES rich_text. Skip if notes is empty."""
    if not notes:
        return True
    return _update_page(page_id, {
        FIELD_FILEX_NOTES: {"rich_text": [{"text": {"content": notes}}]},
    })


def set_dispatched_at(page_id: str, dt_iso: str) -> bool:
    """Set Dispatched At date to ISO timestamp string."""
    return _update_page(page_id, {
        FIELD_DISPATCHED_AT: {"date": {"start": dt_iso}},
    })


def set_last_update(page_id: str, dt_iso: str) -> bool:
    """Set Last Update date to ISO timestamp string."""
    return _update_page(page_id, {
        FIELD_LAST_UPDATE: {"date": {"start": dt_iso}},
    })


def mark_filex_submitted(page_id: str, submitted: bool = True) -> bool:
    """Toggle the Filex Submitted checkbox."""
    return _update_page(page_id, {
        FIELD_FILEX_SUBMITTED: {"checkbox": submitted},
    })


def mark_out_for_delivery_sent(page_id: str, sent: bool = True) -> bool:
    """Set the 'Out For Delivery Sent' dedup checkbox."""
    return _update_page(page_id, {
        FIELD_OUT_FOR_DELIVERY_SENT: {"checkbox": sent},
    })


def find_order_by_shipper_ref(shipper_ref: str) -> dict | None:
    """
    Look up a Notion order by its ORDER ID (which equals Filex's ShipperRef).
    Returns parsed order dict or None.
    """
    return find_order_by_id(shipper_ref)


# ---------------------------------------------------------------------------
# Filex queries
# ---------------------------------------------------------------------------

def _run_query(payload: dict) -> list[dict]:
    """Helper: run a paginated data_source query and return parsed orders."""
    orders: list[dict] = []
    has_more = True
    start_cursor = None

    try:
        with httpx.Client(timeout=30) as client:
            while has_more:
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = client.post(
                    f"{NOTION_API_BASE}/data_sources/{_get_data_source_id()}/query",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                for page in data.get("results", []):
                    try:
                        orders.append(parse_order(page))
                    except Exception as e:
                        log.warning(f"Failed to parse order page {page.get('id', '?')}: {e}")

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

        return orders

    except httpx.HTTPStatusError as e:
        log.error(f"Notion API error: {e.response.status_code} — {e.response.text}")
        return orders
    except Exception as e:
        log.error(f"Notion query failed: {e}")
        return orders


def query_orders_by_tracking(tracking_no: str) -> list[dict]:
    """Find all Notion orders that have this Tracking Number."""
    if not tracking_no:
        return []
    filter_ = {"property": FIELD_TRACKING_NUMBER, "rich_text": {"equals": tracking_no}}
    return _run_query({"filter": filter_})


def find_orders_by_phone(
    phone: str, brand_prefixes=None, limit: int = 5, max_age_days: int = 14
) -> list[dict]:
    """
    Find recent orders whose PHONE contains the customer's national digits,
    NEWEST FIRST, optionally scoped to a set of brands via the ORDER ID prefix.

    Confirm-button fallback: when a customer clicks confirm but their WhatChimp
    subscriber has no synced order_id (e.g. the send-time pre-sync timed out),
    recover the order straight from the CRM by phone + brand so the note isn't
    silently lost. Matches on the last 9 digits so +971…, 0…, 971… all resolve.

    Only the caller's matches[0] should be tagged — the single MOST RECENT order.
    A `max_age_days` recency guard excludes stale orders entirely, so a click on
    an old/spurious button can never put the note on some months-old order.

    brand_prefixes is a LIST because shared WhatsApp numbers carry several brands
    (e.g. Customer Care = Orlento/Velix/Lune); scoping to one would miss the rest.
    """
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    tail = digits[-9:] if len(digits) >= 9 else digits   # UAE national part
    if len(tail) < 7:
        return []
    if isinstance(brand_prefixes, str):
        brand_prefixes = [brand_prefixes]
    filters = [{"property": FIELD_PHONE, "rich_text": {"contains": tail}}]
    prefixes = [p for p in (brand_prefixes or []) if p]
    if prefixes:
        filters.append({"or": [
            {"property": FIELD_ORDER_ID, "title": {"starts_with": p}} for p in prefixes
        ]})
    if max_age_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        filters.append({"timestamp": "created_time", "created_time": {"on_or_after": cutoff}})
    payload = {
        "filter": {"and": filters},
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": limit,
    }
    return _run_query(payload)


def query_filex_processed() -> list[dict]:
    """
    Every order whose ORDER STATUS == "Processed", regardless of
    Sourcing Notified, Fulfillment Notified, or Filex Submitted state.

    Used by /print all (the per-order classifier inspects FILEX STATUS
    and validation result to decide what to do with each row).
    """
    payload = {
        "filter": {"and": [
            {"property": FIELD_ORDER_STATUS,   "select":   {"equals": "Processed"}},
            {"property": FIELD_PRIVATE_DRIVER, "checkbox": {"equals": False}},
        ]},
    }
    return _run_query(payload)


def query_private_driver_processed() -> list[dict]:
    """Processed orders with the Private Driver checkbox CHECKED. These are skipped
    by /print all (no Filex label, no TJR label) — surfaced only for a skip count."""
    payload = {
        "filter": {"and": [
            {"property": FIELD_ORDER_STATUS,   "select":   {"equals": "Processed"}},
            {"property": FIELD_PRIVATE_DRIVER, "checkbox": {"equals": True}},
        ]},
    }
    return _run_query(payload)


def query_filex_active(within_days: int = 14) -> list[dict]:
    """
    Orders dispatched within the last N days that haven't reached terminal:
      Filex Submitted == true
      AND FILEX STATUS != "Return to Origin"
      AND Dispatched At >= now - N days
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    payload = {
        "filter": {"and": [
            {"property": FIELD_FILEX_SUBMITTED, "checkbox": {"equals": True}},
            {"property": FIELD_FILEX_STATUS,    "select":   {"does_not_equal": "Return to Origin"}},
            {"property": FIELD_DISPATCHED_AT,   "date":     {"on_or_after": cutoff}},
        ]},
    }
    return _run_query(payload)


def query_filex_active_since(cutoff_iso: str) -> list[dict]:
    """
    Orders dispatched at or after cutoff_iso, Filex Submitted == true,
    FILEX STATUS != "Return to Origin". Used by the polling timer to
    scope updates to a specific window (e.g. "today after 2 AM PKT").
    """
    payload = {
        "filter": {"and": [
            {"property": FIELD_FILEX_SUBMITTED, "checkbox": {"equals": True}},
            {"property": FIELD_FILEX_STATUS,    "select":   {"does_not_equal": "Return to Origin"}},
            {"property": FIELD_DISPATCHED_AT,   "date":     {"on_or_after": cutoff_iso}},
        ]},
    }
    return _run_query(payload)


def query_shipped_unnotified(grace_minutes: int = 2) -> list[dict]:
    """Orders ready for an out-for-delivery message:
        ORDER STATUS == 🚚 SHIPPED
        AND Out For Delivery Sent == false
        AND (Last Update is older than grace_minutes OR is empty)

    The grace window keeps the poller from racing the at-source send in
    filex_reconcile: a fresh auto-promotion sets Last Update to ~now, so the
    poller waits one grace window before treating the row as a missed send
    (manual edit, or a failed at-source attempt).
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)).isoformat()
    payload = {
        "filter": {"and": [
            {"property": FIELD_ORDER_STATUS,            "select":   {"equals": STATUS_SHIPPED}},
            {"property": FIELD_OUT_FOR_DELIVERY_SENT,   "checkbox": {"equals": False}},
            {"or": [
                {"property": FIELD_LAST_UPDATE, "date": {"on_or_before": cutoff}},
                {"property": FIELD_LAST_UPDATE, "date": {"is_empty": True}},
            ]},
        ]},
    }
    return _run_query(payload)


def query_filex_stuck(hours: int = 24) -> list[dict]:
    """
    Orders stuck at 'Label Created' for more than N hours.
      Filex Submitted == true
      AND FILEX STATUS == "Label Created"
      AND Dispatched At < now - N hours
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    payload = {
        "filter": {"and": [
            {"property": FIELD_FILEX_SUBMITTED, "checkbox": {"equals": True}},
            {"property": FIELD_FILEX_STATUS,    "select":   {"equals": "Label Created"}},
            {"property": FIELD_DISPATCHED_AT,   "date":     {"before": cutoff}},
        ]},
    }
    return _run_query(payload)
