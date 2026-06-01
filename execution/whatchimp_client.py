import os
import logging
import requests
import json
from dotenv import load_dotenv
from pathlib import Path

# Load env variables
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Use a specific logger that will show up in the main logs
log = logging.getLogger("whatchimp_client")
log.setLevel(logging.INFO)

# Config from .env
WHATCHIMP_API_TOKEN = os.getenv("WHATCHIMP_API_TOKEN", "")

API_BASE = "https://app.whatchimp.com/api/v1/whatsapp"

# Per-brand WhatChimp routing. Key = 2-char order_id prefix.
# phone_number_id / template_id / confirm_button_qr come from the WhatChimp dashboard.
# brand_display is rendered into templateVariable-brand-2 (shown inside the message body).
BRAND_CONFIG = {
    "PT": {  # Elara (legacy PrettyByShd number)
        "phone_number_id":   "1031340813395459",
        "template_id":       "340859",
        "confirm_button_qr": "Nop_RZOKKksN72n",
        "brand_display":     "Elara",
        "sender_phone":      "971521179533",
    },
    "Di": {  # Dialo UAE
        "phone_number_id":   "1002123586328400",
        "template_id":       "354662",
        "confirm_button_qr": "c6GBqbMceC17Rud",
        "brand_display":     "Dialo UAE",
        "sender_phone":      "971524205211",
    },
    "LU": {  # Lune Collection
        "phone_number_id":   "1138942462625909",
        "template_id":       "355333",
        "confirm_button_qr": "FC4cJLqFUsE8gi5",
        "brand_display":     "Lune Collection",
        "sender_phone":      "971523349770",
    },
    "VX": {  # Virex UAE
        "phone_number_id":   "1073890042476443",
        "template_id":       "354663",
        "confirm_button_qr": "kZICJ4ZHWVcSDOC",
        "brand_display":     "Virex UAE",
        "sender_phone":      "971521539779",
    },
    "AM": {  # Amara's Room
        "phone_number_id":   "1045332455333591",
        "template_id":       "354661",
        "confirm_button_qr": "t4Y_y-k2p1uOeMB",
        "brand_display":     "Amara's Room",
        "sender_phone":      "971564898669",
    },
}

# Fallback for legacy call paths (Elara is the original sender number).
DEFAULT_PHONE_NUMBER_ID = BRAND_CONFIG["PT"]["phone_number_id"]

# Reverse lookup tables for identifying brand from a webhook payload
POSTBACK_TO_PREFIX    = {cfg["confirm_button_qr"]: prefix for prefix, cfg in BRAND_CONFIG.items()}
SENDER_PHONE_TO_PREFIX = {cfg["sender_phone"]:     prefix for prefix, cfg in BRAND_CONFIG.items()}


# ---------------------------------------------------------------------------
# Out-for-Delivery routing (separate from BRAND_CONFIG on purpose: Pelvini and
# Orlento share another brand's WhatsApp number and have no confirm-button /
# sender-phone of their own, so they must not enter the BRAND_CONFIG comprehensions).
# Key = ORDER ID prefix. brand_display fills the #!brand!# template variable.
# ---------------------------------------------------------------------------
OFD_CONFIG = {
    "PT": {"phone_number_id": "1031340813395459", "ofd_template_id": "377952", "brand_display": "Elara UAE"},
    "Di": {"phone_number_id": "1002123586328400", "ofd_template_id": "377954", "brand_display": "Dialo UAE"},
    "LU": {"phone_number_id": "1138942462625909", "ofd_template_id": "377955", "brand_display": "Lune Collection"},
    "PV": {"phone_number_id": "1138942462625909", "ofd_template_id": "377955", "brand_display": "Pelvini"},
    "VX": {"phone_number_id": "1073890042476443", "ofd_template_id": "377956", "brand_display": "Virex UAE"},
    "O":  {"phone_number_id": "1073890042476443", "ofd_template_id": "377956", "brand_display": "Orlento"},
    # Amara DISABLED: template 377951 (ofd_amara) is not approved in en_US, so every
    # /send/template is rejected. `pending` makes the sender skip Amara cleanly (no send,
    # no subscriber churn) until a working en_US template is approved — then remove `pending`.
    "AM": {"phone_number_id": "1045332455333591", "ofd_template_id": "377951", "brand_display": "Amara's Room", "no_vars": True, "pending": True},
}

