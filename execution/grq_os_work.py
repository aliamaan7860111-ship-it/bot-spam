"""
grq_os_work.py
==============
Where the automations get their work, once it is GRQ OS and not Notion.

Three groups of calls, matching the three endpoints:

    labels      /print all, /print <ID>, /pvt all
    notify      the out-for-delivery message and the confirmation template
    courier     Filex status in  (this one already existed: /api/ingest/courier)

All signed with the shared ingest secret, the same as every other automation
on this box. None of them are fire-and-forget: unlike grq_os_ingest, where a
GRQ OS problem must never become a Notion capture outage, here GRQ OS IS the
work list. A failed claim means no work; a failed checkpoint means a message
could go twice, and the caller has to know.

Inert unless GRQ_OS_URL and GRQ_OS_INGEST_SECRET are both set.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import httpx

import grq_os_db as db

log = logging.getLogger("grq_os.work")

# Config is read at call time, not at import. Reading it at import makes the
# module silently inert whenever it is imported before load_dotenv runs - and
# inert looks exactly like "nothing to do". That is the same shape as the
# grq-ac bug where empty config quietly turned a signature check off.


def _url() -> str:
    return os.getenv("GRQ_OS_URL", "").strip().rstrip("/")


def _secret() -> str:
    return os.getenv("GRQ_OS_INGEST_SECRET", "").strip()


def _bypass() -> str:
    return os.getenv("GRQ_OS_BYPASS", "").strip()


TIMEOUT = float(os.getenv("GRQ_OS_TIMEOUT", "30"))


def configured() -> bool:
    return bool(_url() and _secret())



# A dropped connection is worth another go; an HTTP answer is not. Only the
# transport failures are retried - httpx.TransportError covers timeouts,
# connect/read/write errors and protocol errors, which is where the TLS
# handshake timeout lands.
_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5


def _post_with_retry(url: str, raw: bytes, headers: dict, what: str):
    """
    POST, retrying only when the request never got an answer.

    Returns the response, or None when every attempt failed in transit.
    Safe to retry: every endpoint these clients call is idempotent, and the
    calls that matter most run after a message has already been sent - the
    one case where giving up quietly causes a customer to be messaged twice.
    """
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return httpx.post(url, content=raw, headers=headers, timeout=TIMEOUT)
        except httpx.TransportError as e:
            if attempt == _ATTEMPTS:
                log.error("GRQ OS %s failed after %d attempts: %s", what, _ATTEMPTS, e)
                return None
            log.warning("GRQ OS %s did not get through (%s); retrying %d/%d",
                        what, e, attempt + 1, _ATTEMPTS)
            time.sleep(_BACKOFF_SECONDS * attempt)
        except Exception as e:
            # Not a transport problem - repeating it will not help.
            log.error("GRQ OS %s failed: %s", what, e)
            return None
    return None




# ---------------------------------------------------------------------------
# Straight to the database
# ---------------------------------------------------------------------------
#
# Each entry turns one endpoint action into the function call the endpoint
# would have made, and rebuilds the envelope it would have returned. Same
# RPC, same parameter names, same defaults - read off the route so the two
# cannot disagree.
#
# An action missing from here goes the long way round, which is right for
# anything that is more than a function call.

def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v if v is not None else default)))
    except (TypeError, ValueError):
        return default


_NOTIFY_DIRECT = {
    "claim_ofd": lambda p: (
        "claim_for_ofd",
        {"p_limit": _clamp(p.get("limit"), 0, 200, 50),
         "p_grace_minutes": p.get("grace_minutes", 2),
         "p_worker": p.get("worker") or "grq-ofd",
         "p_stale_minutes": p.get("stale_minutes", 10)},
        lambda d: {"orders": d},
    ),
    "claim_confirmation": lambda p: (
        "claim_for_confirmation",
        {"p_limit": _clamp(p.get("limit"), 0, 100, 20),
         "p_worker": p.get("worker") or "whatsapp-bot",
         "p_max_age_hours": p.get("max_age_hours", 24),
         "p_stale_minutes": p.get("stale_minutes", 10)},
        lambda d: {"orders": d},
    ),
    "ofd_sent": lambda p: (
        "mark_ofd_sent",
        {"p_order": p.get("order_id"), "p_template": p.get("template")},
        lambda d: {"ok": True},
    ),
    "release_ofd": lambda p: (
        "release_ofd",
        {"p_order": p.get("order_id"), "p_reason": p.get("reason")},
        lambda d: {"ok": True},
    ),
    "confirmation_sent": lambda p: (
        "mark_confirmation_sent",
        {"p_order": p.get("order_id"), "p_template": p.get("template")},
        lambda d: {"ok": True},
    ),
    "block": lambda p: (
        "block_confirmation",
        {"p_order": p.get("order_id"), "p_reason": p.get("reason")},
        lambda d: {"ok": True},
    ),
    "release": lambda p: (
        "release_confirmation",
        {"p_order": p.get("order_id")},
        lambda d: {"ok": True},
    ),
}

_FULFILMENT_DIRECT = {
    "claim": lambda p: (
        "claim_for_fulfilment",
        {"p_limit": _clamp(p.get("limit"), 0, 100, 20),
         "p_worker": p.get("worker") or "order-bridge",
         "p_stale_minutes": p.get("stale_minutes", 10)},
        lambda d: {"orders": d},
    ),
    "albums": lambda p: (
        "record_albums_sent",
        {"p_order": p.get("order_id"), "p_sent": p.get("sent"),
         "p_message_id": p.get("message_id")},
        lambda d: {"albums_sent": d},
    ),
    "finish": lambda p: (
        "finish_fulfilment",
        {"p_order": p.get("order_id"), "p_total_albums": p.get("total_albums")},
        lambda d: {"ok": True},
    ),
    "release": lambda p: (
        "release_fulfilment",
        {"p_order": p.get("order_id"), "p_reason": p.get("reason")},
        lambda d: {"ok": True},
    ),
}

_DIRECT = {"notify": _NOTIFY_DIRECT, "fulfilment": _FULFILMENT_DIRECT}


def _try_direct(path: str, payload: dict):
    """
    (handled, body). `handled` is False when this one still needs Vercel.

    A refusal from the database is a refusal either way, so it comes back
    as None exactly as a 409 from the endpoint would.
    """
    if not db.enabled():
        return False, None
    entry = _DIRECT.get(path, {}).get(payload.get("action"))
    if entry is None:
        return False, None
    fn, args, envelope = entry(payload)
    try:
        return True, envelope(db.rpc(fn, args))
    except db.Failed as e:
        log.error("GRQ OS %s/%s direct: %s", path, payload.get("action"), e)
        return True, None


def _post(path: str, payload: dict) -> dict | None:
    handled, body = _try_direct(path, payload)
    if handled:
        return body

    if not configured():
        return None
    raw = json.dumps(payload, ensure_ascii=False)
    # Sign the exact bytes sent: an Arabic customer name 401s otherwise.
    sig = hmac.new(_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-grq-signature": sig}
    if _bypass():
        headers["x-vercel-protection-bypass"] = _bypass()
    res = _post_with_retry(f"{_url()}/api/ingest/{path}", raw.encode("utf-8"), headers,
                           f"{path}/{payload.get('action')}")
    if res is None:
        return None
    if res.status_code == 200:
        return res.json()
    # 409 is a refusal rather than a fault - a delivered parcel cannot be
    # relabelled, a half-sent order cannot be finished. Worth the caller
    # seeing, not worth an error-level log.
    level = log.info if res.status_code == 409 else log.error
    level("GRQ OS %s/%s returned %s: %s", path, payload.get("action"), res.status_code, res.text[:200])
    return None


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

def labelling_board(limit: int = 200) -> dict:
    """What /print all is looking at: to_place, already_labelled, and a count."""
    return _post("labels", {"action": "board", "limit": limit}) or {
        "to_place": [], "already_labelled": [], "private_driver_waiting": 0
    }


def private_board(limit: int = 200) -> list[dict]:
    """What /pvt all is looking at: ticked, and not yet handed over."""
    return (_post("labels", {"action": "private", "limit": limit}) or {}).get("orders") or []


def label_one(order_code: str) -> dict:
    """
    What /print <ID> should do with one order, in a word.

    `place`, `refetch`, `private_driver` or `not_processed`. `refetch` means
    the label already exists: go and get the PDF, do not buy another.
    """
    return _post("labels", {"action": "one", "order_code": order_code}) or {"found": False}


def begin_label_run(order_ids: list[str], worker: str = "print-all") -> str | None:
    """The Filex Submitted lock, with an owner. Returns the run id."""
    return (_post("labels", {"action": "begin", "order_ids": order_ids, "worker": worker}) or {}).get("run_id")


def end_label_run(run_id: str) -> bool:
    return _post("labels", {"action": "end", "run_id": run_id}) is not None


def record_labels(labels: list[dict]) -> bool:
    """
    Tracking came back. Each entry is {tracking_no, order_ids} because Filex
    merges a customer's orders from one store onto a single shipment.
    """
    return _post("labels", {"action": "labels", "labels": labels}) is not None


def parcels_in_flight(within_days: int = 14, courier: str = "filex") -> list[dict]:
    """
    Every parcel still out with the courier, for the reconciler to ask about.

    Delivered, returned and cancelled parcels are excluded on the far side -
    re-asking about a finished one is how a stale event un-delivers an order.
    """
    body = _post("labels", {"action": "in_flight", "within_days": within_days, "courier": courier})
    return (body or {}).get("orders") or []


def parcels_stuck(hours: int = 24, courier: str = "filex") -> list[dict]:
    """Label bought, never scanned. The parcel is on somebody's desk."""
    body = _post("labels", {"action": "stuck", "hours": hours, "courier": courier})
    return (body or {}).get("orders") or []


