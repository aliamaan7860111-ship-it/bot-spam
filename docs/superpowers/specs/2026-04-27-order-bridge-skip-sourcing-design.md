# Order Bridge: Skip Sourcing Group + Per-Order Filex Label Replies

**Date:** 2026-04-27
**Status:** Design pending operator review
**Scope:** Order bridge bot + Filex `/print` commands
**Builds on:** [2026-04-25-order-bridge-atomic-delivery-design.md](2026-04-25-order-bridge-atomic-delivery-design.md) (atomic delivery, already shipped)

---

## Problem

Today the order bridge runs a two-stage flow:

1. Operator flips an order to `Confirmed | Processing`. The bot polls Notion, sends the order to the **sourcing group**, sets status to `SOURCING`, and saves the Telegram `file_id`s for later reuse.
2. Operator types `#ready ORDERID` or `#ready all` in sourcing chat. The bot reads the saved `file_id`s, rebuilds the album in the **fulfillment group** with full customer details as the caption, and sets status to `Processed`.

The sourcing group adds no value now that the team uses fulfillment as the single working chat. Every order goes through both groups; the manual `#ready` step is just busywork. Operators want the bot to skip sourcing entirely — order goes from `Confirmed | Processing` directly to `Processed`, fulfillment group receives the order in one shot.

A second pain point: the Filex `/print all` command currently calls `FilexClient.get_label_pdf(tracking_numbers)` once with all tracking numbers and posts **one combined multi-page PDF**. With 40 orders in a batch, the operator has to scroll through a 40-page PDF to find a specific order's label. Operators want each label posted as a **reply to that order's fulfillment message**, so each order has its label visually attached directly below it.

## Goals

1. Single delivery: Notion `Confirmed | Processing` → fulfillment group → `Processed`. No sourcing group, no `#ready` command.
2. `/print all` and `/print <ORDER_ID>` post per-order label PDFs as replies to each order's original fulfillment message, sorted chronologically.
3. Survive partial failure on multi-album orders (>10 items) without leaving orphan content or sending duplicates.
4. Strip dead sourcing infrastructure from code and Notion schema.

## Non-goals

- Order workflow visibility outside of Notion + fulfillment chat (no new dashboard, no separate failure log)
- Performance optimization beyond what falls out naturally
- Webhook migration (still deferred to a future spec)
- Cleanup of existing messages in the sourcing group (operator can mute or leave the group manually)

---

## Architecture

### Single-stage delivery flow

```
Operator flips order: ORDER STATUS = "Confirmed | Processing"
        ↓
Bot polls Notion every 5s (existing cadence)
        ↓
For each matched order, sequentially:
    Fetch images: GraphQL primary (ORDER SOURCE URL), Notion IMAGE URL files fallback
    Compute total_albums = ceil(len(downloaded_images) / 10)
    Read current ALBUMS SENT counter from Notion (defaults to 0)
    For album_index from ALBUMS SENT to total_albums - 1:
        is_last = (album_index == total_albums - 1)
        caption = full_fulfillment_caption if is_last else None
        Send media group to FULFILLMENT group → if it raises, exit loop
        On success:
            If is_last → save FULFILLMENT MESSAGE ID (first msg of this album)
            Increment ALBUMS SENT counter in Notion (with retry)
    After the loop:
        If ALBUMS SENT == total_albums:
            Send separator → set ORDER STATUS = Processed
        Else:
            Leave status as Confirmed | Processing — next poll resumes from where we stopped
```

### Per-album resilience

Sending is broken into per-album units. The `ALBUMS SENT` counter records how many albums have successfully delivered. Status only flips to `Processed` once all albums are sent; otherwise the order remains `Confirmed | Processing` and the next poll resumes at the unsent album.

Concrete example for a 15-item order with a Telegram blip mid-flight:

| Poll | total_albums | ALBUMS SENT before | What happens | ALBUMS SENT after | Final status |
|---|---|---|---|---|---|
| 1 | 2 | 0 | Album 1 (10 images, no caption) sends. Counter increments to 1. Album 2 send raises. | 1 | `Confirmed \| Processing` |
| 2 (~5s later) | 2 | 1 | Loop resumes at index 1. Album 2 (5 images + caption) sends. Counter increments to 2. Counter == total → flip status. | 2 | `Processed` |

The 10 images from poll 1 stay in the chat; poll 2 sends the missing 5 images + caption + separator below them. Visually: a 10-image block, then a 5-image block with caption, then separator. The break is unfortunate but contiguous — no duplication.

### Filex `/print` label-as-reply flow

