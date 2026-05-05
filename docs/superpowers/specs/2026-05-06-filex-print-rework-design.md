# Filex `/print` Command Rework — Design

**Date:** 2026-05-06
**Owner:** order-bridge / Filex submission flow
**Status:** Approved

## Background

The current `/print all` Telegram command in `execution/order_bridge.py` was added in
the initial Filex automation rollout. In production it has produced two recurring
problems:

1. **Silent skips.** When orders fail validation (e.g. unparseable city, malformed
   total), the handler sends one Telegram message per skipped order. Telegram's
   per-chat rate limit drops most of those messages, so operators see only a fraction
   of what was skipped. Today's run placed 43 of 58 eligible orders; 15 were skipped
   but only 2-3 skip messages reached the group.

2. **Over-strict eligibility.** The current Notion query requires
   `Status = Processed AND Sourcing Notified = ✓ AND Fulfillment Notified = ✓ AND
   Filex Submitted = ☐`. The two notified-flags are not maintained reliably and
   exclude orders that should be printed.

Two additional gaps have surfaced:

3. There is no command to print a single order. Operators who fix a problematic
   address must wait for the next `/print all` run or manually call the API.

4. The total parser introduced a recent bug where `"AED1,949.39\n\nnote: ..."` parsed
   as `1.00`. This caused AM3088 to be submitted to Filex with a 1 AED COD instead of
   1949 AED COD — a real operational incident.

## Goals

- Make `/print all` print **every** Processed order in one pass, with skip reasons
  consolidated into at most two Telegram messages so nothing is lost to rate limits.
- Add `/print <ORDER_ID>` for single-order placement and label retrieval.
- Replace the total parser with one that handles every CRM-observed format.

## Non-Goals

- Inferring city from neighborhood (e.g. "JVC" → Dubai). Operators clean addresses
  manually; the bot only flags missing cities.
- Retrying transient Filex API failures. Existing single-attempt behavior with lock
  rollback is unchanged.
- Resolving the upstream Filex AWB-flip bug. Out of scope.
- A new "force re-place" mode for already-submitted orders.

## Eligibility Rule (`/print all`)

**Notion filter:** `ORDER STATUS = "Processed"`. Nothing else.

The Sourcing Notified and Fulfillment Notified checkbox filters are removed
entirely.

**Per-order classification at runtime** (after the query):

| Order state | Outcome |
|---|---|
| `FILEX STATUS` is non-empty (any value) | Skip → "already labeled" message |
| Validation fails (city / total / phone / address / name) | Skip → "validation skip" message |
| Otherwise | Build payload, place on Filex, update Notion, include in PDF |

A non-empty `FILEX STATUS` is treated as the source of truth for "already
submitted", regardless of the `Filex Submitted` checkbox state. This catches the
edge case where an order has a Filex label but the checkbox was never ticked.

## Telegram Message Structure (`/print all`)

At most three Telegram messages per run, each sent as **one** API call so nothing
is dropped to rate limits.

### Message 1 — Validation skips (only if any)

Sent before the placement step. Grouped by reason. Every skipped order ID and the
relevant field appear in this single message.

```
⚠️ Skipped 8 order(s) — fix and re-run /print or use /print <ID>:

Missing city in address (5):
  • AM3088 — "Damac Hills 2, Coursetia, Villa 12"
  • WA130  — "Al Barsha1, Seventeen Villas..."
  • WA131  — "JVC, Diamond Tower 901"
  • WA136  — "Khalifa City, Al Raha Gardens..."
  • WA142  — "Business Bay, Sol Avenue P1"

Invalid total (1):
  • AM3091 — total: "AED" (no number)

Missing phone (1):
  • PT2010 — phone field empty

Missing customer name (1):
  • WA145 — name field empty
```

Skip reasons are grouped under bold-style headers in fixed order:
1. Missing city in address
2. Invalid total
3. Missing phone
4. Missing customer name
5. Missing address

The reason quoted next to each order ID is the offending field value (truncated
to 60 chars).

### Message 2 — "Already labeled" skips (only if any)

Sent after Message 1 and before placement. Lists every Processed order that
already has a non-empty FILEX STATUS.

```
ℹ️ 2 order(s) already have a Filex label — verify and tick "Filex Submitted":
  • AM3025 — status: Label Created, tracking: 4866850123
  • LU1240 — status: In Transit,    tracking: 0000850456
```

### Message 3 — PDF document

Sent last. Standard `send_document` with the combined airway-bill PDF and a
caption summarizing the run:

```
✅ Placed 38 order(s). Filex labels attached.
```

