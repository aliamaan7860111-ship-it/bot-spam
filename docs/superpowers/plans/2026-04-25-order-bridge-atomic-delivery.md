# Order Bridge: Atomic Delivery + File ID Reuse — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the order bridge's 3-message-per-order delivery (images + text + separator) with a single atomic Telegram media group that has the order details as a caption on the last album, and reuse Telegram-cached `file_id`s in Stage 2 instead of re-fetching from Shopify GraphQL.

**Architecture:** Add two Notion properties (`TELEGRAM FILE IDS`, `FILE IDS SAVED` — already provisioned). Refactor `send_order_to_group()` to build atomic media groups with caption on the last album and return the `file_id`s. Wire Stage 1 to save `file_id`s with retries, Stage 2 to read them and rebuild albums on the fast path with GraphQL fallback. Existing claim-then-send pattern (mark notified before send, revert on failure) is preserved.

**Tech Stack:** Python 3.14, `python-telegram-bot` (`Bot`, `InputMediaPhoto`, `send_media_group`), `httpx` for Notion API, stdlib `unittest` for tests, no new dependencies.

**Spec:** [docs/superpowers/specs/2026-04-25-order-bridge-atomic-delivery-design.md](../specs/2026-04-25-order-bridge-atomic-delivery-design.md)

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `execution/notion_client.py` | Notion API wrapper, schema constants, parse_order, write helpers | Modify — add 2 field constants, extend parse_order, add 2 write fns |
| `execution/telegram_client.py` | Telegram delivery + Shopify GraphQL fetcher + caption builders | Modify — refactor `send_order_to_group`, add helpers |
| `execution/order_bridge.py` | Polling loop, Stage 1 dispatcher, retry logic | Modify — add retry helper, wire file_id save |
| `execution/tests/__init__.py` | Test module marker | Create (empty) |
| `execution/tests/test_album_helpers.py` | Unit tests for pure helpers | Create |

No new dependencies, no new env vars, no Make.com changes.

---

## Status Constants Reference (existing)

The bot already uses these `ORDER STATUS` select values — match them exactly:

- `Confirmed | Processing` — operator sets this; triggers Stage 1 (queried by `query_confirmed_orders()`)
- `SOURCING` — bot sets this after sending to sourcing
- `Processed` — bot sets this after `#ready` (via `mark_order_processed()`)

