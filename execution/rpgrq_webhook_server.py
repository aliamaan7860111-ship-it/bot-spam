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
from urllib.parse import parse_qs

import httpx
from dotenv import load_dotenv

# ── Path setup — make `from execution import ...` work ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from execution import rpgrq_notion as notion
from execution import rpgrq_whatchimp as wc
from execution.rpgrq_round_robin import RoundRobin, calculate_response_speed

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rpgrq")

PORT = int(os.getenv("RPGRQ_PORT", "8082"))

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
):
    labels = pick_active_labels(label_names_raw)
    if not labels:
        return
    if not label_cache.changed(ticket_id, labels):
        return
    ok = await notion.update_status_labels(client, ticket_id, labels)
    if ok:
        log.info(f"🏷️  labels synced for {ticket_id[-6:]}: {labels}")


async def handle_incoming(
    client: httpx.AsyncClient,
    rr: RoundRobin,
    payload: dict,
):
    phone = str(payload.get("chat_id") or "").strip()
    bot_name = str(payload.get("whatsapp_bot_name") or "").strip()
    bot_id = str(payload.get("whatsapp_bot_id") or "").strip()
    wa_message_id = str(payload.get("wa_message_id") or "").strip()
    label_names_raw = str(payload.get("label_names") or "")

    if not phone:
        log.warning(f"incoming: missing chat_id, payload={payload}")
        return

    # Determine brand (prefer bot name, fall back to bot_id)
    brand = wc.bot_id_to_brand(bot_id) or wc.normalize_brand(bot_name) or wc.phone_id_to_brand(bot_id)
    if not brand:
        log.warning(f"incoming: unknown brand bot_name={bot_name!r} bot_id={bot_id!r}")
        return

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
                await sync_labels_side_effect(client, new_id, label_names_raw)
            return
        new_id = await notion.create_ticket(client, phone, brand, agent["name"], now_iso)
        if new_id:
            log.info(f"🆕 new ticket {brand} / {phone} → {agent['name']}")
            pid = wc.brand_to_phone_id(brand)
            if pid and agent.get("team_member_id"):
                await wc.assign_to_team_member(client, pid, phone, int(agent["team_member_id"]))
            await sync_labels_side_effect(client, new_id, label_names_raw)
        return

    # Any existing ticket — ping-pong. Closed is just a label, not special state:
    # a customer message still flips Outcome to Pending, stamps Last Customer
    # Message, and leaves the ticket's labels/agent assignment intact.
    await notion.set_pending(client, ticket["id"])
    await notion.stamp_customer_message(client, ticket["id"], now_iso)
    log.info(f"🔄 ping-pong {brand} / {phone}")
    await sync_labels_side_effect(client, ticket["id"], label_names_raw)


async def handle_outgoing(
    client: httpx.AsyncClient,
    payload: dict,
):
    phone = str(payload.get("chat_id") or "").strip()
    bot_name = str(payload.get("whatsapp_bot_name") or "").strip()
    bot_id = str(payload.get("whatsapp_bot_id") or "").strip()
    wa_message_id = str(payload.get("wa_message_id") or "").strip()
    label_names_raw = str(payload.get("label_names") or "")

    if not phone:
        log.warning(f"outgoing: missing chat_id, payload={payload}")
        return

    brand = wc.bot_id_to_brand(bot_id) or wc.normalize_brand(bot_name) or wc.phone_id_to_brand(bot_id)
    if not brand:
        log.warning(f"outgoing: unknown brand bot_name={bot_name!r} bot_id={bot_id!r}")
        return

    if wa_message_id and not message_id_seen.add(wa_message_id):
        return

    ticket = await notion.find_ticket(client, phone, brand)
    if ticket is None:
        log.debug(f"outgoing: no ticket for {phone}/{brand}; ignoring")
        return

    # Labels can sync regardless of who sent the message
    await sync_labels_side_effect(client, ticket["id"], label_names_raw)

    # Identify the sender via targeted conversation lookup
    pid = wc.brand_to_phone_id(brand)
    if not pid:
        return

    latest = await wc.get_latest_message(client, pid, phone)
    if not latest:
        log.info(f"outgoing: {phone}/{brand} no conversation history returned")
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
    # We require sender=='bot' AND agent_name to be a digit string. All 3 agents
    # share the same session id (currently '264412') so we credit whoever was
    # round-robin'd as Agent Assigned.
    needle = (agent_name or "").strip() if isinstance(agent_name, str) else ""
    if sender != "bot":
        log.info(f"outgoing: {phone}/{brand} sender={sender!r} not 'bot' -> system/ghost event; ignoring")
        return
    if not needle:
        log.info(f"outgoing: {phone}/{brand} agent_name empty/None -> automation; ignoring")
        return
    if not needle.isdigit():
        log.info(f"outgoing: {phone}/{brand} agent_name={needle!r} not numeric -> not a real agent; ignoring")
        return
    roster = await notion.get_active_roster(client)

    # Find the assigned agent to key response-speed on their shift
    assigned_name = notion.ticket_agent_assigned(ticket) or ""
    assigned_shift_start = 12
    assigned_shift_end = 20
    for a in roster:
        if a["name"] == assigned_name:
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
        log.info(
            f"✍️  human reply (uid={needle}) on {brand} / {phone} — "
            f"credited to {assigned_name or 'Unassigned'}{suffix}"
        )


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
            await handle_incoming(http_client, rr, payload)
        elif path_only == "/rpgrq/outgoing":
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted.")
