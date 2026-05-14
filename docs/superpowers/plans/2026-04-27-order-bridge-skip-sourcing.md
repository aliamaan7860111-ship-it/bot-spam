# Order Bridge: Skip Sourcing + Per-Order Label Replies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-stage Notion → sourcing → fulfillment flow with a single-stage Notion → fulfillment flow, and rewrite `/print all` and `/print <ORDER_ID>` to post each label PDF as a reply to its order's fulfillment message.

**Architecture:** Add two Notion properties (`ALBUMS SENT` counter, `FULFILLMENT MESSAGE ID`). Rewrite `send_order_to_group()` to support resuming from a per-album checkpoint so multi-album orders survive partial failures. Rewrite the poller to send directly to the fulfillment group and flip status from `Confirmed | Processing` to `Processed` after all albums land. Rewrite `cmd_print_all` / `cmd_print_one` to fetch one-tracking-number labels and post each as a reply to the saved fulfillment message_id, with a "legacy order" fallback for orders without a stored message_id. Hard-remove all sourcing infrastructure (command handler, Notion fields, helper functions).

**Tech Stack:** Python 3.14, `python-telegram-bot` (`Bot`, `InputMediaPhoto`, `send_media_group`, `send_document` with `reply_to_message_id`), `httpx` for Notion API, stdlib `unittest` for tests, no new dependencies.

**Spec:** [docs/superpowers/specs/2026-04-27-order-bridge-skip-sourcing-design.md](../specs/2026-04-27-order-bridge-skip-sourcing-design.md)

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `scratch/add_order_bridge_v2_fields.py` | One-shot Notion schema setup | Create |
| `execution/notion_client.py` | Notion API wrapper, field constants, parse_order, write helpers | Modify — add 2 constants/helpers, delete 4 constants + 6 helpers |
| `execution/telegram_client.py` | Telegram delivery, Shopify GraphQL, caption helpers | Modify — refactor `send_order_to_group` for resume, add 3 pure helpers, delete `send_order_via_file_ids` + `forward_ready_orders` |
| `execution/order_bridge.py` | Polling loop, command dispatch | Modify — rewrite `poll_notion_once` for single-stage, rewrite `cmd_print_all`/`cmd_print_one` for per-order labels, drop `#ready` registration |
| `execution/tests/test_album_helpers.py` | Existing unit tests | Unchanged — must still pass |
| `execution/tests/test_resume_and_print.py` | Unit tests for resume range, sort-by-message-id, legacy caption | Create |

No new dependencies, no env var changes.

---

## Status Constants Reference

| Status value | Set by | Meaning |
|---|---|---|
| `Confirmed \| Processing` | Operator (manual) | Trigger for Stage 1 send |
| `Processed` | Bot, after all albums of an order land | Eligible for `/print all` |

`SOURCING` is **removed** — no longer used as an intermediate state.

Existing Notion helpers you'll call:
- `notion.query_confirmed_orders()` — returns orders with `ORDER STATUS = Confirmed | Processing`
- `notion.find_order_by_id(order_id)` — for `/print <ORDER_ID>`
- `notion.query_filex_processed()` — for `/print all` (unchanged from current Filex /print rework)
- `notion.update_order_status(page_id, status)` — for the final flip to `Processed`
- `notion.update_internal_note(page_id, note)` — preserved
- `notion.mark_order_processed(page_id)` — existing helper that sets `ORDER STATUS = Processed`

Telegram constants:
- `tg.TELEGRAM_FULFILLMENT_GROUP_ID` — the only group used now
- `tg.SEPARATOR_PATH` — `execution/assets/separator.png`

---

## Task 1: One-shot Notion schema setup script

**Files:**
- Create: `scratch/add_order_bridge_v2_fields.py`

- [ ] **Step 1.1: Write the setup script**

Create `scratch/add_order_bridge_v2_fields.py`:

