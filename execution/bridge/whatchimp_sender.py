"""
WhatChimp template send — stub-safe.

If the brand has no WHATCHIMP_TEMPLATE_ID configured, we log and skip the send
but still mark the Notion row as "Recovery Pending (no template)" so we know
which rows would have fired. Once the template is approved and the env var is
set + service restarted, future sends go live.
"""
from __future__ import annotations

import logging

import httpx

from config import BrandConfig, whatchimp_api_token

log = logging.getLogger(__name__)

WHATCHIMP_BASE = "https://app.whatchimp.com/api/v1"


async def upsert_subscriber(
    client: httpx.AsyncClient, phone: str, brand: BrandConfig, name: str | None
) -> dict:
    """Idempotent subscriber create with name. Calls /whatsapp/subscriber/create
    (camelCase params per the API). 'Already exists' is expected and treated as
    success — see whatchimp-api skill §12. Sets the subscriber's name so
    #User-Name# substitutes correctly in templates and flow messages.
    """
    if not brand.whatchimp_phone_number_id:
        return {"status": "skipped_no_phone_number_id"}
    if not name:
        # Still call create so the subscriber exists; just no name to set.
        name = ""
    payload = {
        "apiToken": whatchimp_api_token(),
        "phoneNumberID": brand.whatchimp_phone_number_id,
        "phoneNumber": phone,
        "name": name,
    }
    resp = await client.post(
        f"{WHATCHIMP_BASE}/whatsapp/subscriber/create",
        data=payload,
        timeout=15.0,
    )
    # Don't raise on 4xx — "already exists" comes back with status string;
    # subscriber is then still present + name updated.
    return _normalize(resp)


async def assign_custom_fields(
    client: httpx.AsyncClient, phone: str, brand: BrandConfig, fields: dict[str, str]
) -> dict:
    """Set custom field values on a subscriber profile before triggering a flow."""
    if not brand.whatchimp_phone_number_id:
        log.warning("[%s] missing whatchimp_phone_number_id, skipping custom-fields assign", brand.slug)
        return {"status": "skipped_no_phone_number_id"}

    import json as _json
    payload = {
        "apiToken": whatchimp_api_token(),
        "phone_number_id": brand.whatchimp_phone_number_id,
        "phone_number": phone,
        "custom_fields": _json.dumps(fields),
    }
    resp = await client.post(
        f"{WHATCHIMP_BASE}/whatsapp/subscriber/chat/assign-custom-fields",
        data=payload,
        timeout=15.0,
    )
    return _normalize(resp)


async def send_recovery_template(
    client: httpx.AsyncClient,
    *,
    brand: BrandConfig,
    phone: str,
    checkout_url: str,
) -> dict:
    """Send the brand's recovery template to a phone number.
    Returns a dict with status info.
    """
    if not brand.whatchimp_ready:
        log.warning(
            "[%s] WhatChimp not ready — phone_number_id=%s template_id=%s — skipping send",
            brand.slug,
            brand.whatchimp_phone_number_id,
            brand.whatchimp_template_id,
        )
        return {"status": "stubbed", "reason": "whatchimp not configured for brand"}

    import json as _json
    payload = {
        "apiToken": whatchimp_api_token(),
        "phone_number_id": brand.whatchimp_phone_number_id,
        "template_id": brand.whatchimp_template_id,
        "phone_number": phone,
        "templateVariable-url-2": checkout_url,
        "templateVariable-#!url!#-2": checkout_url,
        "templateVariable-url-1": checkout_url,
        "templateVariable-#!url!#-1": checkout_url,
    }

    resp = await client.post(
        f"{WHATCHIMP_BASE}/whatsapp/send/template",
        data=payload,
        timeout=15.0,
    )
    return _normalize(resp)


def _normalize(resp: httpx.Response) -> dict:
    """Treat WhatChimp's variable status representation uniformly."""
    try:
        data = resp.json()
    except Exception:
        return {"status": "error", "http": resp.status_code, "body": resp.text[:500]}

    status_raw = data.get("status")
    ok = status_raw in (1, "1", True, "true")
    return {
        "status": "ok" if ok else "error",
        "http": resp.status_code,
        "raw_status": status_raw,
        "message_id": data.get("wa_message_id") or (data.get("data") or {}).get("message_id"),
        "raw": data,
    }
