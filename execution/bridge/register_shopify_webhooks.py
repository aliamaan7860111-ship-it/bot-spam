"""
One-shot script: register the 3 Shopify webhooks (checkouts/create,
checkouts/update, orders/create) on every configured brand store.

Run from your local machine. Requires the brand's SHOPIFY_TOKEN_<BRAND> and
SHOPIFY_DOMAIN_<BRAND> in .env. Will skip brands that are missing config.

Idempotent — if a webhook already exists for the same topic+address, Shopify
returns 422 and we move on.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "https://grqholdings.duckdns.org")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-04")

# Single source of truth: config.BRAND_SLUGS. This used to be a hardcoded copy,
# which silently drifted — the three stores added with the 2026-09 portfolio
# (amarawatches / saqr / wristgallery) were missing here, so running this skipped
# them and their abandoned-checkout recovery never received a single event.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BRAND_SLUGS as BRANDS  # noqa: E402

TOPIC_TO_PATH = {
    "checkouts/create": "checkout-created",
    "checkouts/update": "checkout-updated",
    "orders/create": "order-created",
}


def register_for_brand(slug: str) -> None:
    u = slug.upper()
    domain = os.environ.get(f"SHOPIFY_DOMAIN_{u}")
    token = os.environ.get(f"SHOPIFY_TOKEN_{u}")
    if not (domain and token):
        print(f"[{slug}] skipped — SHOPIFY_DOMAIN_{u} or SHOPIFY_TOKEN_{u} missing")
        return

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    base = f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}"

    # List existing webhooks first to skip duplicates
    existing = requests.get(f"{base}/webhooks.json", headers=headers, timeout=20).json()
    existing_pairs = {
        (w.get("topic"), w.get("address")) for w in existing.get("webhooks", [])
    }

    for topic, path in TOPIC_TO_PATH.items():
        address = f"{BRIDGE_BASE_URL}/webhooks/{slug}/{path}"
        if (topic, address) in existing_pairs:
            print(f"[{slug}] {topic} — already registered -> {address}")
            continue
        resp = requests.post(
            f"{base}/webhooks.json",
            headers=headers,
            json={
                "webhook": {
                    "topic": topic,
                    "address": address,
                    "format": "json",
                }
            },
            timeout=20,
        )
        if resp.status_code in (200, 201):
            wid = resp.json()["webhook"]["id"]
            print(f"[{slug}] {topic} -> {address}  (id={wid})")
        else:
            print(f"[{slug}] {topic} FAILED ({resp.status_code}): {resp.text[:200]}")


def main() -> int:
    print(f"Registering webhooks -> {BRIDGE_BASE_URL}")
    for slug in BRANDS:
        register_for_brand(slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
