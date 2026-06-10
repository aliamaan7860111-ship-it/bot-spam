# 24h Window Rescue Automation (`grq-rescue`) — Design

**Date:** 2026-06-10
**Status:** Approved

## Problem

When a customer messages one of our WhatsApp numbers and no one replies, Meta's
24-hour customer-service window closes. After that, only paid pre-approved
template messages can be sent — free-form messages and bot flows are blocked
entirely. We lose the conversation unless we pay for a template send.

## Solution

Fire a **free bot-flow message just before the window closes** (at the 23-hour
mark) for conversations we never answered:

> "Apologies for the delayed response, how may we help you?"

with two quick-reply buttons:

- **Connect with Agent** — confirmation reply + conversation assigned to an agent
- **Not Interested** — polite close + conversation still assigned to an agent

A button tap counts as a customer inbound message, which **resets the 24h
window** and buys a fresh day for an agent to respond. The whole exchange is a
session message, so it costs nothing.

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Fire time | ~23h after the customer's last real message |
| Eligibility | Only **unanswered** conversations (no agent or bot outbound since the customer's last real message) |
| Brand scope | All 5 brand bots: Virex UAE, Dialo UAE, Amara, Lune Collection, Elara |
| Connect with Agent | Reply + assign conversation in WhatChimp (no external notify) |
| Not Interested | Polite close + still assign conversation to an agent |
| Repeat rule | **One rescue per conversation.** A button tap does not re-arm a second rescue; only a new real customer message resets eligibility (and the 23h clock) |

## Why fire *before* expiry (not after)

Meta's rule: only customer inbounds open/reset the session window. Bot flows
and free-form sends are impossible once the window is closed — outside the
window, paid templates are the only channel. So "rescue after expiry" cannot
exist for free; the free version is "rescue at hour 23."

## Architecture

```
WhatChimp (per-bot incoming + outgoing message webhooks, already configured)
   ↓
rpgrq_webhook_server.py  :8082  (existing leads bot — unchanged behavior)
   │  + NEW ~10-line tee: fire-and-forget POST {direction, payload}
   ↓
grq-rescue  127.0.0.1:8085  (NEW service, localhost-only, no Caddy change)
   ├─ POST /events   ← tee feed
   ├─ GET  /         ← health check
   ├─ SQLite state   (conversation clocks)
   └─ scheduler loop (60s) → WhatChimp POST /whatsapp/trigger-bot
```

WhatChimp allows **one webhook URL per bot**, and the rpgrq leads bot already
owns it for all 5 brands — so the rescue service is fed by a tee, not by its
own webhook registration.

### Component 1 — WhatChimp UI setup (manual, per bot × 5)

A "Window Rescue" bot flow per bot:

1. Text node: "Apologies for the delayed response, how may we help you?"
2. Quick-reply button **Connect with Agent** → reply ("You're connected — an
   agent will be with you shortly.") + *Assign Conversation* to agent/team.
3. Quick-reply button **Not Interested** → reply ("No problem — feel free to
   reach out anytime!") + *Assign Conversation* to agent/team.

Record per bot: `bot_flow_unique_id` (for `trigger-bot`) and the exact
inbound text each button tap produces (postback/label — captured live, see
Verification). No Meta approval involved; session messages are free-form.

### Component 2 — Tee in `rpgrq_webhook_server.py`

After payload parsing in the incoming/outgoing handlers: fire-and-forget
`POST http://127.0.0.1:8085/events` with `{"direction": "in"|"out", "payload": {...}}`.
2s timeout; failures logged at warning and swallowed. The tee can never block
or fail the leads pipeline. Target URL via env `RESCUE_EVENTS_URL` so it can
be disabled by unsetting.

### Component 3 — `grq-rescue` service

Files: `execution/rescue_server.py` (HTTP + scheduler), `execution/rescue_store.py`
(SQLite). Reuses `whatchimp_client.py` for the `trigger-bot` call (function
added there if missing).

**State** — SQLite table `conversations`, key `(bot_id, phone)`:

| Column | Meaning |
|---|---|
| `brand` | canonical brand name |
| `last_real_inbound_at` | last customer message that was NOT a rescue-button tap |
| `last_outbound_at` | last outbound of any kind (agent, bot, template) |
| `rescued_at` | when the rescue flow was triggered for the current conversation |
| `attempts` | trigger-bot attempts for the current conversation |

**Event classification (on `/events`):**

- `in` + text matches a configured rescue-button postback → button tap:
  update nothing that re-arms a rescue (recorded for observability only).
- `in` + anything else → real message: set `last_real_inbound_at`,
  clear `rescued_at` and `attempts`.
- `out` → set `last_outbound_at`. **Any** outbound counts as "answered",
  including automated sends (OFD, abandoned-checkout). Accepted simplification.

**Eligibility (scheduler, every 60s):**

```
last_real_inbound_at > last_outbound_at        (unanswered)
AND rescued_at IS NULL                          (one rescue per conversation)
AND 23h00m <= age(last_real_inbound_at) <= 23h45m   (firing window)
AND attempts < 3                                (retry cap)
AND brand enabled in RESCUE_CONFIG
```

If the firing window was missed (downtime), skip and log — never risk a
trigger after the session window closed. On successful `trigger-bot` (2xx),
set `rescued_at`. On failure, increment `attempts`; after 3, stop and log.
The retry cap is a hard requirement (lesson from the OFD retry-forever bug).

The rescue flow's own outbound also bumps `last_outbound_at`, which makes the
conversation "answered" — a second, independent guard against double-rescue.

### Component 4 — Config

`RESCUE_CONFIG` per brand: `bot_flow_unique_id`, `enabled`, button postback
strings. Brands go live one at a time as their flows are built in the UI.
Env: `RESCUE_PORT` (8085), `RESCUE_FIRE_AFTER_HOURS` (23), `RESCUE_DB_PATH`,
`RESCUE_EVENTS_URL` (consumed by rpgrq tee).

### Component 5 — Tests & deployment

- pytest under `execution/tests/`, mirroring the OFD suite: event
  classification (button tap vs real message vs outbound), eligibility query
  (window edges, answered/unanswered, rescued flag), retry cap, trigger-bot
  payload shape.
- systemd unit `grq-rescue.service`; deploy via the standard path (push →
  GCP VM `git pull` → `systemctl restart grq-rescue`), plus one
  `systemctl restart` of the rpgrq service to pick up the tee.

## Verification before go-live

1. Point one bot's rescue flow at a test number; capture the **real webhook
   payload of each button tap** before hardcoding postback strings in
   `RESCUE_CONFIG` (WhatChimp doesn't document payload shapes).
2. End-to-end on one brand (pilot = whichever flow is built first): real
   inbound → wait for the 23h trigger (or temporarily lower
   `RESCUE_FIRE_AFTER_HOURS`) → confirm flow delivery, button tap, agent
   assignment, and no second rescue.

## Accepted gaps

- Conversations already mid-window at first deploy are invisible until the
  customer's next message (no clock history).
- Automated outbounds (e.g. an OFD shipping notification) count as "answered"
  even though they don't address the customer's question.
- In-memory→SQLite means state survives restarts, but events arriving while
  `grq-rescue` is down are lost (tee is fire-and-forget). Worst case: a
  conversation is mis-seen as answered/unanswered until the next event.
