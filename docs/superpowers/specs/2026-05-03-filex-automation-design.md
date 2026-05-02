# Filex Automation — Design Spec

**Date**: 2026-05-03
**Owner**: Operations Automation
**Status**: Approved for implementation

---

## Context

Filex is the courier we use for COD deliveries across UAE. Today, the fulfillment team manually creates each shipment in Filex's app — typing customer name, address, phone, COD value, and item description — then prints labels. Once dispatched, status updates (delivered / cancelled / returned) live only in Filex's dashboard, with no flow back to the Notion CRM that drives the rest of our order pipeline (Order Bridge, WhatChimp confirmation, Telegram fulfillment).

Over the past 3 days we proved the Filex API end-to-end with 81 real orders: `placebulk` accepts the same data the team types manually, returns tracking numbers + a combined PDF of labels, and Filex now offers a webhook for real-time status push. With the API verified and webhooks available, we can replace manual entry + manual status checking with one Telegram command (`/print all`) plus a passive webhook listener.

**Outcome**: 100% visibility on every dispatched order — from "Label Created" through "Delivered / Return to Origin" — directly inside the Notion CRM the team already lives in. Zero manual data entry. Zero polling. Status updates flow live.

---

## Goals

- One Telegram command in the fulfillment group (`/print all`) places all eligible Notion orders in Filex and returns a combined PDF of labels.
- Tracking number + tracking link automatically populate in the CRM for each placed order.
- Filex status changes flow into Notion in near-real-time via webhook.
- Stuck orders (>24h at "To be Picked Up") trigger a Telegram alert.
- Nightly reconciliation catches any webhook events missed during service downtime.

## Non-Goals

- No `/print <specific-order>` command in v1 — `/print all` only.
- No cancellation/edit flows — Filex's API doesn't support them.
- No multi-account brand routing — single account `SH4866`. (Add later if separate brand accounts open up.)
- No bot-side cash reconciliation for "Receiver Cancelled With Money" — just status update; team handles cash.

---

## Architecture

Follows the existing 3-layer pattern (`directives/` → orchestration → `execution/`). The new bot lives **inside** the existing GCP VM service (`order_bridge`), not as a separate process.

### File layout

```
Automation/
├── execution/
│   ├── notion_client.py            ← extend (new property setters: tracking, filex_status, etc.)
│   ├── order_bridge.py             ← extend (add /filex_webhook route + /print all command)
│   ├── filex_client.py             ← NEW: API wrapper (auth cache, placebulk, status, label PDF)
│   ├── filex_status_mapper.py      ← NEW: Filex status text → Notion FILEX STATUS value
│   ├── filex_city_normalizer.py    ← NEW: full address → Filex-spec city
│   ├── filex_reconcile.py          ← NEW: nightly catch-up job
│   └── filex-reconcile.timer       ← NEW: systemd timer (runs nightly)
├── directives/
│   └── filex_automation.md         ← NEW: SOP for the team
└── .env                             ← extend: FILEX_USERNAME, FILEX_PASSWORD,
                                                 FILEX_ACCOUNT_NUMBER, FILEX_WEBHOOK_TOKEN,
                                                 FILEX_TRACKING_BASE_URL
```

### Process boundaries

| Module | Pure / Stateful | Depends on |
|---|---|---|
| `filex_client.py` | Pure (wraps HTTP) | `requests`, `.env` |
| `filex_status_mapper.py` | Pure (lookup) | nothing |
| `filex_city_normalizer.py` | Pure (lookup + fuzzy) | `rapidfuzz` (or Levenshtein) |
| `notion_client.py` | Stateful (writes Notion) | Notion API |
| `order_bridge.py` route handlers | Orchestrates above | telegram + Flask |
| `filex_reconcile.py` | Standalone CLI script | all of the above |

**Why this split**: each pure module is testable in isolation. `filex_client` can be tested against the real Filex sandbox without Notion. `filex_city_normalizer` can be tested with a fixture file of addresses. The orchestration in `order_bridge.py` is thin glue.

