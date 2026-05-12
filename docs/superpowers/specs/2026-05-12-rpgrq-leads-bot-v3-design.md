# RPGRQ Leads Bot v3 — Design Spec

**Date:** 2026-05-12
**Status:** Approved by user, ready for implementation planning.

## Background

v2 of the RPGRQ Leads Bot is live and stable: webhook-driven, round-robin assignment, per-(phone, brand) ticket identity, dynamic reassignment on agent reply, label sync, daily report. v3 extends it on three axes that v2 punted:

1. **Shift- and day-off-aware round-robin** so leads only flow to agents who are actually on shift.
2. **Multi-select `Agent Assigned`** so all participants stay visible with the latest replier always at position [0].
3. **Accurate Closed-event accounting** via a new `Closed Date` field stamped from label-sync.

Plus reliability hardening for `Created At` / `Actioned At` / `Last Agent Reply` stamping, and removal of the auto-set `Waiting` / `Active` Status values which obscure rather than help.

## Goals

- Assignment respects shift hours and off days; off-hours leads pre-emptively go to whoever's shift starts next.
- `Agent Assigned` shows full participation history, latest first. Daily report counts each agent's actual contribution.
- `Closed Date` is the authoritative timestamp for daily closed counts (replacing the brittle `Status contains Closed + Last Agent Reply today` proxy).
- Timestamp stamping (`Created At`, `Actioned At`, `Last Agent Reply`) is consistent — no silent drops on race conditions.
- `Status` field is a pure mirror of WhatChimp's tracked labels; no bot-injected values.

## Non-goals

- Per-typer accuracy under shared admin WhatChimp sessions. Already addressed in v2 — replies from non-roster session ids leave `Agent Assigned` unchanged but still stamp under the current position-[0] owner.
- Wraparound shifts (end_hour < start_hour, crossing midnight). All current agents use same-day shifts. Note the limitation; revisit if a future hire needs it.
- Specific off-date support (one-time PTO). Use day-of-week recurrence + temporarily unchecking `Active` for ad-hoc days.
- LLM conversation summaries (parked separately).

## Schema changes

### Leads DB (`344c320eba59800a90a6e804a575d272`)

| Field | Change |
|---|---|
| `Agent Assigned` | single-select → **multi-select**. Position [0] = latest responder. |
| `Closed Date` | **NEW**. Type: date. Stamped when `Status` transitions to include `Closed` (re-stamped on every fresh add). |
| `Status` | unchanged schema. Bot stops writing `Waiting` and `Active` automatically. Now purely mirrors mapped WhatChimp labels. |

Existing tickets where `Agent Assigned` was a single-select value (e.g. `"Ushda"`) need to migrate to the multi-select equivalent `["Ushda"]`. Migration script handles this transparently.

### Roster DB (`346c320eba598175969dd15472249081`)

| Field | Change |
|---|---|
| `Off Days` | **NEW**. Type: multi-select with seven options — `Sunday`, `Monday`, ..., `Saturday`. Empty = no recurring days off. |

## Shift-aware round-robin algorithm

Computed in PKT (UTC+5).

**Inputs:** current PKT timestamp; active roster (cached 60s, filter `Active = true`).

**Step 1 — `available_today`:** active agents whose `Off Days` does NOT contain today's day-name.

**Step 2 — `on_shift_now`:** subset of `available_today` whose current PKT hour ∈ `[Shift Start Hour, Shift End Hour)`. Interval is half-open: at exactly `Shift End Hour` (e.g. 18:00 sharp) the agent is no longer on shift. At exactly `Shift Start Hour` they are on shift.

**Step 3 — pick the pool:**
- If `on_shift_now` is non-empty → pool = `on_shift_now`.
- Else if `available_today` is non-empty → pool = `{agent with earliest Shift Start Hour today that is later than now}`. If multiple tie, alphabetical.
  - If no `Shift Start Hour` later today (e.g. it's already 23:00 and everyone's done) → pool = `{agent with earliest Shift Start Hour tomorrow respecting Off Days}`.