def to_filex_order(row: dict) -> dict:
    """
    A GRQ OS label-board row in the shape the Filex payload builder wants.

    The builder was written against `notion_client.parse_order`, and it stays
    that way: it is the piece that knows Filex's rules about pieces, cities
    and string lengths, and rewriting it to take a second shape would mean
    two things to keep in step.

    Three things need care:

    * The address. Filex needs a city and `normalize_city` digs it out of the
      address text, so the city column is appended when the line does not
      already contain it. Otherwise a perfectly good Abu Dhabi order is
      skipped as "missing city in address".

    * The total. An order whose total is not known is passed through as None
      on purpose, so the builder refuses it and it appears in the skip list
      with a reason. Sending it as 0 would print a label that collects
      nothing at the door, and nobody would find out until the driver did.

    * `page_id`. The batch keys its lock set and its reply threads on this.
      It is the GRQ OS id now, and nothing downstream sends it to Notion.
    """
    address = (row.get("address") or "").strip()
    city = (row.get("city") or "").strip()
    if city and city.lower() not in address.lower():
        address = f"{address}, {city}" if address else city

    return {
        "order_id":          row.get("order_code"),
        "page_id":           row.get("order_id"),
        "grq_os_order_id":   row.get("order_id"),
        "customer_name":     row.get("customer") or "",
        "phone":             row.get("phone") or "",
        "full_address":      address,
        "total_aed":         row.get("total") if row.get("total_known") else None,
        "item_qty":          row.get("item_qty") or "",
        "internal_note":     row.get("internal_note") or "",
        "fulfillment_message_id": row.get("fulfilment_message_id"),
        # Empty means unlabelled, which is what `to_place` already promises.
        "filex_status":      "",
    }