---

## Notion Schema (additions to existing CRM database)

| Field Name | Type | Purpose | Default |
|---|---|---|---|
| `Tracking Number` | Rich text | Filex AWB (digits only) | empty |
| `Tracking Link` | URL | Constructed from `FILEX_TRACKING_BASE_URL` + AWB | empty |
| `FILEX STATUS` | Select | Current state (auto-creates options on write) | empty |
| `FILEX NOTES` | Rich text | `Notes` field from webhook payload | empty |
| `Filex Submitted` | Checkbox | Lock to prevent double-submission | unchecked |
| `Dispatched At` | Date | When `/print all` placed it in Filex | empty |
| `Last Update` | Date | `OrderDate` from latest webhook event | empty |

### `FILEX STATUS` initial select values

```
Label Created                      ← To be Picked Up
Handed Off                         ← Received at Hubs
Shipped                            ← Assigned to Driver
Delivered                          ← (NOT terminal — can revert to Cancelled)
Cancelled                          ← intermediate
Receiver Cancelled With Money      ← intermediate (special)
Received in CSA                    ← intermediate
Return to Origin                   ← TERMINAL
LC                                 ← Location Changed (no OPS yet)
SFT                                ← Schedule for Tomorrow (no OPS yet)
MNA                                ← Mobile Not Answered (no OPS yet)
FD                                 ← Future Delivery (no OPS yet)
LC-OPS / SFT-OPS / MNA-OPS / FD-OPS    ← compound when In OPS arrives
In OPS                             ← raw fallback if no preceding sub-reason
```

Notion auto-creates new select options when the API writes an unseen value, so unknown future statuses from Filex won't break the bot.

### Field write triggers

| Trigger | Fields written |
|---|---|
| `/print all` succeeds | `Tracking Number`, `Tracking Link`, `FILEX STATUS = "Label Created"`, `Filex Submitted = true`, `Dispatched At = now()`, `Last Update = now()` |
| Webhook arrives | `FILEX STATUS = mapped`, `FILEX NOTES = payload.Notes`, `Last Update = payload.OrderDate` |
| Reconcile poll detects drift | Same as webhook |

---

## `/print all` Command Flow

Trigger: anyone in fulfillment Telegram group types `/print all`.

```
1. QUERY Notion CRM:
   filter:
     ORDER STATUS == "Processed"
     AND SOURCING NOTIFIED == true
     AND FULFILLMENT NOTIFIED == true
     AND Filex Submitted == false

2. VALIDATE each matched order:
   ✓ CUSTOMER NAME present
   ✓ PHONE present (normalize via existing whatchimp_client.clean_phone_number)
   ✓ FULL ADDRESS present
   ✓ City extractable (filex_city_normalizer with fuzzy fallback)
   ✓ TOTAL is a number (0 allowed for exchanges)
   ✗ On any failure: send "⚠️ Skipped {ORDER_ID}: {reason}" in Telegram, exclude from batch

3. PARSE NumberOfPieces:
   - Split ITEM | QTY field by newlines
   - For each line, extract trailing integer as that item's qty
   - Sum all qtys; if parse fails or sum == 0, default to 1

4. LOCK orders BEFORE API call:
   For each valid order: PATCH Notion to set Filex Submitted = true
   (This is the idempotency safeguard — even if step 5 crashes, locked orders
    are excluded from the next /print all run.)

5. CALL Filex placebulk with the full batch:
   - Auth (cached token, refresh if >23h old)
   - POST /api/order/placebulk
   - On success: response contains tracking_no per ShipperRef
   - On network error or 4xx/5xx: REVERT all locks (set Filex Submitted = false),
     post error to Telegram, abort

6. UPDATE Notion per order:
   - Tracking Number = response.tracking_no
   - Tracking Link = FILEX_TRACKING_BASE_URL + tracking_no
   - FILEX STATUS = "Label Created"
   - Dispatched At = now()
   - Last Update = now()

7. FETCH combined PDF:
   - GET /api/order/GetAirWayBill?TrackingNos={comma-separated}
   - Detect empty pages (content stream <1000 bytes), drop them
   - Reassemble cleaned PDF

8. SEND PDF to Telegram fulfillment group as file attachment.
   No text summary in the group — Notion has all the data.
```

