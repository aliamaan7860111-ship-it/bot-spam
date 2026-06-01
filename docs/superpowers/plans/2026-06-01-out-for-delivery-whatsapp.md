# Out-for-Delivery WhatsApp Notification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send each customer a one-time WhatsApp "out for delivery" message when their order's `ORDER STATUS` in the Notion CRM becomes `🚚 SHIPPED`.

**Architecture:** One idempotent sender funnels two triggers — an at-source call from `filex_reconcile.py` (instant, when it auto-promotes status) and a standalone poller in a new `out_for_delivery.py` (safety net for manual edits / failed sends). A new Notion checkbox `Out For Delivery Sent` is the single dedup gate. Brand → number/template/display is resolved from the ORDER ID prefix via a new `OFD_CONFIG`.

**Tech Stack:** Python 3.11+, `httpx` (Notion), `requests` (WhatChimp), `python-dotenv`, stdlib `unittest`. WhatChimp Developer API. Notion API `2025-09-03` (data-source query).

**Spec:** `docs/superpowers/specs/2026-06-01-out-for-delivery-whatsapp-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `execution/whatchimp_client.py` | WhatChimp API primitives. Adds `OFD_CONFIG`, prefix resolver, payload builder, and the OFD send primitive. | Modify |
| `execution/notion_client.py` | Notion primitives. Adds the `Out For Delivery Sent` field, `STATUS_SHIPPED`, parse field, mark function, and the shipped-unnotified query. | Modify |
| `execution/out_for_delivery.py` | Orchestration: the idempotent `send_out_for_delivery(order)`, `poll_once`, poll loop `main()`, and a `--backfill` one-shot. | Create |
| `execution/filex_reconcile.py` | At-source hook: call `send_out_for_delivery(order)` when it promotes status to SHIPPED. | Modify |
| `execution/bridge/add_notion_properties.py` *(pattern only)* | Reference for the one-shot property-add script. | Reference |
| `execution/add_ofd_property.py` | One-shot: add the `Out For Delivery Sent` checkbox to the CRM DB. | Create |
| `execution/tests/test_out_for_delivery.py` | Unit tests for the pure helpers + the send gate. | Create |

**Routing data (locked):**

| Prefix | Brand | `phone_number_id` | `brand_display` (`#!brand!#`) | `ofd_template_id` | notes |
|---|---|---|---|---|---|
| `PT` | Elara | 1031340813395459 | Elara UAE | 377952 | |
| `Di` | Dialo | 1002123586328400 | Dialo UAE | 377954 | |
| `LU` | Lune | 1138942462625909 | Lune Collection | 377955 | |
| `PV` | Pelvini | 1138942462625909 | Pelvini | 377955 | shares Lune number+template |
| `VX` | Virex | 1073890042476443 | Virex UAE | 377956 | |
| `O` | Orlento | 1073890042476443 | Orlento | 377956 | shares Virex number+template |
| `AM` | Amara | 1045332455333591 | Amara's Room | 377951 | `no_vars` — legacy template, no body variables |

---

## Task 1: OFD routing + send primitive in `whatchimp_client.py`

**Files:**
- Modify: `execution/whatchimp_client.py` (add after the `SENDER_PHONE_TO_PREFIX = …` line, ~line 67; send helpers near `send_template_message`)
- Test: `execution/tests/test_out_for_delivery.py`

- [ ] **Step 1: Write failing tests for the prefix resolver, config lookup, and payload builder**