def supersede(order_id: str, courier: str, tracking: str | None = None, status: str | None = None) -> bool:
    """The parcel changed hands. One way: TJR does not go back to Filex."""
    return _post("labels", {
        "action": "supersede", "order_id": order_id, "courier": courier,
        "tracking": tracking, "status": status,
    }) is not None


# ---------------------------------------------------------------------------
# Messages to customers
# ---------------------------------------------------------------------------

def claim_ofd(limit: int = 50, grace_minutes: int = 2, worker: str = "grq-ofd",
              stale_minutes: int = 10) -> list[dict]:
    """
    Orders whose customer has not been told, TAKEN so nobody else takes them.

    This used to be a read dressed up as a claim: it returned rows and wrote
    nothing, so the only thing stopping a second message was `ofd_sent_at`,
    stamped after the WhatsApp had already gone. The claim is real now and
    expires on its own if the send never finishes.
    """
    body = _post("notify", {"action": "claim_ofd", "limit": limit,
                            "grace_minutes": grace_minutes, "worker": worker,
                            "stale_minutes": stale_minutes})
    return (body or {}).get("orders") or []


def mark_ofd_sent(order_id: str, template: str | None = None) -> bool:
    return _post("notify", {"action": "ofd_sent", "order_id": order_id, "template": template}) is not None


