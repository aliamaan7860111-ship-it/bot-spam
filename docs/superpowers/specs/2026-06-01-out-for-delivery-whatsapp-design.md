# Out-for-Delivery WhatsApp Notification — Design

**Date:** 2026-06-01
**Status:** Approved design, pending implementation plan

## Goal

When an order's **`ORDER STATUS`** in the Notion CRM becomes **`🚚 SHIPPED`**, send the
customer a WhatsApp "out for delivery" message via WhatChimp. Each order is messaged
**exactly once**. The message carries the brand name and order ID (Amara excepted — see
below).

The `🚚 SHIPPED` status is set one of two ways:
- **Automatically** by `filex_reconcile.py`, which promotes `ORDER STATUS` to `🚚 SHIPPED`
  when Filex reports the parcel as `Assigned to Driver`
  (`ORDER_STATUS_FROM_FILEX["Shipped"]` in `filex_status_mapper.py`).
- **Manually** by an operator editing the record in Notion.

The automation must fire in both cases.

## Architecture — one sender, two triggers, one dedup gate

```
 Filex "Assigned to Driver"   ┌─────────────────────────────────┐
   → filex_reconcile.py    ──▶│  reconcile promotes ORDER STATUS │──┐ (at-source, instant)
     promotes ORDER STATUS    │  to 🚚 SHIPPED                    │  │
                              └─────────────────────────────────┘  │
                                                                    ▼
 Operator manually sets       ┌─────────────────────────────────┐  ┌──────────────────────────────┐
   ORDER STATUS = 🚚 SHIPPED ▶│  out_for_delivery.py poller      │─▶│ send_out_for_delivery(order) │
   in Notion                  │  (safety net, grace window)      │  │  • dedup checkbox gate       │
                              └─────────────────────────────────┘  │  • brand routing (prefix)     │
                                                                    │  • WhatChimp template send    │
                                                                    └───────────────┬──────────────┘
                                                                                    ▼
                                                                    WhatChimp → customer WhatsApp
                                                                    then ☑ "Out For Delivery Sent"
```

- **At-source (speed):** Right after `filex_reconcile.py` promotes `ORDER STATUS` to
  `🚚 SHIPPED`, it calls `send_out_for_delivery(order)`. Fires the instant the auto
  transition happens.
- **Poller (robustness):** A standalone loop in a new `execution/out_for_delivery.py`
  queries the CRM for `ORDER STATUS == 🚚 SHIPPED` AND `Out For Delivery Sent == false`,
  **and** `Last Update` older than a short grace window (default **2 minutes**). The grace
  window means the poller only ever catches what the at-source path missed (manual edits,
  or a failed at-source send) — it never races reconcile into a double-send.
- **Dedup gate:** a new Notion checkbox **`Out For Delivery Sent`**. The shared sender sets
  it only on confirmed send success. Both triggers funnel through that one function, so the
  checkbox is the single source of truth. (Distinct from the existing `Confirmation Sent`
  flag used by the order-confirmation bot.)

## Brand routing

Brand is resolved from the **ORDER ID prefix**. The existing `BRAND_CONFIG` in
`whatchimp_client.py` already maps prefixes → `phone_number_id` / confirmation `template_id`
/ `brand_display`. We extend it with `ofd_template_id` and `ofd_brand_display`, and add the
two new shared-number brands.

| Order-ID prefix | Brand | `phone_number_id` | `#!brand!#` value | OFD template |
|---|---|---|---|---|
| `PT` | Elara | 1031340813395459 | Elara UAE | 377952 |
| `Di` | Dialo | 1002123586328400 | Dialo UAE | 377954 |
| `LU` | Lune | 1138942462625909 | Lune Collection | 377955 |
| `PV` | Pelvini | 1138942462625909 *(shares Lune)* | Pelvini | 377955 *(shares Lune)* |
| `VX` | Virex | 1073890042476443 | Virex UAE | 377956 |
| `O`  | Orlento | 1073890042476443 *(shares Virex)* | Orlento | 377956 *(shares Virex)* |
| `AM` | Amara | 1045332455333591 | *(none)* | 377951 *(no variables)* |

### Prefix resolution

Orlento's prefix is a single character (`O`), but the current code hard-codes
`order_id[:2]`. Replace that with a **known-prefix matcher**: test the order ID against the
set of known prefixes, **longest first** (2-char prefixes `PT/Di/LU/PV/VX/AM` before the
1-char `O`). None of the 2-char prefixes begin with `O`, so this is unambiguous and remains
correct for the existing confirmation flow.