```python
"""
add_order_bridge_v2_fields.py
=============================
Adds two Notion properties to the orders data source for the skip-sourcing rollout:
    - ALBUMS SENT          (number)   - per-album send checkpoint counter
    - FULFILLMENT MESSAGE ID (number) - Telegram message_id of the caption-bearing msg

Idempotent: re-running is safe; existing properties of the right type are kept.

Prints a list of legacy properties for the operator to manually hide/delete in Notion
after the new flow ships. The script does NOT delete properties to avoid data loss.
"""

import os
import sys
import httpx
from dotenv import load_dotenv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2025-09-03"

FIELDS = {
    "ALBUMS SENT":            ("number",  {"number": {}}),
    "FULFILLMENT MESSAGE ID": ("number",  {"number": {}}),
}

LEGACY_TO_HIDE = [
    "SOURCING NOTIFIED",
    "FULFILLMENT NOTIFIED",
    "TELEGRAM FILE IDS",
    "FILE IDS SAVED",
]

if not NOTION_API_KEY or not NOTION_DATABASE_ID:
    sys.exit("Missing NOTION_API_KEY or NOTION_DATABASE_ID in .env")

headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


def main():
    with httpx.Client(timeout=15) as client:
        r = client.get(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
            headers=headers,
        )
        r.raise_for_status()
        data_sources = r.json().get("data_sources", [])
        if not data_sources:
            sys.exit("No data sources found for this database.")
        ds_id = data_sources[0]["id"]
        print(f"Data source: {ds_id} ({data_sources[0].get('name', '?')})")

        r2 = client.get(
            f"https://api.notion.com/v1/data_sources/{ds_id}",
            headers=headers,
        )
        r2.raise_for_status()
        existing = r2.json().get("properties", {})

        to_add = {}
        for name, (expected_type, schema) in FIELDS.items():
            if name in existing:
                current_type = existing[name].get("type")
                if current_type == expected_type:
                    print(f"OK: '{name}' already exists ({expected_type}).")
                else:
                    sys.exit(
                        f"'{name}' exists but has type={current_type!r}, "
                        f"expected {expected_type!r}. Resolve manually."
                    )
            else:
                to_add[name] = schema

        if to_add:
            r3 = client.patch(
                f"https://api.notion.com/v1/data_sources/{ds_id}",
                headers=headers,
                json={"properties": to_add},
            )
            if r3.status_code >= 400:
                print("PATCH failed:", r3.status_code, r3.text)
                r3.raise_for_status()

            new_props = r3.json().get("properties", {})
            for name in to_add:
                actual_type = new_props.get(name, {}).get("type")
                if actual_type == FIELDS[name][0]:
                    print(f"OK: added '{name}' ({actual_type}).")
                else:
                    print(f"WARN: '{name}' missing or wrong type after PATCH (got {actual_type!r}).")
        else:
            print("All new properties already in place. Nothing to add.")

        print()
        print("Manual cleanup steps after the rollout (operator must do these in Notion UI):")
        for legacy in LEGACY_TO_HIDE:
            if legacy in existing:
                print(f"  - Hide or delete column: '{legacy}'")
            else:
                print(f"  - '{legacy}' is already absent from the schema.")
        print("  - Remove 'SOURCING' option from the ORDER STATUS select dropdown.")
        print("  - Hide 'ALBUMS SENT' and 'FULFILLMENT MESSAGE ID' from operator-facing views.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.2: Run the script against the live DB**

```bash
cd c:/Users/PMLS/Desktop/Automation && python scratch/add_order_bridge_v2_fields.py
```

Expected: two `OK: added '...'` lines (or "already exists" on re-run), followed by the cleanup checklist.

- [ ] **Step 1.3: Verify properties via API**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import os, httpx
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
api_key = os.getenv('NOTION_API_KEY','')
db_id = os.getenv('NOTION_DATABASE_ID','')
headers = {'Authorization': f'Bearer {api_key}', 'Notion-Version': '2025-09-03'}
r = httpx.get(f'https://api.notion.com/v1/databases/{db_id}', headers=headers, timeout=15)
ds_id = r.json()['data_sources'][0]['id']
r2 = httpx.get(f'https://api.notion.com/v1/data_sources/{ds_id}', headers=headers, timeout=15)
props = r2.json().get('properties', {})
for n in ['ALBUMS SENT', 'FULFILLMENT MESSAGE ID']:
    p = props.get(n)
    print(f'{n}: type={p.get(\"type\")!r}' if p else f'{n}: MISSING')
"
```

Expected:
```
ALBUMS SENT: type='number'
FULFILLMENT MESSAGE ID: type='number'
```

- [ ] **Step 1.4: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add scratch/add_order_bridge_v2_fields.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(order_bridge): setup script for ALBUMS SENT + FULFILLMENT MESSAGE ID fields"
```

---

## Task 2: Notion client — add new field constants and helpers

**Files:**
- Modify: `execution/notion_client.py`

- [ ] **Step 2.1: Add field constants near the existing `FIELD_*` block**

Find the line `FIELD_FULFILLMENT_NOTIFIED = "FULFILLMENT NOTIFIED"` (currently around line 97). Add after it:

```python
FIELD_ALBUMS_SENT = "ALBUMS SENT"
FIELD_FULFILLMENT_MESSAGE_ID = "FULFILLMENT MESSAGE ID"
```

- [ ] **Step 2.2: Add a `_get_number_or_default` helper near the other `_get_*` helpers**

Find `def _get_number(props: dict, field: str)` and add immediately after it:

```python
def _get_number_or_default(props: dict, field: str, default: int = 0) -> int:
    """Extract an int from a number property; return default if unset or non-numeric."""
    try:
        n = props[field]["number"]
    except (KeyError, TypeError):
        return default
    if n is None:
        return default
    try:
        return int(n)
    except (TypeError, ValueError):
        return default
```

- [ ] **Step 2.3: Extend `parse_order()` to expose the new fields**

In the `parse_order()` function, inside the returned dict (currently has `whatsapp_sent`, `telegram_file_ids`, `file_ids_saved`), add two more entries:

```python
        "albums_sent": _get_number_or_default(props, FIELD_ALBUMS_SENT, default=0),
        "fulfillment_message_id": _get_number_or_default(props, FIELD_FULFILLMENT_MESSAGE_ID, default=0) or None,
```

(`or None` converts the integer 0 → None so callers can use truthiness checks like `if order["fulfillment_message_id"]`.)

- [ ] **Step 2.4: Add the write helpers near `mark_sourcing_notified`**

Find `def mark_sourcing_notified` (currently around line 565). Add after it:

```python
def update_albums_sent(page_id: str, count: int) -> bool:
    """Set the ALBUMS SENT number on the order page."""
    return _update_page(page_id, {
        FIELD_ALBUMS_SENT: {"number": int(count)},
    })


def update_fulfillment_message_id(page_id: str, message_id: int) -> bool:
    """Set the FULFILLMENT MESSAGE ID number on the order page."""
    return _update_page(page_id, {
        FIELD_FULFILLMENT_MESSAGE_ID: {"number": int(message_id)},
    })
```

- [ ] **Step 2.5: Smoke test the parse extension against the live DB**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import notion_client as n
import os, httpx
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.env'))
api_key = os.getenv('NOTION_API_KEY','')
db_id = os.getenv('NOTION_DATABASE_ID','')
headers = {'Authorization': f'Bearer {api_key}', 'Notion-Version': '2025-09-03', 'Content-Type':'application/json'}
r = httpx.get(f'https://api.notion.com/v1/databases/{db_id}', headers=headers, timeout=15)
ds_id = r.json()['data_sources'][0]['id']
r2 = httpx.post(f'https://api.notion.com/v1/data_sources/{ds_id}/query', headers=headers, json={'page_size':3}, timeout=15)
for page in r2.json().get('results', []):
    o = n.parse_order(page)
    print(f\"order_id={o.get('order_id')!r:10} albums_sent={o.get('albums_sent')} fulfillment_message_id={o.get('fulfillment_message_id')!r}\")
"
```

