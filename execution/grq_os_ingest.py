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


def courier(payload: dict) -> bool:
    return _post("/api/ingest/courier", payload)