- Else (everyone's off today) → pool = all `Active = true` agents (ignore shifts AND off days as a last resort).

**Step 4 — round-robin within the pool:**
- Pointer derived from the most recent ticket whose `Agent Assigned[0]` is in the pool (sort by `Created At` desc, post-filter in Python because multi_select first-element isn't query-filterable).
- In-memory batch counter prevents same-second double assignment.
- Next agent = the one after pointer (alphabetical), wrapping.

### Worked example — current 3-agent roster

| Time (PKT) | A1 9–18 | A2 13–22 | A3 15–24 | Pool |
|---|---|---|---|---|
| 00:00–09:00 | – | – | – | next-to-start → `[A1]` |
| 09:00–13:00 | ✓ | – | – | `[A1]` |
| 13:00–15:00 | ✓ | ✓ | – | `[A1, A2]` |
| 15:00–18:00 | ✓ | ✓ | ✓ | `[A1, A2, A3]` |
| 18:00–22:00 | – | ✓ | ✓ | `[A2, A3]` |
| 22:00–24:00 | – | – | ✓ | `[A3]` |

If today is Sunday and A1 has Sunday off: A1 excluded from every row above. 00:00–13:00 pool becomes next-to-start = A2 starting 13:00.

If today is a public holiday with all three on Off Days: pool = `[A1, A2, A3]` (last-resort fallback).

## Dynamic reassignment + multi-select Agent Assigned

On a verified human reply (numeric `agent_name` matched to a roster member's `Team Member ID`):

1. Read current `Agent Assigned` multi-select array.
2. Remove the replier if already present.
3. Prepend the replier to position [0].
4. Write the new array.

If position [0] changed compared to before this reply → also call WhatChimp `/subscriber/chat/assign-to-team-member` with the new owner's `team_member_id`.

If the reply came from a numeric `agent_name` that doesn't match any active roster member (e.g. shared admin session uid `264412`):
- Leave `Agent Assigned` untouched.
- Still stamp `Last Agent Reply` and (if first-ever reply) `Actioned At` + `Response Speed` under whoever is currently at position [0].

Bot automations (`agent_name = None`) and label-ghost events (`sender = 'system'`) are still filtered out — no stamping, no reassignment.

## Closed Date stamping

Lives in the label-sync side-effect already in `handle_incoming` and `handle_outgoing`.

```
1. Build the candidate Status array from webhook label_names (via LABEL_MAP).
2. Read the ticket's current Status from Notion.
3. If candidate != current:
     write candidate to Notion.
     if 'Closed' ∈ candidate AND 'Closed' ∉ current:
         also write Closed Date = now (PKT ISO).
```

Removing-and-re-adding the `Confirmed → Closed` label triggers a fresh `Closed Date` stamp on the re-add. Other label add/removes never touch `Closed Date`.

## Status field cleanup

- `create_ticket` no longer writes `"Status": {"multi_select": [{"name": "Waiting"}]}`. New tickets land with empty Status. Label sync (if labels are already on the subscriber when they message in) fills it during the same webhook handler.
- `reopen_ticket` was removed in v2's "closed is just a label" refactor. No further action needed.
- Existing `Waiting` / `Active` rows in Notion stay as historical data. Dropdown options stay; we just stop adding new values.

## Daily report rewrite

`RPGRQ Leads Bot/daily_report.py`:

- **Active agents:** unchanged — fetch from Roster DB where `Active = true`.
- **Total Responded today, per agent:** filter Leads DB on `Agent Assigned contains <agent>` AND `Last Agent Reply` between day_start and day_end. Counts each multi-select participant who replied today as +1 per ticket.
- **Total Closed today, per agent:**
  1. Query Leads DB filtered on `Closed Date` between day_start and day_end.
  2. For each row, read `Agent Assigned` array. If position [0] equals this agent → +1.
- **Pending, per agent:** filter on `Agent Assigned contains <agent>` AND `Outcome = Pending`. Current state, all-time. Each participant counted.
- **Conv Rate:** Notion formula in Report DB, unchanged.

The `wait_for_report_time()` scheduled-mode entry path is removed. Script runs only on-demand via `python "RPGRQ Leads Bot/daily_report.py" --now`.

## Stamping reliability hardening

Three changes:

1. **Outgoing-without-ticket retry.** When `handle_outgoing` calls `find_ticket(phone, brand)` and gets `None`, sleep 2s and retry once. Covers the race where an agent's first reply lands in the same poll-window as the customer's incoming message. If still no ticket on retry, log and drop (no false stamps).

2. **Conversation-lookup staleness retry.** When `handle_outgoing` calls `/get/conversation` and the latest message's `wa_message_id` doesn't match the webhook's `wa_message_id`, sleep 1s and retry once. Covers WhatChimp propagation lag.

3. **Defensive `Created At` backfill.** Every stamp call also writes `Created At` if it's currently null on the ticket. Idempotent for rows that already have it.

Plus a one-off backfill script: `scratch/backfill_stamps.py`. Walks Leads DB, finds rows where `Outcome != Pending` but `Last Agent Reply` is null, calls `/get/conversation` for each, identifies the latest agent reply, and stamps the missing fields.

Diagnostic upgrade: every `outgoing: ignored ...` log line includes the specific reason (filter rejected, no ticket found, stale conversation lookup, etc.). After deploy, grep on a day's worth of logs reveals systematic gaps.

## Implementation work breakdown

1. **Schema migration script** `scratch/migrate_leads_db_v3.py`:
   - Detect existing `Agent Assigned` type. If single-select with rows, read each row's value, then PATCH the DB property to `multi_select` (preserving option list), then rewrite each row's value as a 1-item array.
   - Add `Closed Date` (date) to Leads DB.
   - Add `Off Days` (multi-select w/ 7 day-name options) to Roster DB.
   - Re-run safe.

2. **Code updates** in `execution/`:
   - `rpgrq_notion.py`:
     - `create_ticket`: drop Status default; ensure Agent Assigned writes as 1-item multi-select.
     - `reassign_agent` → `update_agent_assigned_list(client, page_id, ordered_agents_list)` that writes the multi-select array.
     - `stamp_agent_reply`: handle defensive `Created At` backfill.
     - Add helper `stamp_closed_date(client, page_id)`.
     - Add `ticket_agent_assigned_list(ticket)` reading the multi-select array.
     - Add helper `get_active_roster_with_off_days(client)` returning the new field.
   - `rpgrq_round_robin.py`:
     - Compute `available_today` / `on_shift_now` / pool selection per the algorithm above.
     - Pointer derivation reads `Agent Assigned[0]` instead of single-select value.
   - `rpgrq_webhook_server.py`:
     - `handle_incoming`: use the new round-robin pool selection; drop Status=Waiting write.
     - `handle_outgoing`:
       - Add the retry around `find_ticket`.
       - Add the retry around the conversation lookup mismatch.
       - Rewrite reassignment branch to update the multi-select list.
       - Diagnostic log lines for every drop reason.
     - `sync_labels_side_effect`: detect Closed transition and call `stamp_closed_date`.

3. **Daily report rewrite** `RPGRQ Leads Bot/daily_report.py`:
   - `count_responded_today`: switch to `contains` filter on multi-select.
   - `count_closed_today`: query by `Closed Date`, post-filter in Python on `Agent Assigned[0]`.
   - `count_pending`: switch to `contains` filter.
   - Remove `wait_for_report_time` and the non-`--now` startup path.

4. **Backfill script** `scratch/backfill_stamps.py`: one-off scan + repair.

5. **Directive update** `directives/rpgrq_leads_bot.md`: reflect all of the above.

6. **Memory update**: `project_rpgrq_leads_bot.md` describes v3 rules.

## Deploy sequence

1. Run `migrate_leads_db_v3.py` locally. Verify schema changes via Notion UI.
2. Pre-populate `Off Days` for current agents (user does in Notion UI).
3. Push code to `production` remote.
4. SSH to VM → `git pull production main` → `sudo systemctl restart rpgrq-webhook`.
5. Verify with `curl http://localhost:8082/health` and tail logs.
6. Run `backfill_stamps.py` once.
7. Manual smoke test: send a test customer message during off-hours, verify it lands with the next-to-start agent. Have a roster member reply, verify Agent Assigned multi-select updates.
8. Run daily report on-demand to confirm new counting works.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Existing tickets break when `Agent Assigned` schema changes | Migration script reads each row's existing single-select value and writes back as 1-item multi-select array atomically. |
| Notion API doesn't allow converting select → multi-select in place | Fallback: create a new multi-select column, copy values, archive the old column. Same end-state. Migration script attempts in-place first, falls back if rejected. |
| Race condition between incoming and outgoing webhooks for the same chat | 2-second retry on `find_ticket` covers most cases. Anything still missing gets caught by the backfill script. |
| Off-hours fallback assigns all overnight leads to one agent who then comes in to 50 pending tickets | Acceptable per user's design choice. Manual reassignment via WhatChimp possible. |
| Public holiday with everyone off → reverts to all-active round-robin, agents see leads on their day off | Acceptable per user's design choice (d-ii). |
| Per-agent reply timestamps don't exist. `Responded today` filter uses `Agent Assigned contains <agent>` + `Last Agent Reply today`. If A1 replied yesterday and A2 replied today, A1 still counts as responded-today because A1 is in the multi-select and Last Agent Reply is today. | Documented limitation. Slight over-count of cross-day participants. Can be fixed later by adding a per-agent reply log (out of scope for v3). |