Create `execution/tests/test_out_for_delivery.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import whatchimp_client as wc


class TestResolveOfdPrefix(unittest.TestCase):
    def test_two_char_prefixes(self):
        self.assertEqual(wc.resolve_ofd_prefix("PT1793"), "PT")
        self.assertEqual(wc.resolve_ofd_prefix("Di500"), "Di")
        self.assertEqual(wc.resolve_ofd_prefix("LU10"), "LU")
        self.assertEqual(wc.resolve_ofd_prefix("PV7"), "PV")
        self.assertEqual(wc.resolve_ofd_prefix("VX9"), "VX")
        self.assertEqual(wc.resolve_ofd_prefix("AM55"), "AM")

    def test_one_char_orlento_not_shadowed(self):
        # 'O' must resolve to Orlento, and must NOT swallow 2-char prefixes.
        self.assertEqual(wc.resolve_ofd_prefix("O123"), "O")
        self.assertEqual(wc.resolve_ofd_prefix("VX1"), "VX")

    def test_unknown_and_empty(self):
        self.assertIsNone(wc.resolve_ofd_prefix("ZZ9"))
        self.assertIsNone(wc.resolve_ofd_prefix(""))
        self.assertIsNone(wc.resolve_ofd_prefix(None))


class TestGetOfdConfig(unittest.TestCase):
    def test_pelvini_shares_lune(self):
        pv = wc.get_ofd_config("PV1")
        lu = wc.get_ofd_config("LU1")
        self.assertEqual(pv["phone_number_id"], lu["phone_number_id"])
        self.assertEqual(pv["ofd_template_id"], lu["ofd_template_id"])
        self.assertEqual(pv["brand_display"], "Pelvini")

    def test_orlento_shares_virex(self):
        o = wc.get_ofd_config("O5")
        vx = wc.get_ofd_config("VX5")
        self.assertEqual(o["phone_number_id"], vx["phone_number_id"])
        self.assertEqual(o["ofd_template_id"], vx["ofd_template_id"])
        self.assertEqual(o["brand_display"], "Orlento")

    def test_amara_no_vars(self):
        am = wc.get_ofd_config("AM9")
        self.assertTrue(am.get("no_vars"))
        self.assertEqual(am["ofd_template_id"], "377951")

    def test_unknown_is_none(self):
        self.assertIsNone(wc.get_ofd_config("ZZ1"))


class TestBuildOfdPayload(unittest.TestCase):
    def test_standard_brand_includes_variables(self):
        cfg = wc.get_ofd_config("PT1")
        p = wc.build_ofd_payload(cfg, "PT1793", "971500000000", "TOK")
        self.assertEqual(p["apiToken"], "TOK")
        self.assertEqual(p["template_id"], "377952")
        self.assertEqual(p["phone_number_id"], "1031340813395459")
        self.assertEqual(p["phone_number"], "971500000000")
        self.assertEqual(p["templateVariable-brand-2"], "Elara UAE")
        self.assertEqual(p["templateVariable-id-3"], "PT1793")

    def test_amara_omits_variables(self):
        cfg = wc.get_ofd_config("AM9")
        p = wc.build_ofd_payload(cfg, "AM9", "971500000000", "TOK")
        self.assertNotIn("templateVariable-brand-2", p)
        self.assertNotIn("templateVariable-id-3", p)
        self.assertEqual(p["template_id"], "377951")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: FAIL — `AttributeError: module 'whatchimp_client' has no attribute 'resolve_ofd_prefix'`

- [ ] **Step 3: Implement `OFD_CONFIG`, resolver, config lookup, and payload builder**

In `execution/whatchimp_client.py`, immediately after the `SENDER_PHONE_TO_PREFIX = …` line (~line 67), add:

```python
# ---------------------------------------------------------------------------
# Out-for-Delivery routing (separate from BRAND_CONFIG on purpose: Pelvini and
# Orlento share another brand's WhatsApp number and have no confirm-button /
# sender-phone of their own, so they must not enter the BRAND_CONFIG comprehensions).
# Key = ORDER ID prefix. brand_display fills the #!brand!# template variable.
# ---------------------------------------------------------------------------
OFD_CONFIG = {
    "PT": {"phone_number_id": "1031340813395459", "ofd_template_id": "377952", "brand_display": "Elara UAE"},
    "Di": {"phone_number_id": "1002123586328400", "ofd_template_id": "377954", "brand_display": "Dialo UAE"},
    "LU": {"phone_number_id": "1138942462625909", "ofd_template_id": "377955", "brand_display": "Lune Collection"},
    "PV": {"phone_number_id": "1138942462625909", "ofd_template_id": "377955", "brand_display": "Pelvini"},
    "VX": {"phone_number_id": "1073890042476443", "ofd_template_id": "377956", "brand_display": "Virex UAE"},
    "O":  {"phone_number_id": "1073890042476443", "ofd_template_id": "377956", "brand_display": "Orlento"},
    "AM": {"phone_number_id": "1045332455333591", "ofd_template_id": "377951", "brand_display": "Amara's Room", "no_vars": True},
}

