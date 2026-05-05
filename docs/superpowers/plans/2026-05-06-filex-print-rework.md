# Filex `/print` Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `/print all` to drop notification-checkbox gates, batch all skip messages into ≤2 single-shot Telegram messages, fix the broken total parser, and add `/print <ORDER_ID>` for single-order placement & label-retrieval.

**Architecture:** Pure refactor of three existing modules — no new files except tests. `filex_payload_builder.py` gets a new `parse_total()` helper used by both `build_payload` and `build_merged_payload`. `notion_client.py` gets a `query_filex_processed()` query that filters only on Status. `order_bridge.py` gets a dispatch wrapper, a rewritten `cmd_print_all`, and a new `cmd_print_one` that share validation/placement/label-fetch helpers.

**Tech Stack:** Python 3.10+, `python-telegram-bot` v21+, `pypdf`, `requests`, `httpx`, `unittest`, `pytest`. Notion API 2025-09-03 data_sources endpoint.

**Spec:** `docs/superpowers/specs/2026-05-06-filex-print-rework-design.md`

---

### Task 1: Add `parse_total()` helper with full test coverage

**Files:**
- Modify: `execution/filex_payload_builder.py`
- Test: `tests/test_filex_payload_builder.py`

This task replaces the inline total-parsing code in `build_payload` and the duplicate copy in `build_merged_payload` with a single regex-based helper.

- [ ] **Step 1: Write the failing tests**

Add this test class to `tests/test_filex_payload_builder.py` after the existing `TestBuildPayload` class (top-level, NOT nested):

```python
from filex_payload_builder import parse_total


class TestParseTotal(unittest.TestCase):
    def test_int_passes_through(self):
        self.assertEqual(parse_total(300), 300.0)

    def test_float_passes_through(self):
        self.assertEqual(parse_total(300.99), 300.99)

    def test_zero_int_allowed(self):
        self.assertEqual(parse_total(0), 0.0)

    def test_zero_float_allowed(self):
        self.assertEqual(parse_total(0.0), 0.0)

    def test_negative_int_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(-5)

    def test_plain_string_number(self):
        self.assertEqual(parse_total("300"), 300.0)

    def test_string_with_decimal(self):
        self.assertEqual(parse_total("300.99"), 300.99)

    def test_aed_prefix_with_space(self):
        self.assertEqual(parse_total("AED 300.00"), 300.0)

    def test_aed_prefix_no_space(self):
        self.assertEqual(parse_total("AED1,111.99"), 1111.99)

    def test_inline_note_after_amount(self):
        # The bug from the AM3088 incident: was returning 1.00 instead of 1949.39.
        self.assertEqual(
            parse_total("AED1,949.39\n\nnote: send all good quality and same"),
            1949.39,
        )

    def test_million_thousand_separators(self):
        self.assertEqual(parse_total("AED 1,234,567.89"), 1234567.89)

    def test_trailing_slash_dash(self):
        self.assertEqual(parse_total("300/-"), 300.0)

    def test_aed_only_no_number_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total("AED")

    def test_empty_string_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total("")

    def test_none_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(None)

    def test_unsupported_type_rejected(self):
        with self.assertRaises(ValidationError):
            parse_total(["300"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py::TestParseTotal -v`
Expected: ImportError or "cannot import name 'parse_total'" — fails on import.

- [ ] **Step 3: Implement `parse_total()` in the payload builder**

In `execution/filex_payload_builder.py`, add this function immediately after the `ValidationError` class definition (before `build_payload`):

```python
import re

_TOTAL_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def parse_total(raw) -> float:
    """
    Extract a non-negative float from a Notion total field.

    Accepts:
      - int / float: returned as float (zero allowed for exchange orders).
      - str: extracts the FIRST numeric token. Handles 'AED' prefix,
        thousand-separator commas, decimals, trailing notes, '/-' suffix.

    Raises:
        ValidationError if the value is None, an unsupported type,
        a string with no numeric content, or a negative number.
    """
    if isinstance(raw, bool):
        # bool is a subclass of int; reject explicitly to avoid surprises
        raise ValidationError(f"invalid total: {raw!r}")
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        m = _TOTAL_RE.search(raw)
        if not m:
            raise ValidationError(f"invalid total: {raw!r}")
        try:
            value = float(m.group(0).replace(",", ""))
        except ValueError:
            raise ValidationError(f"invalid total: {raw!r}")
    else:
        raise ValidationError(f"invalid total: {raw!r}")
    if value < 0:
        raise ValidationError(f"invalid total: {raw!r}")
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py::TestParseTotal -v`
Expected: 16 passed.

- [ ] **Step 5: Replace the inline total logic in `build_payload`**

In `execution/filex_payload_builder.py`, replace the block currently at lines 53–67:

```python
    # Total can come from Notion as a number OR a formatted string like "AED299.00"
    total_raw = order.get("total_aed") if order.get("total_aed") is not None else order.get("total")
    total: float | None = None
    if isinstance(total_raw, (int, float)):
        total = float(total_raw)
    elif isinstance(total_raw, str):
        # Strip AED prefix, currency symbols, commas, whitespace
        cleaned = total_raw.strip().upper().removeprefix("AED").replace(",", "").strip()
        try:
            total = float(cleaned)
        except ValueError:
            total = None
    if total is None:
        raise ValidationError(f"invalid total: {total_raw!r}")
    total_str = f"{total:.2f}"
```

with:

```python
    total_raw = order.get("total_aed") if order.get("total_aed") is not None else order.get("total")
    total = parse_total(total_raw)
    total_str = f"{total:.2f}"
```

- [ ] **Step 6: Replace the inline total logic in `build_merged_payload`**

In the same file, replace the block currently at lines 113–125:

```python
    total_sum = 0.0
    pieces_sum = 0
    desc_parts = []
    for o in orders:
        total_raw = o.get("total_aed") if o.get("total_aed") is not None else o.get("total")
        if isinstance(total_raw, (int, float)):
            total_sum += float(total_raw)
        elif isinstance(total_raw, str):
            cleaned = total_raw.strip().upper().removeprefix("AED").replace(",", "").strip()
            try:
                total_sum += float(cleaned)
            except ValueError:
                pass
        pieces_sum += parse_pieces(o.get("item_qty") or "")
        desc_parts.append((o.get("item_qty") or "Item").replace("\n", " + ").strip())
```

with:

```python
    total_sum = 0.0
    pieces_sum = 0
    desc_parts = []
    for o in orders:
        total_raw = o.get("total_aed") if o.get("total_aed") is not None else o.get("total")
        try:
            total_sum += parse_total(total_raw)
        except ValidationError:
            # In a merged group, the first order's total has already been
            # validated by build_payload. Subsequent unparseable totals are
            # treated as 0 to avoid losing the merged shipment.
            pass
        pieces_sum += parse_pieces(o.get("item_qty") or "")
        desc_parts.append((o.get("item_qty") or "Item").replace("\n", " + ").strip())
```

- [ ] **Step 7: Run all payload builder tests to ensure no regression**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py -v`
Expected: all tests in `TestBuildPayload` and `TestParseTotal` pass.

- [ ] **Step 8: Commit**

```bash
git add execution/filex_payload_builder.py tests/test_filex_payload_builder.py
git commit -m "Add parse_total() helper, fix AM3088-style 'AED1,949.39\\n\\nnote' bug

