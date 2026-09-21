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

import httpx

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


def _post(path: str, payload: dict) -> dict | None:
    if not configured():
        return None
    raw = json.dumps(payload, ensure_ascii=False)
    # Sign the exact bytes sent: an Arabic customer name 401s otherwise.
    sig = hmac.new(_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-grq-signature": sig}
    if _bypass():
        headers["x-vercel-protection-bypass"] = _bypass()
    try:
        res = httpx.post(f"{_url()}/api/ingest/{path}", content=raw.encode("utf-8"),
                         headers=headers, timeout=TIMEOUT)
    except Exception as e:
        log.error("GRQ OS %s/%s failed: %s", path, payload.get("action"), e)
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


def supersede(order_id: str, courier: str, tracking: str | None = None, status: str | None = None) -> bool:
    """The parcel changed hands. One way: TJR does not go back to Filex."""
    return _post("labels", {
        "action": "supersede", "order_id": order_id, "courier": courier,
        "tracking": tracking, "status": status,
    }) is not None


# ---------------------------------------------------------------------------
# Messages to customers
# ---------------------------------------------------------------------------

def claim_ofd(limit: int = 50, grace_minutes: int = 2, worker: str = "grq-ofd") -> list[dict]:
    body = _post("notify", {"action": "claim_ofd", "limit": limit,
                            "grace_minutes": grace_minutes, "worker": worker})
    return (body or {}).get("orders") or []


def mark_ofd_sent(order_id: str, template: str | None = None) -> bool:
    return _post("notify", {"action": "ofd_sent", "order_id": order_id, "template": template}) is not None


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


def release_confirmation(order_id: str) -> bool:
    return _post("notify", {"action": "release", "order_id": order_id}) is not None
