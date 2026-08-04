"""
RPGRQ Leads Bot — webhook server.

Listens on port 8082 (env PORT). Endpoints:
  - GET  /               health check (200 OK "rpgrq up")
  - POST /rpgrq/incoming incoming-message webhook from WhatChimp
  - POST /rpgrq/outgoing outgoing-message webhook from WhatChimp

Entry point. Run with: python -m execution.rpgrq_webhook_server
"""

import os
import sys
import json
import logging
import asyncio
import signal
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs

import httpx
from dotenv import load_dotenv

# ── Path setup — make `from execution import ...` work ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from execution import rpgrq_notion as notion
from execution import rpgrq_whatchimp as wc
from execution import rpgrq_order_sync as order_sync
from execution.rpgrq_round_robin import RoundRobin, calculate_response_speed

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rpgrq")

PORT = int(os.getenv("RPGRQ_PORT", "8082"))

# grq-rescue tee: every message event is copied (fire-and-forget) to the 24h
# window rescue service on the same VM. Empty URL disables the tee entirely.
RESCUE_EVENTS_URL = os.getenv("RESCUE_EVENTS_URL", "http://127.0.0.1:8085/events")

# ── Label mapping (WhatChimp name → Notion Status value) ──
LABEL_MAP = {
    "Confirmed": "Confirmation",
    "Dry": "Dry",
    "Follow-up": "Follow-up",
    "Cancelled": "Cancelled",
    "Closed": "Closed",
    "Support": "Support",
}


# ────────────────────────────────────────────────────────────
# Dedup caches (bounded, in-memory)
# ────────────────────────────────────────────────────────────

class BoundedSet:
    """Ordered set with max size; evicts oldest."""
    def __init__(self, max_size: int = 5000):
        self._data: OrderedDict = OrderedDict()
        self.max_size = max_size

    def add(self, key: str) -> bool:
        """Return True if added (was new), False if already seen."""
        if key in self._data:
            self._data.move_to_end(key)
            return False
        self._data[key] = True
        if len(self._data) > self.max_size:
            self._data.popitem(last=False)
        return True


class LabelFingerprintCache:
    """Avoid redundant Notion Status writes when labels haven't changed."""
    def __init__(self):
        self._data: dict[str, str] = {}

    def changed(self, ticket_id: str, labels: list[str]) -> bool:
        fp = ",".join(sorted(labels))
        if self._data.get(ticket_id) == fp:
            return False
        self._data[ticket_id] = fp
        return True


message_id_seen = BoundedSet(max_size=5000)
label_cache = LabelFingerprintCache()


# ────────────────────────────────────────────────────────────
# Payload parsing helpers
# ────────────────────────────────────────────────────────────

def parse_request_body(headers: dict[str, str], body: bytes) -> dict:
    """Decode request body as JSON or form-encoded into a flat dict of strings."""
    if not body:
        return {}
    content_type = headers.get("content-type", "").lower()
    text = body.decode("utf-8", errors="replace")

    if "application/json" in content_type:
        try:
            return json.loads(text)
        except Exception as e:
            log.warning(f"Bad JSON body: {e}")
            return {}

    # Fallback: treat as form-encoded (WhatChimp occasionally sends this way)
    try:
        parsed = parse_qs(text, keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in parsed.items()}
    except Exception:
        pass

    # Last resort: try JSON anyway
    try:
        return json.loads(text)
    except Exception:
        return {}


def pick_active_labels(label_names_raw: str) -> list[str]:
    """Turn WhatChimp's comma-separated label_names into a list of mapped Notion labels."""
    if not label_names_raw:
        return []
    raw = [l.strip() for l in label_names_raw.split(",") if l.strip()]
    mapped = [LABEL_MAP[l] for l in raw if l in LABEL_MAP]
    # dedupe while preserving order
    seen = set()
    out = []
    for l in mapped:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────────────────────
# Business handlers
# ────────────────────────────────────────────────────────────

async def sync_labels_side_effect(
    client: httpx.AsyncClient,
    ticket_id: str,
    label_names_raw: str,
    ticket: Optional[dict] = None,
):
    """Sync mapped WhatChimp labels to Notion Status. Stamp Closed Date on transition."""
    new_labels = pick_active_labels(label_names_raw)
    if not new_labels:
        return
    if not label_cache.changed(ticket_id, new_labels):
        return

    # Read existing Status from the ticket dict (if provided) to detect Closed transition.
    prev_labels: list[str] = []
    if ticket is not None:
        status_arr = ticket.get("properties", {}).get("Status", {}).get("multi_select", [])
        prev_labels = [s.get("name", "") for s in status_arr if s.get("name")]

    ok = await notion.update_status_labels(client, ticket_id, new_labels)
    if ok:
        log.info(f"🏷️  labels synced for {ticket_id[-6:]}: {new_labels}")
        # Closed transition: stamp Closed Date.
        if "Closed" in new_labels and "Closed" not in prev_labels:
            await notion.stamp_closed_date(client, ticket_id, utc_iso_now())
            log.info(f"🔒 closed-date stamped for {ticket_id[-6:]}")