# Match longest prefix first so the 1-char "O" (Orlento) never shadows a 2-char prefix.
_OFD_PREFIXES = sorted(OFD_CONFIG.keys(), key=len, reverse=True)


def resolve_ofd_prefix(order_id: str) -> str | None:
    """Return the known OFD prefix an order_id starts with, or None. Case-sensitive."""
    oid = (order_id or "").strip()
    for prefix in _OFD_PREFIXES:
        if oid.startswith(prefix):
            return prefix
    return None


def get_ofd_config(order_id: str) -> dict | None:
    """Resolve the OFD routing config from an order_id, or None for unknown brands."""
    prefix = resolve_ofd_prefix(order_id)
    return OFD_CONFIG[prefix] if prefix is not None else None


def build_ofd_payload(cfg: dict, order_id: str, cleaned_phone: str, api_token: str) -> dict:
    """Build the /send/template POST body for an out-for-delivery message.
    Amara's legacy template (no_vars) carries no body variables; every other
    brand fills #!brand!# (templateVariable-brand-2) and #!id!# (templateVariable-id-3)."""
    payload = {
        "apiToken":        api_token,
        "phone_number_id": cfg["phone_number_id"],
        "template_id":     cfg["ofd_template_id"],
        "phone_number":    cleaned_phone,
    }
    if not cfg.get("no_vars"):
        payload["templateVariable-brand-2"] = cfg["brand_display"]
        payload["templateVariable-id-3"]    = order_id
    return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: PASS (all tests in the three classes above).

- [ ] **Step 5: Add the OFD send primitive (no separate test — it performs network I/O; logic is covered by `build_ofd_payload`)**

In `execution/whatchimp_client.py`, directly below `build_ofd_payload`, add:

```python
def send_out_for_delivery_template(phone_number: str, order_id: str, cfg: dict) -> bool:
    """Send one out-for-delivery WhatsApp template via WhatChimp.

    `cfg` must come from get_ofd_config(order_id). Pre-syncs the subscriber on the
    brand's own phone_number_id (matches the confirmation flow), then posts the
    template. Returns True only on WhatChimp status == "1".
    """
    if not WHATCHIMP_API_TOKEN:
        log.error("Missing WHATCHIMP_API_TOKEN in .env")
        return False

    phone_number_id = cfg["phone_number_id"]
    template_id     = cfg["ofd_template_id"]
    display_brand   = cfg["brand_display"]

    cleaned_phone = clean_phone_number(phone_number)
    if not cleaned_phone.startswith("971") or len(cleaned_phone) != 12:
        log.error(
            f"Phone '{phone_number}' failed UAE normalization "
            f"(got '{cleaned_phone}') — skipping OFD {order_id}"
        )
        return False

    # Pre-sync custom fields so #!id!# / #!brand!# resolve; harmless for Amara's no-var template.
    create_or_update_subscriber(phone_number, "", order_id, display_brand, phone_number_id)

    payload = build_ofd_payload(cfg, order_id, cleaned_phone, WHATCHIMP_API_TOKEN)
    url = f"{API_BASE}/send/template"
    try:
        log.info(
            f"🚚 OFD template {template_id} → {cleaned_phone} via {phone_number_id} "
            f"({display_brand}, order {order_id})"
        )
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        if str(data.get("status")) == "1":
            log.info(f"✅ OFD delivered: {order_id}")
            return True
        log.error(f"❌ OFD rejected ({display_brand}): {data.get('message', data)}")
        return False
    except Exception as e:
        log.error(f"OFD request failed: {e}")
        return False
```

- [ ] **Step 6: Commit**

```bash
git add execution/whatchimp_client.py execution/tests/test_out_for_delivery.py
git commit -m "feat(whatchimp): OFD routing config + send primitive"
```

---

## Task 2: Notion field, status constant, parse, mark, and query in `notion_client.py`

**Files:**
- Modify: `execution/notion_client.py` (field constants ~lines 98-108; `parse_order` ~line 261; new functions near the other `set_*`/query helpers)
- Test: `execution/tests/test_out_for_delivery.py`

- [ ] **Step 1: Write a failing test for the `STATUS_SHIPPED` constant (drift guard) and the new parse field**

