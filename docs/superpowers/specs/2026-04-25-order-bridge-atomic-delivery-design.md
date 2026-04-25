# Order Bridge: Atomic Delivery + File ID Reuse

**Date:** 2026-04-25
**Status:** Design approved, awaiting implementation plan
**Scope:** Order bridge bot — Stage 1 (Notion → Sourcing) and Stage 2 (`#ready` → Fulfillment)

---

## Problem

The current order bridge sends three separate Telegram messages per order: a media group of images, a text message with order details, and a black separator image. When multiple orders process concurrently, Telegram delivers messages in network arrival order rather than submission order, causing visible interleaving — operators see images attached to the wrong order's text, or text with no images directly above it. This is the source of perceived "missing images" complaints.

A separate problem: Stage 2 (`#ready`) re-fetches the same images from Shopify GraphQL that were already successfully sent in Stage 1. Each re-fetch is an opportunity for failure, and there is no benefit to re-fetching images Telegram has already cached.

## Goals

1. Eliminate visible message interleaving in both Telegram chats
2. Eliminate Stage 2 re-fetch by reusing Telegram-cached images
3. Make any failure visible to operators (no silent loss)
4. Keep operator workflow unchanged (same Notion status flips, same `#ready ALL` command)

## Non-goals

- Strict FIFO ordering across orders (operator does not need it)
- Performance optimization beyond what falls out of the design
- Webhook migration (separate follow-up project)

---

## Architecture

### Stage 1: Notion → Sourcing group

The poller picks up an order with `ORDER STATUS = Confirmed Processed` and `SOURCING NOTIFIED = false`. Orders are processed **sequentially** — each order completes (album + separator + Notion writes) before the next order starts. This is the only mechanism that guarantees the separator lands directly under its own order's caption.

Per-order flow:

1. Fetch images: GraphQL via `ORDER SOURCE URL` (primary), Notion `IMAGE URL` files property (fallback). This logic was shipped earlier and is unchanged.
2. Build one or more Telegram media groups from the images. Telegram caps media groups at 10 items, so an order with 15 images becomes 2 albums (10 + 5). The caption (Order ID + brief details) is attached to the **first image of the LAST album**, so it visually closes the order block.
3. `await send_media_group(album)` for each album in order.
4. `await send_message(separator_image)` to send the black separator.
5. Mark `SOURCING NOTIFIED = true` immediately. This prevents re-sending images on subsequent polling cycles regardless of what happens with the file_id bookkeeping below.
6. Save `file_id`s returned from step 3 to Notion `TELEGRAM FILE IDS` (comma-separated). Use **in-process retries with exponential backoff**: 3 attempts at 1s, 2s, 4s.
7. On successful save, set `FILE IDS SAVED = true`.
8. On all 3 retries failing, leave `FILE IDS SAVED = false` and post a warning message in the sourcing group: `⚠️ AM1664: file IDs not saved after 3 retries, manual fulfillment may be needed`.

### Stage 1 image count check

After successful image send, compare delivered image count to expected item count (parsed from Notion `ITEM | QTY`, counting line entries — every item is qty 1, so line count equals item count). If the counts differ, post a follow-up message in the sourcing group: `⚠️ AM1664: 3 of 4 images sent, 1 missing`. The order is still marked notified — operator handles the discrepancy manually.

### Stage 2: `#ready` → Fulfillment group

When the operator types `#ready ALL` or `#ready <ORDER_ID>` in the sourcing group, the bot processes each matching order sequentially.

Per-order flow:

1. Read `FILE IDS SAVED` from Notion.
2. **If `true`:** read `TELEGRAM FILE IDS`, split into albums of up to 10 file_ids each, build media groups using the file_id references. Caption goes on the first image of the **last album** with full customer details (name, address, phone, total, internal note). Send to fulfillment group, then send separator.
3. **If `false`:** fall back to the existing GraphQL re-fetch path. Build albums from freshly downloaded images. Same caption-on-last-album logic. Post a warning in the fulfillment group: `⚠️ AM1664: no file IDs were saved, resorted to fallback fetch`.
4. Mark `FULFILLMENT NOTIFIED = true` and set `ORDER STATUS = Processed`.

### Caption overflow rule

Telegram caption max is 1,024 characters. Stage 1 captions are short (Order ID + brief details, ~100 chars) and never overflow. Stage 2 captions are longer (~250-400 chars typically) but can spike if a customer has a long address plus a long internal note. The fallback rule applies to both stages: if the caption would exceed 1,024 characters, send the album with a short caption (Order ID + customer name + total only) and the full text as a `reply_to_message_id` reply to the album. The reply attaches visually to the album, preserving the atomic appearance.