# Match longest prefix first so the 1-char "O" (Orlento) never shadows a 2-char prefix.
_OFD_PREFIXES = sorted(OFD_CONFIG.keys(), key=len, reverse=True)


def resolve_ofd_prefix(order_id: str) -> str | None:
    """Return the known OFD prefix an order_id starts with, or None. Case-sensitive."""
    oid = (order_id or "").strip()
    for prefix in _OFD_PREFIXES:
        if oid.startswith(prefix):
            return prefix
    return None


def get_ofd_config(order_id: str) -> dict | None:
    """Resolve the OFD routing config from an order_id, or None for unknown brands."""
    prefix = resolve_ofd_prefix(order_id)
    return OFD_CONFIG[prefix] if prefix is not None else None


def build_ofd_payload(cfg: dict, order_id: str, cleaned_phone: str, api_token: str) -> dict:
    """Build the /send/template POST body for an out-for-delivery message.
    Amara's legacy template (no_vars) carries no body variables; every other
    brand fills #!brand!# (templateVariable-brand-2) and #!id!# (templateVariable-id-3)."""
    payload = {
        "apiToken":        api_token,
        "phone_number_id": cfg["phone_number_id"],
        "template_id":     cfg["ofd_template_id"],
        "phone_number":    cleaned_phone,
    }
    if not cfg.get("no_vars"):
        payload["templateVariable-brand-2"] = cfg["brand_display"]
        payload["templateVariable-id-3"]    = order_id
    return payload


def send_out_for_delivery_template(phone_number: str, order_id: str, cfg: dict) -> bool:
    """Send one out-for-delivery WhatsApp template via WhatChimp.

    `cfg` must come from get_ofd_config(order_id). Pre-syncs the subscriber on the
    brand's own phone_number_id (matches the confirmation flow), then posts the
    template. Returns True only on WhatChimp status == "1".
    """
    if not WHATCHIMP_API_TOKEN:
        log.error("Missing WHATCHIMP_API_TOKEN in .env")
        return False

    phone_number_id = cfg["phone_number_id"]
    template_id     = cfg["ofd_template_id"]
    display_brand   = cfg["brand_display"]

    cleaned_phone = clean_phone_number(phone_number)
    if not cleaned_phone.startswith("971") or len(cleaned_phone) != 12:
        log.error(
            f"Phone '{phone_number}' failed UAE normalization "
            f"(got '{cleaned_phone}') — skipping OFD {order_id}"
        )
        return False

    # Pre-sync custom fields so #!id!# / #!brand!# resolve; harmless for Amara's no-var template.
    create_or_update_subscriber(phone_number, "", order_id, display_brand, phone_number_id)

    payload = build_ofd_payload(cfg, order_id, cleaned_phone, WHATCHIMP_API_TOKEN)
    url = f"{API_BASE}/send/template"
    try:
        log.info(
            f"🚚 OFD template {template_id} → {cleaned_phone} via {phone_number_id} "
            f"({display_brand}, order {order_id})"
        )
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        if str(data.get("status")) == "1":
            log.info(f"✅ OFD delivered: {order_id}")
            return True
        log.error(f"❌ OFD rejected ({display_brand}): {data.get('message', data)}")
        return False
    except Exception as e:
        log.error(f"OFD request failed: {e}")
        return False


def get_brand_config(order_id_or_prefix: str) -> dict:
    """Look up the brand config from an order_id or its 2-char prefix. Defaults to PT (Elara)."""
    prefix = (order_id_or_prefix or "")[:2]
    return BRAND_CONFIG.get(prefix, BRAND_CONFIG["PT"])


