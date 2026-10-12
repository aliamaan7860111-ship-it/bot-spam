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

import grq_os_db as db

log = logging.getLogger("rpgrq.grq_os")

"""
Each action, and the RPC the Vercel route would have called for it.

The route is the specification: same function, same argument name, same
meaning for a null answer. Anything not in here still goes over HTTP, so a
new action cannot be silently half-migrated.
"""
_DIRECT = {
    "inbound": ("upsert_lead", lambda p: {"p": p}),
    "outbound": ("touch_lead_reply", lambda p: {"p": p}),
}

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


async def _try_direct(client: httpx.AsyncClient, payload: dict) -> tuple[bool, bool]:
    """
    (handled, ok). `handled` is False when this one still needs Vercel.

    A refusal from the database is a refusal either way, so it comes back as
    False exactly as a non-200 from the endpoint would. This path stays
    fire-and-forget for the same reason the endpoint path is: Notion is the
    system of record and GRQ OS is the copy, so a problem here must never
    stop a lead being captured.
    """
    entry = _DIRECT.get(payload.get("action", ""))
    if entry is None or not db.enabled():
        return False, False
    fn, args = entry
    try:
        await db.arpc(client, fn, args(payload))
        return True, True
    except db.Failed as e:
        # A store GRQ OS has never heard of - the support lines and retired
        # shops - is worth one line so it can be counted, not a warning. The
        # endpoint says the same thing with a 422.
        level = log.info if "no store" in str(e) else log.warning
        level("GRQ OS leads %s direct: %s", payload.get("action"), e)
        return True, False


async def _post(client: httpx.AsyncClient, payload: dict) -> bool:
    handled, ok = await _try_direct(client, payload)
    if handled:
        return ok

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


def _now_iso() -> str:
    """The route defaults `at` to now; doing it here keeps both roads equal."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _as_agent(a: dict | None) -> dict | None:
    """
    A roster row in the shape the bot already expects.

    Shared by both transports so they cannot drift: the whole value of moving
    off the endpoint is lost if the two answers differ in a field name.
    """
    if not a:
        log.error("Nobody is on shift and nobody is coming - check the rota")
        return None
    return {
        "name": a.get("name"),
        "team_member_id": a.get("whatchimp_member_id"),
        "shift_start": a.get("starts_hour"),
        "shift_end": a.get("ends_hour"),
        "off_days": a.get("off_days") or [],
    }


async def _next_agent_direct(client: httpx.AsyncClient, at: str) -> dict | None:
    """
    What the route does, in two calls: whose turn, then who that is.

    Returns the roster row, or None - which covers both "nobody is on" and
    "the database could not be reached". The caller logs the first; the
    second is logged here, so the two are told apart in the log even though
    the bot treats them the same way.
    """
    try:
        staff_id = await db.arpc(client, "next_lead_agent", {"p_at": at})
        if not staff_id:
            return None
        rows = await db.arpc(client, "agent_roster", {"p_at": at}) or []
        return next((r for r in rows if r.get("staff_id") == staff_id), None)
    except db.Failed as e:
        log.error("GRQ OS could not say whose turn it is: %s", e)
        return None


async def next_agent(client: httpx.AsyncClient, at: str | None = None) -> dict | None:
    """
    Whose turn it is, decided by the rota in GRQ OS.

    Returns the bot's own shape - {name, team_member_id, shift_start,
    shift_end} - so nothing downstream has to change. None means GRQ OS
    could not be reached, or genuinely nobody is on and nobody is coming.

    The rule itself lives in the database: shifts that cross midnight, off
    days that belong to the day the shift started, and the gaps between
    shifts going to whoever opens next.
    """
    if db.enabled():
        return _as_agent(await _next_agent_direct(client, at or _now_iso()))

    if not configured():
        return None
    payload = {"action": "next_agent", "at": at}
    raw = json.dumps(payload, ensure_ascii=False)
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
        log.error("GRQ OS could not say whose turn it is: %s", e)
        return None
    if res.status_code != 200:
        log.error("GRQ OS next_agent: %s %s", res.status_code, res.text[:160])
        return None

    return _as_agent((res.json() or {}).get("agent"))


async def inbound(
    client: httpx.AsyncClient,
    phone: str,
    brand: str,
    agent: str | None = None,
    name: str | None = None,
    at: str | None = None,
    bot_id: str | None = None,
    phone_number_id: str | None = None,
) -> bool:
    """
    A customer messaged. `brand` is the store's name, as the bot resolves it.

    `bot_id` and `phone_number_id` say which of OUR numbers it landed on, which
    is what decides whether this is a checkout enquiry or a campaign lead.
    Both are sent because WhatChimp sends one or the other depending on the
    bot. Neither is a brand: a store can have both kinds of number.
    """
    return await _post(client, {
        "action": "inbound", "phone": phone, "brand_code": brand,
        "agent": agent, "name": name, "at": at, "source": "whatsapp",
        "bot_id": bot_id, "phone_number_id": phone_number_id,
    })


async def outbound(
    client: httpx.AsyncClient,
    phone: str,
    brand: str,
    agent: str | None = None,
    at: str | None = None,
    bot_id: str | None = None,
    phone_number_id: str | None = None,
) -> bool:
    """
    An agent replied. Never creates a conversation.

    The number matters here too: a customer can have a checkout conversation
    and a campaign one with the same store, and stamping the reply on the
    wrong one would mark one answered and leave the other waiting forever.
    """
    return await _post(client, {
        "action": "outbound", "phone": phone, "brand_code": brand,
        "agent": agent, "at": at,
        "bot_id": bot_id, "phone_number_id": phone_number_id,
    })