Expected: three orders printed with `albums_sent=0` and `fulfillment_message_id=None` (since no order has these populated yet).

- [ ] **Step 2.6: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/notion_client.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(order_bridge): notion_client exposes ALBUMS SENT + FULFILLMENT MESSAGE ID"
```

---

## Task 3: Pure helpers in `telegram_client.py` — TDD

**Files:**
- Modify: `execution/telegram_client.py` (add 3 helpers near the existing chunk/parse_item_count helpers)
- Create: `execution/tests/test_resume_and_print.py`

- [ ] **Step 3.1: Write the failing tests**

Create `execution/tests/test_resume_and_print.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_client import (
    resume_range,
    sort_orders_for_label_replies,
    legacy_label_caption,
)


class TestResumeRange(unittest.TestCase):
    def test_fresh_order_one_album(self):
        self.assertEqual(resume_range(0, 1), [(0, True)])

    def test_fresh_order_two_albums(self):
        self.assertEqual(resume_range(0, 2), [(0, False), (1, True)])

    def test_resume_after_first_album(self):
        self.assertEqual(resume_range(1, 2), [(1, True)])

    def test_already_complete(self):
        self.assertEqual(resume_range(2, 2), [])

    def test_overshoot_complete(self):
        # Counter higher than total (e.g. data shrank) → nothing to do
        self.assertEqual(resume_range(5, 2), [])

    def test_zero_total(self):
        self.assertEqual(resume_range(0, 0), [])

    def test_three_albums_resume_middle(self):
        self.assertEqual(resume_range(1, 3), [(1, False), (2, True)])