If no orders were placed (everything skipped), the PDF step is omitted and a
plain-text summary message is sent instead:

```
No labels generated — all eligible orders were skipped.
```

### Message size handling

Telegram's per-message limit is 4096 characters. If Message 1's content exceeds
~3800 chars (a defensive margin), the body is written to an in-memory `.txt`
file and sent as a document attachment with a short caption. Same fallback for
Message 2.

## `/print <ORDER_ID>` Command

### Command dispatch

`python-telegram-bot`'s `CommandHandler` splits the command's arguments on
whitespace into `context.args`. The single `/print` handler now dispatches based
on those args:

- `args == ["all"]` → `cmd_print_all`
- `args == []` → reply with usage hint: `Usage: /print all  OR  /print <ORDER_ID>`
- Anything else → `cmd_print_one(order_id=" ".join(args))` so that `/print WA 127`
  is treated identically to `/print WA127` (the join + matching tolerance covers
  both forms).

### Order ID matching

Order ID matching is tolerant: tries the exact joined string first, then with a
space inserted between the alphabetic prefix and the digits (`WA127` →
`WA 127`), then with the space removed (`WA 127` → `WA127`). Matching is
case-sensitive (Notion is case-sensitive).

Branches:

### (a) Order doesn't exist in Notion

Reply: `⚠️ Order AM3030 not found in CRM.`

### (b) Order status is not "Processed"

Reply: `⚠️ AM3030 status is "Confirmed" — only "Processed" orders can be printed.`

The status name in the message is the actual current status from Notion.

### (c) Order is Processed and FILEX STATUS is non-empty

Re-fetch the existing label PDF from Filex using the stored tracking number. **No
placement, no Notion writes.** Send the 1-page PDF with caption:

```
ℹ️ AM3030 already has a Filex label. Status: Label Created. Tracking: 4866850123.
```

If the order has FILEX STATUS but no stored tracking number, reply with
`⚠️ AM3030 has FILEX STATUS but no tracking number. Manual investigation needed.`

### (d) Order is Processed and FILEX STATUS is empty

Run the same validation as `/print all`. Two outcomes:

**Validation succeeds.** Place a single order on Filex, write tracking + status
+ `Filex Submitted = ✓` + Dispatched At + Last Update to Notion, fetch the
1-page label, send with caption:

```
✅ AM3030 placed. Tracking: 4866850123.
```

**Validation fails.** Reply:

```
⚠️ AM3030 cannot be placed: missing city in address. Fix the address in CRM and retry.
```

The reason text matches the validation reason categories used in `/print all`'s
Message 1.

## Total Parser

Replace the current parser in `execution/filex_payload_builder.py`. The new
implementation:

1. If the field is `int` or `float`, treat it as `float(field)` and skip to step 4.
2. If the field is a string:
   a. Run regex against the **first** match of:
      `\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?`
   b. Strip commas from the match.
   c. Parse as `float`.
   d. If no match found, raise `ValidationError("invalid total: ...")`.
3. If the field is `None` or any other type, raise `ValidationError("invalid total: ...")`.
4. Reject any result with `value < 0` — raises `ValidationError("invalid total: ...")`. Zero is allowed (exchange orders).

The regex captures either:
- A grouped form with thousand separators: `1,234.56`, `1,234,567.89`
- A plain decimal: `300.99`, `1949.39`, `300`

Whichever pattern starts earlier in the string wins (regex's natural first-match
behavior). Trailing notes, `AED` prefix/suffix, slashes (`/-`), or whitespace
do not interfere because the regex matches the digit run regardless of
surrounding characters.

### Test matrix

| Input | Expected output |
|---|---|
| `"300"` | `300.0` |
| `"300.99"` | `300.99` |
| `"AED 300.00"` | `300.0` |
| `"AED1,111.99"` | `1111.99` |
| `"AED1,949.39\n\nnote: send all good quality and same"` | `1949.39` |
| `"AED 1,234,567.89"` | `1234567.89` |
| `"300/-"` | `300.0` |
| `"AED"` | raises ValidationError |
| `""` | raises ValidationError |
| `None` | raises ValidationError |
| `0` | `0.0` (allowed for exchange orders) |
| `0.0` | `0.0` (allowed for exchange orders) |
| `-5` | raises ValidationError (negative) |

## Architecture

No new modules. Changes are localized to:

- `execution/order_bridge.py`
  - `cmd_print_all`: replaces eligibility filter with simple Status=Processed
    query, adds two-phase classification (validation skips → already-labeled
    skips → placement), batches skip output into single messages.
  - New `cmd_print_one(update, context)` handler dispatched when `/print
    <ORDER_ID>` is received with a non-`all` argument. The existing handler is
    refactored to dispatch to `cmd_print_all` when `args == ["all"]` and to
    `cmd_print_one` when `args == [<order_id>]`.
- `execution/notion_client.py`
  - New `query_filex_processed()` returning every Processed order regardless of
    checkboxes. The existing `query_filex_eligible()` is left in place for any
    other callers (none today, but kept for safety).
- `execution/filex_payload_builder.py`
  - `parse_total()` (new helper, extracted from the inline logic in
    `build_payload` and `build_merged_payload`) replaces both call sites.
  - Validation error messages are normalized to match the categories used by
    the new skip-message structure (`missing city in address`, `invalid total`,
    `missing phone`, `missing customer name`, `missing address`).
- `execution/filex_client.py`
  - No changes; existing `place_orders` and `get_label_pdf` are reused.

## Data Flow

`/print all`:

```
Telegram /print all
    └─→ cmd_print_all
         ├─ query Notion for Status=Processed
         ├─ classify each row:
         │     ├─ FILEX STATUS non-empty → already_labeled[]
         │     ├─ validation fail        → validation_skips[reason][]
         │     └─ otherwise               → to_place[]
         ├─ if validation_skips: send_message(grouped summary, single API call)
         ├─ if already_labeled: send_message(list, single API call)
         ├─ if to_place is empty: send "no labels generated"; return
         ├─ group to_place by phone (existing merge logic, unchanged)
         ├─ build payloads (using new parse_total)
         ├─ lock all page_ids: Filex Submitted = ✓
         ├─ filex.place_orders(payloads)
         │     └─ on failure: revert all locks, send error message, return
         ├─ for each returned (ref, tracking):
         │     write Tracking, Filex Status="Label Created",
         │     Dispatched At, Last Update to all linked rows
         ├─ filex.get_label_pdf(trackings)
         └─ send_document(pdf, caption)
```

`/print <ORDER_ID>`:

```
Telegram /print AM3030
    └─→ cmd_print_one(order_id="AM3030")
         ├─ Notion lookup (with WA127/WA 127 tolerance)
         ├─ if not found        → reply "not found in CRM"
         ├─ if status != Processed → reply "status is X — only Processed can be printed"
         ├─ if FILEX STATUS set   → fetch existing label, send with "already has label" caption
         ├─ validate the order   → on fail, reply with reason
         ├─ build payload, place via Filex, write Notion fields
         ├─ filex.get_label_pdf([tracking])
         └─ send_document(pdf, caption)
```

## Error Handling

- **Filex API failure** (`/print all`): existing behavior — revert all `Filex
  Submitted` locks, send a single error message containing the exception text,
  return without sending the PDF.
- **Filex API failure** (`/print <ID>`): same — revert the single lock, reply with
  the error message.
- **PDF fetch failure after successful placement**: existing behavior — Notion
  is fully updated, but the label send fails. Reply tells the operator to
  re-run `/print <ID>` to retrieve the label (which will now hit branch `c`
  because FILEX STATUS is set).
- **Telegram send failure** is wrapped in `_safe_send_message` /
  `_safe_send_document` (existing helpers). Errors are logged, not raised — the
  Notion writes have already happened.

## Testing

- Unit tests for `parse_total()` covering every row in the test matrix above
  (including the bug input that produced `1.00`).
- Unit tests for the order-ID matching helper (`AM3030`, `WA127`, `WA 127`,
  unknown ID, lowercase variant).
- Integration test (mock Filex client + mock Notion client) covering:
  - `/print all` with a mix of valid, invalid-city, invalid-total, and
    already-labeled orders. Asserts exactly N Telegram messages, content of
    each.
  - `/print <ID>` for each branch (a-d).
- Manual smoke test in the staging fulfillment Telegram group with one Processed
  order before merging.

## Rollout

1. Implement the parser change with its tests.
2. Implement `cmd_print_one` and the dispatch refactor.
3. Implement the `cmd_print_all` rewrite.
4. Smoke test in fulfillment group.
5. Merge to main, restart `order-bridge.service`.

## Out of Scope (Future Work)

- Bulk re-fetch of labels for arbitrary date ranges (`/print labels 2026-05-04`).
- A neighborhood→city lookup table (operators clean addresses instead).
- Self-healing for orders stuck with `Filex Submitted = ✓` but no tracking number
  (caused by the upstream AWB-flip bug). The reconcile job partially handles
  this; a dedicated repair flow can come later.