```
Operator types /print all in fulfillment group
        ↓
query_filex_processed() returns orders with ORDER STATUS = Processed and matching eligibility
   (unchanged from existing rework)
        ↓
Validate orders, post any skip messages at the start (unchanged)
        ↓
For each eligible order, place in Filex (unchanged) → get tracking_number
        ↓
Sort the placed orders by FULFILLMENT MESSAGE ID ascending
   (Telegram message_ids are monotonic per chat → chronological order)
        ↓
For each placed order in sorted order:
    pdf = FilexClient.get_label_pdf([tracking_number])  # single tracking, returns one-page PDF
    If FULFILLMENT MESSAGE ID exists:
        bot.send_document(
            chat_id=fulfillment_group,
            document=pdf,
            filename=f"{order_id}.pdf",
            reply_to_message_id=FULFILLMENT MESSAGE ID,
        )
    Else (legacy order):
        bot.send_document(
            chat_id=fulfillment_group,
            document=pdf,
            filename=f"{order_id}.pdf",
            caption=f"📄 {order_id} — legacy order, no thread anchor",
        )
    On per-order Filex API or Telegram send failure:
        log error, post `⚠️ {order_id}: label fetch/send failed (see logs)` in fulfillment.
        Continue with the remaining orders.
```

`/print <ORDER_ID>` follows the same per-order branch — single label PDF, replies to the order's `FULFILLMENT MESSAGE ID`, or falls back to a legacy-style caption if missing.

---

## Notion schema changes

### Add

| Property | Type | Filled by | Purpose |
|---|---|---|---|
| `ALBUMS SENT` | number | Bot during Stage 1 send loop | Count of media groups successfully delivered; resume index for partial-success retries |
| `FULFILLMENT MESSAGE ID` | number | Bot when the last album sends | Telegram message_id of the caption-bearing message; used as `reply_to_message_id` by `/print` |

Both fields are bot plumbing — hide from the operator-facing Notion view.

### Remove

| Property | Reason |
|---|---|
| `SOURCING NOTIFIED` (checkbox) | No sourcing stage |
| `FULFILLMENT NOTIFIED` (checkbox) | Redundant with `ORDER STATUS = Processed` as the dedup gate |
| `TELEGRAM FILE IDS` (rich_text) | Was for file_id reuse between sourcing→fulfillment; not needed in single-send flow |
| `FILE IDS SAVED` (checkbox) | Was the gate for the Stage 2 fast path; not needed |
| `SOURCING` select option in `ORDER STATUS` | No intermediate status anymore |

The setup script will only **add** the new properties. The remove operations are operator manual steps to avoid risk of data loss — the script will print a list of properties to manually hide or delete in the Notion UI.

---

## Code changes

### `execution/notion_client.py`

- Add constants `FIELD_ALBUMS_SENT = "ALBUMS SENT"` and `FIELD_FULFILLMENT_MESSAGE_ID = "FULFILLMENT MESSAGE ID"`.
- Extend `parse_order()` to expose `albums_sent: int` (defaulting to 0) and `fulfillment_message_id: int | None`.
- Add `update_albums_sent(page_id: str, count: int) -> bool`.
- Add `update_fulfillment_message_id(page_id: str, message_id: int) -> bool`.
- Delete `query_sourcing_orders()`, `mark_sourcing_notified()`, `mark_fulfillment_notified()`, `unmark_notified()`.
- Delete `update_telegram_file_ids()`, `mark_file_ids_saved()`.
- Remove `telegram_file_ids` and `file_ids_saved` entries from `parse_order()` return dict.
- Remove `FIELD_SOURCING_NOTIFIED`, `FIELD_FULFILLMENT_NOTIFIED`, `FIELD_TELEGRAM_FILE_IDS`, `FIELD_FILE_IDS_SAVED` constants and `_parse_telegram_file_ids` helper.

### `execution/telegram_client.py`

- Rewrite `send_order_to_group()` to send one album at a time with a `start_album_index: int = 0` parameter and a callback or return value that lets the caller checkpoint after each successful album. Return shape becomes:

  ```python
  class SendOrderResult(TypedDict):
      success: bool             # True if ALL albums in range sent successfully
      albums_sent: int          # number of albums delivered in THIS call
      total_albums: int         # planned total for the order
      caption_message_id: int | None   # set when the last album (caption-bearing) was just sent
      image_count: int          # delivered images this call
      expected_count: int       # parsed from item_qty
  ```

- Delete `send_order_via_file_ids()` — no Stage 2 fast path anymore.
- Delete `forward_ready_orders()` and the `#ready` command handler. Remove its registration in `create_command_handlers()`.
- `_send_separator()` keeps its current behavior — called once at the end after all albums of an order are sent.
- Remove `SendOrderResult` fields `file_ids` (no longer captured).

### `execution/order_bridge.py`

- `poll_notion_once()` body rewritten:
  1. Filter to orders with `ORDER STATUS = "Confirmed | Processing"`.
  2. For each order, sequentially:
     - Call `send_order_to_group(bot, fulfillment_group, order, start_album_index=order["albums_sent"])` (note: fulfillment, not sourcing).
     - After **each** album sends successfully, persist the new `ALBUMS SENT` value to Notion using `notion_write_with_retry`. To keep the wire shape simple, expose the per-album checkpoint via a callback parameter on `send_order_to_group` (e.g. `on_album_sent: Callable[[int, int | None], None]`) that the bot supplies; the callback writes both `ALBUMS SENT` and (when `caption_message_id` is set) `FULFILLMENT MESSAGE ID`.
     - When the call returns with `success=True` and `albums_sent == total_albums`, flip `ORDER STATUS` to `Processed`.
     - Otherwise (raised partway), log and leave status alone for the next poll.
  3. Image count mismatch warning still fires after the final album when `delivered < expected`.
