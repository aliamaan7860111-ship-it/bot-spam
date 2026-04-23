"""
inspect_subscriber.py
=====================
Diagnostic: given a phone number, query each of the 5 brand subscriber lists
in WhatChimp and print what custom fields each one holds.

Useful when a webhook fallback returns the "wrong" order_id — usually a sign
the customer has stale data on another brand's subscriber record.

Usage:
    python execution/inspect_subscriber.py 971555614190
    python execution/inspect_subscriber.py +971 55 561 4190
    python execution/inspect_subscriber.py 0555614190
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import whatchimp_client as wc


def inspect(raw_phone: str) -> None:
    cleaned = wc.clean_phone_number(raw_phone)
    print(f"\nInput: {raw_phone!r}  ->  normalized: {cleaned!r}\n")
    print(f"{'Brand':<20} {'phone_number_id':<20} {'order_id':<12} {'brand_name':<20}")
    print("-" * 76)

    for prefix, cfg in wc.BRAND_CONFIG.items():
        pnid = cfg["phone_number_id"]
        display = cfg["brand_display"]
        fields = wc.get_subscriber_custom_fields(cleaned, phone_number_id=pnid)
        order_id  = fields.get("order_id", "-")
        brand_nm  = fields.get("brand_name", "-")
        marker = "  <-- STALE?" if order_id != "-" and not order_id.startswith(prefix) else ""
        print(f"{display:<20} {pnid:<20} {order_id:<12} {brand_nm:<20}{marker}")

    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python execution/inspect_subscriber.py <phone_number>")
        print("Example: python execution/inspect_subscriber.py 971555614190")
        sys.exit(1)
    # Join all args in case the user pasted a number with spaces
    inspect(" ".join(sys.argv[1:]))