async def handle_incoming(
    client: httpx.AsyncClient,
    rr: RoundRobin,
    payload: dict,
):
    phone = str(payload.get("chat_id") or "").strip()
    bot_name = str(payload.get("whatsapp_bot_name") or "").strip()
    bot_id = str(payload.get("whatsapp_bot_id") or "").strip()
    phone_number_id = str(payload.get("phone_number_id") or "").strip()
    wa_message_id = str(payload.get("wa_message_id") or "").strip()
    label_names_raw = str(payload.get("label_names") or "")

    if not phone:
        log.warning(f"incoming: missing chat_id, payload={payload}")
        return

    # Determine source. resolve_source NEVER returns None — a lead is never
    # dropped for want of a clean brand (zero-miss requirement). Unmapped bots
    # land under their raw name so we can add a mapping without losing the lead.
    brand = wc.resolve_source(bot_id, bot_name, phone_number_id)
    if brand not in wc.CANONICAL_SOURCES:
        log.warning(
            f"incoming: UNMAPPED source captured as {brand!r} "
            f"(bot_name={bot_name!r} bot_id={bot_id!r} pnid={phone_number_id!r}) "
            f"— add to BRAND_ALIASES/WHATCHIMP_BOT_ID_TO_BRAND"
        )

    # Idempotency
    if wa_message_id and not message_id_seen.add(wa_message_id):
        log.debug(f"incoming: duplicate message_id {wa_message_id}")
        return

    now_iso = utc_iso_now()
    ticket = await notion.find_ticket(client, phone, brand)

    if ticket is None:
        # New lead
        agent = await rr.next_agent(client)
        if not agent:
            log.error("incoming: no active agent, ticket created as Unassigned")
            new_id = await notion.create_ticket(client, phone, brand, "Unassigned", now_iso)
            if new_id:
                await sync_labels_side_effect(client, new_id, label_names_raw, ticket=None)
            return
        new_id = await notion.create_ticket(client, phone, brand, agent["name"], now_iso)
        if new_id:
            log.info(f"🆕 new ticket {brand} / {phone} → {agent['name']}")
            # Try every pnid for the brand (customer may be on the old number).
            if agent.get("team_member_id"):
                pids = wc.phone_id_candidates(brand, phone_number_id)
                ok_pid = await wc.assign_to_team_member_any(
                    client, pids, phone, int(agent["team_member_id"]))
                if not ok_pid:
                    log.warning(f"assign failed on all pnids {pids} for {brand}/{phone}")
            await sync_labels_side_effect(client, new_id, label_names_raw, ticket=None)
        return

    # Any existing ticket — ping-pong. Closed is just a label, not special state:
    # a customer message still flips Outcome to Pending, stamps Last Customer
    # Message, and leaves the ticket's labels/agent assignment intact.
    await notion.set_pending(client, ticket["id"])
    await notion.stamp_customer_message(client, ticket["id"], now_iso)
    log.info(f"🔄 ping-pong {brand} / {phone}")
    await sync_labels_side_effect(client, ticket["id"], label_names_raw, ticket=ticket)