Append to `execution/tests/test_out_for_delivery.py` (above the `if __name__` block):

```python
import notion_client as nc
import filex_status_mapper


class TestStatusShippedConstant(unittest.TestCase):
    def test_matches_filex_mapper(self):
        # The at-source hook compares the promoted status to nc.STATUS_SHIPPED;
        # it must stay identical to what the mapper actually writes.
        self.assertEqual(
            nc.STATUS_SHIPPED,
            filex_status_mapper.ORDER_STATUS_FROM_FILEX["Shipped"],
        )


class TestParseOutForDeliverySent(unittest.TestCase):
    def test_absent_checkbox_is_false(self):
        page = {"id": "p1", "properties": {}}
        order = nc.parse_order(page)
        self.assertFalse(order["out_for_delivery_sent"])

    def test_checked_is_true(self):
        page = {"id": "p1", "properties": {
            nc.FIELD_OUT_FOR_DELIVERY_SENT: {"type": "checkbox", "checkbox": True},
        }}
        order = nc.parse_order(page)
        self.assertTrue(order["out_for_delivery_sent"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: FAIL — `AttributeError: module 'notion_client' has no attribute 'STATUS_SHIPPED'`

- [ ] **Step 3: Add the field constant and status constant**

In `execution/notion_client.py`, directly after the `FIELD_LAST_UPDATE = "Last Update"` line (~line 108), add:

```python
FIELD_OUT_FOR_DELIVERY_SENT = "Out For Delivery Sent"

# Main ORDER STATUS value an order reaches when shipped. MUST stay identical to
# filex_status_mapper.ORDER_STATUS_FROM_FILEX["Shipped"] (guarded by a unit test).
STATUS_SHIPPED = "\U0001F69A SHIPPED"  # 🚚 SHIPPED
```

- [ ] **Step 4: Add the parse field**

In `execution/notion_client.py`, inside `parse_order`'s returned dict, after the `"filex_submitted": _get_checkbox(props, FIELD_FILEX_SUBMITTED),` line, add:

```python
        "out_for_delivery_sent": _get_checkbox(props, FIELD_OUT_FOR_DELIVERY_SENT),
```

- [ ] **Step 5: Run to verify the constant + parse tests pass**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: PASS for `TestStatusShippedConstant` and `TestParseOutForDeliverySent`.

- [ ] **Step 6: Add the mark function and the shipped-unnotified query (no separate test — network I/O)**

In `execution/notion_client.py`, near the other `set_*` helpers (e.g. after `mark_filex_submitted`), add:

```python
def mark_out_for_delivery_sent(page_id: str, sent: bool = True) -> bool:
    """Set the 'Out For Delivery Sent' dedup checkbox."""
    return _update_page(page_id, {
        FIELD_OUT_FOR_DELIVERY_SENT: {"checkbox": sent},
    })