class TestSortOrdersForLabelReplies(unittest.TestCase):
    def test_chronological_by_message_id(self):
        orders = [
            {"order_id": "AM3", "fulfillment_message_id": 300},
            {"order_id": "AM1", "fulfillment_message_id": 100},
            {"order_id": "AM2", "fulfillment_message_id": 200},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2", "AM3"])

    def test_legacy_orders_go_last_sorted_by_order_id(self):
        orders = [
            {"order_id": "AM5", "fulfillment_message_id": 500},
            {"order_id": "AM7", "fulfillment_message_id": None},
            {"order_id": "AM6", "fulfillment_message_id": None},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM5", "AM6", "AM7"])

    def test_only_legacy(self):
        orders = [
            {"order_id": "AM2", "fulfillment_message_id": None},
            {"order_id": "AM1", "fulfillment_message_id": None},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2"])

    def test_only_anchored(self):
        orders = [
            {"order_id": "AM2", "fulfillment_message_id": 200},
            {"order_id": "AM1", "fulfillment_message_id": 100},
        ]
        result = sort_orders_for_label_replies(orders)
        self.assertEqual([o["order_id"] for o in result], ["AM1", "AM2"])

    def test_empty_input(self):
        self.assertEqual(sort_orders_for_label_replies([]), [])


class TestLegacyLabelCaption(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            legacy_label_caption("AM1234"),
            "📄 AM1234 — legacy order, no thread anchor",
        )

    def test_strips_whitespace(self):
        self.assertEqual(
            legacy_label_caption("  AM1234  "),
            "📄 AM1234 — legacy order, no thread anchor",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_resume_and_print -v
```

Expected: `ImportError: cannot import name 'resume_range' from 'telegram_client'`.

- [ ] **Step 3.3: Implement the helpers in `telegram_client.py`**

Find the existing `truncate_caption_with_overflow` helper (in the "Album / caption helpers" section). Add these three new helpers immediately after it:

```python
def resume_range(start_album_index: int, total_albums: int) -> list[tuple[int, bool]]:
    """Return the list of (album_index, is_last) tuples for albums still to send.

    Empty list when everything has already been sent or there is nothing to send.
    is_last is True only for the final album in the order (where the caption goes).
    """
    if total_albums <= 0 or start_album_index >= total_albums:
        return []
    return [(i, i == total_albums - 1) for i in range(start_album_index, total_albums)]


def sort_orders_for_label_replies(orders: list[dict]) -> list[dict]:
    """Sort orders for /print label dispatch.

    Orders with a stored fulfillment_message_id go first, ascending by that id
    (Telegram message_ids are monotonic per chat, so this is chronological).
    Legacy orders (no message_id) go last, alphabetical by order_id for determinism.
    """
    anchored = [o for o in orders if o.get("fulfillment_message_id")]
    legacy = [o for o in orders if not o.get("fulfillment_message_id")]
    anchored.sort(key=lambda o: o["fulfillment_message_id"])
    legacy.sort(key=lambda o: o.get("order_id", ""))
    return anchored + legacy


def legacy_label_caption(order_id: str) -> str:
    """Caption for label PDFs sent for legacy orders (no message_id anchor)."""
    return f"📄 {order_id.strip()} — legacy order, no thread anchor"
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_resume_and_print -v
```

Expected: 13 tests passing (7 resume + 5 sort + 2 caption — wait, recount: 7 resume + 5 sort + 2 caption = 14). All OK.

- [ ] **Step 3.5: Re-run the existing test suite to confirm nothing broke**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers -v
```

Expected: 13 existing tests still pass.

- [ ] **Step 3.6: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/telegram_client.py execution/tests/test_resume_and_print.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(order_bridge): add resume_range + sort_orders_for_label_replies + legacy_label_caption helpers"
```

---

## Task 4: Refactor `send_order_to_group` to support per-album resume

**Files:**
- Modify: `execution/telegram_client.py` (`SendOrderResult` TypedDict + `send_order_to_group` function body)

The current function sends ALL albums for an order in one call and returns a single `SendOrderResult`. The new version takes a `start_album_index` parameter and an async `on_album_sent` callback that fires after each successful album, so the caller can checkpoint `ALBUMS SENT` in Notion between sends.

- [ ] **Step 4.1: Update the `SendOrderResult` TypedDict**

Find `class SendOrderResult(TypedDict)` (currently around line 25). Replace its body with:

```python
class SendOrderResult(TypedDict):
    success: bool                # True if ALL planned albums in this call sent
    albums_sent_this_call: int   # Number of albums delivered in THIS invocation
    total_albums: int            # Total albums planned for the order
    caption_message_id: int | None   # First msg_id of the caption-bearing album (only set when last sent)
    image_count: int             # Images successfully delivered in this call
    expected_count: int          # Parsed from order's item_qty field
```

(`file_ids` field is gone — Stage 2 fast path is no longer used.)

- [ ] **Step 4.2: Rewrite `send_order_to_group`**

Find `async def send_order_to_group` (currently around line 399). Replace its entire body with:

```python
async def send_order_to_group(
    bot: Bot,
    group_id: str | int,
    order: dict,
    header: str = "✅ READY FOR FULFILLMENT",
    start_album_index: int = 0,
    on_album_sent=None,  # async callable: (new_albums_sent_count, caption_message_id_or_None) -> None
) -> SendOrderResult:
    """
    Send order as a sequence of atomic media groups (Telegram caps each at 10 items).
    The caption goes on the FIRST image of the LAST album.

    Resume protocol: if start_album_index > 0, skip the first N albums (the caller has
    already confirmed they were sent before). After each album sends successfully,
    invokes on_album_sent(new_count, caption_message_id) so the caller can checkpoint.

    Returns SendOrderResult. success=True only when all planned albums sent.
    """
    result: SendOrderResult = {
        "success": False,
        "albums_sent_this_call": 0,
        "total_albums": 0,
        "caption_message_id": None,
        "image_count": 0,
        "expected_count": parse_item_count(order.get("item_qty", "")),
    }

    try:
        group_id = int(group_id)
    except (ValueError, TypeError):
        log.error(f"Invalid Telegram group ID: {group_id}")
        return result

    # Image priority: GraphQL primary, Notion files fallback (existing behavior).
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

    # Download all images upfront so we know actual delivered count.
    downloaded: list[bytes] = []
    for url in image_urls:
        img_bytes = download_image(url)
        if img_bytes:
            downloaded.append(img_bytes)
        else:
            log.warning(f"  Skipped image: {url}")

    caption_full = format_order_message(order, header)

    if not downloaded:
        # No images. Send caption as a plain message + separator so the order is visible.
        # No resume protocol needed (text-only orders are atomic in one shot).
        if start_album_index > 0:
            log.warning(f"  No images for {order.get('order_id', '?')} but start_album_index={start_album_index}; sending text-only fallback")
        try:
            text_msg = await bot.send_message(
                chat_id=group_id, text=caption_full, parse_mode="Markdown",
                read_timeout=60, write_timeout=60,
            )
            await _send_separator(bot, group_id)
            result["success"] = True
            result["total_albums"] = 0
            result["caption_message_id"] = text_msg.message_id
            if on_album_sent is not None:
                await on_album_sent(0, text_msg.message_id)
        except Exception as e:
            log.error(f"Failed to send text-only order: {e}")
        return result

    short_caption, overflow_text = truncate_caption_with_overflow(caption_full)

    # Chunk the downloaded bytes into albums of <=10.
    album_chunks = chunk_for_albums(downloaded)
    total_albums = len(album_chunks)
    result["total_albums"] = total_albums
    result["image_count"] = len(downloaded)

    # Compute which albums actually need sending.
    plan = resume_range(start_album_index, total_albums)
    if not plan:
        # Counter says all albums already sent — return success without sending anything.
        log.info(f"  Skipping send for {order.get('order_id', '?')}: counter {start_album_index} >= total {total_albums}")
        result["success"] = True
        return result

    last_message_id: int | None = None
    try:
        for album_index, is_last in plan:
            chunk = album_chunks[album_index]
            media: list[InputMediaPhoto] = []
            for j, img_bytes in enumerate(chunk):
                if is_last and j == 0:
                    media.append(InputMediaPhoto(
                        media=io.BytesIO(img_bytes),
                        caption=short_caption,
                        parse_mode="Markdown",
                    ))
                else:
                    media.append(InputMediaPhoto(media=io.BytesIO(img_bytes)))

            if len(media) == 1:
                first = media[0]
                photo_msg = await bot.send_photo(
                    chat_id=group_id,
                    photo=first.media,
                    caption=getattr(first, "caption", None),
                    parse_mode="Markdown" if getattr(first, "caption", None) else None,
                    read_timeout=60, write_timeout=60,
                )
                first_msg_id_of_album = photo_msg.message_id
                last_message_id = photo_msg.message_id
            else:
                msgs = await bot.send_media_group(
                    chat_id=group_id, media=media,
                    read_timeout=60, write_timeout=60,
                )
                first_msg_id_of_album = msgs[0].message_id
                last_message_id = msgs[-1].message_id

            result["albums_sent_this_call"] += 1
            cap_id_for_callback: int | None = first_msg_id_of_album if is_last else None
            if is_last:
                result["caption_message_id"] = first_msg_id_of_album

            # Caption overflow handling — fires once, on the LAST album only.
            if is_last and overflow_text and last_message_id is not None:
                try:
                    await bot.send_message(
                        chat_id=group_id,
                        text=overflow_text,
                        reply_to_message_id=last_message_id,
                        parse_mode="Markdown",
                        read_timeout=60, write_timeout=60,
                    )
                except Exception as e:
                    log.warning(f"Failed to send overflow reply: {e}")

            # Checkpoint after each successful album.
            new_count = album_index + 1
            if on_album_sent is not None:
                try:
                    await on_album_sent(new_count, cap_id_for_callback)
                except Exception as e:
                    # If the caller's checkpoint fails, log and continue; the counter
                    # in Notion may be stale, but the album was sent. Caller will see
                    # success=True at the end and decide what to do.
                    log.error(f"  on_album_sent callback raised: {e}", exc_info=True)

        log.info(f"  ✓ Sent {result['albums_sent_this_call']} album(s) to group {group_id}")

    except Exception as e:
        log.error(f"Failed to send album to Telegram: {e}", exc_info=True)
        # Best-effort fallback: text-only post so operator still sees the order.
        try:
            await bot.send_message(
                chat_id=group_id,
                text=f"⚠️ Could not send album. URLs:\n" + "\n".join(image_urls) + f"\n\n{caption_full}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return result

    await _send_separator(bot, group_id)
    result["success"] = True
    return result
```

- [ ] **Step 4.3: Verify the module still imports**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import telegram_client
print('SendOrderResult keys:', list(telegram_client.SendOrderResult.__annotations__.keys()))
print('OK')
"
```

Expected:
```
SendOrderResult keys: ['success', 'albums_sent_this_call', 'total_albums', 'caption_message_id', 'image_count', 'expected_count']
OK
```

- [ ] **Step 4.4: Run all tests to verify no regression**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers execution.tests.test_resume_and_print -v
```

Expected: all 27 tests pass (13 album_helpers + 14 resume_and_print).

- [ ] **Step 4.5: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/telegram_client.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "refactor(order_bridge): send_order_to_group supports per-album resume + callback"
```

---

## Task 5: Rewrite `poll_notion_once` for single-stage flow

**Files:**
- Modify: `execution/order_bridge.py` (`poll_notion_once` function body)

- [ ] **Step 5.1: Replace the body of `poll_notion_once`**

Find `async def poll_notion_once(bot: Bot)` (currently around line 208). Replace its body with:

```python
async def poll_notion_once(bot: Bot) -> int:
    """
    Poll Notion for orders with status `Confirmed | Processing`, send each directly
    to the fulfillment group as an atomic media album (caption on the last album).
    Resumes from ALBUMS SENT on multi-album orders. Flips status to `Processed`
    only when all albums for the order have landed.

    Returns number of orders fully delivered this cycle.
    """
    fulfillment_group = tg.TELEGRAM_FULFILLMENT_GROUP_ID
    if not fulfillment_group:
        log.error("TELEGRAM_FULFILLMENT_GROUP_ID is not set")
        return 0

    orders = notion.query_confirmed_orders()
    if not orders:
        return 0

    # Filter out orders currently being sent (in-memory lock guards against re-poll race).
    new_orders = [
        o for o in orders
        if o.get("order_id", "") not in _sending_in_progress
    ]
    if not new_orders:
        return 0

    log.info(f"Found {len(new_orders)} order(s) needing fulfillment send")

    processed = 0
    for order in new_orders:
        order_id = order.get("order_id", "?")
        _sending_in_progress.add(order_id)

        try:
            start_album_index = order.get("albums_sent", 0) or 0
            page_id = order["page_id"]

            # Build the per-album checkpoint callback. Writes ALBUMS SENT (always)
            # and FULFILLMENT MESSAGE ID (only when the caption-bearing album lands).
            async def on_album_sent(new_count: int, caption_msg_id):
                await notion_write_with_retry(
                    notion.update_albums_sent,
                    page_id, new_count,
                    description=f"ALBUMS SENT={new_count} for {order_id}",
                )
                if caption_msg_id is not None:
                    await notion_write_with_retry(
                        notion.update_fulfillment_message_id,
                        page_id, caption_msg_id,
                        description=f"FULFILLMENT MESSAGE ID={caption_msg_id} for {order_id}",
                    )

            log.info(f"📤 Sending order {order_id} to fulfillment group (resume from album {start_album_index})...")

            send_result = await tg.send_order_to_group(
                bot, fulfillment_group, order,
                header="✅ NEW ORDER FOR FULFILLMENT",
                start_album_index=start_album_index,
                on_album_sent=on_album_sent,
            )

            if not send_result["success"]:
                log.error(f"  ✗ Send failed for {order_id} — will retry next poll")
                continue

            # Final flip to Processed only when all planned albums are accounted for.
            total_albums = send_result.get("total_albums", 0)
            done_count = start_album_index + send_result.get("albums_sent_this_call", 0)
            if total_albums == 0 or done_count >= total_albums:
                ok = await notion_write_with_retry(
                    notion.mark_order_processed,
                    page_id,
                    description=f"status→Processed for {order_id}",
                )
                if ok:
                    log.info(f"  ✓ Order {order_id} fully delivered, status → Processed")
                    processed += 1
                else:
                    try:
                        await bot.send_message(
                            chat_id=fulfillment_group,
                            text=f"⚠️ {order_id}: status flip to Processed failed after 3 retries (will retry on next poll)",
                        )
                    except Exception:
                        pass
            else:
                log.warning(f"  Partial send for {order_id}: {done_count}/{total_albums} albums sent — next poll resumes")

            # Image count vs items mismatch warning (only once, when fully done)
            delivered = send_result.get("image_count", 0)
            expected = send_result.get("expected_count", 0)
            if (total_albums == 0 or done_count >= total_albums) and expected > 0 and delivered < expected:
                missing = expected - delivered
                try:
                    await bot.send_message(
                        chat_id=fulfillment_group,
                        text=f"⚠️ {order_id}: {delivered} of {expected} images sent, {missing} missing",
                    )
                except Exception as e:
                    log.error(f"  Failed to post image-count warning: {e}")

        finally:
            _sending_in_progress.discard(order_id)
            # Rate limit between orders to keep well below Telegram per-chat limits.
            if processed < len(new_orders):
                await asyncio.sleep(1.5)

    return processed
```

- [ ] **Step 5.2: Verify module imports**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import order_bridge
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5.3: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/order_bridge.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(order_bridge): poll sends direct to fulfillment with per-album resume"
```

---

## Task 6: Rewrite `cmd_print_all` for per-order label replies

**Files:**
- Modify: `execution/order_bridge.py` (`cmd_print_all` function body)

The current function calls `client.get_label_pdf(tracking_numbers)` once with all tracking numbers, posting one combined PDF. The new version loops per-order, fetches a single-tracking-number PDF, and posts it as a reply to each order's stored `FULFILLMENT MESSAGE ID`.

- [ ] **Step 6.1: Locate the existing combined-PDF block**

Look at `execution/order_bridge.py` around lines 1099–1135. The block we're replacing starts after `_write_tracking_to_notion(...)` and ends with the document-send call that includes `caption=f"✅ Placed {len(tracking_numbers)} order(s). Filex labels attached."`.

- [ ] **Step 6.2: Replace the combined-PDF block with per-order dispatch**

Find this section in `cmd_print_all`:

```python
    tracking_numbers = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)
    ...
    if not tracking_numbers:
        ...
        return

    # ... (existing get_label_pdf + send_document with combined PDF)
```

Replace from `if not tracking_numbers: ... return` through the end of the combined-PDF send with:

```python
    tracking_numbers = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)
    if not tracking_numbers:
        await _safe_send_message(
            bot, chat_id,
            "⚠️ No tracking numbers came back from Filex. Nothing to print.",
        )
        return

    # Build (order_dict, tracking_number) pairs in the same order as tracking_pairs.
    # Each pair carries the original Notion order dict so we have order_id and message_id.
    order_pairs: list[tuple[dict, str]] = []
    for ref, tn in tracking_pairs:
        page_id = page_ids_by_ref.get(ref)
        if not page_id:
            continue
        order = notion.find_order_by_id(ref) or {}
        order_pairs.append((order, tn))

    # Sort: anchored orders by fulfillment_message_id ascending, legacy at the tail.
    order_pairs_sorted = sorted(
        order_pairs,
        key=lambda p: (
            0 if p[0].get("fulfillment_message_id") else 1,
            p[0].get("fulfillment_message_id") or 0,
            p[0].get("order_id", ""),
        ),
    )

    sent_count = 0
    fail_count = 0
    for order, tn in order_pairs_sorted:
        oid = order.get("order_id", "?")
        msg_id = order.get("fulfillment_message_id")

        # Fetch the single-order label PDF.
        try:
            pdf_bytes = client.get_label_pdf([tn])
        except Exception as e:
            log.error("/print all: get_label_pdf failed for %s (%s): %s", oid, tn, e, exc_info=True)
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {oid}: label fetch failed (see logs)",
            )
            fail_count += 1
            continue

        # Send the PDF: as a reply if we have a message anchor, else as a regular message.
        try:
            if msg_id:
                await bot.send_document(
                    chat_id=chat_id,
                    document=io.BytesIO(pdf_bytes),
                    filename=f"{oid}.pdf",
                    reply_to_message_id=int(msg_id),
                    read_timeout=60, write_timeout=60,
                )
            else:
                await bot.send_document(
                    chat_id=chat_id,
                    document=io.BytesIO(pdf_bytes),
                    filename=f"{oid}.pdf",
                    caption=tg.legacy_label_caption(oid),
                    read_timeout=60, write_timeout=60,
                )
            sent_count += 1
        except Exception as e:
            log.error("/print all: send_document failed for %s: %s", oid, e, exc_info=True)
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {oid}: label send failed (see logs)",
            )
            fail_count += 1
            continue

        # Light rate-limit cushion between sends.
        await asyncio.sleep(0.5)

    summary = f"✅ Sent {sent_count} label(s)."
    if fail_count:
        summary += f" ⚠️ {fail_count} failed."
    await _safe_send_message(bot, chat_id, summary)
```

(Make sure the file already has `import io` near the top — it does, but verify before running the next step.)

- [ ] **Step 6.3: Verify imports**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import order_bridge
import telegram_client
print('legacy_label_caption:', telegram_client.legacy_label_caption('AM1'))
print('OK')
"
```

Expected:
```
legacy_label_caption: 📄 AM1 — legacy order, no thread anchor
OK
```

- [ ] **Step 6.4: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/order_bridge.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(filex): /print all posts per-order labels as fulfillment-thread replies"
```

---

## Task 7: Rewrite `cmd_print_one` for per-order label reply

**Files:**
- Modify: `execution/order_bridge.py` (`cmd_print_one`, branches (c) "already labeled" and (d) "fresh placement")

`cmd_print_one` has two label-fetching branches (existing tracking re-fetch, fresh placement). Both currently send the label PDF as a reply to the operator's `/print` command message. We're switching both to reply to the order's stored `FULFILLMENT MESSAGE ID`.

- [ ] **Step 7.1: Update branch (c) "already labeled" — line ~1196**

Find this block inside `cmd_print_one`:

```python
            pdf_bytes = client.get_label_pdf([tn])
```

Look at the surrounding `send_document` call — currently it posts the PDF and falls back into the existing chat. Replace the whole send_document call in branch (c) with:

```python
            try:
                pdf_bytes = client.get_label_pdf([tn])
            except Exception as e:
                log.error("/print %s: get_label_pdf failed: %s", canonical_id, e, exc_info=True)
                await _safe_send_message(
                    bot, chat_id,
                    f"⚠️ {canonical_id}: label fetch failed (see logs)",
                )
                return

            msg_id = order.get("fulfillment_message_id")
            try:
                if msg_id:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(pdf_bytes),
                        filename=f"{canonical_id}.pdf",
                        reply_to_message_id=int(msg_id),
                        read_timeout=60, write_timeout=60,
                    )
                else:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(pdf_bytes),
                        filename=f"{canonical_id}.pdf",
                        caption=tg.legacy_label_caption(canonical_id),
                        read_timeout=60, write_timeout=60,
                    )
            except Exception as e:
                log.error("/print %s: send_document failed: %s", canonical_id, e, exc_info=True)
                await _safe_send_message(
                    bot, chat_id,
                    f"⚠️ {canonical_id}: label send failed (see logs)",
                )
                return