def identify_brand_from_webhook(params: dict) -> str:
    """
    Identify which of the 5 brands a webhook postback came from, using the
    payload WhatChimp actually sends for flow-triggered webhooks.

    Primary signal: `postbackid` — unique per template's confirm button.
    Fallback signal: `whatsapp_bot_username` — the sender phone (digits only).

    Returns the 2-char brand prefix (e.g. "Di") or "" if unidentifiable.
    """
    postback = (params.get("postbackid") or "").strip()
    if postback and postback in POSTBACK_TO_PREFIX:
        return POSTBACK_TO_PREFIX[postback]

    bot_username = params.get("whatsapp_bot_username") or ""
    bot_digits = ''.join(c for c in str(bot_username) if c.isdigit())
    if bot_digits and bot_digits in SENDER_PHONE_TO_PREFIX:
        return SENDER_PHONE_TO_PREFIX[bot_digits]

    return ""

def clean_phone_number(phone: str) -> str:
    """
    Normalize any UAE phone input to canonical `971XXXXXXXXX` (12 digits).
    Handles +971…, 00971…, 971…, 05XXXXXXXX, 0XXXXXXXXX, 5XXXXXXXX and any
    spaces/dashes/parens. Returns the raw digits unchanged if the shape is
    unrecognized so the caller can detect and skip.
    """
    if not phone:
        return ""
    digits = ''.join(c for c in str(phone) if c.isdigit())
    if not digits:
        return ""
    # 00971… → 971…
    if digits.startswith("00"):
        digits = digits[2:]
    # Already canonical
    if digits.startswith("971") and len(digits) == 12:
        return digits
    # Local UAE format with leading 0 (e.g. 0521234567 or 0501234567)
    if digits.startswith("0") and len(digits) == 10:
        return "971" + digits[1:]
    # Local UAE format without the leading 0 (e.g. 521234567)
    if len(digits) == 9 and digits.startswith("5"):
        return "971" + digits
    # Unknown shape — return raw digits so caller can log & skip
    return digits

def create_or_update_subscriber(
    phone_number: str,
    name: str,
    order_id: str = "",
    brand: str = "",
    phone_number_id: str = DEFAULT_PHONE_NUMBER_ID,
) -> bool:
    """
    Ensures a subscriber exists in WhatChimp and assigns custom fields so
    flow merge tags like #order_id# resolve when the customer taps a button.

    Subscriber records are scoped per `phone_number_id` — the caller must pass
    the brand's own number so pre-sync lands on the correct subscriber list.
    """
    cleaned_phone = clean_phone_number(phone_number)

    # 1. Ensure subscriber exists (camelCase)
    create_url = f"{API_BASE}/subscriber/create"
    create_payload = {
        "apiToken": WHATCHIMP_API_TOKEN,
        "phoneNumberID": phone_number_id,
        "name": name,
        "phoneNumber": cleaned_phone
    }

    try:
        # 'already exists' returns status 0, so we don't gate on this response
        requests.post(create_url, data=create_payload, timeout=10)

        # 2. Assign custom fields (snake_case; custom_fields is a JSON string)
        assign_url = f"{API_BASE}/subscriber/chat/assign-custom-fields"
        custom_fields = {
            "order_id": order_id,
            "brand_name": brand
        }
        assign_payload = {
            "apiToken": WHATCHIMP_API_TOKEN,
            "phone_number_id": phone_number_id,
            "phone_number": cleaned_phone,
            "custom_fields": json.dumps(custom_fields)
        }

        resp = requests.post(assign_url, data=assign_payload, timeout=10)
        data = resp.json()
        if str(data.get("status")) == "1":
            log.info(f"  ✓ Subscriber metadata synced on {phone_number_id}: {custom_fields}")
            return True
        else:
            log.warning(f"  ⚠️ Custom field sync warning: {data.get('message')}")
            return False
    except Exception as e:
        log.error(f"  ✗ Subscriber sync failed: {str(e)}")
        return False

