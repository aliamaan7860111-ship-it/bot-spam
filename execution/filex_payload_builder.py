"""Build Filex placebulk payloads from Notion order dicts, with validation."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from filex_city_normalizer import normalize_city
from filex_client import parse_pieces
from whatchimp_client import clean_phone_number


class ValidationError(ValueError):
    """Raised when an order is missing required fields for Filex."""


def build_payload(order: dict) -> dict:
    """
    Convert a parsed Notion order into a Filex placebulk item dict.

    Args:
        order: dict from notion_client.parse_order with keys
               'order_id', 'customer_name', 'phone', 'full_address',
               'total', 'item_qty', 'internal_note'.

    Returns:
        dict matching Filex placebulk schema.

    Raises:
        ValidationError if any required field is missing/invalid.
    """
    name = (order.get("customer_name") or "").strip()
    if not name:
        raise ValidationError("missing customer name")

    phone_raw = (order.get("phone") or "").strip()
    if not phone_raw:
        raise ValidationError("missing phone")
    phone = clean_phone_number(phone_raw)
    # clean_phone_number returns 971XXXXXXXXX; convert to UAE 0XXXXXXXXX
    if phone.startswith("971"):
        phone = "0" + phone[3:]
    if not phone or len(phone) < 10:
        raise ValidationError(f"invalid phone after normalization: {phone}")

    address = (order.get("full_address") or "").strip()
    if not address:
        raise ValidationError("missing address")

    city = normalize_city(address)
    if not city:
        raise ValidationError(f"could not extract city from address: {address[:50]}")

    total = order.get("total")
    if total is None or not isinstance(total, (int, float)):
        raise ValidationError(f"invalid total: {total!r}")
    total_str = f"{float(total):.2f}"

    pieces = parse_pieces(order.get("item_qty") or "")
    desc = (order.get("item_qty") or "").replace("\n", " + ").strip() or "Item"

    return {
        "RecipientName": name,
        "TotalCOG": total_str,
        "MobileNumber": phone,
        "ShipperRef": order["order_id"],
        "AddressCountry": "United Arab Emirates",
        "City": city,
        "Area": "",
        "Street": address[:200],  # Filex limits street length
        "MobileNumber2": "",
        "Remarks": (order.get("internal_note") or "")[:200],
        "NumberOfPieces": str(pieces),
        "Desc1": desc[:200],
    }
