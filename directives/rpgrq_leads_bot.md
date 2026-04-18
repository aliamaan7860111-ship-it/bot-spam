# RPGRQ Leads Bot — Directive

Event-driven CRM for 5 WhatsApp stores. Replaces the old polling-based `agent_tracking_bot.py`.

## Goal

For every customer on our 5 WhatsApp numbers:
1. Create a Notion ticket per (phone, brand) the moment they message us.
2. Auto-assign an agent via round-robin.
3. Assign the chat inside WhatChimp too.
4. Track response time, outcome, status — accurately.

## Inputs

- **WhatChimp incoming message webhook** → `POST /rpgrq/incoming`.
- **WhatChimp outgoing message webhook** → `POST /rpgrq/outgoing`.
- Notion **Leads DB** (`344c320eba59800a90a6e804a575d272`) — CRM.
- Notion **Agent Roster DB** (`346c320eba598175969dd15472249081`) — agents, IDs, shifts, active flag.

## The 5 brands (canonical ALL-CAPS)

| whatsapp_bot_name (webhook) | phone_number_id (API) | Canonical brand |
|---|---|---|
| Elara (any case)   | 1031340813395459 | ELARA      |
| Amara (any case)   | 1045332455333591 | AMARA      |
| Virex UAE (any case)| 1073890042476443 | VIREX UAE  |
| Lune (any case)    | 1138942462625909 | LUNE       |
| Dialo UAE (any case)| 1002123586328400 | DIALO UAE  |

Always normalize via a mapping dictionary before writing to Notion.

## Core rules

### Rule 1: Inbound (new customer message)

- Webhook fires at `/rpgrq/incoming`.
- Extract `chat_id` (phone), `whatsapp_bot_name` (→ brand), `wa_message_id`, `label_names`.
- Idempotency: dedup on `wa_message_id` (in-memory set, bounded).
- Look up Leads DB for a ticket matching **(phone, brand)**.
  - No ticket → **create** with `Status=Waiting`, `Outcome=Pending`, `Created At=now`, `Agent Assigned=<next round-robin>`. Also call WhatChimp `assign-to-team-member` for the assigned agent.
  - Active ticket → **ping-pong**: set `Outcome=Pending`. No assignment change.
  - Closed ticket (Status contains `Closed`) → **reopen**: overwrite `Created At = now`, set `Status` to `[Active]`, `Outcome=Pending`, reassign to a new agent via round-robin. Also call WhatChimp `assign-to-team-member`.
- As a side-effect, sync labels (see Rule 4).

### Rule 2: Outgoing (our side replied)

- Webhook fires at `/rpgrq/outgoing`. Payload has no agent identifier.
- Idempotency: dedup on `wa_message_id`.
- Find the ticket for (phone, brand). If none → ignore (edge case: we replied with no ticket).
- Call WhatChimp `/get/conversation` for that chat (limit=5) to read `agent_name` of the most recent message.
- Filter:
  - `agent_name` ∈ roster active names (currently Ushda, Amaan, Nauman) → real agent reply → apply Rule 3.
  - Else (Ibrahim Taha, admin, bot, automation) → **ignore entirely**. Do not stamp anything.
- As a side-effect, sync labels (see Rule 4).

### Rule 3: Stamp on agent reply

- `Last Contact Date = now`.
- `Outcome = No Response` (exact casing — DB uses capital R).
- If `Actioned At` is not set (first reply):
  - `Actioned At = reply time`.
  - `Response Speed` = business-hours elapsed between `Created At` and reply time, measured against the **assigned agent's** personal shift (from Roster DB).
    - ≤ 5 min → `Fast`
    - ≤ 15 min → `Medium`
    - \> 15 min → `Slow`
- **Never mutate `Agent Assigned`.** Even if a different agent replies, the ticket stays owned by whoever was originally round-robin'd.

### Rule 4: Label sync

- Both webhook payloads include `label_names` (comma-separated string of current subscriber labels).
- Apply this mapping, WhatChimp → Notion Status:
  ```
  Confirmed  → Confirmation
  Dry        → Dry
  Follow-up  → Follow-up
  Cancelled  → Cancelled
  Closed     → Closed
  Support    → Support
  ```
- Unmapped labels are ignored.
- Write the mapped set to Notion `Status` (multi-select). Skip the write if the set hasn't changed (fingerprint cache).

### Rule 5: Round-robin

- On startup and every 60s, re-fetch active roster rows from the Roster DB (filter `Active=true`).
- **Derive the next agent** from the most recently created Notion ticket that has an `Agent Assigned` value in the active roster. Next = the agent that comes after them in the roster order (by `Name` alphabetical for stability).
- **Batch safety:** hold the pointer in memory during processing. After assigning Amaan, next assignment in the same process is Nauman, regardless of Notion propagation lag.
- If zero agents are active, log error and skip assignment (leave ticket `Unassigned`).

## Outputs

- Notion Leads DB: 1 ticket per (phone, brand), fields stamped accurately.
- WhatChimp: chat assigned to team member via `POST /subscriber/chat/assign-to-team-member`.

## Scripts

- `execution/rpgrq_notion.py` — Notion client.
- `execution/rpgrq_whatchimp.py` — WhatChimp client.
- `execution/rpgrq_round_robin.py` — rotation logic.
- `execution/rpgrq_webhook_server.py` — asyncio HTTP server on port 8082. Entry point.
- `execution/daily_report.py` — already exists. Unchanged.

## Infra

- GCP VM `34.30.125.177`.
- Port **8082** (firewall rule `allow-rpgrq-webhookk`).
- HTTP (no TLS).
- Systemd service `rpgrq-webhook.service`.
- Existing `order-bridge` service on port 8080 untouched.

## Webhook URLs to register in WhatChimp

- Incoming: `http://34.30.125.177:8082/rpgrq/incoming`
- Outgoing: `http://34.30.125.177:8082/rpgrq/outgoing`

## Shift-based response time

- Each agent's shift comes from Roster DB `Shift Start Hour` / `Shift End Hour` (integers 0–23, PKT).
- Only count minutes that fall within the assigned agent's shift window.
- Current shifts: all three agents on 12–20.
- Wraparound shifts (end < start, crossing midnight) are NOT supported in v1.

## Known constraints

- `wa_message_id` dedup cache is in-memory — restarts re-accept duplicates. Acceptable for now; Notion writes are naturally idempotent on update.
- HTTP, not HTTPS — matches existing confirmation bot; upgrade later if needed.
- No dedicated label webhook — labels flow in piggy-backed on `label_names` in the in/out payloads.

## Self-annealing notes

- If a new brand is added, add it to the brand mapping dict in `rpgrq_webhook_server.py` and the Notion `Source (Store)` options.
- If a new agent joins, add a row to the Roster DB with their Name, Team Member ID, Active=true, shift — picked up within 60s.
- If WhatChimp renames a webhook field, update the extraction in `rpgrq_webhook_server.py` only.