def release_ofd(order_id: str, reason: str | None = None) -> bool:
    """Hand an order back when the message did not go out."""
    return _post("notify", {"action": "release_ofd", "order_id": order_id,
                            "reason": reason}) is not None


def claim_confirmation(limit: int = 20, worker: str = "whatsapp-bot",
                       max_age_hours: int = 24, stale_minutes: int = 10) -> list[dict]:
    body = _post("notify", {"action": "claim_confirmation", "limit": limit, "worker": worker,
                            "max_age_hours": max_age_hours, "stale_minutes": stale_minutes})
    return (body or {}).get("orders") or []


def mark_confirmation_sent(order_id: str, template: str | None = None) -> bool:
    return _post("notify", {"action": "confirmation_sent", "order_id": order_id, "template": template}) is not None


def block_confirmation(order_id: str, reason: str) -> bool:
    """For a failure that can never succeed, so the poller stops reconsidering it."""
    return _post("notify", {"action": "block", "order_id": order_id, "reason": reason}) is not None


def reset_fulfilment(order_code: str, note: str | None = None) -> tuple[bool, str]:
    """
    Undo sending an order to the fulfilment group, so the album can go again.

    Returns (ok, message) and the message is the REAL one. Every refusal
    from this endpoint is a sentence written for the person who typed the
    command - "that parcel is already with Filex" is the answer, not a
    failure to report one. The first version threw the body away and said
    "see the log", which sent the operator looking for a log on a box they
    have no shell on.

    It does not go through `_post` for that reason: `_post` logs the body
    and returns None, which is right for fire-and-forget and wrong here.
    """
    if not configured():
        return False, "this bot has no GRQ OS credentials"
    payload = {"action": "reset", "order_code": order_code, "note": note}
    raw = json.dumps(payload, ensure_ascii=False)
    sig = hmac.new(_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-grq-signature": sig}
    if _bypass():
        headers["x-vercel-protection-bypass"] = _bypass()
    try:
        res = httpx.post(f"{_url()}/api/ingest/fulfilment", content=raw.encode("utf-8"),
                         headers=headers, timeout=TIMEOUT)
    except Exception as e:
        log.error("GRQ OS reset failed for %s: %s", order_code, e)
        return False, f"could not reach GRQ OS: {e}"
    if res.status_code == 200:
        return True, "reset"
    try:
        why = res.json().get("error") or res.text[:160]
    except Exception:
        why = res.text[:160]
    log.info("GRQ OS refused the reset of %s: %s", order_code, why)
    return False, why


def notion_link(order_code: str, page_id: str) -> bool:
    """Record which Notion page an order became, so the mirror matches on an
    id rather than on an order code somebody might retype."""
    return _post("notion", {"action": "link", "order_code": order_code, "page_id": page_id}) is not None


def customer_confirmed(order_code: str, note: str | None = None) -> bool:
    """
    The customer pressed Confirm on the template.

    By order code, because the webhook is holding what WhatChimp sent back
    and that is the only identifier both systems agree on. Recorded as a
    timeline event and nothing more - whether a press is a confirmation is
    the agent's call.
    """
    return _post("notify", {
        "action": "customer_confirmed", "order_code": order_code, "note": note,
    }) is not None


def release_confirmation(order_id: str) -> bool:
    return _post("notify", {"action": "release", "order_id": order_id}) is not None
