"""
One-shot: add the 'Paid via Stripe' checkbox property to the main CRM Notion
data source. Idempotent - re-running just confirms it.

The main CRM database has MULTIPLE data sources (Notion 2025-09-03 model), so the
legacy /databases/{id} PATCH is rejected. We PATCH the exact data source that
notion_client queries/writes - _get_data_source_id() - using its 2025-09-03
headers, so the new checkbox is visible to payment_bridge.mark_stripe_paid().

Run once before deploying the payment-bridge service.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notion_client as nc


def main() -> int:
    ds_id = nc._get_data_source_id()
    props_patch = {"Paid via Stripe": {"checkbox": {}}}
    with httpx.Client(timeout=20) as client:
        resp = client.patch(
            f"{nc.NOTION_API_BASE}/data_sources/{ds_id}",
            headers=nc._headers(),
            json={"properties": props_patch},
        )
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text[:600]}", file=sys.stderr)
        return 1
    print(f"OK: 'Paid via Stripe' checkbox added/confirmed on data source {ds_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