- `cmd_print_all()` and `cmd_print_one()` rewritten per the Filex flow above. The collective-PDF code path is deleted.
- The `#ready` command registration and `unmark_notified` calls disappear with the sourcing rip.
- Remove `TELEGRAM_SOURCING_GROUP_ID` import and any sourcing-group reference.

### `execution/filex_client.py`

- No code changes. `get_label_pdf([single_tracking])` already supports per-order labels.

### One-shot setup script

- New file: `scratch/add_order_bridge_v2_fields.py`. Adds `ALBUMS SENT` (number) and `FULFILLMENT MESSAGE ID` (number) to the orders data source. Prints the list of properties the operator should manually hide or delete (sourcing fields and old file_id fields).
- Idempotent: re-running is safe.

### Tests

- `execution/tests/test_album_helpers.py` — keep existing 13 tests, all should still pass.
- New file: `execution/tests/test_send_order_resume.py` — unit tests for the resume-from-checkpoint behavior on `send_order_to_group` using a Telegram bot mock.
- New file: `execution/tests/test_print_label_replies.py` — unit tests for sort-by-message-id ordering and the legacy-fallback caption format.

---

## Failure handling matrix

| Failure | What the bot does | What the operator sees |
|---|---|---|
| Image download fails (Shopify CDN) | Existing behavior: log warning, skip that image, continue | Image-count mismatch warning posts after the final album |
| Telegram media-group send fails | Loop exits; `ALBUMS SENT` not incremented for the failed album | No user-visible message; next poll cycle retries the missing albums |
| Notion `ALBUMS SENT` write fails after a successful Telegram send | 3-retry exponential backoff (existing `notion_write_with_retry`). If still failing, post `⚠️ {order_id}: ALBUMS SENT counter write failed after 3 retries` and exit the loop. | Operator sees the warning; next poll may re-send the just-completed album → orphan images. Rare edge case. |
| Final flip to `Processed` fails | 3-retry backoff. If still failing, post `⚠️ {order_id}: status flip to Processed failed` and exit. | Order stays `Confirmed | Processing`. Next poll sees `ALBUMS SENT == total_albums` and retries only the status flip — no re-sends. |
| Filex `/print` per-order label fetch fails | Log error, post `⚠️ {order_id}: label fetch failed (see logs)`. Continue with remaining orders. | Operator can re-run `/print <ORDER_ID>` for failed individual orders. |
| Filex `/print` per-order Telegram send fails | Same — log and post warning, continue with the rest. | Same recovery path. |
| Legacy order in `/print all` (no `FULFILLMENT MESSAGE ID`) | Post label as a regular fulfillment-group message with caption `📄 {order_id} — legacy order, no thread anchor`. | Label still appears; operator just doesn't get the threaded view. |

---

## Visual layout reference

**Sub-10-item order — Stage 1:**
```
[Album: imgs + caption "Order ID: AM2884 / Customer / Address / Phone / Total"]
[⬛ separator]
```

**15-item order — Stage 1 (multi-album):**
```
[Album 1: 10 images, no caption]
[Album 2: 5 images + caption "Order ID: AM2900 / ..."]
[⬛ separator]
```

**After /print all (with stored FULFILLMENT MESSAGE ID):**
```
[Album for AM2884]
"caption with customer details"
↳ [📄 AM2884.pdf]                ← label reply, indented under album
[⬛ separator]
[Album for AM2885]
"caption"
↳ [📄 AM2885.pdf]
[⬛ separator]
```

**Legacy order in /print all (no message_id):**
```
[📄 AM1100.pdf "📄 AM1100 — legacy order, no thread anchor"]   ← regular message, not threaded
```

---

## Operator manual steps after rollout

1. Run the setup script `scratch/add_order_bridge_v2_fields.py` to provision the new Notion properties.
2. In Notion, hide or delete these columns from operator-facing views (the script will print this list):
   - `SOURCING NOTIFIED`
   - `FULFILLMENT NOTIFIED`
   - `TELEGRAM FILE IDS`
   - `FILE IDS SAVED`
3. Hide `ALBUMS SENT` and `FULFILLMENT MESSAGE ID` from operator-facing views.
4. Optional: remove the `SOURCING` option from the `ORDER STATUS` select dropdown.
5. Optional: mute or archive the sourcing Telegram group — the bot will never post there again.
6. Restart the bot on the VM after `git pull`.

---

## Out of scope (future work)

- **Notion webhook migration** — still deferred; the 5-second poll cadence is fine for current volume.
- **Backfill stored message_id for old Processed orders** — none of those orders need labels anymore (the `/print` rework spec already ran). Legacy fallback handles the edge case if any do.
- **Per-order failure dashboard or detailed audit log** — not justified by current volume.
- **Strict atomic guarantee across albums for multi-album orders** — Telegram has no transactional multi-send. The per-album resume + counter is the best we can do without re-architecting the storage layer.