### Concurrent `/print all` calls

The `Filex Submitted = true` lock per-order makes it safe. Two team members triggering simultaneously will partition the eligible orders between them; both calls succeed independently. No global lock needed.

### Auth caching

`filex_client.py` caches the bearer token in memory. Refresh only when `(now - token_obtained_at) > 23h` or on 401 response. One auth call per day under normal load.

---

## Webhook Receiver Flow

Endpoint: `POST http://34.30.125.177:8080/filex_webhook`
(HTTPS via Cloudflare Tunnel if Filex requires it.)

### Expected payload (from Filex's developer)

```json
{
  "Token": "<shared-secret>",
  "Status": "Delivered",
  "TrackingNo": "4866846054",
  "Track_id": "4866846054",
  "ShipperRef": "AM2985",
  "Notes": "",
  "OrderDate": "2026-05-02T14:37:00"
}
```

### Handler logic

```
1. Parse JSON. If malformed → return 400.

2. Verify Token == env.FILEX_WEBHOOK_TOKEN.
   On mismatch → return 401, log attempt with source IP.

3. Look up Notion order WHERE ORDER ID == ShipperRef.
   On not-found → return 200 (don't trigger Filex retry loop),
                  log to .tmp/filex_unknown_refs.log.

4. Stale-event check:
   parsed_event_time = parse(OrderDate)
   if Notion.Last Update is set AND parsed_event_time < Notion.Last Update:
     return 200 (idempotent, ignore stale)

5. Map Status to FILEX STATUS via filex_status_mapper.
   For "In OPS": look at current Notion FILEX STATUS:
     MNA  → "MNA-OPS"
     SFT  → "SFT-OPS"
     LC   → "LC-OPS"
     FD   → "FD-OPS"
     else → "In OPS"

6. Update Notion:
   - FILEX STATUS = mapped value
   - FILEX NOTES = Notes (only if non-empty; don't clobber existing)
   - Last Update = parsed_event_time

7. Respond with EXACT shape Filex expects (preserve "isUpdeted" typo):
   { "code": 200, "isUpdeted": true, "message": "Updated successfully" }
```

### Idempotency

Webhook handler is fully idempotent:
- Same payload twice → same result, second call no-ops effectively
- Stale-event check protects against out-of-order delivery
- Notion writes are PATCH operations (overwrite, not append)

---

## Status Mapper

```python
DIRECT_MAP = {
    "Order Placed":                     "Label Created",
    "To be Picked Up":                  "Label Created",
    "Received at Hubs":                 "Handed Off",
    "Assigned to Driver":               "Shipped",
    "Delivered":                        "Delivered",
    "Cancelled":                        "Cancelled",
    "Receiver Cancelled With Money":    "Receiver Cancelled With Money",
    "Received in CSA":                  "Received in CSA",
    "Return to Origin":                 "Return to Origin",
    "Location Changed":                 "LC",
    "Schedule for tomorrow":            "SFT",
    "Mobile Not Answered":              "MNA",
    "Future Delivery":                  "FD",
}

IN_OPS_COMPOUND = {
    "MNA": "MNA-OPS",
    "SFT": "SFT-OPS",
    "LC":  "LC-OPS",
    "FD":  "FD-OPS",
}

def map_status(filex_status: str, current_notion_status: str) -> str:
    if filex_status.strip() == "In OPS":
        return IN_OPS_COMPOUND.get(current_notion_status, "In OPS")
    return DIRECT_MAP.get(filex_status.strip(), filex_status.strip())
```

Unknown statuses pass through as-is, getting auto-created as new Notion select options. Logged for review.

---