async def handle_outgoing(
    client: httpx.AsyncClient,
    payload: dict,
):
    phone = str(payload.get("chat_id") or "").strip()
    bot_name = str(payload.get("whatsapp_bot_name") or "").strip()
    bot_id = str(payload.get("whatsapp_bot_id") or "").strip()
    phone_number_id = str(payload.get("phone_number_id") or "").strip()
    wa_message_id = str(payload.get("wa_message_id") or "").strip()
    label_names_raw = str(payload.get("label_names") or "")

    if not phone:
        log.warning(f"outgoing: missing chat_id, payload={payload}")
        return

    # Same resolver as incoming so the ticket's Source matches (lookups align).
    brand = wc.resolve_source(bot_id, bot_name, phone_number_id)

    if wa_message_id and not message_id_seen.add(wa_message_id):
        return

    ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        # Race: outgoing webhook fired before the incoming webhook finished
        # creating the ticket. Sleep 2s and retry once.
        await asyncio.sleep(2.0)
        ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        log.info(f"outgoing: ignored — no ticket for {phone}/{brand} after retry")
        return

    # Labels can sync regardless of who sent the message
    await sync_labels_side_effect(client, ticket["id"], label_names_raw, ticket=ticket)

    # Identify the sender via targeted conversation lookup. During migration the
    # customer may be on the brand's OLD number, so try every candidate pnid and
    # keep the one that actually has this conversation.
    pids = wc.phone_id_candidates(brand, phone_number_id)
    if not pids:
        return

    pid, latest = await wc.get_latest_message_any(client, pids, phone)
    # If the webhook's wa_message_id doesn't match the conversation's latest,
    # WhatChimp may not have propagated yet. Sleep 1s and retry once on that pnid.
    webhook_msg_id = wa_message_id or ""
    if pid and latest and webhook_msg_id and latest.get("wa_message_id") != webhook_msg_id:
        log.info(
            f"outgoing: stale conversation lookup for {phone}/{brand} "
            f"(latest={latest.get('wa_message_id')!r} webhook={webhook_msg_id!r}); retrying"
        )
        await asyncio.sleep(1.0)
        latest = await wc.get_latest_message(client, pid, phone)
    if not latest:
        log.info(f"outgoing: ignored — no conversation history for {phone}/{brand} on pnids {pids}")
        return

    sender = latest.get("sender")
    agent_name_raw = latest.get("agent_name")
    agent_name = (agent_name_raw or "").strip() if isinstance(agent_name_raw, str) else agent_name_raw
    reply_time = latest.get("conversation_time") or utc_iso_now()

    log.info(
        f"outgoing: {phone}/{brand} latest sender={sender!r} "
        f"agent_name={agent_name_raw!r} time={reply_time!r}"
    )
    log.info(f"outgoing: raw msg keys={list(latest.keys())} full={latest!r}")

    # Filter: only stamp when this is genuinely a human agent reply.
    #   - Human reply:        sender='bot',    agent_name='<numeric user_id>'  -> accept
    #   - Automation reply:   sender='bot',    agent_name=None/empty           -> reject
    #   - Label/system ghost: sender='system', agent_name='bot' or other       -> reject
    # We require sender=='bot' AND agent_name to be a digit string.
    needle = (agent_name or "").strip() if isinstance(agent_name, str) else ""
    if sender != "bot":
        log.info(f"outgoing: ignored — {phone}/{brand} sender={sender!r} not 'bot' (system/ghost event)")
        return
    if not needle:
        log.info(f"outgoing: ignored — {phone}/{brand} agent_name empty/None (automation)")
        return
    if not needle.isdigit():
        log.info(f"outgoing: ignored — {phone}/{brand} agent_name={needle!r} not numeric (not a real agent)")
        return

    needle_int = int(needle)
    roster = await notion.get_active_roster(client)

    # Identify the replier by matching agent_name against each roster member's
    # Team Member ID (and WhatChimp User ID as a secondary key).
    replier = None
    for a in roster:
        if a.get("team_member_id") is not None and int(a["team_member_id"]) == needle_int:
            replier = a
            break
        if a.get("user_id") is not None and int(a["user_id"]) == needle_int:
            replier = a
            break

    # Update Agent Assigned multi-select: prepend the replier to position [0],
    # removing them from elsewhere in the list if present. If replier isn't in
    # the active roster (e.g. shared admin session), leave the list untouched.
    if replier:
        current_list = notion.ticket_agent_assigned_list(ticket)
        new_list = [replier["name"]] + [n for n in current_list if n != replier["name"]]
        if new_list != current_list:
            ok = await notion.update_agent_assigned_list(client, ticket["id"], new_list)
            if ok:
                log.info(
                    f"🔀 reassigned {brand} / {phone}: {current_list} -> {new_list}"
                )
                # WhatChimp side-assign only when position [0] actually changed.
                # Reuse the pnid the conversation was actually found on.
                prev_owner = current_list[0] if current_list else None
                if prev_owner != replier["name"]:
                    if pid and replier.get("team_member_id"):
                        await wc.assign_to_team_member(
                            client, pid, phone, int(replier["team_member_id"])
                        )
        current_assigned = replier["name"]
    else:
        current_assigned = notion.ticket_agent_assigned(ticket) or ""

    # Look up the current assignee's shift for response-speed calculation.
    assigned_shift_start = 12
    assigned_shift_end = 20
    for a in roster:
        if a["name"] == current_assigned:
            assigned_shift_start = a["shift_start"] if a["shift_start"] is not None else 12
            assigned_shift_end   = a["shift_end"]   if a["shift_end"]   is not None else 20
            break

    is_first_reply = notion.ticket_actioned_at(ticket) is None
    response_speed = None
    if is_first_reply:
        created_at = notion.ticket_created_at(ticket)
        if created_at:
            response_speed = calculate_response_speed(
                created_at, reply_time, assigned_shift_start, assigned_shift_end
            )

    ok = await notion.stamp_agent_reply(
        client, ticket["id"], reply_time, is_first_reply, response_speed
    )
    if ok:
        suffix = f" ({response_speed})" if response_speed else ""
        replier_label = replier["name"] if replier else f"uid={needle}"
        log.info(
            f"✍️  reply by {replier_label} on {brand} / {phone} — "
            f"owner={current_assigned or 'Unassigned'}{suffix}"
        )