def send_template_message(
    phone_number: str,
    customer_name: str,
    order_id: str,
    total: str = "",
    brand_name: str = "",
    brand_prefix: str = "",
) -> bool:
    """
    Sends the WhatsApp confirmation template. Routes sender number, template ID,
    and confirm-button QR per brand via BRAND_CONFIG (keyed by order_id prefix).

    Always pre-syncs the subscriber's custom fields against the brand's own
    phone_number_id so the flow's `#order_id#` merge tag resolves on click.
    """
    if not WHATCHIMP_API_TOKEN:
        log.error("Missing WHATCHIMP_API_TOKEN in .env")
        return False

    # Pick brand from explicit prefix arg, else infer from order_id
    prefix = (brand_prefix or order_id or "")[:2]
    cfg = BRAND_CONFIG.get(prefix)
    if not cfg:
        log.error(f"No BRAND_CONFIG entry for prefix '{prefix}' (order_id={order_id}) — skipping")
        return False

    phone_number_id   = cfg["phone_number_id"]
    template_id       = cfg["template_id"]
    confirm_button_qr = cfg["confirm_button_qr"]
    display_brand     = brand_name or cfg["brand_display"]

    # Normalize phone and guard on UAE shape — prevents sending to wrong number
    cleaned_phone = clean_phone_number(phone_number)
    if not cleaned_phone.startswith("971") or len(cleaned_phone) != 12:
        log.error(
            f"Phone '{phone_number}' failed UAE normalization "
            f"(got '{cleaned_phone}') — skipping {order_id}"
        )
        return False

    # Pre-sync so flow webhook can resolve #order_id#
    create_or_update_subscriber(
        phone_number, customer_name, order_id, display_brand, phone_number_id
    )

    url = f"{API_BASE}/send/template"
    payload = {
        "apiToken":        WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_number_id,
        "template_id":     template_id,
        "phone_number":    cleaned_phone,

        # Template body variables (safe to pass even if a template doesn't use name-1)
        "templateVariable-brand-2": display_brand,
        "templateVariable-id-3":    order_id,
        "templateVariable-name-1":  customer_name,

        # Button values — no spaces after comma per WhatChimp API
        "template_quick_reply_button_values": json.dumps(
            ["YES_START_CHAT_WITH_HUMAN", confirm_button_qr], separators=(',', ':')
        ),
    }

    try:
        log.info(
            f"🚀 Send template {template_id} → {cleaned_phone} via {phone_number_id} "
            f"({display_brand}, order {order_id})"
        )
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()

        if str(data.get("status")) == "1":
            log.info(f"✅ SUCCESS: {order_id} confirmation delivered")
            return True
        else:
            log.error(f"❌ API Rejected ({display_brand}): {data.get('message', data)}")
            return False
    except Exception as e:
        log.error(f"Request failed: {e}")
        return False

def get_subscriber_custom_fields(
    phone_number: str,
    phone_number_id: str = None,
) -> dict:
    """
    Retrieves a subscriber's custom fields from WhatChimp.

    Subscribers are scoped per phone_number_id. If no id is passed, we sweep
    all 5 brand numbers and return the first hit — useful for the webhook
    fallback when we don't yet know which brand the chat_id belongs to.
    Response custom_fields format: "brand_name:Elara,order_id:PT1793"
    """
    cleaned_phone = clean_phone_number(phone_number)
    url = f"{API_BASE}/subscriber/get"

    # If caller knows the brand, use that single number; otherwise try all 5
    ids_to_try = (
        [phone_number_id]
        if phone_number_id
        else [cfg["phone_number_id"] for cfg in BRAND_CONFIG.values()]
    )

    for pnid in ids_to_try:
        payload = {
            "apiToken":        WHATCHIMP_API_TOKEN,
            "phone_number_id": pnid,
            "phone_number":    cleaned_phone,
            "phoneNumberID":   pnid,
            "phoneNumber":     cleaned_phone,
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            data = resp.json()
            if str(data.get("status")) == "1" and data.get("message"):
                subscribers = data["message"]
                if isinstance(subscribers, list) and len(subscribers) > 0:
                    custom_fields_str = subscribers[0].get("custom_fields", "") or ""
                    result = {}
                    for item in custom_fields_str.split(","):
                        if ":" in item:
                            k, v = item.split(":", 1)
                            result[k.strip()] = v.strip()
                    if result:
                        return result
        except Exception as e:
            log.warning(f"  WhatChimp subscriber lookup error on {pnid}: {e}")
    return {}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("WhatChimp DEFINITIVE Client Ready.")