```

And near the other `query_*` helpers (e.g. after `query_filex_active_since`), add:

```python
def query_shipped_unnotified(grace_minutes: int = 2) -> list[dict]:
    """Orders ready for an out-for-delivery message:
        ORDER STATUS == 🚚 SHIPPED
        AND Out For Delivery Sent == false
        AND (Last Update is older than grace_minutes OR is empty)

    The grace window keeps the poller from racing the at-source send in
    filex_reconcile: a fresh auto-promotion sets Last Update to ~now, so the
    poller waits one grace window before treating the row as a missed send
    (manual edit, or a failed at-source attempt).
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=grace_minutes)).isoformat()
    payload = {
        "filter": {"and": [
            {"property": FIELD_ORDER_STATUS,            "select":   {"equals": STATUS_SHIPPED}},
            {"property": FIELD_OUT_FOR_DELIVERY_SENT,   "checkbox": {"equals": False}},
            {"or": [
                {"property": FIELD_LAST_UPDATE, "date": {"on_or_before": cutoff}},
                {"property": FIELD_LAST_UPDATE, "date": {"is_empty": True}},
            ]},
        ]},
    }
    return _run_query(payload)
```

- [ ] **Step 7: Commit**

```bash
git add execution/notion_client.py execution/tests/test_out_for_delivery.py
git commit -m "feat(notion): Out For Delivery Sent field, STATUS_SHIPPED, shipped-unnotified query"
```

---

## Task 3: Orchestration module `out_for_delivery.py`

**Files:**
- Create: `execution/out_for_delivery.py`
- Test: `execution/tests/test_out_for_delivery.py`

- [ ] **Step 1: Write failing tests for the `send_out_for_delivery` gate**

Append to `execution/tests/test_out_for_delivery.py` (above the `if __name__` block):

```python
from unittest import mock

import out_for_delivery as ofd


def _order(**over):
    base = {
        "page_id": "pg1", "order_id": "PT1793", "phone": "971500000000",
        "out_for_delivery_sent": False,
    }
    base.update(over)
    return base


class TestSendOutForDeliveryGate(unittest.TestCase):
    def test_skips_when_already_sent(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(out_for_delivery_sent=True))
        self.assertFalse(result)
        send.assert_not_called()

    def test_skips_unknown_brand(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(order_id="ZZ9"))
        self.assertFalse(result)
        send.assert_not_called()

    def test_skips_when_no_phone(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(phone=""))
        self.assertFalse(result)
        send.assert_not_called()

    def test_sends_and_marks_on_success(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template", return_value=True) as send, \
             mock.patch.object(ofd.nc, "mark_out_for_delivery_sent") as mark:
            result = ofd.send_out_for_delivery(_order())
        self.assertTrue(result)
        send.assert_called_once()
        mark.assert_called_once_with("pg1")

    def test_does_not_mark_on_send_failure(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template", return_value=False), \
             mock.patch.object(ofd.nc, "mark_out_for_delivery_sent") as mark:
            result = ofd.send_out_for_delivery(_order())
        self.assertFalse(result)
        mark.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'out_for_delivery'`

- [ ] **Step 3: Create `execution/out_for_delivery.py`**

```python
"""
out_for_delivery.py
===================
Send a one-time WhatsApp "out for delivery" notification when an order's
ORDER STATUS becomes 🚚 SHIPPED.

Two entry points share one idempotent sender:
  • send_out_for_delivery(order)  — called at-source by filex_reconcile and by the poller
  • main()                        — standalone poll loop (safety net) + --backfill one-shot