---

## Notion schema additions

Both properties were added to the orders data source on 2026-04-25 via [scratch/add_telegram_file_ids_field.py](../../../scratch/add_telegram_file_ids_field.py):

| Property name | Type | Purpose |
|---|---|---|
| `TELEGRAM FILE IDS` | rich_text | Comma-separated Telegram file_ids saved during Stage 1, reused during Stage 2 |
| `FILE IDS SAVED` | checkbox | True when file_ids successfully persisted; gates Stage 2's fast path vs fallback |

Operator should hide both columns from the agent-facing Notion view — they are bot plumbing.

---

## Failure handling matrix

| Failure | What the bot does | What the operator sees |
|---|---|---|
| Image download fails (Shopify CDN timeout) | Existing behavior: log warning, skip that image, continue with others | Image count mismatch warning in sourcing chat |
| Telegram media group send fails | Existing behavior: catch exception, post URL-list fallback message, return False | Plain-text URL list in chat instead of album |
| Notion file_id write fails (after 3 retries) | `SOURCING NOTIFIED` already marked true at step 5; leave `FILE IDS SAVED = false` and post warning | Warning in sourcing chat; Stage 2 will use GraphQL fallback automatically |
| Image count < item count | Mark notified anyway, post mismatch warning | Warning in sourcing chat |
| Stage 2 finds `FILE IDS SAVED = false` | Falls back to GraphQL re-fetch, posts warning | Order still goes to fulfillment correctly; warning surfaces the recovery |
| Stage 2 file_id rejected by Telegram (extremely rare) | Falls back to GraphQL re-fetch path | Same recovery, no special handling needed |

---

## Visual layout reference

**Order with 1-10 items, Stage 1 (sourcing):**
```
┌─────┬─────┐
│ img1│ img2│       ← single album
├─────┼─────┤
│ img3│     │
└─────┴─────┘
"📦 Order ID: AM1664"   ← caption on this album (the last/only one)
[⬛ separator]
```

**Order with 15 items, Stage 1 (sourcing):**
```
┌─────┬─────┬─────┐
│ img1│ img2│ img3│
├─────┼─────┼─────┤   ← Album 1: 10 images, NO caption
│ ... │ ... │ ... │
└─────┴─────┴─────┘
┌─────┬─────┐
│img11│img12│
├─────┼─────┤        ← Album 2: 5 images
│img13│img14│
├─────┼─────┤
│img15│     │
└─────┴─────┘
"📦 Order ID: AM1664"   ← caption on the LAST album
[⬛ separator]
```

**Stage 2 (fulfillment), same order rebuilt from saved file_ids:**
```
[Album(s) of cached images via file_id reuse — same chunking rule]
"🚚 Order ID: AM1664
 Customer: S Q
 Phone: 0566960022
 Product:
 * Quantity: 1
 * Quantity: 1
 Amount: AED 528.30
 Delivery Address: ..."
[⬛ separator]
```

---

## What changes in the codebase

| File | Change summary |
|---|---|
| [execution/telegram_client.py](../../../execution/telegram_client.py) | `send_order_to_group()` rewritten to build atomic media groups with caption-on-last-album. New helper to chunk images into 10-per-group. Returns list of `file_id`s from successful sends. New helper for caption overflow → reply rule. |
| [execution/notion_client.py](../../../execution/notion_client.py) | Add `FIELD_TELEGRAM_FILE_IDS` and `FIELD_FILE_IDS_SAVED` constants. Add helpers to read/write both. Add `parse_order()` extension to expose them in the order dict. |
| [execution/order_bridge.py](../../../execution/order_bridge.py) | `poll_notion_once()` switches from concurrent `asyncio.gather` to sequential `await` per order. New retry helper for Notion writes (3 attempts, exponential backoff). Stage 2 (`#ready` handler) reads `FILE IDS SAVED` to decide fast path vs GraphQL fallback. |

No new dependencies, no new env vars, no infra changes.

---

## Out of scope (future work)

- **Notion webhook migration:** replace 30s polling with event-driven webhook plus polling as low-frequency safety net. Discussed and agreed in principle, deferred to a separate spec.
- **Per-order failure dashboard in Notion:** dedicated view of orders that hit warnings. Skipped for now per low-volume constraint.
- **Strict FIFO ordering:** operator confirmed this is not required.

---

## Manual operator steps

1. Hide `TELEGRAM FILE IDS` and `FILE IDS SAVED` columns from the agent-facing Notion view.
2. Optionally update [directives/order_automation.md](../../../directives/order_automation.md) to reflect the new flow once shipped.

No other manual changes required.