```

(Adjust the local variable names to match what's already in scope inside that branch — the existing code uses `canonical_id`, `tn`, `order`, `bot`, `chat_id`.)

- [ ] **Step 7.2: Update branch (d) "fresh placement" — line ~1244**

Find the analogous block in branch (d) (fresh Filex placement returns a new tracking number, then fetches the label). The pattern is exactly the same. Replace its `get_label_pdf` + `send_document` block with the same code from Step 7.1, using whatever local variables that branch already has (likely `canonical_id`, `tn`, `order`, `bot`, `chat_id`).

- [ ] **Step 7.3: Verify import + module loads**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import order_bridge
print('OK')
"
```

- [ ] **Step 7.4: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/order_bridge.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "feat(filex): /print <ID> posts label as reply to fulfillment message"
```

---

## Task 8: Hard removal of sourcing infrastructure

**Files:**
- Modify: `execution/notion_client.py`
- Modify: `execution/telegram_client.py`
- Modify: `execution/order_bridge.py`

This task does the cleanup: deletes the now-dead sourcing helpers, command handler, and file_id reuse code paths.

- [ ] **Step 8.1: `notion_client.py` — remove sourcing/file_ids field constants**

In `notion_client.py`, delete these lines (currently around lines 95–98 and the file_id constants we added in the previous spec):

```python
FIELD_SOURCING_NOTIFIED = "SOURCING NOTIFIED"
FIELD_FULFILLMENT_NOTIFIED = "FULFILLMENT NOTIFIED"
FIELD_TELEGRAM_FILE_IDS = "TELEGRAM FILE IDS"
FIELD_FILE_IDS_SAVED = "FILE IDS SAVED"
```

- [ ] **Step 8.2: `notion_client.py` — remove the helper `_parse_telegram_file_ids`**

Find and delete the entire `_parse_telegram_file_ids` function definition (small helper near `_get_files`).

- [ ] **Step 8.3: `notion_client.py` — remove dead fields from `parse_order()`**

In the dict returned by `parse_order()`, delete these entries:

```python
        "sourcing_notified": _get_checkbox(props, FIELD_SOURCING_NOTIFIED),
        "fulfillment_notified": _get_checkbox(props, FIELD_FULFILLMENT_NOTIFIED),
        "telegram_file_ids": _parse_telegram_file_ids(_get_rich_text(props, FIELD_TELEGRAM_FILE_IDS)),
        "file_ids_saved": _get_checkbox(props, FIELD_FILE_IDS_SAVED),