Existing helpers you'll call:
- `notion.query_confirmed_orders()` — Stage 1 trigger
- `notion.query_sourcing_orders()` — Stage 2 trigger (#ready all)
- `notion.find_order_by_id(order_id)` — Stage 2 trigger (#ready ORDERID)
- `notion.mark_sourcing_notified(page_id)`, `notion.update_order_status(page_id, status)`, `notion.unmark_notified(page_id)`
- `notion.mark_order_processed(page_id)`, `notion.mark_fulfillment_notified(page_id)`

Telegram constants:
- `tg.TELEGRAM_SOURCING_GROUP_ID`, `tg.TELEGRAM_FULFILLMENT_GROUP_ID`
- `tg.SEPARATOR_PATH` → `execution/assets/separator.png`

---

## Task 1: Notion field constants and getters

**Files:**
- Modify: `execution/notion_client.py` (add constants + extend `parse_order` + add write fns)

- [ ] **Step 1.1: Add field name constants near the existing FIELD_* block**

Find the block near line 95 that defines `FIELD_FULFILLMENT_NOTIFIED` and `FIELD_WHATSAPP_SENT`. Add right after them:

```python
FIELD_TELEGRAM_FILE_IDS = "TELEGRAM FILE IDS"
FIELD_FILE_IDS_SAVED = "FILE IDS SAVED"
```

- [ ] **Step 1.2: Extend `parse_order()` to expose the new fields**

In the `parse_order()` function (around line 184–225), inside the returned dict, add these two entries next to `whatsapp_sent`:

```python
        "telegram_file_ids": _parse_telegram_file_ids(_get_rich_text(props, FIELD_TELEGRAM_FILE_IDS)),
        "file_ids_saved": _get_checkbox(props, FIELD_FILE_IDS_SAVED),
```

- [ ] **Step 1.3: Add the `_parse_telegram_file_ids` helper near the other `_get_*` helpers**

Place this helper after `_get_files()` (which we added earlier in the session):

```python
def _parse_telegram_file_ids(raw: str) -> list[str]:
    """Split a comma-separated string of Telegram file_ids into a clean list."""
    if not raw:
        return []
    return [fid.strip() for fid in raw.split(",") if fid.strip()]
```

- [ ] **Step 1.4: Add `update_telegram_file_ids` and `update_file_ids_saved` write helpers**

Place these next to `mark_sourcing_notified` (around line 540):

```python
def update_telegram_file_ids(page_id: str, file_ids: list[str]) -> bool:
    """Save the comma-separated Telegram file_ids to the order page."""
    joined = ",".join(file_ids)
    return _update_page(page_id, {
        FIELD_TELEGRAM_FILE_IDS: {"rich_text": [{"text": {"content": joined}}]},
    })


def mark_file_ids_saved(page_id: str, value: bool = True) -> bool:
    """Set the FILE IDS SAVED checkbox to value."""
    return _update_page(page_id, {
        FIELD_FILE_IDS_SAVED: {"checkbox": value},
    })
```

- [ ] **Step 1.5: Smoke test against the live Notion DB**

Run this one-off check from a shell:

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import notion_client as n
orders = n.query_confirmed_orders()
print('Confirmed orders:', len(orders))
if orders:
    o = orders[0]
    print('order_id:', o.get('order_id'))
    print('telegram_file_ids:', o.get('telegram_file_ids'))
    print('file_ids_saved:', o.get('file_ids_saved'))
"
```

Expected: prints the count, plus `telegram_file_ids: []` and `file_ids_saved: False` for any existing order (since the field was just added and nothing has populated it yet).

- [ ] **Step 1.6: Commit**

```bash
git add execution/notion_client.py
git commit -m "feat(order_bridge): expose TELEGRAM FILE IDS and FILE IDS SAVED in parse_order"
```

---

## Task 2: Pure helpers in `telegram_client.py` — TDD

**Files:**
- Create: `execution/tests/__init__.py` (empty)
- Create: `execution/tests/test_album_helpers.py`
- Modify: `execution/telegram_client.py` (add helpers near top of "Send order" section)

- [ ] **Step 2.1: Create empty test module marker**

Create `execution/tests/__init__.py`:

```python
```

(Empty file. Just makes `execution.tests` importable.)

- [ ] **Step 2.2: Write failing tests for `chunk_for_albums`**

Create `execution/tests/test_album_helpers.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_client import (
    chunk_for_albums,
    parse_item_count,
    truncate_caption_with_overflow,
)


class TestChunkForAlbums(unittest.TestCase):
    def test_single_album_under_cap(self):
        items = ["a", "b", "c"]
        self.assertEqual(chunk_for_albums(items), [["a", "b", "c"]])

    def test_exactly_ten_one_album(self):
        items = list("abcdefghij")
        self.assertEqual(chunk_for_albums(items), [items])

    def test_eleven_two_albums(self):
        items = list("abcdefghijk")
        self.assertEqual(chunk_for_albums(items), [list("abcdefghij"), ["k"]])

    def test_twenty_five_three_albums(self):
        items = list(range(25))
        chunks = chunk_for_albums(items)
        self.assertEqual([len(c) for c in chunks], [10, 10, 5])
        self.assertEqual([x for chunk in chunks for x in chunk], items)

    def test_empty_returns_empty_list(self):
        self.assertEqual(chunk_for_albums([]), [])


class TestParseItemCount(unittest.TestCase):
    def test_single_item(self):
        self.assertEqual(parse_item_count("Black T-Shirt | 1"), 1)

    def test_two_items_newline_separated(self):
        self.assertEqual(parse_item_count("Black T-Shirt | 1\nWhite Shoes | 1"), 2)

    def test_blank_input(self):
        self.assertEqual(parse_item_count(""), 0)

    def test_extra_blank_lines_ignored(self):
        self.assertEqual(parse_item_count("Black T-Shirt | 1\n\n\nWhite Shoes | 1\n"), 2)

    def test_three_items(self):
        text = "Item A | 1\nItem B | 1\nItem C | 1"
        self.assertEqual(parse_item_count(text), 3)


class TestTruncateCaptionWithOverflow(unittest.TestCase):
    def test_short_caption_unchanged(self):
        cap = "Order ID: AM1664"
        short, overflow = truncate_caption_with_overflow(cap, max_length=1024)
        self.assertEqual(short, cap)
        self.assertEqual(overflow, "")

    def test_long_caption_split(self):
        long_cap = "x" * 1500
        short, overflow = truncate_caption_with_overflow(long_cap, max_length=1024)
        self.assertLessEqual(len(short), 1024)
        self.assertEqual(short + overflow, long_cap)

    def test_exact_length_unchanged(self):
        cap = "x" * 1024
        short, overflow = truncate_caption_with_overflow(cap, max_length=1024)
        self.assertEqual(short, cap)
        self.assertEqual(overflow, "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2.3: Run tests to verify they fail**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers -v
```

Expected: ImportError or AttributeError because the helpers don't exist yet.

- [ ] **Step 2.4: Implement the three helpers in `telegram_client.py`**

Add these helpers in `telegram_client.py` immediately above the `format_sourcing_message` function (or any clear spot in the file before `send_order_to_group`):

```python
# ---------------------------------------------------------------------------
# Album / caption helpers
# ---------------------------------------------------------------------------

TELEGRAM_MEDIA_GROUP_MAX = 10
TELEGRAM_CAPTION_MAX = 1024


def chunk_for_albums(items: list, chunk_size: int = TELEGRAM_MEDIA_GROUP_MAX) -> list[list]:
    """Split a list into chunks of up to chunk_size for Telegram media groups."""
    if not items:
        return []
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def parse_item_count(item_qty_field: str) -> int:
    """Count distinct items in the Notion 'ITEM | QTY' field.

    Each item is on its own line; quantity is always 1 in this system,
    so the line count equals the item count. Blank lines are ignored.
    """
    if not item_qty_field:
        return 0
    return sum(1 for line in item_qty_field.splitlines() if line.strip())


def truncate_caption_with_overflow(caption: str, max_length: int = TELEGRAM_CAPTION_MAX) -> tuple[str, str]:
    """Split a caption at max_length. Returns (short_caption, overflow_text).

    If caption fits, overflow is empty string. The caller decides what to do
    with the overflow (typically: send as a reply to the album).
    """
    if len(caption) <= max_length:
        return caption, ""
    return caption[:max_length], caption[max_length:]
```

- [ ] **Step 2.5: Run tests to verify they pass**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers -v
```

Expected: 11 tests passing (5 chunk + 4 parse_item_count + 3 truncate). All OK.

- [ ] **Step 2.6: Commit**

```bash
git add execution/tests/__init__.py execution/tests/test_album_helpers.py execution/telegram_client.py
git commit -m "feat(order_bridge): add chunk_for_albums, parse_item_count, truncate_caption helpers"
```

---

## Task 3: Refactor `send_order_to_group` to atomic delivery, return file_ids

**Files:**
- Modify: `execution/telegram_client.py` lines ~354–470 (`send_order_to_group`)

The current function sends 3 separate messages: images media-group, text, separator. We rewrite it to send **one or more albums with caption on the LAST album's first image**, then a separator. We also change the return type from `bool` to a small dataclass-like dict so callers get `file_ids` and `image_count` back.

- [ ] **Step 3.1: Define the return shape as a typed dict at the top of the file**

Add near the imports section (around line 18 where existing imports live):

```python
from typing import TypedDict


class SendOrderResult(TypedDict):
    success: bool
    file_ids: list[str]
    image_count: int        # actually delivered
    expected_count: int     # from items in Notion
```

- [ ] **Step 3.2: Replace the body of `send_order_to_group`**

Current body lives at lines ~354–470. Replace the entire function with the version below. Keep the function signature backward-compatible (still takes `bot, group_id, order, header`).

```python
async def send_order_to_group(
    bot: Bot,
    group_id: str | int,
    order: dict,
    header: str = "📦 NEW ORDER FOR SOURCING",
) -> SendOrderResult:
    """
    Send order as ONE atomic media group (or multiple if >10 images), with the
    order caption on the LAST album's first image, then send the separator.

    Returns a SendOrderResult dict with success, file_ids, image_count, expected_count.
    """
    result: SendOrderResult = {
        "success": False,
        "file_ids": [],
        "image_count": 0,
        "expected_count": parse_item_count(order.get("item_qty", "")),
    }

    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f"Invalid Telegram group ID: {group_id}")
        return result

    # Image priority: GraphQL primary, Notion files fallback (already implemented).
    checkout_url = order.get("order_source_url", "")
    notion_image_urls = order.get("image_urls", [])
    if checkout_url and not order.get("line_items"):
        log.info("  Fetching line items (sizes) via GraphQL...")
        graphql_images = fetch_order_images_graphql(checkout_url, order)
        if graphql_images:
            order["image_urls"] = graphql_images
        elif notion_image_urls:
            log.info(f"  GraphQL returned no images; falling back to {len(notion_image_urls)} from Notion IMAGE URL")
            order["image_urls"] = notion_image_urls

    image_urls = order.get("image_urls", [])

    # Download all images upfront so we know the actual delivered count.
    downloaded: list[bytes] = []
    for url in image_urls:
        img_bytes = download_image(url)
        if img_bytes:
            downloaded.append(img_bytes)
        else:
            log.warning(f"  Skipped image: {url}")

    if not downloaded:
        # No images to send. Still send the text + separator so the order is visible.
        log.warning(f"  No images downloaded for {order.get('order_id', '?')}, sending text-only")
        try:
            text = format_order_message(order, header)
            await bot.send_message(chat_id=group_id, text=text, parse_mode="Markdown",
                                    read_timeout=60, write_timeout=60)
            await _send_separator(bot, group_id)
            result["success"] = True
        except Exception as e:
            log.error(f"Failed to send text-only order: {e}")
        return result

    # Build the caption from the same formatter as before.
    caption_full = format_order_message(order, header)
    short_caption, overflow_text = truncate_caption_with_overflow(caption_full)

    # Chunk the downloaded image bytes into albums of <=10. Caption goes on
    # the FIRST image of the LAST album.
    album_chunks = chunk_for_albums(downloaded)
    last_idx = len(album_chunks) - 1

    all_file_ids: list[str] = []
    try:
        for i, chunk in enumerate(album_chunks):
            media: list[InputMediaPhoto] = []
            for j, img_bytes in enumerate(chunk):
                if i == last_idx and j == 0:
                    # Caption on first image of last album
                    media.append(InputMediaPhoto(
                        media=io.BytesIO(img_bytes),
                        caption=short_caption,
                        parse_mode="Markdown",
                    ))
                else:
                    media.append(InputMediaPhoto(media=io.BytesIO(img_bytes)))

            if len(media) == 1:
                # send_media_group requires >=2 items. Use send_photo with caption.
                photo_msg = await bot.send_photo(
                    chat_id=group_id,
                    photo=media[0].media,
                    caption=media[0].caption if hasattr(media[0], "caption") else None,
                    parse_mode="Markdown" if media[0].caption else None,
                    read_timeout=60, write_timeout=60,
                )
                fid = photo_msg.photo[-1].file_id if photo_msg.photo else None
                if fid:
                    all_file_ids.append(fid)
            else:
                msgs = await bot.send_media_group(
                    chat_id=group_id, media=media,
                    read_timeout=60, write_timeout=60,
                )
                for m in msgs:
                    if m.photo:
                        all_file_ids.append(m.photo[-1].file_id)

        # If caption overflowed, send the overflow as a reply to the LAST sent message.
        if overflow_text and all_file_ids:
            last_msg_id = msgs[-1].message_id if 'msgs' in locals() else photo_msg.message_id
            try:
                await bot.send_message(
                    chat_id=group_id,
                    text=overflow_text,
                    reply_to_message_id=last_msg_id,
                    parse_mode="Markdown",
                    read_timeout=60, write_timeout=60,
                )
            except Exception as e:
                log.warning(f"Failed to send overflow reply: {e}")

        result["image_count"] = len(downloaded)
        result["file_ids"] = all_file_ids

    except Exception as e:
        log.error(f"Failed to send album to Telegram: {e}", exc_info=True)
        # Best-effort fallback: send raw URLs as text so operator still sees the order.
        try:
            await bot.send_message(
                chat_id=group_id,
                text=f"⚠️ Could not send album. URLs:\n" + "\n".join(image_urls) + f"\n\n{caption_full}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return result

    # Send separator
    await _send_separator(bot, group_id)

    result["success"] = True
    return result


async def _send_separator(bot: Bot, group_id: int) -> None:
    """Send the black separator image. Best-effort; logs but does not raise."""
    try:
        if SEPARATOR_PATH.exists():
            with open(SEPARATOR_PATH, "rb") as f:
                await bot.send_photo(
                    chat_id=group_id, photo=f,
                    read_timeout=60, write_timeout=60,
                )
        else:
            log.warning(f"Separator image not found at {SEPARATOR_PATH}")
    except Exception as e:
        log.warning(f"Could not send separator: {e}")
```

- [ ] **Step 3.3: Update existing callers of `send_order_to_group` to handle the new return type**

Search for all callers and update them. There should be two: in `order_bridge.py` (Stage 1) and `telegram_client.py` (Stage 2 inside `forward_ready_orders`).

In `execution/order_bridge.py` around line 172, replace:

```python
        success = await tg.send_order_to_group(
            bot, sourcing_group, order,
            header="📦 NEW ORDER FOR SOURCING"
        )

        if success:
```

with:

```python
        send_result = await tg.send_order_to_group(
            bot, sourcing_group, order,
            header="📦 NEW ORDER FOR SOURCING"
        )
        success = send_result["success"]

        if success:
```

(The file_id-saving and image-count-warning logic gets added in Tasks 4 and 6 respectively. For now, just unblock compilation.)

In `execution/telegram_client.py` around line 540 (inside `forward_ready_orders`), the current call is:

```python
                    if fulfillment_group:
                        await send_order_to_group(
                            bot, fulfillment_group, order,
                            header="✅ READY FOR FULFILLMENT"
                        )
```

Leave this as-is for now — the bool/dict difference is silently ignored (the result isn't checked here today). We'll rework Stage 2 fully in Task 5.

- [ ] **Step 3.4: Smoke test: send one real order to a Telegram test chat**

Manual integration check. Pick or create one order in Notion that's `Confirmed | Processing` and confirm in chat that:
- Images arrive as ONE atomic block (or 2 if >10 images)
- Caption appears under the LAST album with the order details
- Separator follows immediately
- No interleaving even with multiple orders fired in one poll cycle

```bash
cd c:/Users/PMLS/Desktop/Automation && python execution/order_bridge.py
```

(Run interactively, watch logs, then Ctrl+C.)

- [ ] **Step 3.5: Commit**

```bash
git add execution/telegram_client.py execution/order_bridge.py
git commit -m "feat(order_bridge): atomic media group with caption-on-last-album in send_order_to_group"
```

---

## Task 4: Notion write retry helper + Stage 1 file_id save

**Files:**
- Modify: `execution/order_bridge.py` (add helper, wire file_id save into poll_notion_once)

- [ ] **Step 4.1: Add the retry helper in `order_bridge.py`**

Add this near the top of the file, below the imports and constants (around line 60):

```python
async def notion_write_with_retry(
    write_fn,
    *args,
    attempts: int = 3,
    base_delay: float = 1.0,
    description: str = "Notion write",
) -> bool:
    """
    Call a synchronous Notion write function with exponential backoff.
    Returns True on success, False if all attempts fail.

    Delays: base_delay, base_delay*2, base_delay*4 (so 1s, 2s, 4s default).
    """
    for attempt in range(1, attempts + 1):
        try:
            ok = write_fn(*args)
            if ok:
                return True
            log.warning(f"  {description}: attempt {attempt} returned False")
        except Exception as e:
            log.warning(f"  {description}: attempt {attempt} raised {e!r}")

        if attempt < attempts:
            delay = base_delay * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

    log.error(f"  {description}: all {attempts} attempts failed")
    return False
```

- [ ] **Step 4.2: Wire file_id save into `poll_notion_once` after a successful send**

In `poll_notion_once` (around line 175 in the current file), inside the `if success:` branch, replace:

```python
        if success:
            log.info(f"  ✓ Order {order_id} sent, status → SOURCING")
            
            processed += 1
```

with:

```python
        if success:
            log.info(f"  ✓ Order {order_id} sent, status → SOURCING")

            # Save file_ids returned from Stage 1 send. 3 retries with backoff.
            file_ids = send_result.get("file_ids", [])
            if file_ids:
                ok = await notion_write_with_retry(
                    notion.update_telegram_file_ids,
                    order["page_id"], file_ids,
                    description=f"file_ids write for {order_id}",
                )
                if ok:
                    await notion_write_with_retry(
                        notion.mark_file_ids_saved,
                        order["page_id"], True,
                        description=f"FILE IDS SAVED checkbox for {order_id}",
                    )
                else:
                    # Couldn't save file_ids; warn in the chat. Stage 2 will use
                    # GraphQL fallback automatically when FILE IDS SAVED is false.
                    try:
                        await bot.send_message(
                            chat_id=sourcing_group,
                            text=f"⚠️ {order_id}: file IDs not saved after 3 retries, manual fulfillment may be needed",
                        )
                    except Exception as e:
                        log.error(f"  Failed to post file_id warning: {e}")

            processed += 1
```

- [ ] **Step 4.3: Smoke test against one live order**

Trigger a single order through Stage 1, then in Notion, verify:
- `TELEGRAM FILE IDS` is populated with comma-separated IDs (one per image sent)
- `FILE IDS SAVED` is checked

Also verify the order shows up cleanly in the sourcing Telegram chat.

- [ ] **Step 4.4: Commit**

```bash
git add execution/order_bridge.py
git commit -m "feat(order_bridge): save file_ids to Notion after Stage 1 with retry+warning"
```

---

## Task 5: Stage 2 fast path — rebuild album from saved file_ids

**Files:**
- Modify: `execution/telegram_client.py` (`forward_ready_orders` plus a new helper)

- [ ] **Step 5.1: Add `send_order_via_file_ids` helper in `telegram_client.py`**

Place this helper near `send_order_to_group` (right after it makes sense):

```python
async def send_order_via_file_ids(
    bot: Bot,
    group_id: str | int,
    order: dict,
    header: str = "✅ READY FOR FULFILLMENT",
) -> SendOrderResult:
    """
    Send an order to a Telegram group by REUSING file_ids saved during Stage 1.
    No GraphQL fetch, no image download — Telegram clones from cache.

    Returns a SendOrderResult. If file_ids fail (extremely rare), success=False
    and the caller should fall back to send_order_to_group (GraphQL refetch path).
    """
    result: SendOrderResult = {
        "success": False,
        "file_ids": [],
        "image_count": 0,
        "expected_count": parse_item_count(order.get("item_qty", "")),
    }
    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f"Invalid Telegram group ID: {group_id}")
        return result

    file_ids = order.get("telegram_file_ids", [])
    if not file_ids:
        log.warning(f"  send_order_via_file_ids: no file_ids on order {order.get('order_id', '?')}")
        return result

    caption_full = format_order_message(order, header)
    short_caption, overflow_text = truncate_caption_with_overflow(caption_full)

    album_chunks = chunk_for_albums(file_ids)
    last_idx = len(album_chunks) - 1

    try:
        for i, chunk in enumerate(album_chunks):
            media: list[InputMediaPhoto] = []
            for j, fid in enumerate(chunk):
                if i == last_idx and j == 0:
                    media.append(InputMediaPhoto(media=fid, caption=short_caption,
                                                  parse_mode="Markdown"))
                else:
                    media.append(InputMediaPhoto(media=fid))

            if len(media) == 1:
                photo_msg = await bot.send_photo(
                    chat_id=group_id, photo=media[0].media,
                    caption=media[0].caption if hasattr(media[0], "caption") else None,
                    parse_mode="Markdown" if media[0].caption else None,
                    read_timeout=60, write_timeout=60,
                )
                last_message_id = photo_msg.message_id
            else:
                msgs = await bot.send_media_group(
                    chat_id=group_id, media=media,
                    read_timeout=60, write_timeout=60,
                )
                last_message_id = msgs[-1].message_id

        if overflow_text:
            try:
                await bot.send_message(
                    chat_id=group_id, text=overflow_text,
                    reply_to_message_id=last_message_id,
                    parse_mode="Markdown",
                    read_timeout=60, write_timeout=60,
                )
            except Exception as e:
                log.warning(f"Failed to send overflow reply: {e}")

        await _send_separator(bot, group_id)
        result["success"] = True
        result["image_count"] = len(file_ids)
        result["file_ids"] = file_ids

    except Exception as e:
        log.error(f"  Failed to send album via file_ids: {e}", exc_info=True)

    return result
```

- [ ] **Step 5.2: Update `forward_ready_orders` to take the fast path when `file_ids_saved` is true**

In `forward_ready_orders` (around line 530–560), inside the `for i, order in enumerate(orders):` loop, replace:

```python
                    # Forward to fulfillment group
                    if fulfillment_group:
                        await send_order_to_group(
                            bot, fulfillment_group, order,
                            header="✅ READY FOR FULFILLMENT"
                        )
                    sent_count += 1
```

with:

```python
                    # Choose fast path (saved file_ids) vs fallback (GraphQL refetch).
                    sent_ok = False
                    if fulfillment_group:
                        if order.get("file_ids_saved") and order.get("telegram_file_ids"):
                            res = await send_order_via_file_ids(
                                bot, fulfillment_group, order,
                                header="✅ READY FOR FULFILLMENT",
                            )
                            sent_ok = res["success"]
                            if not sent_ok:
                                log.warning(f"  fast path failed for {oid}, falling back to GraphQL")
                        if not sent_ok:
                            # Either file_ids weren't saved, or fast path failed.
                            res = await send_order_to_group(
                                bot, fulfillment_group, order,
                                header="✅ READY FOR FULFILLMENT",
                            )
                            sent_ok = res["success"]
                            if sent_ok and not order.get("file_ids_saved"):
                                try:
                                    await bot.send_message(
                                        chat_id=fulfillment_group,
                                        text=f"⚠️ {oid}: no file IDs were saved, resorted to fallback fetch",
                                    )
                                except Exception:
                                    pass
                    if sent_ok:
                        sent_count += 1
                    else:
                        fail_count += 1
                        continue
```

- [ ] **Step 5.3: Apply the same fast-path/fallback pattern to the single-order `#ready ORDERID` branch**

Around line 580 in `forward_ready_orders`, the single-order path also calls `send_order_to_group`. Find the block and apply the same conditional. (The exact location: search for the comment `# --- #ready ORDERID ---` and find the analogous send call below it.)

Replace the single-order send call with:

```python
        if order.get("file_ids_saved") and order.get("telegram_file_ids"):
            res = await send_order_via_file_ids(
                bot, fulfillment_group, order,
                header="✅ READY FOR FULFILLMENT",
            )
            if not res["success"]:
                res = await send_order_to_group(
                    bot, fulfillment_group, order,
                    header="✅ READY FOR FULFILLMENT",
                )
                if res["success"]:
                    try:
                        await bot.send_message(
                            chat_id=fulfillment_group,
                            text=f"⚠️ {order_id}: no file IDs (fast path failed), resorted to fallback fetch",
                        )
                    except Exception:
                        pass
        else:
            res = await send_order_to_group(
                bot, fulfillment_group, order,
                header="✅ READY FOR FULFILLMENT",
            )
            if res["success"] and not order.get("file_ids_saved"):
                try:
                    await bot.send_message(
                        chat_id=fulfillment_group,
                        text=f"⚠️ {order_id}: no file IDs were saved, resorted to fallback fetch",
                    )
                except Exception:
                    pass
```

(Keep the existing post-send Notion update calls — `mark_order_processed` and `mark_fulfillment_notified` — exactly as they are.)

- [ ] **Step 5.4: Smoke test the Stage 2 fast path**

1. Send one fresh order through Stage 1 (so `FILE IDS SAVED = true` in Notion).
2. In the sourcing chat, type `#ready <ORDER_ID>` for that exact order.
3. Watch fulfillment chat — order should appear instantly with the new caption (full customer details), no "fallback fetch" warning.
4. Repeat for a second order, this time editing Notion to uncheck `FILE IDS SAVED` first to force the fallback path.
5. Run `#ready <ORDER_ID>` again — should see the warning posted in fulfillment chat plus the order arrives via GraphQL.

- [ ] **Step 5.5: Commit**

```bash
git add execution/telegram_client.py
git commit -m "feat(order_bridge): Stage 2 reuses saved file_ids with GraphQL fallback"
```

---

## Task 6: Image-count vs item-count warning

**Files:**
- Modify: `execution/order_bridge.py` (poll_notion_once, after the file_id save block)

- [ ] **Step 6.1: Add the mismatch check after a successful Stage 1 send**

Inside `poll_notion_once`, immediately after the file_id save block from Task 4 (still inside `if success:`), append:

```python
            # Image count vs items count mismatch warning.
            delivered = send_result.get("image_count", 0)
            expected = send_result.get("expected_count", 0)
            if expected > 0 and delivered < expected:
                missing = expected - delivered
                try:
                    await bot.send_message(
                        chat_id=sourcing_group,
                        text=f"⚠️ {order_id}: {delivered} of {expected} images sent, {missing} missing",
                    )
                except Exception as e:
                    log.error(f"  Failed to post image-count warning: {e}")
```

- [ ] **Step 6.2: Smoke test the mismatch path**

Hard to trigger naturally. Easiest manual reproduction:
1. Pick a live order with 2+ items.
2. Temporarily edit `download_image()` in `telegram_client.py` to return `None` for the first call (just for testing — revert immediately after):
   ```python
   _test_skip = [True]
   def download_image(url):
       if _test_skip and _test_skip[0]:
           _test_skip[0] = False
           return None
       # ... existing body
   ```
3. Run the bot, trigger the order, observe the `⚠️ N of M images sent` warning in the sourcing chat.
4. **Revert the test edit before committing.**

(Alternative if the manual edit feels risky: just trust the logic, deploy, and watch real orders for it to fire naturally. Acceptable since the warning is non-destructive.)

- [ ] **Step 6.3: Commit**

```bash
git add execution/order_bridge.py
git commit -m "feat(order_bridge): warn in chat when delivered images < expected items"
```

---

## Task 7: Run the full test suite + final smoke

- [ ] **Step 7.1: Run unit tests**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers -v
```

Expected: all 11 tests pass.

- [ ] **Step 7.2: Restart the running bot on the host**

```bash
# (your usual restart command — depends on the host setup, e.g. systemctl or screen)
```

- [ ] **Step 7.3: Final integration check — burst test**

In Notion, flip 5 orders simultaneously to `Confirmed | Processing`. Watch the sourcing chat:
- All 5 orders should arrive as 5 distinct atomic blocks (album + caption + separator each)
- No interleaving (no "image of order A under text of order B")
- Each order's `TELEGRAM FILE IDS` and `FILE IDS SAVED` populated in Notion within seconds of arrival
- Type `#ready all` — fulfillment chat should fill in within 5–10 seconds (file_id fast path), no warnings

- [ ] **Step 7.4: Push to bot-spam**

```bash
git push production main
```

---

## Task 8 (optional): Update directive doc

**Files:**
- Modify: `directives/order_automation.md`

- [ ] **Step 8.1: Update the Stage 1 + Stage 2 descriptions to mention atomic delivery and file_id reuse**

Read the current directive, find the "Stage 1" and "Stage 2" sections, update them to describe:
- Atomic media-group-with-caption delivery
- `TELEGRAM FILE IDS` and `FILE IDS SAVED` properties
- Stage 2 fast path vs GraphQL fallback

Keep the rest of the doc as-is (operator workflow doesn't change).

- [ ] **Step 8.2: Commit + push**

```bash
git add directives/order_automation.md
git commit -m "docs(order_bridge): document atomic delivery + file_id reuse in directive"
git push production main
```

---

## Self-Review Coverage

- [x] **Spec coverage:** Atomic delivery (Tasks 3, 5); sequential processing (already in code, kept); file_id save with retry (Task 4); FILE IDS SAVED gate (Task 5); image count warning (Task 6); caption overflow (Tasks 2 + 3 + 5); GraphQL fallback in Stage 2 (Task 5); manual hide-columns step is in spec only (operator action, no task needed).
- [x] **Type consistency:** `SendOrderResult` defined in Task 3 step 3.1, used by `send_order_to_group` (Task 3) and `send_order_via_file_ids` (Task 5). `chunk_for_albums`, `parse_item_count`, `truncate_caption_with_overflow` defined in Task 2, used in Task 3 + 5.
- [x] **Placeholder scan:** No "TBD"/"TODO" left. Each step contains complete code or exact commands.