Replaces inline total parsing in build_payload and build_merged_payload
with a single regex-based helper that captures full numbers (incl.
thousand separators) and ignores trailing inline notes."
```

---

### Task 2: Normalize validation error messages to match the new skip categories

**Files:**
- Modify: `execution/filex_payload_builder.py`
- Test: `tests/test_filex_payload_builder.py`

The skip message in `/print all` (Message 1) groups orders under fixed-string headers. To group reliably, ValidationError messages need stable category prefixes.

- [ ] **Step 1: Write the failing tests**

Add to the existing `TestBuildPayload` class in `tests/test_filex_payload_builder.py`:

```python
    def test_no_city_error_starts_with_category(self):
        order = self._full_order()
        order["full_address"] = "Random building street villa no_city_token"
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertTrue(str(ctx.exception).startswith("missing city in address"))

    def test_invalid_total_error_starts_with_category(self):
        order = self._full_order()
        order["total"] = "AED"
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertTrue(str(ctx.exception).startswith("invalid total"))

    def test_missing_phone_error_starts_with_category(self):
        order = self._full_order()
        order["phone"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertTrue(str(ctx.exception).startswith("missing phone"))

    def test_missing_name_error_starts_with_category(self):
        order = self._full_order()
        order["customer_name"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertTrue(str(ctx.exception).startswith("missing customer name"))

    def test_missing_address_error_starts_with_category(self):
        order = self._full_order()
        order["full_address"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertTrue(str(ctx.exception).startswith("missing address"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py::TestBuildPayload -v -k "starts_with_category"`
Expected: 5 fail (current messages don't have these exact prefixes).

- [ ] **Step 3: Update the error messages in `build_payload`**

In `execution/filex_payload_builder.py`, replace the existing `raise ValidationError(...)` lines in `build_payload` with these:

```python
    name = (order.get("customer_name") or "").strip()
    if not name:
        raise ValidationError("missing customer name")

    phone_raw = (order.get("phone") or "").strip()
    if not phone_raw:
        raise ValidationError("missing phone")
    phone = clean_phone_number(phone_raw)
    if phone.startswith("971"):
        phone = "0" + phone[3:]
    if not phone or len(phone) < 10:
        raise ValidationError(f"missing phone (invalid after normalization: {phone!r})")

    address = (order.get("full_address") or "").strip()
    if not address:
        raise ValidationError("missing address")

    city = normalize_city(address)
    if not city:
        raise ValidationError(f"missing city in address: {address[:60]!r}")
```

Then in the total step replace `raise ValidationError(f"invalid total: {total_raw!r}")` (which already comes from `parse_total`) — note this is already correct, but ensure no `f"invalid total: ..."` variant remains in builder code itself.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py -v`
Expected: all tests pass (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add execution/filex_payload_builder.py tests/test_filex_payload_builder.py
git commit -m "Normalize ValidationError messages with stable category prefixes

The new /print all skip-message structure groups orders by reason.
Stable string prefixes (missing customer name, missing phone, missing
address, missing city in address, invalid total) make grouping reliable."
```

---

### Task 3: Add `query_filex_processed()` to notion_client

**Files:**
- Modify: `execution/notion_client.py`

This task adds a Notion query that returns every order with `Status = Processed`, regardless of any other checkbox. The existing `query_filex_eligible()` is left in place for backward compatibility.

- [ ] **Step 1: Locate the existing eligible query**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -c "from execution import notion_client as nc; help(nc.query_filex_eligible)" 2>&1 | head -20` (informational only — no assertion needed).

- [ ] **Step 2: Add `query_filex_processed()` immediately below `query_filex_eligible()`**

In `execution/notion_client.py`, find the function `query_filex_eligible()` (search for `def query_filex_eligible`). Add this function directly below it:

```python
def query_filex_processed() -> list[dict]:
    """
    Every order whose ORDER STATUS == "Processed", regardless of
    Sourcing Notified, Fulfillment Notified, or Filex Submitted state.

    Used by /print all (the per-order classifier inspects FILEX STATUS
    and validation result to decide what to do with each row).
    """
    payload = {
        "filter": {"property": FIELD_ORDER_STATUS, "select": {"equals": "Processed"}},
    }
    return _run_query(payload)
```

- [ ] **Step 3: Smoke-test the query against live Notion**

Run:
```bash
cd c:/Users/PMLS/Desktop/Automation && PYTHONIOENCODING=utf-8 python -c "
import sys
sys.path.insert(0, 'execution')
from dotenv import load_dotenv; load_dotenv()
import notion_client as nc
rows = nc.query_filex_processed()
print(f'Status=Processed: {len(rows)} rows')
print(f'First 3:', [r.get('order_id') for r in rows[:3]])
"
```
Expected: Prints a row count (whatever is currently in CRM) plus the first three order IDs. Non-zero count is OK (those rows already have Filex Submitted=✓ from prior runs).

- [ ] **Step 4: Commit**

```bash
git add execution/notion_client.py
git commit -m "Add query_filex_processed() — Status=Processed, no other gates

Used by the rewritten /print all so eligibility is decided per-order at
runtime (FILEX STATUS as the source of truth for 'already submitted')
rather than via Notion-side checkbox filters that operators don't keep
in sync."
```

---

### Task 4: Extract shared placement helpers from `cmd_print_all`

**Files:**
- Modify: `execution/order_bridge.py`

Both `/print all` and `/print <ID>` need to:
- Lock pages with `Filex Submitted = ✓`
- Call Filex `place_orders`
- Update Notion (tracking, status, dispatched, last update)
- Fetch the label PDF

This task pulls those steps into helpers so the two commands don't duplicate code. The existing `cmd_print_all` continues to work after this refactor.

- [ ] **Step 1: Add three helpers above `cmd_print_all`**

In `execution/order_bridge.py`, find the line `async def cmd_print_all(update, context):`. Add these three helpers directly above it:

```python
def _lock_pages(page_ids_by_ref: dict[str, list[str]]) -> None:
    """Set Filex Submitted=✓ on every page in the lock set."""
    for page_ids in page_ids_by_ref.values():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, True)


def _unlock_pages(page_ids_by_ref: dict[str, list[str]]) -> None:
    """Revert Filex Submitted=☐ on every page in the lock set."""
    for page_ids in page_ids_by_ref.values():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, False)


def _write_tracking_to_notion(
    page_ids_by_ref: dict[str, list[str]],
    tracking_pairs: list[dict],
) -> list[str]:
    """Write tracking + status + timestamps to every Notion row that placed.
    Returns the list of tracking numbers that were written (in placebulk order)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    written = []
    for entry in tracking_pairs:
        ref = entry.get("barcode")
        tn = entry.get("tracking_no")
        page_ids = page_ids_by_ref.get(ref, [])
        if not page_ids:
            log.warning("Returned ref %s not in our locked set", ref)
            continue
        for page_id in page_ids:
            nc.set_tracking_info(page_id, tn, FILEX_TRACKING_BASE + tn)
            nc.set_filex_status(page_id, "Label Created")
            nc.set_dispatched_at(page_id, now_iso)
            nc.set_last_update(page_id, now_iso)
        if len(page_ids) > 1:
            log.info(f"  ↳ Wrote tracking {tn} to {len(page_ids)} merged Notion rows ({ref})")
        if tn:
            written.append(tn)
    return written
```

- [ ] **Step 2: Wire `cmd_print_all` to use the new helpers**

In `cmd_print_all`, replace the existing block at the line that begins `# 4. Lock orders BEFORE the API call`:

Before (existing):
```python
    # 4. Lock orders BEFORE the API call (prevents double-submit on retry)
    for ref, page_ids in page_ids_by_ref.items():
        for page_id in page_ids:
            nc.mark_filex_submitted(page_id, True)

    # 5. Place orders via Filex
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        # Revert locks on failure so a retry can re-submit
        for ref, page_ids in page_ids_by_ref.items():
            for page_id in page_ids:
                nc.mark_filex_submitted(page_id, False)
        await _safe_send_message(bot, chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    # 6. Update Notion with tracking info — fan out across all linked rows for merged shipments
    now_iso = datetime.now(timezone.utc).isoformat()
    tracking_pairs = result.get("trackingnos", [])
    for entry in tracking_pairs:
        ref = entry.get("barcode")
        tn = entry.get("tracking_no")
        page_ids = page_ids_by_ref.get(ref, [])
        if not page_ids:
            log.warning("Returned ref %s not in our locked set", ref)
            continue
        for page_id in page_ids:
            nc.set_tracking_info(page_id, tn, FILEX_TRACKING_BASE + tn)
            nc.set_filex_status(page_id, "Label Created")
            nc.set_dispatched_at(page_id, now_iso)
            nc.set_last_update(page_id, now_iso)
        if len(page_ids) > 1:
            log.info(f"  ↳ Wrote tracking {tn} to {len(page_ids)} merged Notion rows ({ref})")

    # 6. Fetch combined PDF
    tracking_numbers = [e["tracking_no"] for e in tracking_pairs if e.get("tracking_no")]
```

After:
```python
    # 4. Lock orders BEFORE the API call (prevents double-submit on retry)
    _lock_pages(page_ids_by_ref)

    # 5. Place orders via Filex
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        _unlock_pages(page_ids_by_ref)
        await _safe_send_message(bot, chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    # 6. Update Notion with tracking info — fan out across all linked rows for merged shipments
    tracking_pairs = result.get("trackingnos", [])
    tracking_numbers = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)
```

- [ ] **Step 3: Restart logic test — run order_bridge import sanity check**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -c "import sys; sys.path.insert(0, 'execution'); from dotenv import load_dotenv; load_dotenv(); import order_bridge; print('import OK')"`
Expected: `import OK`.

- [ ] **Step 4: Commit**

```bash
git add execution/order_bridge.py
git commit -m "Extract _lock_pages, _unlock_pages, _write_tracking_to_notion helpers

Pulls placement-flow steps out of cmd_print_all so cmd_print_one (next
task) can reuse them without duplicating logic."
```

---

### Task 5: Build the new `cmd_print_all` classifier + skip-message structure

**Files:**
- Modify: `execution/order_bridge.py`

This task replaces the body of `cmd_print_all` to use `query_filex_processed()`, classifies each row, batches skip messages into single Telegram messages, and only places orders that pass classification.

- [ ] **Step 1: Add the skip-grouping helpers above the existing `cmd_print_all`**

In `execution/order_bridge.py`, immediately above `async def cmd_print_all` (and below the helpers from Task 4), add:

```python
# Validation reason categories — must match prefixes in build_payload's ValidationError messages.
_SKIP_CATEGORIES: list[tuple[str, str]] = [
    ("missing city in address", "Missing city in address"),
    ("invalid total",            "Invalid total"),
    ("missing phone",            "Missing phone"),
    ("missing customer name",    "Missing customer name"),
    ("missing address",          "Missing address"),
]


def _categorize_validation_error(msg: str) -> str:
    """Map a ValidationError message to one of the fixed category headers."""
    for prefix, header in _SKIP_CATEGORIES:
        if msg.startswith(prefix):
            return header
    return "Other"


def _format_validation_skip_message(
    skips_by_category: dict[str, list[tuple[str, str]]],
) -> str:
    """Build the single Telegram message body for validation skips.

    skips_by_category maps the category header to a list of (order_id, reason_detail).
    """
    total = sum(len(v) for v in skips_by_category.values())
    lines = [
        f"⚠️ Skipped {total} order(s) — fix and re-run /print or use /print <ID>:",
        "",
    ]
    # Preserve fixed category order
    for _, header in _SKIP_CATEGORIES:
        items = skips_by_category.get(header, [])
        if not items:
            continue
        lines.append(f"{header} ({len(items)}):")
        for oid, detail in items:
            short = (detail[:60] + "…") if len(detail) > 60 else detail
            lines.append(f"  • {oid} — {short}")
        lines.append("")
    other = skips_by_category.get("Other", [])
    if other:
        lines.append(f"Other ({len(other)}):")
        for oid, detail in other:
            short = (detail[:60] + "…") if len(detail) > 60 else detail
            lines.append(f"  • {oid} — {short}")
    return "\n".join(lines).rstrip()


def _format_already_labeled_message(rows: list[dict]) -> str:
    """Build the single Telegram message body for 'already has Filex label' skips."""
    lines = [
        f"ℹ️ {len(rows)} order(s) already have a Filex label — verify and tick \"Filex Submitted\":",
    ]
    for r in rows:
        oid = r.get("order_id", "?")
        status = r.get("filex_status", "?") or "?"
        tn = r.get("tracking_number", "") or "(no tracking)"
        lines.append(f"  • {oid} — status: {status}, tracking: {tn}")
    return "\n".join(lines)


async def _send_long_message(bot, chat_id: int, body: str, fallback_filename: str) -> None:
    """Send a single Telegram message; fall back to .txt attachment if too long."""
    LIMIT = 3800  # defensive margin under Telegram's 4096 char cap
    if len(body) <= LIMIT:
        await _safe_send_message(bot, chat_id, body)
        return
    # Fallback: attach as text file
    await _safe_send_document(
        bot,
        chat_id,
        document=body.encode("utf-8"),
        filename=fallback_filename,
    )
```

- [ ] **Step 2: Replace the body of `cmd_print_all`**

Replace the entire body of `cmd_print_all` (everything after the `log.info("/print all triggered ...")` line) with:

```python
    # 1. Query every Processed order — no checkbox gates.
    eligible = nc.query_filex_processed()
    if not eligible:
        await _safe_send_message(bot, chat_id, "No orders with status Processed.")
        return

    # 2. Classify each row.
    skips_by_category: dict[str, list[tuple[str, str]]] = {}
    already_labeled: list[dict] = []
    to_place: list[dict] = []

    for order in eligible:
        if (order.get("filex_status") or "").strip():
            already_labeled.append(order)
            continue
        to_place.append(order)

    # 3. Group placeable orders by phone for auto-merge.
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    orphans: list[dict] = []
    for order in to_place:
        raw_phone = order.get("phone", "") or ""
        if not raw_phone:
            orphans.append(order)
            continue
        normalized = clean_phone_number(raw_phone)
        if not normalized:
            orphans.append(order)
            continue
        grouped[normalized].append(order)

    # 4. Build payloads — validation errors collected, not sent per-order.
    payloads: list[dict] = []
    page_ids_by_ref: dict[str, list[str]] = {}

    def _record_skip(order_id: str, err_msg: str) -> None:
        category = _categorize_validation_error(err_msg)
        skips_by_category.setdefault(category, []).append((order_id, err_msg))

    for phone, group in grouped.items():
        if len(group) == 1:
            order = group[0]
            try:
                payload = build_payload(order)
            except ValidationError as e:
                _record_skip(order.get("order_id", "?"), str(e))
                continue
            payloads.append(payload)
            page_ids_by_ref[payload["ShipperRef"]] = [order["page_id"]]
        else:
            try:
                merged_payload = build_merged_payload(group)
            except ValidationError as e:
                # Merged group fails on the FIRST order's validation; report all members.
                for o in group:
                    _record_skip(o.get("order_id", "?"), str(e))
                continue
            payloads.append(merged_payload)
            page_ids_by_ref[merged_payload["ShipperRef"]] = [o["page_id"] for o in group]
            log.info(
                f"  ↳ Merging {len(group)} orders for phone {phone}: "
                f"{[o.get('order_id') for o in group]}"
            )

    for order in orphans:
        try:
            payload = build_payload(order)
        except ValidationError as e:
            _record_skip(order.get("order_id", "?"), str(e))
            continue
        payloads.append(payload)
        page_ids_by_ref[payload["ShipperRef"]] = [order["page_id"]]

    # 5. Send the two skip messages (single API call each).
    if skips_by_category:
        body = _format_validation_skip_message(skips_by_category)
        await _send_long_message(bot, chat_id, body, "validation_skips.txt")
    if already_labeled:
        body = _format_already_labeled_message(already_labeled)
        await _send_long_message(bot, chat_id, body, "already_labeled.txt")

    if not payloads:
        await _safe_send_message(bot, chat_id, "No labels generated — all eligible orders were skipped.")
        return

    # 6. Lock, place, update.
    _lock_pages(page_ids_by_ref)
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        _unlock_pages(page_ids_by_ref)
        await _safe_send_message(bot, chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    tracking_pairs = result.get("trackingnos", [])
    tracking_numbers = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)

    # 7. Fetch combined PDF.
    if not tracking_numbers:
        await _safe_send_message(
            bot, chat_id,
            "⚠️ Filex returned 'success' but no tracking numbers — verify in Filex portal.",
        )
        return
    try:
        pdf_bytes = client.get_label_pdf(tracking_numbers)
    except Exception as e:
        await _safe_send_message(
            bot, chat_id,
            f"✅ Placed {len(tracking_numbers)} orders, but label PDF fetch failed: {e}\n"
            f"Use /print <ID> for each order to retrieve the label.",
        )
        log.error("filex label pdf fetch failed", exc_info=True)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"filex_labels_{today}_{len(tracking_numbers)}.pdf"
    await _safe_send_document(
        bot, chat_id,
        document=pdf_bytes,
        filename=filename,
        caption=f"✅ Placed {len(tracking_numbers)} order(s). Filex labels attached.",
    )
    log.info(
        "/print all completed: %d orders, PDF %d bytes",
        len(tracking_numbers), len(pdf_bytes),
    )
```

- [ ] **Step 3: Verify `_safe_send_document` accepts a `caption` kwarg**

Run: `cd c:/Users/PMLS/Desktop/Automation && grep -n "async def _safe_send_document" execution/order_bridge.py`. Read the function signature; confirm it forwards `**kwargs` (or extend it if it doesn't).

If `_safe_send_document` does NOT accept `caption`, update its signature to accept `**kwargs` and forward them:

```python
async def _safe_send_document(bot, chat_id: int, document, filename: str, **kwargs) -> None:
    """Send a Telegram document, log timeouts/errors instead of crashing."""
    try:
        await bot.send_document(chat_id, document=document, filename=filename, **kwargs)
    except (TimedOut, TelegramError) as e:
        log.warning("Telegram send_document failed: %s | filename=%s", e, filename)
```

- [ ] **Step 4: Run import sanity check**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -c "import sys; sys.path.insert(0, 'execution'); from dotenv import load_dotenv; load_dotenv(); import order_bridge; print('import OK')"`
Expected: `import OK`.

- [ ] **Step 5: Commit**

```bash
git add execution/order_bridge.py
git commit -m "Rewrite cmd_print_all: drop notified gates, batch skip messages

Status=Processed is the only Notion filter. Per-order classification:
non-empty FILEX STATUS -> 'already labeled' skip; ValidationError ->
'validation' skip grouped by category. Both skip messages are sent as
single Telegram messages with .txt fallback for very long lists."
```

---

### Task 6: Add `cmd_print_one` for single-order placement / label retrieval

**Files:**
- Modify: `execution/order_bridge.py`

- [ ] **Step 1: Add `cmd_print_one` directly below `cmd_print_all`**

In `execution/order_bridge.py`, append below the rewritten `cmd_print_all`:

```python
def _resolve_order_id(raw: str) -> dict | None:
    """Look up an order in Notion, tolerating WA127/WA 127 spelling variants.

    Returns the parsed order dict on first match, or None if no variant matches.
    """
    candidates = [raw]
    if " " not in raw and len(raw) >= 3 and raw[:2].isalpha() and raw[2:3].isdigit():
        candidates.append(f"{raw[:2]} {raw[2:]}")
    if " " in raw:
        candidates.append(raw.replace(" ", ""))
    seen = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        order = nc.find_order_by_shipper_ref(cand)
        if order:
            return order
    return None


async def cmd_print_one(update, context, order_id: str) -> None:
    """/print <ORDER_ID> — single-order placement OR label re-fetch."""
    chat_id = update.effective_chat.id
    bot = context.bot

    if FULFILLMENT_GROUP_ID and chat_id != FULFILLMENT_GROUP_ID:
        await _safe_send_message(bot, chat_id, "/print is only available in the fulfillment group.")
        return

    user_id = update.effective_user.id if update.effective_user else "?"
    log.info("/print %r triggered by %s in chat %s", order_id, user_id, chat_id)

    # 1. Lookup with spelling-variant tolerance.
    order = _resolve_order_id(order_id)
    if not order:
        await _safe_send_message(bot, chat_id, f"⚠️ Order {order_id} not found in CRM.")
        return

    canonical_id = order.get("order_id") or order_id

    # 2. Status gate.
    status = (order.get("order_status") or "").strip()
    if status != "Processed":
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} status is \"{status or '(blank)'}\" — only \"Processed\" orders can be printed.",
        )
        return

    client = get_filex_client()

    # 3. Already-labeled branch: re-fetch existing label, no placement.
    filex_status = (order.get("filex_status") or "").strip()
    if filex_status:
        tn = (order.get("tracking_number") or "").strip()
        if not tn:
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {canonical_id} has FILEX STATUS but no tracking number. Manual investigation needed.",
            )
            return
        try:
            pdf_bytes = client.get_label_pdf([tn])
        except Exception as e:
            await _safe_send_message(
                bot, chat_id,
                f"⚠️ {canonical_id} label fetch failed: {e}",
            )
            log.error("/print %s label fetch failed", canonical_id, exc_info=True)
            return
        today = datetime.now().strftime("%Y-%m-%d")
        await _safe_send_document(
            bot, chat_id,
            document=pdf_bytes,
            filename=f"{canonical_id.replace(' ', '_')}_{today}.pdf",
            caption=f"ℹ️ {canonical_id} already has a Filex label. Status: {filex_status}. Tracking: {tn}.",
        )
        return

    # 4. Fresh placement branch.
    try:
        payload = build_payload(order)
    except ValidationError as e:
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} cannot be placed: {e}. Fix the order in CRM and retry.",
        )
        return

    page_ids_by_ref = {payload["ShipperRef"]: [order["page_id"]]}
    _lock_pages(page_ids_by_ref)
    try:
        result = client.place_orders([payload])
    except Exception as e:
        _unlock_pages(page_ids_by_ref)
        await _safe_send_message(bot, chat_id, f"⚠️ {canonical_id} Filex submission failed: {e}")
        log.error("/print %s placebulk failed", canonical_id, exc_info=True)
        return

    tracking_pairs = result.get("trackingnos", [])
    written = _write_tracking_to_notion(page_ids_by_ref, tracking_pairs)
    if not written:
        await _safe_send_message(
            bot, chat_id,
            f"⚠️ {canonical_id} placed but Filex returned no tracking number — verify in Filex portal.",
        )
        return

    tn = written[0]
    try:
        pdf_bytes = client.get_label_pdf([tn])
    except Exception as e:
        await _safe_send_message(
            bot, chat_id,
            f"✅ {canonical_id} placed (tracking: {tn}), but label fetch failed: {e}",
        )
        log.error("/print %s label fetch failed after placement", canonical_id, exc_info=True)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    await _safe_send_document(
        bot, chat_id,
        document=pdf_bytes,
        filename=f"{canonical_id.replace(' ', '_')}_{today}.pdf",
        caption=f"✅ {canonical_id} placed. Tracking: {tn}.",
    )
```

- [ ] **Step 2: Add a dispatch wrapper that routes `/print` based on its args**

In `execution/order_bridge.py`, find the `cmd_print_all` function and add this wrapper directly above it:

```python
async def cmd_print(update, context):
    """Dispatch /print:
       - /print all           → bulk placement
       - /print <ORDER_ID>    → single placement / label re-fetch
       - /print               → usage hint
       - /print AM 3030       → joined as 'AM 3030' for the Notion lookup
    """
    args = context.args or []
    if not args:
        chat_id = update.effective_chat.id
        await _safe_send_message(
            context.bot, chat_id,
            "Usage:\n  /print all          — print every Processed order\n"
            "  /print <ORDER_ID>   — print one order (e.g. /print AM3030)",
        )
        return
    if len(args) == 1 and args[0].lower() == "all":
        await cmd_print_all(update, context)
        return
    order_id = " ".join(args)
    await cmd_print_one(update, context, order_id)
```

- [ ] **Step 3: Update the handler registration to call the dispatcher**

In `execution/order_bridge.py`, find the line:

```python
    app.add_handler(CommandHandler("print", cmd_print_all))
```

Replace with:

```python
    app.add_handler(CommandHandler("print", cmd_print))
```

- [ ] **Step 4: Run import sanity check**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -c "import sys; sys.path.insert(0, 'execution'); from dotenv import load_dotenv; load_dotenv(); import order_bridge; print('import OK'); print('cmd_print:', order_bridge.cmd_print); print('cmd_print_one:', order_bridge.cmd_print_one)"`
Expected: `import OK` and two coroutine function references.

- [ ] **Step 5: Commit**

```bash
git add execution/order_bridge.py
git commit -m "Add /print <ORDER_ID> single-order command + dispatch wrapper

Three branches: (a) Processed + no Filex status -> place fresh, send
label; (b) Processed + has Filex status -> re-fetch existing label,
no placement; (c) status != Processed -> refuse. Order ID matching is
tolerant to WA127/WA 127 variants."
```

---

### Task 7: Unit tests for the order-ID resolver

**Files:**
- Create: `tests/test_print_dispatch.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_print_dispatch.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

# We patch nc.find_order_by_shipper_ref before importing _resolve_order_id.
import notion_client as _nc

from order_bridge import _resolve_order_id


class TestResolveOrderId(unittest.TestCase):
    def test_exact_match_first(self):
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=lambda x: {"order_id": x} if x == "AM3030" else None):
            order = _resolve_order_id("AM3030")
            self.assertEqual(order["order_id"], "AM3030")

    def test_inserts_space_for_wa127(self):
        # Notion has 'WA 127' but user typed 'WA127'.
        def fake(x):
            return {"order_id": "WA 127"} if x == "WA 127" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            order = _resolve_order_id("WA127")
            self.assertEqual(order["order_id"], "WA 127")

    def test_strips_space_for_wa127(self):
        # Notion has 'WA127' but user typed 'WA 127'.
        def fake(x):
            return {"order_id": "WA127"} if x == "WA127" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            order = _resolve_order_id("WA 127")
            self.assertEqual(order["order_id"], "WA127")

    def test_unknown_id_returns_none(self):
        with patch.object(_nc, "find_order_by_shipper_ref", return_value=None):
            self.assertIsNone(_resolve_order_id("ZZ9999"))

    def test_preserves_case_sensitivity(self):
        # Notion is case-sensitive; lowercase variant should not match an uppercase row.
        def fake(x):
            return {"order_id": "AM3030"} if x == "AM3030" else None
        with patch.object(_nc, "find_order_by_shipper_ref", side_effect=fake):
            self.assertIsNone(_resolve_order_id("am3030"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_print_dispatch.py -v`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_print_dispatch.py
git commit -m "Tests for _resolve_order_id WA127/WA 127 spelling tolerance"
```

---

### Task 8: Integration test — `cmd_print_all` classifier

**Files:**
- Create: `tests/test_print_all_classifier.py`

This test verifies the new classifier pure-function behavior: validation skips grouped by category, already-labeled rows split out, payloads built only for the rest. It does NOT test Telegram or Filex network calls — those are mocked.

- [ ] **Step 1: Write the test file**

Create `tests/test_print_all_classifier.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))


def _order(**overrides):
    """A baseline valid Processed order."""
    base = {
        "page_id": "page-" + overrides.get("order_id", "X"),
        "order_id": "AM3030",
        "customer_name": "Test Customer",
        "phone": "0501234567",
        "full_address": "Apt 1 Some Tower Dubai",
        "total": 100.00,
        "total_aed": None,
        "item_qty": "Item 1",
        "internal_note": "",
        "order_status": "Processed",
        "filex_status": None,
        "tracking_number": None,
        "filex_submitted": False,
    }
    base.update(overrides)
    return base


class TestPrintAllClassifier(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_separates_already_labeled_validation_and_placeable(self):
        from order_bridge import cmd_print_all

        eligible = [
            _order(order_id="AM3001"),                                    # placeable
            _order(order_id="AM3002", filex_status="Label Created",
                   tracking_number="4866850001"),                          # already labeled
            _order(order_id="AM3003", full_address="Random no city here"), # validation skip: city
            _order(order_id="AM3004", total="AED"),                        # validation skip: total
        ]

        # Patch all the side-effects.
        with patch("order_bridge.nc.query_filex_processed", return_value=eligible), \
             patch("order_bridge._lock_pages") as mock_lock, \
             patch("order_bridge._unlock_pages"), \
             patch("order_bridge._write_tracking_to_notion", return_value=["TRK001"]), \
             patch("order_bridge.get_filex_client") as mock_get_client, \
             patch("order_bridge._safe_send_message", new=AsyncMock()) as mock_msg, \
             patch("order_bridge._safe_send_document", new=AsyncMock()) as mock_doc, \
             patch("order_bridge._send_long_message", new=AsyncMock()) as mock_long:

            mock_client = MagicMock()
            mock_client.place_orders.return_value = {"data": "success", "trackingnos": [
                {"barcode": "AM3001", "tracking_no": "TRK001"},
            ]}
            mock_client.get_label_pdf.return_value = b"%PDF-1.4 fake"
            mock_get_client.return_value = mock_client

            update = MagicMock()
            update.effective_chat.id = 0  # FULFILLMENT_GROUP_ID is 0 by default in tests
            update.effective_user.id = 1
            context = MagicMock()
            context.args = ["all"]
            await cmd_print_all(update, context)

        # Validation skip body must mention BOTH categories.
        skip_calls = mock_long.await_args_list
        self.assertTrue(any("Missing city" in str(c) for c in skip_calls))
        self.assertTrue(any("Invalid total" in str(c) for c in skip_calls))
        # Already-labeled message must include AM3002 + tracking.
        self.assertTrue(any("AM3002" in str(c) and "4866850001" in str(c) for c in skip_calls))
        # Placement happened for AM3001.
        mock_client.place_orders.assert_called_once()
        called_payloads = mock_client.place_orders.call_args.args[0]
        self.assertEqual(len(called_payloads), 1)
        self.assertEqual(called_payloads[0]["ShipperRef"], "AM3001")
        # Final PDF was sent.
        mock_doc.assert_awaited()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_print_all_classifier.py -v`
Expected: 1 passed.

If the test fails because `FULFILLMENT_GROUP_ID` blocks the chat, set `update.effective_chat.id` to whatever value `order_bridge.FULFILLMENT_GROUP_ID` has in the test env (or 0 if unset). Read the actual value via:

```bash
cd c:/Users/PMLS/Desktop/Automation && python -c "import sys; sys.path.insert(0, 'execution'); from dotenv import load_dotenv; load_dotenv(); import order_bridge; print(order_bridge.FULFILLMENT_GROUP_ID)"
```

Use that value in `update.effective_chat.id` in the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_print_all_classifier.py
git commit -m "Integration test for cmd_print_all classifier branches"
```

---

### Task 9: Integration test — `cmd_print_one` branches

**Files:**
- Create: `tests/test_print_one.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_print_one.py`:

```python
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))


def _order(**overrides):
    base = {
        "page_id": "page-X",
        "order_id": "AM3030",
        "customer_name": "Test Customer",
        "phone": "0501234567",
        "full_address": "Apt 1 Some Tower Dubai",
        "total": 100.00,
        "total_aed": None,
        "item_qty": "Item 1",
        "internal_note": "",
        "order_status": "Processed",
        "filex_status": None,
        "tracking_number": None,
        "filex_submitted": False,
    }
    base.update(overrides)
    return base


class TestPrintOne(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Confirm FULFILLMENT_GROUP_ID at test time so we use the same chat id.
        from order_bridge import FULFILLMENT_GROUP_ID
        self.chat_id = FULFILLMENT_GROUP_ID or 0

    async def _run(self, order_lookup_result, expect_place=False, fixture_order_id="AM3030"):
        from order_bridge import cmd_print_one
        with patch("order_bridge._resolve_order_id", return_value=order_lookup_result), \
             patch("order_bridge._lock_pages"), \
             patch("order_bridge._unlock_pages"), \
             patch("order_bridge._write_tracking_to_notion", return_value=["TRK001"]), \
             patch("order_bridge.get_filex_client") as mock_get_client, \
             patch("order_bridge._safe_send_message", new=AsyncMock()) as mock_msg, \
             patch("order_bridge._safe_send_document", new=AsyncMock()) as mock_doc:
            mock_client = MagicMock()
            mock_client.place_orders.return_value = {
                "data": "success",
                "trackingnos": [{"barcode": fixture_order_id, "tracking_no": "TRK001"}],
            }
            mock_client.get_label_pdf.return_value = b"%PDF-1.4 fake"
            mock_get_client.return_value = mock_client

            update = MagicMock()
            update.effective_chat.id = self.chat_id
            update.effective_user.id = 1
            context = MagicMock()
            await cmd_print_one(update, context, fixture_order_id)
            return mock_msg, mock_doc, mock_client

    async def test_not_found(self):
        mock_msg, mock_doc, mock_client = await self._run(None)
        # No placement, just one error message.
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("not found" in str(c).lower() for c in mock_msg.await_args_list))

    async def test_not_processed(self):
        mock_msg, mock_doc, mock_client = await self._run(_order(order_status="Confirmed"))
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("only \"Processed\"" in str(c) for c in mock_msg.await_args_list))

    async def test_already_labeled_refetch(self):
        mock_msg, mock_doc, mock_client = await self._run(
            _order(filex_status="Label Created", tracking_number="4866850001")
        )
        mock_client.place_orders.assert_not_called()
        mock_client.get_label_pdf.assert_called_once_with(["4866850001"])
        mock_doc.assert_awaited()

    async def test_fresh_placement(self):
        mock_msg, mock_doc, mock_client = await self._run(_order(), expect_place=True)
        mock_client.place_orders.assert_called_once()
        mock_client.get_label_pdf.assert_called_once_with(["TRK001"])
        mock_doc.assert_awaited()

    async def test_validation_failure_refuses(self):
        mock_msg, mock_doc, mock_client = await self._run(
            _order(full_address="Random no city here")
        )
        mock_client.place_orders.assert_not_called()
        mock_doc.assert_not_called()
        self.assertTrue(any("missing city" in str(c).lower() for c in mock_msg.await_args_list))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_print_one.py -v`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_print_one.py
git commit -m "Integration tests for cmd_print_one branches (a-d + validation-fail)"
```

---

### Task 10: Smoke-test in fulfillment Telegram group

**Files:** none (manual)

This task is an operator action, not code. The plan reaches its final terminal state once smoke-test results are observed.

- [ ] **Step 1: Run all unit + integration tests one last time**

Run: `cd c:/Users/PMLS/Desktop/Automation && python -m pytest tests/test_filex_payload_builder.py tests/test_print_dispatch.py tests/test_print_all_classifier.py tests/test_print_one.py -v`
Expected: all green.

- [ ] **Step 2: Push to production remote**

Run:
```bash
cd c:/Users/PMLS/Desktop/Automation && git push production filex-automation
```
Expected: changes pushed.

- [ ] **Step 3: Have user merge PR & SSH to VM and pull**

Tell the user to:
1. Open https://github.com/aliamaan7860111-ship-it/bot-spam/compare/main...filex-automation
2. Click "Create pull request" → merge
3. SSH to the VM and run `git pull && sudo systemctl restart order-bridge`

- [ ] **Step 4: Smoke test in fulfillment group**

Have user run, in the fulfillment Telegram group:
- `/print` (no args) → expect usage hint
- `/print all` (with at least one Processed + valid order) → expect: validation skip msg (if any), already-labeled msg (if any), PDF
- `/print AM<existing-order>` for a Processed + Filex Submitted=☐ order → expect: 1-page PDF + tracking caption
- `/print AM<existing-order>` for a Processed + already-labeled order → expect: 1-page PDF + "already has a Filex label" caption
- `/print AM<existing-order>` for a Confirmed order → expect: refusal message
- `/print ZZ9999` → expect: "not found in CRM"

- [ ] **Step 5: Mark plan done**

Tell user: "Smoke test complete. /print rework live in production."

---

## Self-Review

**Spec coverage check:**
- ✅ Eligibility = Status only → Task 3 (`query_filex_processed`) + Task 5 wires it
- ✅ FILEX STATUS as source of truth → Task 5 classifier
- ✅ Three-message structure (validation / already-labeled / PDF) → Task 5
- ✅ `.txt` fallback for long messages → Task 5 (`_send_long_message`)
- ✅ Total parser regex + test matrix → Task 1
- ✅ Validation error category prefixes → Task 2
- ✅ `/print <ORDER_ID>` four branches → Task 6 (`cmd_print_one`) + Task 9 tests
- ✅ Order ID tolerance (WA127 ↔ WA 127) → Task 6 (`_resolve_order_id`) + Task 7 tests
- ✅ Dispatch wrapper for args parsing → Task 6 (`cmd_print`)
- ✅ Smoke test → Task 10
- ✅ Existing tests (zero-allowed-for-exchange) preserved → Task 1 (zero allowed, only negatives rejected)

**Placeholder scan:** No TBDs. Every step has full code or exact commands.

**Type consistency:** `_lock_pages`, `_unlock_pages`, `_write_tracking_to_notion`, `_resolve_order_id`, `cmd_print`, `cmd_print_all`, `cmd_print_one` are referenced consistently across Tasks 4-9. `parse_total` is the same name in Task 1 (definition) and Tasks 1, 8, 9 (uses). `_SKIP_CATEGORIES`, `_categorize_validation_error`, `_format_validation_skip_message`, `_format_already_labeled_message`, `_send_long_message` all defined in Task 5 and used only there.