```

- [ ] **Step 8.4: `notion_client.py` — remove dead write helpers and query**

Delete these functions in their entirety:

- `def query_sourcing_orders()`
- `def mark_sourcing_notified(page_id: str)`
- `def mark_fulfillment_notified(page_id: str)`
- `def unmark_notified(page_id: str)`
- `def update_telegram_file_ids(page_id: str, file_ids: list[str])`
- `def mark_file_ids_saved(page_id: str, value: bool = True)`

- [ ] **Step 8.5: `notion_client.py` — remove the SOURCING NOTIFIED filter in `query_confirmed_orders`**

In `query_confirmed_orders()`, find the filter that references `FIELD_SOURCING_NOTIFIED` and remove that filter clause. The remaining filter should match `ORDER STATUS = "Confirmed | Processing"` only.

- [ ] **Step 8.6: `telegram_client.py` — delete `send_order_via_file_ids`**

Find `async def send_order_via_file_ids` and delete the entire function (currently around line 566).

- [ ] **Step 8.7: `telegram_client.py` — delete `forward_ready_orders` and the `#ready` registration**

Find `async def forward_ready_orders` (currently near line ~675). Delete the entire function.

In the same file, find `create_command_handlers(notion_module)` and remove any `MessageHandler` registration for `#ready` and `forward_ready_orders`. Leave `#cost`, `#note`, `#reset`, `#status`, `/print` handlers alone.