Dedup is the Notion checkbox 'Out For Delivery Sent', set only on confirmed send.
Brand routing (number / template / display name) comes from whatchimp_client.OFD_CONFIG.
"""
from __future__ import annotations

import argparse
import logging
import time

import notion_client as nc
import whatchimp_client as wc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("order_bridge.ofd")


def send_out_for_delivery(order: dict) -> bool:
    """Idempotently send the OFD template for one parsed order.

    Returns True only if a message was sent on this call. Skips (returns False)
    when already sent, brand unknown, template pending, or phone missing. On a
    confirmed send it sets the 'Out For Delivery Sent' checkbox.
    """
    order_id = order.get("order_id", "")

    if order.get("out_for_delivery_sent"):
        return False

    cfg = wc.get_ofd_config(order_id)
    if cfg is None:
        log.warning("OFD: unknown brand prefix for order %r — skipping", order_id)
        return False

    if not cfg.get("ofd_template_id"):
        # Future brand with no approved template yet: no-op, leave checkbox unset
        # so it sends automatically once the template_id is filled in OFD_CONFIG.
        log.info("OFD: template pending for %r — skipping", order_id)
        return False

    phone = order.get("phone", "")
    if not phone:
        log.warning("OFD: no phone on order %r — skipping", order_id)
        return False

    sent = wc.send_out_for_delivery_template(phone, order_id, cfg)
    if sent:
        nc.mark_out_for_delivery_sent(order["page_id"])
        log.info("OFD: sent + marked %r", order_id)
    return sent


def poll_once(grace_minutes: int = 2) -> int:
    """One poll pass: send to every shipped-but-unnotified order. Returns count sent."""
    orders = nc.query_shipped_unnotified(grace_minutes=grace_minutes)
    if not orders:
        return 0
    sent = 0
    for order in orders:
        try:
            if send_out_for_delivery(order):
                sent += 1
        except Exception as e:  # never let one bad row kill the loop
            log.error("OFD: error on %r: %s", order.get("order_id"), e)
    return sent


def backfill_mark_shipped() -> int:
    """One-shot: mark every currently-shipped order as already notified WITHOUT
    sending. Run once before first deploy so pre-existing shipped orders don't
    get blasted. Returns count marked."""
    orders = nc.query_shipped_unnotified(grace_minutes=0)
    marked = 0
    for order in orders:
        if nc.mark_out_for_delivery_sent(order["page_id"]):
            marked += 1
            log.info("OFD backfill: marked %r as already notified", order.get("order_id"))
    log.info("OFD backfill: %d orders marked.", marked)
    return marked


def main():
    parser = argparse.ArgumentParser(description="Out-for-delivery WhatsApp notifier.")
    parser.add_argument("--once", action="store_true", help="Run a single poll pass and exit.")
    parser.add_argument("--interval", type=int, default=30,
                        help="Poll interval seconds when looping (default: 30).")
    parser.add_argument("--grace-minutes", type=int, default=2,
                        help="Skip rows whose Last Update is newer than this (default: 2).")
    parser.add_argument("--backfill", action="store_true",
                        help="One-shot: mark all currently-shipped orders as notified, send nothing.")
    args = parser.parse_args()

    if args.backfill:
        backfill_mark_shipped()
        return

    if args.once:
        n = poll_once(grace_minutes=args.grace_minutes)
        log.info("OFD: single pass sent %d message(s).", n)
        return

    log.info("=== OFD notifier loop starting (interval=%ss, grace=%smin) ===",
             args.interval, args.grace_minutes)
    while True:
        try:
            poll_once(grace_minutes=args.grace_minutes)
        except Exception as e:
            log.error("OFD: poll pass failed: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify the gate tests pass**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: PASS — all classes including `TestSendOutForDeliveryGate`.

- [ ] **Step 5: Commit**

```bash
git add execution/out_for_delivery.py execution/tests/test_out_for_delivery.py
git commit -m "feat(ofd): out_for_delivery orchestrator — sender, poller, backfill"
```

---

## Task 4: At-source hook in `filex_reconcile.py`

**Files:**
- Modify: `execution/filex_reconcile.py` (top imports; inside `reconcile_active_orders`, right after the `nc.update_order_status(order["page_id"], promoted)` block)

- [ ] **Step 1: Add the import**

In `execution/filex_reconcile.py`, with the other top-level imports (alongside `import notion_client as nc`), add:

```python
import out_for_delivery as ofd
```

- [ ] **Step 2: Add the at-source send after the status promotion**

In `reconcile_active_orders`, locate this existing block:

```python
                if promoted:
                    nc.update_order_status(order["page_id"], promoted)
                    log.info(
                        "  ↳ ORDER STATUS promoted to %r for %s",
                        promoted, order["order_id"],
                    )
```

Immediately after it (still inside the `if promoted:` indentation level, after the `log.info`), add:

```python
                    if promoted == nc.STATUS_SHIPPED:
                        # Fire the out-for-delivery WhatsApp the instant we auto-promote
                        # to SHIPPED. `order` was queried while not-yet-shipped, so its
                        # out_for_delivery_sent is False; send_out_for_delivery's own guard
                        # + the success-set checkbox prevent any double-send vs. the poller.
                        try:
                            ofd.send_out_for_delivery(order)
                        except Exception as e:
                            log.error(
                                "OFD at-source send failed for %s: %s",
                                order["order_id"], e,
                            )
```

- [ ] **Step 3: Verify no circular-import or syntax breakage by importing the module**

Run: `python -c "import sys; sys.path.insert(0, 'execution'); import filex_reconcile; print('ok')"`
Expected: prints `ok` (no ImportError, no SyntaxError).

- [ ] **Step 4: Run the full test file to confirm nothing regressed**

Run: `python execution/tests/test_out_for_delivery.py`
Expected: PASS (all classes).

- [ ] **Step 5: Commit**

```bash
git add execution/filex_reconcile.py
git commit -m "feat(reconcile): fire out-for-delivery WhatsApp on SHIPPED promotion"
```

---

## Task 5: One-shot property-add script `add_ofd_property.py`

**Files:**
- Create: `execution/add_ofd_property.py` (mirrors `execution/bridge/add_notion_properties.py`)

- [ ] **Step 1: Create the script**

```python
"""
One-shot: add the 'Out For Delivery Sent' checkbox property to the main CRM
Notion database. Idempotent — re-running is a no-op patch if it already exists.
Run once before deploying the OFD notifier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_ID = os.environ["NOTION_DATABASE_ID"]
TOKEN = os.environ["NOTION_API_KEY"]