## City Normalizer

```python
ALIAS_TO_FILEX = {
    # canonical key = exact Filex spelling
    "Dubai":          ["dubai", "dxb", "doboi", "duba", "dubaai", "dubia"],
    "Abu Dhabi":      ["abu dhabi", "abudhabi", "abu-dhabi", "auh", "abudabi", "abu dabi"],
    "Sharjah":        ["sharjah", "shj", "sharja"],
    "Ajman":          ["ajman", "ajm", "ajaman"],
    "Al Ain":         ["al ain", "alain", "al-ain", "aln"],
    "Fujeriah":       ["fujairah", "fujarah", "fujaira", "fuj", "fujeriah"],
    "Um Al Qwain":    ["umm al quwain", "um al qwain", "uaq", "umalquwain"],
    "Ras Al Khaimah": ["ras al khaimah", "ras al-khaimah", "rak", "raskh"],
}

def normalize_city(full_address: str) -> str | None:
    text = full_address.lower()
    # 1. Direct alias match (whole-word)
    for filex_name, aliases in ALIAS_TO_FILEX.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return filex_name
    # 2. Fuzzy fallback (Levenshtein <= 2)
    tokens = re.findall(r"[a-z]+", text)
    for token in tokens:
        for filex_name, aliases in ALIAS_TO_FILEX.items():
            for alias in aliases:
                if levenshtein(token, alias) <= 2 and len(alias) >= 4:
                    return filex_name
    return None  # bot flags this order in Telegram
```

If multiple cities match (e.g., `"Fujairah Abu Dhabi"`), pick the first one found in the address (UAE addresses typically lead with the actual city).

---

## Edge Cases & Reconciliation

### Stuck-order alert (nightly, ~11 PM)

```
Query Notion:
  Filex Submitted == true
  AND FILEX STATUS == "Label Created"
  AND Dispatched At < now() - 24h

If matches:
  Telegram message to fulfillment group:
    "⚠️ N order(s) stuck at 'Label Created' for 24h+:
     - AM2983 (4866846160) — Anas Ballol
     - AM2974 (4866846166) — Khadija Mansoor
     Investigate with Filex / fulfillment."
```

### Reconciliation poll (nightly, after stuck-alert)

```
Query Notion:
  Filex Submitted == true
  AND FILEX STATUS != "Return to Origin"
  AND Dispatched At >= now() - 14d

Batch tracking numbers (max 50 per call):
  POST /api/order/ShipmentLastStatus
  For each result:
    if filex_status != notion_status:
      run same update path as webhook handler

Orders with Dispatched At < (now - 14d) auto-archive: skipped from poll.
```

### Failure handling

| Scenario | Bot action |
|---|---|
| Network error during placebulk | Revert all `Filex Submitted` locks, log error, Telegram message |
| Filex API rejects payload (4xx/5xx) | Same as above, with API error in message |
| Notion update fails after Filex placement | Don't unlock; log to `.tmp/filex_orphans.log` for manual recovery |
| Webhook arrives during service restart | Lost. Caught by next reconciliation poll. |
| Empty page in batched PDF (Filex bug) | Detected by content-stream-size check, dropped silently |
| Unknown ShipperRef in webhook | Return 200 (avoid retry loop), log to `filex_unknown_refs.log` |
| Unknown Status text in webhook | Pass through verbatim, auto-creates Notion select option, log |

### Filex empty-page detection

PDF parsing logic (already proven from test scripts):
```python
content_refs = re.findall(rb"/Contents\s+(\d+)", raw_pdf)
for ref in content_refs:
    stream = extract_stream_for_object(ref)
    if len(zlib.decompress(stream)) < 1000:
        skip_this_page()
```

---

## Critical Files & References

