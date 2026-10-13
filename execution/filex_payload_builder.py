"""Build Filex placebulk payloads from Notion order dicts, with validation."""

import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from filex_city_normalizer import normalize_city
from filex_client import parse_pieces
from whatchimp_client import clean_phone_number


class ValidationError(ValueError):
    """Raised when an order is missing required fields for Filex."""


def merge_store_key(order_id: str | None) -> str:
    """Store identity used for merge grouping: the leading alphabetic prefix of the
    order id (AM, R, Di/DI, LU, DX, ...), upper-cased so case variants of the same store
    still group together. Orders from DIFFERENT stores (different prefixes) must never be
    merged onto one Filex label, even when they share a phone number.
        'AM 403' -> 'AM'   'R1319' -> 'R'   'Di 98'/'DI 100' -> 'DI'   'DX 5' -> 'DX'
    """
    oid = (order_id or "").strip()
    m = re.match(r"[A-Za-z]+", oid)
    return m.group(0).upper() if m else oid.upper()


_TOTAL_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def parse_total(raw) -> float:
    """
    Extract a non-negative float from a Notion total field.

    Accepts:
      - int / float: returned as float (zero allowed for exchange orders).
      - str: extracts the FIRST numeric token. Handles 'AED' prefix,
        thousand-separator commas, decimals, trailing notes, '/-' suffix.

    Raises:
        ValidationError if the value is None, an unsupported type,
        a string with no numeric content, or a negative number.
    """
    if isinstance(raw, bool):
        raise ValidationError(f"invalid total: {raw!r}")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        m = _TOTAL_RE.search(raw)
        if not m:
            raise ValidationError(f"invalid total: {raw!r}")
        try:
            value = float(m.group(0).replace(",", ""))
        except ValueError:
            raise ValidationError(f"invalid total: {raw!r}")
    else:
        raise ValidationError(f"invalid total: {raw!r}")
    if value < 0:
        raise ValidationError(f"invalid total: {raw!r}")
    return value


# What people put between two phone numbers when they only have one box.
_PHONE_SPLIT = re.compile(r"\s*(?:/|\\|,|;|&|\bor\b|\band\b|\+971(?=\s*\d)|\|)\s*", re.I)


def split_phones(raw: str) -> list[str]:
    """
    One field, however many numbers the customer typed into it.

    "+971 56 669 4718/+971 56 436 3637" is two numbers, not a twenty-two
    digit one. Returns them normalised to UAE 0XXXXXXXXX, in the order
    given, with anything implausible dropped rather than guessed at.
    """
    out: list[str] = []
    for part in _PHONE_SPLIT.split(raw or ""):
        digits = clean_phone_number(part)
        if digits.startswith("971"):
            digits = "0" + digits[3:]
        # UAE numbers are ten digits as 0XXXXXXXXX. Foreign ones are kept as
        # their digits and sent as given, which is what has always happened
        # and what Filex accepts - VL 215 is a +44 customer with a real
        # order. The only thing refused is something that cannot be dialled:
        # too short, or too long because two numbers were glued together.
        ok = (len(digits) == 10 and digits.startswith("0")) or (9 <= len(digits) <= 15)
        if ok and digits not in out:
            out.append(digits)
    return out


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

    # Split before validating: a customer who gave two numbers should be
    # served, not refused for being helpful.
    phones = split_phones(phone_raw)
    if not phones:
        raise ValidationError(f"no usable phone number in {phone_raw!r}")
    phone = phones[0]
    phone2 = phones[1] if len(phones) > 1 else ""

    # The old check was `len(phone) < 10` and said nothing about too long,
    # which is how 0566694718971564363637 reached Filex and took a batch of
    # 37 down with it. This is the upper bound that was missing; split_phones
    # already enforces it, and this says so where the failure would be read.
    if not (9 <= len(phone) <= 15):
        raise ValidationError(f"invalid phone after normalization: {phone!r} (from {phone_raw!r})")

    address = (order.get("full_address") or "").strip()
    if not address:
        raise ValidationError("missing address")

    city = normalize_city(address)
    if not city:
        raise ValidationError(f"missing city in address: {address[:60]!r}")

    total_raw = order.get("total_aed") if order.get("total_aed") is not None else order.get("total")
    total = parse_total(total_raw)
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
        # A second number the customer gave. The driver gets both, which is
        # the point of them offering it.
        "MobileNumber2": phone2,
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
        try:
            total_sum += parse_total(total_raw)
        except ValidationError:
            # In a merged group, the first order's total has already been
            # validated by build_payload. Subsequent unparseable totals are
            # treated as 0 to avoid losing the merged shipment.
            pass
        pieces_sum += parse_pieces(o.get("item_qty") or "")
        desc_parts.append((o.get("item_qty") or "Item").replace("\n", " + ").strip())

    base["TotalCOG"] = f"{total_sum:.2f}"
    base["NumberOfPieces"] = str(pieces_sum if pieces_sum > 0 else 1)
    combined_desc = " | ".join(desc_parts)[:200]
    base["Desc1"] = combined_desc
    base["Remarks"] = ((base.get("Remarks") or "") + f" [Merged: {', '.join(refs)}]")[:200]
    return base
