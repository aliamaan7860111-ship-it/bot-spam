"""
grq_os_db.py
============
Talking to the database directly, instead of through a serverless function.

Every poll used to be four hops: bot -> Vercel function -> verify an HMAC ->
Supabase RPC -> and back. The middle two do nothing the database cannot do
itself, and they cost Fluid Active CPU on every single call. At 29,000 calls
a day that was the entire monthly free allowance, and Vercel's answer to
exceeding it is to PAUSE the project - which would take the CRM down along
with every endpoint these bots depend on.

PostgREST serves the same functions over HTTP at /rest/v1/rpc/<name>. They
are already `security definer` and already granted to the service role, so
nothing about them changes; the request simply stops going the long way
round.

What stays on Vercel
--------------------
Anything that is not a plain function call. `photos` downloads images from
Notion and writes them to storage - real server work, and it runs once per
order rather than twice a minute. The webhook endpoints stay too: Shopify
and Filex post to them from outside and they must keep a public, signed
door.

On credentials
--------------
This uses the service role key, which bypasses RLS. That is a real increase
over the HMAC the bots held before: the ingest endpoints let them write
specific things, whereas this key can read and write anything. It is the
same trust boundary - the VM could already create and modify orders - but
it is wider, and worth saying out loud rather than burying.

The narrower version is a dedicated Postgres role granted EXECUTE on just
these functions, reached with a JWT signed by the project secret. That is
the better answer and it needs the JWT secret, which is not on this box.

On failure
----------
Only transport failures retry, for the same reason as everywhere else: a
dropped connection deserves another go, an HTTP status is an answer. The
calls that matter run after a customer has already been messaged, and
losing one of those silently is how somebody gets told twice.
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx

log = logging.getLogger("grq_os.db")

_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.5


def _url() -> str:
    return os.getenv("GRQ_OS_DB_URL", "").strip().rstrip("/")


def _key() -> str:
    return os.getenv("GRQ_OS_DB_KEY", "").strip()


def timeout() -> float:
    return float(os.getenv("GRQ_OS_DB_TIMEOUT", "20"))


def enabled() -> bool:
    """Whether to go straight to the database. Both halves or neither."""
    return bool(_url() and _key())


def _headers() -> dict:
    k = _key()
    return {
        "apikey": k,
        "Authorization": f"Bearer {k}",
        "Content-Type": "application/json",
    }


class Failed(Exception):
    """The call did not complete. Distinct from a function returning null."""


def rpc(name: str, args: dict | None = None):
    """
    Call a Postgres function and return what it returned.

    Raises `Failed` when the call did not get through, which the callers
    turn back into whatever "it did not work" means for them. That
    distinction matters: `next_lead_agent` returning null means nobody is
    on shift, and must not read as an outage.
    """
    if not enabled():
        raise Failed("GRQ_OS_DB_URL / GRQ_OS_DB_KEY are not set")

    body = json.dumps(args or {}, ensure_ascii=False).encode("utf-8")
    url = f"{_url()}/rest/v1/rpc/{name}"

    for attempt in range(1, _ATTEMPTS + 1):
        try:
            res = httpx.post(url, content=body, headers=_headers(), timeout=timeout())
        except httpx.TransportError as e:
            if attempt == _ATTEMPTS:
                raise Failed(f"{name}: {e}") from e
            log.warning("rpc %s did not get through (%s); retrying %d/%d",
                        name, e, attempt + 1, _ATTEMPTS)
            time.sleep(_BACKOFF_SECONDS * attempt)
            continue
        except Exception as e:
            raise Failed(f"{name}: {e}") from e

        if res.status_code in (200, 201, 204):
            if not res.content:
                return None
            try:
                return res.json()
            except Exception:
                return None

        # A refusal is an answer. `reset_fulfilment` says "already with
        # Filex"; repeating the question will not change it.
        raise Failed(f"{name}: {res.status_code} {res.text[:200]}")

    raise Failed(f"{name}: exhausted attempts")


async def arpc(client: httpx.AsyncClient, name: str, args: dict | None = None):
    """The same thing on an existing async client, for the leads webhook."""
    if not enabled():
        raise Failed("GRQ_OS_DB_URL / GRQ_OS_DB_KEY are not set")

    body = json.dumps(args or {}, ensure_ascii=False).encode("utf-8")
    url = f"{_url()}/rest/v1/rpc/{name}"

    for attempt in range(1, _ATTEMPTS + 1):
        try:
            res = await client.post(url, content=body, headers=_headers(), timeout=timeout())
        except httpx.TransportError as e:
            if attempt == _ATTEMPTS:
                raise Failed(f"{name}: {e}") from e
            log.warning("rpc %s did not get through (%s); retrying %d/%d",
                        name, e, attempt + 1, _ATTEMPTS)
            continue
        except Exception as e:
            raise Failed(f"{name}: {e}") from e

        if res.status_code in (200, 201, 204):
            if not res.content:
                return None
            try:
                return res.json()
            except Exception:
                return None
        raise Failed(f"{name}: {res.status_code} {res.text[:200]}")

    raise Failed(f"{name}: exhausted attempts")