### Existing files to extend
- [execution/notion_client.py](execution/notion_client.py) — add setters: `set_tracking_number`, `set_filex_status`, `mark_filex_submitted`, etc.
- [execution/order_bridge.py](execution/order_bridge.py) — add `/filex_webhook` route alongside `/whatchimp_webhook`; add `/print all` Telegram handler
- [.env](.env) — new keys: `FILEX_USERNAME`, `FILEX_PASSWORD`, `FILEX_ACCOUNT_NUMBER`, `FILEX_WEBHOOK_TOKEN`, `FILEX_TRACKING_BASE_URL`

### New files to create
- `execution/filex_client.py` — API wrapper
- `execution/filex_status_mapper.py` — status conversion
- `execution/filex_city_normalizer.py` — city extraction
- `execution/filex_reconcile.py` — nightly job
- `execution/filex-reconcile.service` + `.timer` — systemd units
- `directives/filex_automation.md` — SOP
- `scripts/setup_notion_fields.py` — one-time field creation script

### Existing utilities to reuse
- `whatchimp_client.clean_phone_number()` — phone normalization
- `notion_client._get_*` helpers — property extraction
- The brand-prefix logic (`PT`, `AM`, `VX`, `Di`, `LU`) — for any future per-brand routing

---

## Verification

### Per-module tests

| Module | Test approach |
|---|---|
| `filex_client.py` | Run against test creds (`testapi/SH0052`), verify auth, placebulk, status, label endpoints |
| `filex_status_mapper.py` | Unit test all DIRECT_MAP entries + In OPS compound logic + unknown fallback |
| `filex_city_normalizer.py` | Fixture file with all 81 addresses we've seen, assert correct emirate for each |

### End-to-end manual test sequence

1. Add new fields to Notion CRM via `scripts/setup_notion_fields.py`. Verify in UI.
2. Place a test order in CRM with all required fields. Set status to "Processed", check both checkboxes.
3. Run `/print all` from a Telegram test group connected to a dev bot.
4. Verify: PDF received, Notion fields populated (Tracking Number, Tracking Link, FILEX STATUS=Label Created, Filex Submitted=true, Dispatched At=now).
5. Wait for real Filex status updates OR manually POST a fake webhook payload via curl:
   ```bash
   curl -X POST http://34.30.125.177:8080/filex_webhook \
     -H "Content-Type: application/json" \
     -d '{"Token":"<token>","Status":"Assigned to Driver","TrackingNo":"X","Track_id":"X","ShipperRef":"AM_TEST","Notes":"","OrderDate":"2026-05-04T08:00:00"}'
   ```
6. Verify Notion `FILEX STATUS` updates to "Shipped" and `Last Update` reflects the timestamp.
7. Trigger reconcile manually (`python execution/filex_reconcile.py`); verify it touches no records when state is in sync.
8. Force a status drift (manually edit Notion FILEX STATUS to wrong value), re-run reconcile, verify it corrects.

### Production handover

1. Deploy webhook receiver only.
2. `curl` test to confirm 200 response.
3. Share `http://34.30.125.177:8080/filex_webhook` with Filex developer.
4. Wait for first real webhook event from any of the 81 active orders.
5. Verify Notion field updates correctly.
6. Roll out `/print all` next.
7. Roll out reconciliation job last.

---

## Open Items (resolve during implementation)

- **HTTPS requirement** — confirm with Filex dev whether HTTP works or HTTPS is mandatory. If HTTPS, set up Cloudflare Tunnel before sharing URL.
- **`FILEX_TRACKING_BASE_URL`** — confirm exact tracking page URL format from Filex (likely `https://www.filexexpress.ae/track?awb=` or similar).
- **`FILEX_WEBHOOK_TOKEN`** value — Filex dev to provide actual token (not the `4tn5` placeholder in his message).

---

## Out of scope for v1 (future iterations)

- Per-brand Filex accounts (currently single `SH4866`)
- Outbound shipping cost prediction (would require Filex pricing API if exists)
- Customer-facing tracking notifications via WhatsApp
- Auto-refund on `Receiver Cancelled With Money` (manual cash reconciliation for now)
- Image/proof-of-delivery storage from Filex (not exposed by their API today)
