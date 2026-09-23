"""
grq_os_leads.py
===============
The leads bot's reporter for GRQ OS.

`rpgrq_webhook_server.py` sees every WhatsApp message across the stores. Two
of them matter here: a customer writing in, and an agent writing back.

    inbound(phone, brand, agent)   a customer messaged. Creates the
                                   conversation if it is new, otherwise moves
                                   the clock.
    outbound(phone, brand, agent)  an agent replied.

Fire-and-forget, deliberately, and this is the opposite choice from
`grq_os_work.py`. There, GRQ OS *is* the work list, so a failed call has to be
loud. Here Notion is still the system of record and GRQ OS is the copy: a
problem on this side must never stop a lead being captured, exactly like
`grq_os_ingest`. Every failure returns False and is logged.

Inert unless GRQ_OS_URL and GRQ_OS_INGEST_SECRET are both set.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

import httpx

log = logging.getLogger("rpgrq.grq_os")

# Read config at call time, not at import: a module imported before
# load_dotenv runs would be silently inert, and inert looks exactly like
# "nothing to do".


def _url() -> str:
    return os.getenv("GRQ_OS_URL", "").strip().rstrip("/")


def _secret() -> str:
    return os.getenv("GRQ_OS_INGEST_SECRET", "").strip()


def _bypass() -> str:
    return os.getenv("GRQ_OS_BYPASS", "").strip()


TIMEOUT = float(os.getenv("GRQ_OS_TIMEOUT", "15"))


def configured() -> bool:
    return bool(_url() and _secret())


async def _post(client: httpx.AsyncClient, payload: dict) -> bool:
    if not configured():
        return False
    raw = json.dumps(payload, ensure_ascii=False)
    # Sign the exact bytes sent: an Arabic customer name 401s otherwise.
    sig = hmac.new(_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-grq-signature": sig}
    if _bypass():
        headers["x-vercel-protection-bypass"] = _bypass()
    try:
        res = await client.post(
            f"{_url()}/api/ingest/leads",
            content=raw.encode("utf-8"),
            headers=headers,
            timeout=TIMEOUT,
        )
    except Exception as e:
        log.warning("GRQ OS leads %s failed: %s", payload.get("action"), e)
        return False

    if res.status_code == 200:
        return True

    # 422 is a store GRQ OS has never heard of - the support lines
    # (Customer Care, Shopping Assistance, Shopping Care) and retired shops.
    # Worth one line so it can be counted, not worth an error.
    level = log.info if res.status_code == 422 else log.warning
    level("GRQ OS leads %s: %s %s", payload.get("action"), res.status_code, res.text[:160])
    return False


async def inbound(
    client: httpx.AsyncClient,
    phone: str,
    brand: str,
    agent: str | None = None,
    name: str | None = None,
    at: str | None = None,
) -> bool:
    """A customer messaged. `brand` is the store's name, as the bot resolves it."""
    return await _post(client, {
        "action": "inbound", "phone": phone, "brand_code": brand,
        "agent": agent, "name": name, "at": at, "source": "whatsapp",
    })


async def outbound(
    client: httpx.AsyncClient,
    phone: str,
    brand: str,
    agent: str | None = None,
    at: str | None = None,
) -> bool:
    """An agent replied. Never creates a conversation."""
    return await _post(client, {
        "action": "outbound", "phone": phone, "brand_code": brand,
        "agent": agent, "at": at,
    })