def main() -> int:
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    props_patch = {
        "Out For Delivery Sent": {"checkbox": {}},
    }
    r = requests.patch(
        f"https://api.notion.com/v1/databases/{DB_ID}",
        headers=headers,
        json={"properties": props_patch},
        timeout=20,
    )
    if r.status_code != 200:
        print(f"ERROR {r.status_code}: {r.text[:600]}", file=sys.stderr)
        return 1
    print("OK: 'Out For Delivery Sent' checkbox added/confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Note: the existing `add_notion_properties.py` uses Notion-Version `2022-06-28` for the database-edit endpoint (distinct from the `2025-09-03` data-source query version used by `notion_client.py`). Keep `2022-06-28` here — it is the version known to work for `PATCH /databases/{id}`.

- [ ] **Step 2: Commit**

```bash
git add execution/add_ofd_property.py
git commit -m "feat(ofd): one-shot script to add Out For Delivery Sent property"
```

---

## Task 6: Deploy runbook (ordering is critical)

**Files:**
- None in-repo beyond a new systemd unit on the GCP VM. This task is operational.

Execute **in this exact order** — doing the code deploy before the backfill would message every already-shipped customer.

- [ ] **Step 1: Push the merged branch**

```bash
git push
```

- [ ] **Step 2: SSH to the GCP VM and pull**

```bash
ssh <gcp-vm>
cd <repo-path-on-vm>
git pull
```

- [ ] **Step 3: Add the Notion property**

```bash
python execution/add_ofd_property.py
```
Expected: `OK: 'Out For Delivery Sent' checkbox added/confirmed.`

- [ ] **Step 4: Backfill — mark all currently-shipped orders as already notified (sends nothing)**

```bash
python execution/out_for_delivery.py --backfill
```
Expected: `OFD backfill: N orders marked.` This is what prevents the first-run blast.

- [ ] **Step 5: Smoke-test a single pass (should now send 0, since everything shipped is marked)**

```bash
python execution/out_for_delivery.py --once
```
Expected: `OFD: single pass sent 0 message(s).`

- [ ] **Step 6: Create + enable the systemd service for the poller**

Model it on an existing unit (e.g. `sudo cat /etc/systemd/system/grq-ac.service`) — copy it and change only the description and `ExecStart` to run:
`python execution/out_for_delivery.py --interval 30`
naming the unit `grq-ofd.service`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grq-ofd
sudo systemctl status grq-ofd --no-pager
journalctl -u grq-ofd -n 30 --no-pager
```
Expected: `active (running)`, logs show the loop starting.

- [ ] **Step 7: Restart the reconcile service so the at-source hook goes live**

```bash
sudo systemctl restart <filex-reconcile-service-or-timer>
```

- [ ] **Step 8: Live verification**

Move one test order to `🚚 SHIPPED` in Notion (or let Filex promote one), confirm the WhatsApp arrives once, and confirm `Out For Delivery Sent` is checked on that row. Verify no duplicate is sent on the next poll tick.

---

## Notes / deviations from spec

- **First-deploy protection** uses the `--backfill` one-shot (Task 3 / Task 6 Step 4) instead of the spec's per-send `ORDER_CUTOFF_DATE` check. The cutoff check is unreliable when an order's `created` field is empty, whereas pre-checking the dedup box on all current shipped rows is deterministic. This is strictly safer and is the only behavioral change from the spec.
- **OFD_CONFIG is separate from BRAND_CONFIG** (spec said "extend BRAND_CONFIG"). The module-load comprehensions `POSTBACK_TO_PREFIX` / `SENDER_PHONE_TO_PREFIX` require `confirm_button_qr` and `sender_phone` keys that Pelvini/Orlento lack, so a separate dict avoids a module-import crash and keeps confirmation vs. OFD routing decoupled.
- **Amara** stays on its no-variable template `377951` via the `no_vars` flag; drop the flag and update `ofd_template_id` once a variabled Amara template is approved.
```