### Amara special case

Amara's approved template (`377951`) is the **old body with no variables**. The sender
routes Amara's number + template but **skips the variable prefill** — sends the template
with zero `templateVariable-*` params.

## Message template (already approved in Meta, category UTILITY)

Body (variables `#!id!#` = order ID, `#!brand!#` = brand display name):

> Good news — your order #!id!# from #!brand!# is now out for delivery and on the way to you.
>
> Please keep your phone available, as the delivery driver may contact you before arrival.
>
> We appreciate your order and hope you enjoy your purchase!

Status per brand: approved for Elara, Dialo, Lune, Virex (and Pelvini/Orlento via the
shared Lune/Virex numbers). **Amara** uses its older variable-less template until a
variabled one is approved.

On the wire, WhatChimp resolves `#!brand!#` / `#!id!#` from the call's
`templateVariable-brand-2` / `templateVariable-id-3` params — exactly what the existing
`send_template_message` already passes.

## Components

| Piece | Status |
|---|---|
| `execution/out_for_delivery.py` | **new** — `send_out_for_delivery(order, source)` + poll loop `main()` |
| `execution/whatchimp_client.py` | **edit** — add `ofd_template_id`/`ofd_brand_display` to `BRAND_CONFIG`; add `PV` + `O` entries; replace `[:2]` with known-prefix matcher; add an OFD send path (reuse `send_template_message` mechanics, OFD template, no buttons, Amara no-vars) |
| `execution/notion_client.py` | **edit** — add `mark_out_for_delivery_sent(page_id)` and a query for `ORDER STATUS == 🚚 SHIPPED & Out For Delivery Sent == false` (grace-window filtered) |
| Notion checkbox `Out For Delivery Sent` | **add** to the CRM (via the existing `bridge/add_notion_properties.py` pattern) |
| `execution/filex_reconcile.py` | **edit** — call `send_out_for_delivery(order)` when it promotes `ORDER STATUS` to `🚚 SHIPPED` |

OFD template IDs live in `BRAND_CONFIG` (in code), matching how the existing confirmation
`template_id`s are already stored there — not in `.env`. No `.env` change is required for
this feature.

## `send_out_for_delivery(order, source)` logic

1. If `Out For Delivery Sent` is already true → skip.
2. Respect `ORDER_CUTOFF_DATE` (don't message old backfilled orders).
3. Resolve brand from the ORDER ID prefix (known-prefix matcher) → `phone_number_id`,
   `ofd_template_id`, `ofd_brand_display`.
4. **If the brand's `ofd_template_id` is blank → log "pending template" and skip WITHOUT
   setting the checkbox** (auto-sends once the ID is filled). Same no-op-safe pattern as the
   recovery flow.
5. Prefill the subscriber (`create_or_update_subscriber`, scoped to that `phone_number_id`)
   and send the OFD template via the existing send path:
   - Non-Amara: pass `templateVariable-brand-2 = ofd_brand_display`,
     `templateVariable-id-3 = order_id`. No quick-reply buttons / confirm postback.
   - Amara: send template `377951` with no variable params.
6. On success → set `Out For Delivery Sent` ☑. On failure → log, leave unchecked, retry next
   poll tick.

## Error handling & edge cases

- **Idempotent** via the checkbox; safe to re-run.
- **No phone** on the order → log + skip; never crash the loop (per-order try/except, as in
  reconcile).
- **Send failure** → leave unchecked, retry next tick.
- **Manual `🚚 SHIPPED` edit** → caught by the poller within the grace window + one interval.
- **Template ID blank** (e.g. a future brand) → silent no-op, no checkbox set, picks up
  automatically once filled.
- **Unknown prefix** → log + skip (do not fall back to a default brand for an outbound
  customer message).

## Deployment

A new standalone loop fits the existing pattern: a `grq-ofd` systemd service on the GCP VM
(push → SSH → `git pull` → `systemctl restart grq-ofd`), reusing `POLL_INTERVAL_SECONDS`.
The at-source call ships inside the already-running `filex_reconcile` process.

## Open / deferred

- A variabled Amara template (replaces `377951`) — drop the Amara special case once approved
  and fill its `ofd_template_id`.
- Optional future enhancement: a tracking-link line once a tracking-capable template is
  approved.
