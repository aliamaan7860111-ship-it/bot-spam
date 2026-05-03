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
               'total_aed' (or legacy 'total'), 'item_qty', 'internal_note'.

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

    # Total can come from Notion as a number OR a formatted string like "AED299.00"
    total_raw = order.get("total_aed") if order.get("total_aed") is not None else order.get("total")
    total: float | None = None
    if isinstance(total_raw, (int, float)):
        total = float(total_raw)
    elif isinstance(total_raw, str):
        # Strip AED prefix, currency symbols, commas, whitespace
        cleaned = total_raw.strip().upper().removeprefix("AED").replace(",", "").strip()
        try:
            total = float(cleaned)
        except ValueError:
            total = None
    if total is None:
        raise ValidationError(f"invalid total: {total_raw!r}")
    total_str = f"{total:.2f}"

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


def build_merged_payload(orders: list[dict]) -> dict:
    """
    Combine multiple Notion orders for the same customer into one Filex payload.

    - ShipperRef = "+".join(order_id for each order)  e.g. "AM3013+Di1665"
    - TotalCOG = sum of all totals
    - NumberOfPieces = sum of pieces across orders
    - Desc1 = combined items (truncated to 200 chars)
    - All other fields from the FIRST order (name, phone, address)

    Raises:
        ValidationError if first order is invalid (missing name/phone/address/city)
    """
    if not orders:
        raise ValidationError("empty group")
    if len(orders) == 1:
        return build_payload(orders[0])

    # Use first order as the base
    base = build_payload(orders[0])

    # Combine refs, totals, pieces, descs
    refs = [o.get("order_id", "?") for o in orders]
    base["ShipperRef"] = "+".join(refs)

    total_sum = 0.0
    pieces_sum = 0
    desc_parts = []
    for o in orders:
        total_raw = o.get("total_aed") if o.get("total_aed") is not None else o.get("total")
        if isinstance(total_raw, (int, float)):
            total_sum += float(total_raw)
        elif isinstance(total_raw, str):
            cleaned = total_raw.strip().upper().removeprefix("AED").replace(",", "").strip()
            try:
                total_sum += float(cleaned)
            except ValueError:
                pass
        pieces_sum += parse_pieces(o.get("item_qty") or "")
        desc_parts.append((o.get("item_qty") or "Item").replace("\n", " + ").strip())

    base["TotalCOG"] = f"{total_sum:.2f}"
    base["NumberOfPieces"] = str(pieces_sum if pieces_sum > 0 else 1)
    combined_desc = " | ".join(desc_parts)[:200]
    base["Desc1"] = combined_desc
    base["Remarks"] = ((base.get("Remarks") or "") + f" [Merged: {', '.join(refs)}]")[:200]
    return base