async def tee_to_rescue(http_client: httpx.AsyncClient, direction: str, payload: dict) -> None:
    """Copy one webhook event to grq-rescue. Failures are logged and swallowed —
    the rescue service being down must never affect the leads pipeline."""
    if not RESCUE_EVENTS_URL:
        return
    try:
        await http_client.post(
            RESCUE_EVENTS_URL,
            json={"direction": direction, "payload": payload},
            timeout=2.0,
        )
    except Exception as e:
        log.warning(f"rescue tee failed (ignored): {e}")


# ────────────────────────────────────────────────────────────
# HTTP server
# ────────────────────────────────────────────────────────────

async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    http_client: httpx.AsyncClient,
    rr: RoundRobin,
):
    try:
        # Read request line + headers
        header_bytes = bytearray()
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            header_bytes.extend(chunk)
            if b"\r\n\r\n" in header_bytes:
                break
            if len(header_bytes) > 65536:
                break

        if b"\r\n\r\n" not in header_bytes:
            writer.close()
            return

        head, _, rest = header_bytes.partition(b"\r\n\r\n")
        head_text = head.decode("iso-8859-1", errors="replace")
        lines = head_text.split("\r\n")
        first_line = lines[0].split()
        if len(first_line) < 2:
            writer.close()
            return
        method = first_line[0].upper()
        path = first_line[1]

        # Parse headers (lower-cased keys)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        # GET / — healthcheck
        if method in ("GET", "HEAD") and path in ("/", "/health"):
            body = b"rpgrq up"
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.write(resp + (b"" if method == "HEAD" else body))
            await writer.drain()
            writer.close()
            return

        if method != "POST":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        # Read full body based on Content-Length
        length = int(headers.get("content-length", "0") or 0)
        body = bytes(rest)
        while len(body) < length:
            chunk = await reader.read(min(65536, length - len(body)))
            if not chunk:
                break
            body += chunk

        payload = parse_request_body(headers, body[:length] if length else body)

        # Strip query string from path
        path_only = path.split("?", 1)[0]

        # Respond immediately, then process — webhooks should not wait
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

        if path_only == "/rpgrq/incoming":
            asyncio.create_task(tee_to_rescue(http_client, "in", payload))
            await handle_incoming(http_client, rr, payload)
        elif path_only == "/rpgrq/outgoing":
            asyncio.create_task(tee_to_rescue(http_client, "out", payload))
            await handle_outgoing(http_client, payload)
        else:
            log.debug(f"Unknown path: {path_only}")

    except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError, TimeoutError) as e:
        # Client dropped the connection before we finished reading / responding.
        # Common for retries, probes, and scanners. Not an error we can act on.
        log.debug(f"connection dropped: {e}")
        try:
            writer.close()
        except Exception:
            pass
    except Exception as e:
        import traceback
        log.error(f"connection handler crashed: {e}\n{traceback.format_exc()}")
        try:
            writer.close()
        except Exception:
            pass


async def main():
    log.info("=" * 60)
    log.info("  RPGRQ Leads Bot — Webhook Server")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Port:    {PORT}")
    log.info("=" * 60)

    http_client = httpx.AsyncClient(timeout=30.0)
    rr = RoundRobin()

    # Warm the roster cache once
    try:
        await notion.get_active_roster(http_client)
    except Exception as e:
        log.warning(f"Initial roster fetch failed (continuing anyway): {e}")

    # Background: two-way orders-CRM sync (enriches closed leads with delivery
    # truth). Fire-and-forget — its own loop swallows errors so it can't take
    # the webhook server down.
    asyncio.create_task(order_sync.run_order_sync_loop(http_client))

    async def on_conn(reader, writer):
        await handle_connection(reader, writer, http_client, rr)

    server = await asyncio.start_server(on_conn, "0.0.0.0", PORT)
    log.info(f"🌐 listening on 0.0.0.0:{PORT}")

    # Graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows

    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        stop_task = asyncio.create_task(stop_event.wait())
        done, _ = await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is stop_task:
                server.close()
                await server.wait_closed()

    await http_client.aclose()
    log.info("Shutdown complete.")


if __name__ == "__main__":
    import error_reporter
    error_reporter.install("rpgrq-webhook", host="gcp-vm")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted.")
