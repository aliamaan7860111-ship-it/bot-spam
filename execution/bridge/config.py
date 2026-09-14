"""
Env-driven brand configuration.

Each brand needs a few env vars to be fully wired. Missing vars don't crash
the service — the brand just gets logged as "partial" and its endpoints
return 503 until configured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

BRAND_SLUGS = ["amara", "amarawatches", "pelvini", "elara", "lune", "virex", "dialo",
               "rimal", "orlento", "saqr", "velix", "wristgallery", "viresta"]

# Short brand name rendered into the collective recovery template's #!brand!#
# (templateVariable-brand-2). The copy reads "we kept your <brand> cart safe", so
# these are deliberately the short form, NOT the fuller confirmation/OFD names.
#
# Presence here ALSO selects the payload layout (see whatchimp_sender):
#   in this map  -> new collective template 442130: brand at 2, url at 3
#   absent       -> legacy per-brand template:      url at 2, no brand variable
# Brands that did not move to the 2026-09 Saudi portfolio are intentionally absent.
RECOVERY_BRAND_DISPLAY = {
    "amara":        "Amara",
    "amarawatches": "Amara",
    "rimal":        "Rimal",
    "orlento":      "Orlento",
    "saqr":         "Saqr",
    "lune":         "Lune",
    "velix":        "Velix",
    "wristgallery": "Wrist Gallery",
    "viresta":      "Viresta",
}


@dataclass
class BrandConfig:
    slug: str
    shopify_domain: str | None
    shopify_token: str | None
    shopify_api_secret: str | None
    whatchimp_phone_number_id: str | None
    whatchimp_template_id: str | None
    checkout_discount_code: str
    recovery_brand: str | None = None

    @property
    def shopify_ready(self) -> bool:
        return bool(self.shopify_domain and self.shopify_token)

    @property
    def whatchimp_ready(self) -> bool:
        return bool(self.whatchimp_phone_number_id and self.whatchimp_template_id)


def _g(brand_upper: str, key: str) -> str | None:
    return os.getenv(f"{key}_{brand_upper}")


def load_brand(slug: str) -> BrandConfig:
    u = slug.upper()
    return BrandConfig(
        slug=slug,
        shopify_domain=_g(u, "SHOPIFY_DOMAIN"),
        shopify_token=_g(u, "SHOPIFY_TOKEN"),
        shopify_api_secret=_g(u, "SHOPIFY_API_SECRET"),
        whatchimp_phone_number_id=_g(u, "WHATCHIMP_PHONE_NUMBER_ID"),
        whatchimp_template_id=_g(u, "WHATCHIMP_TEMPLATE_ID"),
        checkout_discount_code=_g(u, "CHECKOUT_DISCOUNT_CODE") or "RECOVER10",
        recovery_brand=RECOVERY_BRAND_DISPLAY.get(slug),
    )


def load_all_brands() -> dict[str, BrandConfig]:
    return {slug: load_brand(slug) for slug in BRAND_SLUGS}


# Shared (non-brand) config
def whatchimp_api_token() -> str:
    return os.environ["WHATCHIMP_API_TOKEN"]


def notion_api_key() -> str:
    return os.environ["NOTION_API_KEY"]


def recovery_db_id() -> str:
    return os.environ["RECOVERY_NOTION_DATABASE_ID"]


def recovery_delay_seconds() -> int:
    return int(os.environ.get("RECOVERY_DELAY_MINUTES", "30")) * 60


def bridge_base_url() -> str:
    return os.environ.get("BRIDGE_BASE_URL", "https://grqholdings.duckdns.org")