- [ ] **Step 8.8: `order_bridge.py` — remove sourcing-related imports and constants**

Find this line near the imports:

```python
from telegram_client import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_SOURCING_GROUP_ID, TELEGRAM_FULFILLMENT_GROUP_ID
```

Remove `TELEGRAM_SOURCING_GROUP_ID` from the import list. Also delete any remaining references to it elsewhere in `order_bridge.py`.

- [ ] **Step 8.9: Sanity check — search for dead references**

```bash
cd c:/Users/PMLS/Desktop/Automation && grep -n "SOURCING NOTIFIED\|sourcing_notified\|FULFILLMENT NOTIFIED\|fulfillment_notified\|TELEGRAM FILE IDS\|telegram_file_ids\|FILE IDS SAVED\|file_ids_saved\|send_order_via_file_ids\|forward_ready_orders\|TELEGRAM_SOURCING_GROUP_ID\|query_sourcing_orders\|mark_sourcing_notified\|mark_fulfillment_notified\|unmark_notified" execution/ 2>/dev/null | grep -v "/tests/\|/__pycache__/\|\.pyc"
```

Expected: no matches (or only matches inside test files that we're leaving alone).

If matches show up, fix them — they're dead references that will break runtime.

- [ ] **Step 8.10: Verify everything still imports and tests pass**

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "
import sys; sys.path.insert(0, 'execution')
import notion_client, telegram_client, order_bridge
print('OK')
" && cd c:/Users/PMLS/Desktop/Automation && python -m unittest execution.tests.test_album_helpers execution.tests.test_resume_and_print -v
```

Expected: `OK` then 27 tests passing.

- [ ] **Step 8.11: Commit**

```bash
git -C c:/Users/PMLS/Desktop/Automation add execution/notion_client.py execution/telegram_client.py execution/order_bridge.py
git -C c:/Users/PMLS/Desktop/Automation commit -m "refactor(order_bridge): hard-remove sourcing group infrastructure"
```

---

## Task 9: Live smoke test + push

The smoke test plan, like last time, requires stopping the production bot (`sudo systemctl stop order-bridge` on the VM) and running the new branch locally with the VPN enabled so Telegram is reachable. Then flipping live orders.

- [ ] **Step 9.1: Operator stops production bot on VM**

User runs on the VM:
```
sudo systemctl stop order-bridge
```

Confirm via `tasklist | grep python` locally that no order_bridge is running on this machine.

- [ ] **Step 9.2: Operator enables VPN, then start the bot locally**

Run locally:
```bash
cd c:/Users/PMLS/Desktop/Automation && python execution/order_bridge.py
```
(in the background)

Wait for `🚀 Bridge is running!` and `🔄 Notion poller started` lines in `.tmp/order_bridge.log` before proceeding.

- [ ] **Step 9.3: Burst test — flip 5 orders to `Confirmed | Processing`**

Operator flips 5 orders in Notion. Verify in the **fulfillment** chat:
- Each order arrives as one atomic block (or 2+ blocks if >10 images), caption on the LAST album, separator below.
- No sourcing chat activity at all.

Verify in Notion:
- `ALBUMS SENT` is set to `total_albums` for each (usually 1).
- `FULFILLMENT MESSAGE ID` is populated.
- `ORDER STATUS` is `Processed`.

- [ ] **Step 9.4: `/print all` test**

In fulfillment chat, type `/print all`. Watch:
- Filex placement runs (existing skip-message format).
- Each order's label PDF lands as a reply directly to its order's caption-bearing message.
- Labels arrive in chronological order (oldest fulfillment message first).
- No combined PDF at the end.

- [ ] **Step 9.5: `/print <ORDER_ID>` test**

In fulfillment chat, type `/print AM<some-id>` for one of the just-processed orders. Watch:
- Label PDF lands as a reply to the order's message (already-labeled branch fires since label was just made).

- [ ] **Step 9.6: Stop the local bot, merge to main, push, restart production**

```bash
# Locally:
# Ctrl+C the running bot (or use the runtime task-stop)
git -C c:/Users/PMLS/Desktop/Automation checkout main
git -C c:/Users/PMLS/Desktop/Automation merge --no-ff filex-automation -m "Merge filex-automation: skip-sourcing + per-order label replies"
git -C c:/Users/PMLS/Desktop/Automation push production main
```

Then on the VM:
```
git pull
sudo systemctl start order-bridge
```

- [ ] **Step 9.7: Operator manual cleanup in Notion UI**

Per the spec, the operator (not the bot) does the Notion cleanup:
- Hide or delete columns: `SOURCING NOTIFIED`, `FULFILLMENT NOTIFIED`, `TELEGRAM FILE IDS`, `FILE IDS SAVED`
- Remove `SOURCING` option from the `ORDER STATUS` select dropdown
- Hide `ALBUMS SENT` and `FULFILLMENT MESSAGE ID` from operator-facing views

---

## Self-Review Coverage

- [x] **Spec coverage:**
  - Single-stage delivery (Stage 1 → fulfillment direct) → Task 5
  - Per-album resume protocol → Tasks 3 (resume_range), 4 (send_order_to_group rewrite), 5 (poll_notion_once wiring)
  - `FULFILLMENT MESSAGE ID` storage → Task 4 (return), Task 5 (write in callback)
  - `/print all` per-order replies + sort → Tasks 3 (sort helper), 6 (rewrite)
  - `/print <ID>` per-order reply → Task 7
  - Legacy-order fallback → Task 3 (caption helper), Tasks 6, 7 (consumers)
  - Hard removal of sourcing infrastructure → Task 8
  - Notion schema additions → Task 1 (setup script), Task 2 (code helpers)
  - Caption overflow rule preserved → Task 4 (kept in rewrite)
  - Image count vs items count warning preserved → Task 5

- [x] **Type consistency:**
  - `SendOrderResult` defined in Task 4, used in Task 5
  - `resume_range`, `sort_orders_for_label_replies`, `legacy_label_caption` defined in Task 3, used in Tasks 4, 6, 7
  - `notion.update_albums_sent`, `notion.update_fulfillment_message_id` defined in Task 2, used in Task 5
  - `notion.mark_order_processed` is pre-existing; used in Task 5
  - All deleted symbols in Task 8 have no callers remaining after Tasks 5–7 are done

- [x] **Placeholder scan:** No "TBD" / "TODO" / "fill in details". Every code-changing step contains the actual code. Each verification step has the exact command + expected output.
