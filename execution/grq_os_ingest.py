"""
Fire-and-forget client for GRQ OS ingestion.

Called from the live services that capture orders into Notion. Every failure
path returns False and logs a warning. This module must never raise, or a GRQ
OS problem becomes a Notion capture outage.

Disabled unless GRQ_OS_URL and GRQ_OS_INGEST_SECRET are both set, so it stays
inert until deliberately switched on.
"""

import hashlib
import hmac
import json
import logging
import os

import requests

try:  # the direct road (section 8a); absent or unset means the endpoint as before
    import grq_os_db as db
except Exception:  # noqa: BLE001
    db = None

log = logging.getLogger(__name__)

BASE_URL = os.getenv("GRQ_OS_URL", "").rstrip("/")
SECRET = os.getenv("GRQ_OS_INGEST_SECRET", "")
TIMEOUT = float(os.getenv("GRQ_OS_TIMEOUT", "8"))

# Vercel Deployment Protection guards the UI, because GRQ OS has no login of
# its own yet. Machines pass this bypass secret to reach the API. Without it
# every call gets Vercel's own 401 and never reaches our handler.
BYPASS = os.getenv("GRQ_OS_BYPASS", "")


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def _headers(raw: bytes) -> dict:
    h = {
        "Content-Type": "application/json",
        "X-GRQ-Signature": hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
    }
    if BYPASS:
        h["x-vercel-protection-bypass"] = BYPASS
    return h


def _post(path: str, payload: dict) -> bool:
    if not BASE_URL or not SECRET:
        return False

    try:
        # Sign the exact bytes that get sent. Signing the str and sending a
        # different encoding would 401 on every Arabic customer name.
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        raw = body.encode("utf-8")

        response = requests.post(
            f"{BASE_URL}{path}",
            data=raw,
            headers=_headers(raw),
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            log.warning(
                "GRQ OS %s returned %s: %s", path, response.status_code, response.text[:300]
            )
            return False
        return True
    except Exception:
        log.warning("GRQ OS %s failed", path, exc_info=True)
        return False


def order(payload: dict) -> bool:
    return _post("/api/ingest/order", payload)


def message(payload: dict) -> bool:
    return _post("/api/ingest/message", payload)


def _direct_enabled() -> bool:
    return db is not None and db.enabled()


def courier(payload: dict) -> bool:
    """
    A courier status from the Filex or TJR poller.

    Straight to Postgres when GRQ_OS_DB_URL is set (2026-10-26, Vercel's free
    allowance spent): ingest_courier_status (0178) translates the courier's
    wording exactly as the endpoint did and records it. A refusal - an
    unmapped status, an unknown order - is a refusal on either road, so it is
    not retried through Vercel.
    """
    if not _direct_enabled():
        return _post("/api/ingest/courier", payload)
    try:
        found = db.rpc("ingest_courier_status", {"p": payload})
    except Exception as e:  # noqa: BLE001 - never raise into a poller
        log.warning("GRQ OS courier (direct) %s: %s", payload.get("order_code"), e)
        return False
    if found is False:
        log.warning("GRQ OS courier (direct): unknown order %s", payload.get("order_code"))
        return False
    return True


def payment(payload: dict) -> bool:
    """
    Stripe says the customer paid.

    Idempotent on the far side by `reference`, so a Stripe retry is free.
    Straight to Postgres when GRQ_OS_DB_URL is set, with the same arguments
    the endpoint passed to ingest_payment.
    """
    if not _direct_enabled():
        return _post("/api/ingest/payment", payload)
    code = str(payload.get("order_code") or "").strip()
    if not code:
        log.warning("GRQ OS payment (direct): order_code is required")
        return False
    try:
        result = db.rpc("ingest_payment", {"p": {
            "order_code": code,
            "reference": payload.get("reference"),
            "amount": payload.get("amount"),
            "method": payload.get("method") or "stripe",
            "at": payload.get("at"),
        }})
    except Exception as e:  # noqa: BLE001
        log.warning("GRQ OS payment (direct) %s: %s", code, e)
        return False
    if not (result or {}).get("found"):
        log.warning("GRQ OS payment (direct): unknown order %s", code)
        return False
    return True
