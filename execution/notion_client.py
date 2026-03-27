"""
notion_client.py
================
Reusable Notion API wrapper for querying and updating the orders database.
Uses httpx for HTTP requests — zero dependency beyond stdlib + httpx.

All field names match the user's exact Notion database schema.
"""

import os
import logging
from datetime import datetime, timezone

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
NOTION_VERSION = "2022-06-28"

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


# ---------------------------------------------------------------------------
# Field name constants (match user's exact Notion property names)
# ---------------------------------------------------------------------------

FIELD_ORDER_ID = "ORDER ID"
FIELD_CREATED = "CREATED"
FIELD_CUSTOMER_NAME = "CUSTOMER NAME"
FIELD_ITEM_QTY = "ITEM | QTY"
FIELD_FULL_ADDRESS = "FULL ADDRESS"
FIELD_PHONE = "PHONE"
FIELD_TOTAL = "TOTAL (AED)"
FIELD_COST = "COST"
FIELD_INTERNAL_NOTE = "INTERNAL NOTE"
FIELD_ORDER_STATUS = "ORDER STATUS"
FIELD_PAYMENT = "PAYMENT"
FIELD_CUSTOMER_TYPE = "CUSTOMER TYPE"
FIELD_ORDER_SOURCE_URL = "ORDER SOURCE URL"
FIELD_IMAGE_URL = "IMAGE URL"
FIELD_EMAIL = "EMAIL"
FIELD_PLATFORM_SOURCE = "PLATFORM SOURCE"
FIELD_SOURCING_NOTIFIED = "SOURCING NOTIFIED"
FIELD_FULFILLMENT_NOTIFIED = "FULFILLMENT NOTIFIED"
FIELD_WHATSAPP_SENT = "Conformation Sent" # User's exact spelling
STATUS_CONFIRMATION_SENT = "Conformation Sent" # User's exact spelling


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

    # Image URL is a text/URL field — may contain multiple URLs separated by
    # commas, newlines, or spaces. We normalize to a list.
    raw_image_url = _get_rich_text(props, FIELD_IMAGE_URL)
    if not raw_image_url:
        raw_image_url = _get_url(props, FIELD_IMAGE_URL)

    image_urls = []
    if raw_image_url:
        # Split on commas, newlines, or whitespace
        for part in raw_image_url.replace(",", "\n").replace(" ", "\n").split("\n"):
            url = part.strip()
            if url and (url.startswith("http://") or url.startswith("https://")):
                image_urls.append(url)

    return {
        "page_id": page["id"],
        "order_id": _get_title(props, FIELD_ORDER_ID),
        "created": _get_date(props, FIELD_CREATED) or _get_rich_text(props, FIELD_CREATED),
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
        "order_source_url": _get_url(props, FIELD_ORDER_SOURCE_URL) or _get_rich_text(props, FIELD_ORDER_SOURCE_URL),
        "image_urls": image_urls,
        "email": _get_rich_text(props, FIELD_EMAIL) or _get_url(props, FIELD_EMAIL),
        "platform_source": _get_select(props, FIELD_PLATFORM_SOURCE) or _get_rich_text(props, FIELD_PLATFORM_SOURCE),
        "sourcing_notified": _get_checkbox(props, FIELD_SOURCING_NOTIFIED),
        "fulfillment_notified": _get_checkbox(props, FIELD_FULFILLMENT_NOTIFIED),
        "whatsapp_sent": _get_checkbox(props, FIELD_WHATSAPP_SENT),
    }


# ---------------------------------------------------------------------------
# Query Operations
# ---------------------------------------------------------------------------

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
                    f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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
                    f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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


def query_sourcing_orders() -> list[dict]:
    """
    Find all orders where ORDER STATUS = 'SOURCING'
    (sent to sourcing group but not yet forwarded to fulfillment).
    Returns a list of parsed order dicts.
    """
    payload = {
        "filter": {
            "and": [
                {
                    "property": FIELD_ORDER_STATUS,
                    "select": {"equals": "SOURCING"},
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
                    f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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
                f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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
                    f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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
    """Update checkbox AND status to Conformation Sent."""
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


def mark_sourcing_notified(page_id: str) -> bool:
    """Check the SOURCING NOTIFIED box in Notion."""
    return _update_page(page_id, {
        FIELD_SOURCING_NOTIFIED: {"checkbox": True},
    })


def mark_fulfillment_notified(page_id: str) -> bool:
    """Check the FULFILLMENT NOTIFIED box in Notion."""
    return _update_page(page_id, {
        FIELD_FULFILLMENT_NOTIFIED: {"checkbox": True},
    })


def unmark_notified(page_id: str) -> bool:
    """Uncheck tracking boxes in Notion for resets."""
    return _update_page(page_id, {
        FIELD_SOURCING_NOTIFIED: {"checkbox": False},
        FIELD_FULFILLMENT_NOTIFIED: {"checkbox": False},
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
                    f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query",
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

            # Log available properties
            props = db.get("properties", {})
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
