import os
import time
import logging
import requests
import json
import re
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
        "phone_number_id":   "1304894276030064",
        "template_id":       "354662",
        "confirm_button_qr": "6a70a6a5b706d",
        "brand_display":     "Dialo UAE",
        "sender_phone":      "966572141803",
    },
    # ---- New Saudi portfolio (2026-09) -------------------------------------
    # Nine stores moved onto their OWN numbers under one portfolio, all sharing the
    # collective confirmation template 442127 ("all_confirmations"), differentiated by
    # templateVariable-brand-2. NOTE `confirm_first`: this template's buttons are
    # [Process, Reschedule], so the confirm postback goes FIRST in the array — the
    # reverse of the legacy templates. See send_template_message().
    "LU": {  # Lune Collection
        "phone_number_id":   "1234704216403689",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f62d5e92e",
        "brand_display":     "Lune Collection",
        "sender_phone":      "966570990638",
        "confirm_first":     True,
    },
    "O": {  # Orlento — 1-char order-id prefix, resolved via 1-char fallback
        "phone_number_id":   "1327104710486442",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f5c48b736",
        "brand_display":     "Orlento UAE",
        "sender_phone":      "966570060432",
        "confirm_first":     True,
    },
    "VL": {  # Velix
        "phone_number_id":   "1306342719226934",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f6575d552",
        "brand_display":     "Velix UAE",
        "sender_phone":      "966571948638",
        "confirm_first":     True,
    },
    "VX": {  # Virex UAE — NOT migrating, stays on the legacy UAE portfolio
        "phone_number_id":   "1073890042476443",
        "template_id":       "354663",
        "confirm_button_qr": "kZICJ4ZHWVcSDOC",
        "brand_display":     "Virex UAE",
        "sender_phone":      "971521539779",
    },
    "AM": {  # Amara's Room
        "phone_number_id":   "1253320094539905",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7eee012c92",
        "brand_display":     "Amara's Room",
        "sender_phone":      "966570280888",
        "confirm_first":     True,
    },
    "AW": {  # Amara's Watches (new store)
        "phone_number_id":   "1199932539879999",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f585b884a",
        "brand_display":     "Amara's Watches",
        "sender_phone":      "966570935924",
        "confirm_first":     True,
    },
    "SQ": {  # Saqr (new store)
        "phone_number_id":   "1268960389639645",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f5e5540ae",
        "brand_display":     "SAQR UAE",
        "sender_phone":      "966571749720",
        "confirm_first":     True,
    },
    "WG": {  # Wrist Gallery (new store)
        "phone_number_id":   "1354836361042768",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f67bd515c",
        "brand_display":     "Wrist Gallery UAE",
        "sender_phone":      "966572517079",
        "confirm_first":     True,
    },
    "R": {  # Rimal UAE — 1-char order-id prefix, resolved via 1-char fallback
        "phone_number_id":   "1397321910124444",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f59ad7113",
        "brand_display":     "Rimal UAE",
        "sender_phone":      "966570864539",
        "confirm_first":     True,
    },
    # Elara(E) / Diwan(DX) / Pelvini(PV) / Viresta(VS) share the "Customer Care 2" number.
    # sender_phone is a placeholder (inbound routing deferred for this number).
    "E": {  # Elara — 1-char order-id prefix (new Shopify), resolved via 1-char fallback
        "phone_number_id":   "1238071629387272",
        "template_id":       "355333",
        "confirm_button_qr": "6a70c6e43a79c",
        "brand_display":     "Elara",
        "sender_phone":      "966571807079",
    },
    "DX": {  # Diwan
        "phone_number_id":   "1238071629387272",
        "template_id":       "355333",
        "confirm_button_qr": "6a70c6e43a79c",
        "brand_display":     "Diwan",
        "sender_phone":      "966571807079",
    },
    "PV": {  # Pelvini
        "phone_number_id":   "1238071629387272",
        "template_id":       "355333",
        "confirm_button_qr": "6a70c6e43a79c",
        "brand_display":     "Pelvini",
        "sender_phone":      "966571807079",
    },
    # Viresta LEFT the "Customer Care 2" group in the 2026-09 migration and now has
    # its own number under the new portfolio. Elara/Diwan/Pelvini stay behind on it.
    "VS": {  # Viresta
        "phone_number_id":   "1260047933866516",
        "template_id":       "442127",
        "confirm_button_qr": "6aa7f6a810471",
        "brand_display":     "Viresta UAE",
        "sender_phone":      "966572862912",
        "confirm_first":     True,
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
    # --- legacy UAE portfolio (not migrating) ---
    "PT": {"phone_number_id": "1031340813395459", "ofd_template_id": "377952", "brand_display": "Elara UAE"},
    "Di": {"phone_number_id": "1304894276030064", "ofd_template_id": "377954", "brand_display": "Dialo UAE"},
    "PV": {"phone_number_id": "1238071629387272", "ofd_template_id": "377955", "brand_display": "Pelvini"},
    "VX": {"phone_number_id": "1073890042476443", "ofd_template_id": "377956", "brand_display": "Virex UAE"},
    "E":  {"phone_number_id": "1238071629387272", "ofd_template_id": "377955", "brand_display": "Elara"},
    "DX": {"phone_number_id": "1238071629387272", "ofd_template_id": "377955", "brand_display": "Diwan"},
    # --- new Saudi portfolio (2026-09): own numbers, collective OFD template 442131 ---
    # Body is "your order #!id!# from #!brand!#" -> same id-1 / brand-2 variable map as
    # the legacy templates, so build_ofd_payload needs no change.
    "AM": {"phone_number_id": "1253320094539905", "ofd_template_id": "442131", "brand_display": "Amara's Room"},
    "AW": {"phone_number_id": "1199932539879999", "ofd_template_id": "442131", "brand_display": "Amara's Watches"},
    "R":  {"phone_number_id": "1397321910124444", "ofd_template_id": "442131", "brand_display": "Rimal UAE"},
    "O":  {"phone_number_id": "1327104710486442", "ofd_template_id": "442131", "brand_display": "Orlento UAE"},
    "SQ": {"phone_number_id": "1268960389639645", "ofd_template_id": "442131", "brand_display": "SAQR UAE"},
    "LU": {"phone_number_id": "1234704216403689", "ofd_template_id": "442131", "brand_display": "Lune Collection"},
    "VL": {"phone_number_id": "1306342719226934", "ofd_template_id": "442131", "brand_display": "Velix UAE"},
    "WG": {"phone_number_id": "1354836361042768", "ofd_template_id": "442131", "brand_display": "Wrist Gallery UAE"},
    "VS": {"phone_number_id": "1260047933866516", "ofd_template_id": "442131", "brand_display": "Viresta UAE"},
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
        # OFD template body is "your order {{1}} from {{2}}":
        #   {{1}} = order id (position 1), {{2}} = brand display (position 2).
        # Position MUST match the body, or the unfilled {{N}} renders literally.
        payload["templateVariable-id-1"]    = order_id
        payload["templateVariable-brand-2"] = cfg["brand_display"]
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
    if not is_valid_msisdn(cleaned_phone):
        log.error(
            f"Phone '{phone_number}' failed normalization "
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
    """Look up the brand config from an order_id or its prefix. Tries the 2-char
    prefix first, then the 1-char prefix (for brands like Rimal 'R'). Defaults to PT (Elara)."""
    oid = order_id_or_prefix or ""
    return BRAND_CONFIG.get(oid[:2]) or BRAND_CONFIG.get(oid[:1]) or BRAND_CONFIG["PT"]


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
    # 971 + local number that still carries its leading 0 (e.g. 971 0547071211)
    if digits.startswith("9710") and len(digits) == 13:
        return "971" + digits[4:]
    # Unknown shape — return raw digits so caller can log & skip
    return digits


def clean_template_param(value) -> str:
    """Sanitize a value for a WhatsApp template parameter.

    WhatsApp rejects template params containing newline/tab characters or more
    than 4 consecutive spaces. Collapse all whitespace runs to a single space.
    """
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_valid_msisdn(digits: str) -> bool:
    """True if `digits` is a plausible E.164 subscriber number (8-15 digits).

    Accepts UAE and international numbers alike, so the caller no longer
    restricts sending to 971 only.
    """
    return bool(digits) and digits.isdigit() and 8 <= len(digits) <= 15

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

        # 2. Assign custom fields (snake_case; custom_fields is a JSON string).
        # RETRY: this endpoint is slow and a single timeout here silently drops
        # order_id, which breaks the confirm-button → CRM-note flow. Retry a few
        # times with a longer timeout before giving up.
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

        last_err = None
        for attempt in range(3):
            if attempt:
                time.sleep(1.5 * attempt)
            try:
                resp = requests.post(assign_url, data=assign_payload, timeout=20)
                data = resp.json()
                if str(data.get("status")) == "1":
                    log.info(f"  ✓ Subscriber metadata synced on {phone_number_id}: {custom_fields}")
                    return True
                last_err = data.get("message")
                log.warning(f"  ⚠️ Custom field sync attempt {attempt + 1}/3 non-success: {last_err}")
            except Exception as e:
                last_err = str(e)
                log.warning(f"  ⚠️ Custom field sync attempt {attempt + 1}/3 error: {e}")
        log.error(f"  ✗ Subscriber sync failed after 3 attempts: {last_err}")
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

    # Pick brand from explicit prefix arg, else infer from order_id.
    # Try 2-char prefix first, then 1-char (e.g. Rimal "R").
    src = brand_prefix or order_id or ""
    prefix = src[:2] if src[:2] in BRAND_CONFIG else src[:1]
    cfg = BRAND_CONFIG.get(prefix)
    if not cfg:
        log.error(f"No BRAND_CONFIG entry for prefix '{prefix}' (order_id={order_id}) — skipping")
        return False

    phone_number_id   = cfg["phone_number_id"]
    template_id       = cfg["template_id"]
    confirm_button_qr = cfg["confirm_button_qr"]
    display_brand     = brand_name or cfg["brand_display"]

    # Normalize phone and validate as an E.164 number (UAE or international)
    cleaned_phone = clean_phone_number(phone_number)
    if not is_valid_msisdn(cleaned_phone):
        log.error(
            f"Phone '{phone_number}' failed normalization "
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
        "templateVariable-brand-2": clean_template_param(display_brand),
        "templateVariable-id-3":    clean_template_param(order_id),
        "templateVariable-name-1":  clean_template_param(customer_name),

        # Button values — order MUST match the template's button order, or the confirm
        # tap fires the chat-with-human flow (and vice versa). No spaces after comma
        # per WhatChimp API.
        #   legacy templates : [Chat with human, Confirm]
        #   new 442127       : [Process(confirm), Reschedule(chat with human)]  -> confirm_first
        "template_quick_reply_button_values": json.dumps(
            [confirm_button_qr, "YES_START_CHAT_WITH_HUMAN"]
            if cfg.get("confirm_first")
            else ["YES_START_CHAT_WITH_HUMAN", confirm_button_qr],
            separators=(',', ':'),
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

# ---------------------------------------------------------------------------
# "Pay By Link" payment template (Stripe checkout link). A SEPARATE Meta template
# from the COD confirmation, sent when a customer picks the "Pay By Link" payment
# method at checkout instead of Cash on Delivery.
#
# Template variables are filled at SEND time (same mechanism as the
# abandoned-checkout recovery template): tags fill positionally in body order.
#
# 442126 "payment_link_all" is the COLLECTIVE template that replaced the
# Amara-only 428721 "payment_link_amara". The old one was created under the
# retired portfolio, so the new sender numbers cannot see it at all -- it fails
# as `template name (payment_link_amara) does not exist in en_US`.
#
# The new body inserts a brand line, which pushed every later tag down one:
#
#   Hi #User-Name#,                         -> {{1}}, filled by WhatChimp from
#                                              the subscriber, not passed here
#   Thank you for shopping with #!brand!#.  -> templateVariable-brand-2
#   Amount: AED #!amount!#                  -> templateVariable-amount-3
#   Pay securely here: #!url!#              -> templateVariable-url-4
#
# Only the POSITION binds to {{N}}; the label between the dashes is cosmetic.
# template_id is WhatChimp's INTERNAL id; phone_number_id reuses the brand's
# confirmation sender number (BRAND_CONFIG).
#
# Every brand in the portfolio is listed because the template is collective and
# carries the brand as a variable. What actually gates sending is PAY_LINK_BRANDS
# in .env (Amara today) together with the store having the "Pay By Link" payment
# method enabled in Shopify -- so listing a brand here turns nothing on by
# itself, it only means the routing exists when that gate opens.
# ---------------------------------------------------------------------------
_PAY_LINK_TEMPLATE_ALL = os.getenv("WHATCHIMP_PAYLINK_TEMPLATE_ALL", "442126")

PAY_LINK_CONFIG = {
    prefix: {"template_id": _PAY_LINK_TEMPLATE_ALL}
    # The nine on template 442127 — i.e. the live portfolio. Note VS is Viresta;
    # VX is Virex, which is retired, and must not be here.
    for prefix in ("AM", "AW", "SQ", "WG", "LU", "O", "VL", "VS", "R")
}


def get_pay_link_config(order_id_or_prefix: str) -> dict | None:
    """Resolve pay-by-link routing (pnid + payment template_id) or None."""
    prefix = (order_id_or_prefix or "")[:2]
    cfg = PAY_LINK_CONFIG.get(prefix)
    brand = BRAND_CONFIG.get(prefix)
    if not cfg or not brand:
        return None
    return {
        "phone_number_id": brand["phone_number_id"],
        "template_id": cfg["template_id"],
        "brand_display": brand["brand_display"],
    }


def send_payment_link_template(
    phone_number: str,
    customer_name: str,
    order_id: str,
    amount: str,
    pay_url: str,
    brand_prefix: str = "",
) -> bool:
    """Send the 'Pay By Link' template carrying a Stripe checkout link.

    Ensures the subscriber exists (name set) so #User-Name# resolves, then sends
    template PAY_LINK_CONFIG[prefix] with brand + amount + url variables.
    Returns True only on WhatChimp status == "1".
    """
    if not WHATCHIMP_API_TOKEN:
        log.error("Missing WHATCHIMP_API_TOKEN in .env")
        return False

    prefix = (brand_prefix or order_id or "")[:2]
    cfg = get_pay_link_config(prefix)
    if not cfg:
        log.error(f"No PAY_LINK_CONFIG for prefix '{prefix}' (order {order_id}) - skipping paylink")
        return False

    phone_number_id = cfg["phone_number_id"]
    template_id = cfg["template_id"]
    display_brand = cfg["brand_display"]

    cleaned_phone = clean_phone_number(phone_number)
    if not cleaned_phone.startswith("971") or len(cleaned_phone) != 12:
        log.error(
            f"Phone '{phone_number}' failed UAE normalization "
            f"(got '{cleaned_phone}') - skipping paylink {order_id}"
        )
        return False

    # Ensure subscriber exists + name set so #User-Name# resolves. Also syncs
    # order_id/brand custom fields (harmless; matches the confirmation flow).
    create_or_update_subscriber(phone_number, customer_name, order_id, display_brand, phone_number_id)

    url = f"{API_BASE}/send/template"
    payload = {
        "apiToken":        WHATCHIMP_API_TOKEN,
        "phone_number_id": phone_number_id,
        "template_id":     template_id,
        "phone_number":    cleaned_phone,
        # Positions, not labels, bind to {{N}}. {{1}} is #User-Name#, which
        # WhatChimp fills from the subscriber synced just above; it is passed
        # anyway so the greeting still renders if that lookup ever comes back
        # empty, exactly as the confirmation sender does.
        "templateVariable-name-1":   clean_template_param(customer_name),
        "templateVariable-brand-2":  clean_template_param(display_brand),
        "templateVariable-amount-3": clean_template_param(amount),
        "templateVariable-url-4":    clean_template_param(pay_url),
    }
    try:
        log.info(
            f"Paylink template {template_id} -> {cleaned_phone} via {phone_number_id} "
            f"({display_brand}, order {order_id})"
        )
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        if str(data.get("status")) == "1":
            log.info(f"Paylink delivered: {order_id}")
            return True
        log.error(f"Paylink rejected ({display_brand}): {data.get('message', data)}")
        return False
    except Exception as e:
        log.error(f"Paylink request failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("WhatChimp DEFINITIVE Client Ready.")
