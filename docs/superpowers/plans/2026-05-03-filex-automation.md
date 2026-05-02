# Filex Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Filex courier automation that places orders via API on `/print all` Telegram command and receives real-time status webhooks into the Notion CRM, with a nightly reconciliation safety net.

**Architecture:** Pure modules (status mapper, city normalizer, API client) tested in isolation; orchestration glue extends the existing `order_bridge.py` Flask + Telegram bot on the same GCP VM systemd unit; nightly `filex_reconcile.py` script as systemd timer for catch-up polling.

**Tech Stack:** Python 3.14, `requests`, `pypdf`, `rapidfuzz`, Notion API v2025-09-03, python-telegram-bot ≥21.0, systemd timers, Flask (existing aiohttp-based health server in `order_bridge.py`).

**Spec:** [`docs/superpowers/specs/2026-05-03-filex-automation-design.md`](../specs/2026-05-03-filex-automation-design.md)

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `execution/filex_client.py` | Pure API wrapper. Auth caching, placebulk, status, label PDF, with empty-page detection. |
| `execution/filex_status_mapper.py` | Pure lookup. Filex status text → Notion `FILEX STATUS` value, including compound `In OPS` logic. |
| `execution/filex_city_normalizer.py` | Pure lookup + fuzzy. Address string → Filex-spec city name. |
| `execution/filex_reconcile.py` | Standalone CLI: nightly stuck-order alerts + status reconciliation poll. |
| `execution/filex-reconcile.service` | Systemd unit running the reconcile script. |
| `execution/filex-reconcile.timer` | Systemd timer firing nightly at 23:00. |
| `directives/filex_automation.md` | SOP for the fulfillment team. |
| `scripts/setup_notion_fields.py` | One-time idempotent script to create the new Notion properties. |
| `tests/test_filex_status_mapper.py` | Unit tests for status mapping. |
| `tests/test_filex_city_normalizer.py` | Unit tests for city normalization. |
| `tests/test_filex_client.py` | Integration tests against the test sandbox (`testapi/SH0052`). |

**Modified files:**

| Path | Change |
|---|---|
| `execution/notion_client.py` | Add property setters: `set_tracking`, `set_filex_status`, `mark_filex_submitted`, etc. Plus `query_filex_eligible()` and `query_filex_active()`. |
| `execution/order_bridge.py` | Add `/filex_webhook` route to existing health server; add `/print all` Telegram command handler. |
| `.env` | New keys: `FILEX_USERNAME`, `FILEX_PASSWORD`, `FILEX_ACCOUNT_NUMBER`, `FILEX_WEBHOOK_TOKEN`, `FILEX_TRACKING_BASE_URL`. |

**Deployment phases (matches spec rollout order):**
1. Tasks 1–8: pure modules + Notion setup (no production impact)
2. Tasks 9–10: webhook receiver deployed → share URL with Filex
3. Tasks 11–14: `/print all` command rolled out
4. Tasks 15–17: reconcile + stuck-alert + SOP docs

---

## Task 1: Add env vars and one-time Notion field setup script

**Files:**
- Modify: `.env`
- Create: `scripts/setup_notion_fields.py`

- [ ] **Step 1: Add the new env keys to `.env`**

Append these lines to `.env` (use the test/sandbox creds for now; swap to live during deploy):

```
# Filex API
FILEX_USERNAME=Gfs
FILEX_PASSWORD=0564898669
FILEX_ACCOUNT_NUMBER=SH4866
FILEX_API_BASE=https://filex-shipperapi.dispatchex.com
FILEX_WEBHOOK_TOKEN=replace-with-token-from-filex-dev
FILEX_TRACKING_BASE_URL=https://www.filexexpress.ae/track?awb=
```

- [ ] **Step 2: Create the field setup script**

`scripts/setup_notion_fields.py`:

```python
"""
One-time script to add Filex-related properties to the Notion CRM database.
Idempotent: safe to re-run; only adds fields that don't already exist.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
from dotenv import load_dotenv
import requests

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_VERSION = "2025-09-03"

NEW_PROPERTIES = {
    "Tracking Number":   {"rich_text": {}},
    "Tracking Link":     {"url": {}},
    "FILEX STATUS":      {"select": {"options": [
        {"name": "Label Created", "color": "yellow"},
        {"name": "Handed Off", "color": "blue"},
        {"name": "Shipped", "color": "blue"},
        {"name": "Delivered", "color": "green"},
        {"name": "Cancelled", "color": "red"},
        {"name": "Receiver Cancelled With Money", "color": "orange"},
        {"name": "Received in CSA", "color": "orange"},
        {"name": "Return to Origin", "color": "red"},
        {"name": "LC", "color": "default"},
        {"name": "SFT", "color": "default"},
        {"name": "MNA", "color": "default"},
        {"name": "FD", "color": "default"},
        {"name": "LC-OPS", "color": "default"},
        {"name": "SFT-OPS", "color": "default"},
        {"name": "MNA-OPS", "color": "default"},
        {"name": "FD-OPS", "color": "default"},
        {"name": "In OPS", "color": "default"},
    ]}},
    "FILEX NOTES":       {"rich_text": {}},
    "Filex Submitted":   {"checkbox": {}},
    "Dispatched At":     {"date": {}},
    "Last Update":       {"date": {}},
}

def main():
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    # Get current schema
    r = requests.get(f"https://api.notion.com/v1/databases/{DATABASE_ID}", headers=headers)
    r.raise_for_status()
    existing = set(r.json().get("properties", {}).keys())
    print(f"Existing properties: {len(existing)}")

    # Add only new ones
    additions = {name: spec for name, spec in NEW_PROPERTIES.items() if name not in existing}
    if not additions:
        print("All Filex fields already present. Nothing to do.")
        return

    print(f"Adding: {list(additions.keys())}")
    r = requests.patch(
        f"https://api.notion.com/v1/databases/{DATABASE_ID}",
        headers=headers,
        json={"properties": additions},
    )
    r.raise_for_status()
    print("Done.")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the script**

```bash
python scripts/setup_notion_fields.py
```

Expected output: `Adding: ['Tracking Number', 'Tracking Link', 'FILEX STATUS', ...]` then `Done.`

Open the Notion database in your browser and verify all 7 new properties appear.

- [ ] **Step 4: Commit**

```bash
git add scripts/setup_notion_fields.py .env.example
git commit -m "Add Filex env vars and Notion field setup script"
```

(Don't commit the actual `.env` — it's in `.gitignore`. If `.env.example` doesn't exist yet, create it as a template with placeholder values.)

---

## Task 2: Create `filex_status_mapper.py` with tests

**Files:**
- Create: `execution/filex_status_mapper.py`
- Create: `tests/test_filex_status_mapper.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_filex_status_mapper.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_status_mapper import map_status

class TestStatusMapper(unittest.TestCase):
    def test_delivered_passthrough(self):
        self.assertEqual(map_status("Delivered", None), "Delivered")

    def test_to_be_picked_up_becomes_label_created(self):
        self.assertEqual(map_status("To be Picked Up", None), "Label Created")

    def test_received_at_hubs_becomes_handed_off(self):
        self.assertEqual(map_status("Received at Hubs", None), "Handed Off")

    def test_assigned_to_driver_becomes_shipped(self):
        self.assertEqual(map_status("Assigned to Driver", None), "Shipped")

    def test_in_ops_after_mna(self):
        self.assertEqual(map_status("In OPS", "MNA"), "MNA-OPS")

    def test_in_ops_after_sft(self):
        self.assertEqual(map_status("In OPS", "SFT"), "SFT-OPS")

    def test_in_ops_no_prior_status_falls_back(self):
        self.assertEqual(map_status("In OPS", "Shipped"), "In OPS")

    def test_unknown_status_passthrough(self):
        self.assertEqual(map_status("Some New Status", None), "Some New Status")

    def test_whitespace_in_input_handled(self):
        self.assertEqual(map_status(" In OPS ", "MNA"), "MNA-OPS")

    def test_receiver_cancelled_with_money(self):
        self.assertEqual(
            map_status("Receiver Cancelled With Money", None),
            "Receiver Cancelled With Money",
        )

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python -m unittest tests.test_filex_status_mapper -v
```

Expected: `ModuleNotFoundError: No module named 'filex_status_mapper'`

- [ ] **Step 3: Write the implementation**

`execution/filex_status_mapper.py`:

```python
"""Map Filex webhook Status text to Notion FILEX STATUS value."""

DIRECT_MAP = {
    "Order Placed":                   "Label Created",
    "To be Picked Up":                "Label Created",
    "Received at Hubs":               "Handed Off",
    "Assigned to Driver":             "Shipped",
    "Delivered":                      "Delivered",
    "Cancelled":                      "Cancelled",
    "Receiver Cancelled With Money":  "Receiver Cancelled With Money",
    "Received in CSA":                "Received in CSA",
    "Return to Origin":               "Return to Origin",
    "Location Changed":               "LC",
    "Schedule for tomorrow":          "SFT",
    "Mobile Not Answered":            "MNA",
    "Future Delivery":                "FD",
}

IN_OPS_COMPOUND = {
    "MNA": "MNA-OPS",
    "SFT": "SFT-OPS",
    "LC":  "LC-OPS",
    "FD":  "FD-OPS",
}


def map_status(filex_status: str, current_notion_status: str | None) -> str:
    """
    Convert a Filex webhook status text into the Notion FILEX STATUS value.

    For 'In OPS': the result depends on the current Notion status (e.g.
    'MNA' + 'In OPS' → 'MNA-OPS'). For everything else, uses DIRECT_MAP
    or passes through unknown values.
    """
    s = filex_status.strip()
    if s == "In OPS":
        return IN_OPS_COMPOUND.get(current_notion_status, "In OPS")
    return DIRECT_MAP.get(s, s)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python -m unittest tests.test_filex_status_mapper -v
```

Expected: `OK` with all 10 tests passing.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_status_mapper.py tests/test_filex_status_mapper.py
git commit -m "Add Filex status mapper with unit tests"
```

---

## Task 3: Create `filex_city_normalizer.py` with tests

**Files:**
- Create: `execution/filex_city_normalizer.py`
- Create: `tests/test_filex_city_normalizer.py`

- [ ] **Step 1: Install rapidfuzz dependency**

```bash
pip install rapidfuzz
```

Add to `requirements.txt`:
```
rapidfuzz>=3.0
```

- [ ] **Step 2: Write the failing tests**

`tests/test_filex_city_normalizer.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_city_normalizer import normalize_city

class TestCityNormalizer(unittest.TestCase):
    # Direct alias hits
    def test_dubai_simple(self):
        self.assertEqual(normalize_city("Apt 916 Binghatti Views Silicon Oasis Dubai"), "Dubai")

    def test_abu_dhabi(self):
        self.assertEqual(normalize_city("Villa 21 Asharej Abu Dhabi"), "Abu Dhabi")

    def test_sharjah(self):
        self.assertEqual(normalize_city("Al-Qurain 2nd Street, Villa No. 10 Sharjah"), "Sharjah")

    def test_ajman(self):
        self.assertEqual(normalize_city("Imam Al-Shafii Street, Al Hamidiyah, Ajman"), "Ajman")

    def test_al_ain(self):
        self.assertEqual(normalize_city("Villa 44 Shabiyat Khalifa Al Ain"), "Al Ain")

    # Filex spelling normalization
    def test_fujairah_maps_to_filex_fujeriah(self):
        self.assertEqual(normalize_city("Mirbah Fujairah 458/5"), "Fujeriah")

    def test_umm_al_quwain_maps_to_filex_um_al_qwain(self):
        self.assertEqual(normalize_city("Some place Umm Al Quwain"), "Um Al Qwain")

    def test_ras_al_khaimah_hyphen(self):
        self.assertEqual(normalize_city("Al-Khararan Villa Ras al-Khaimah"), "Ras Al Khaimah")

    # Typo / fuzzy
    def test_typo_dubaai(self):
        self.assertEqual(normalize_city("Pantheon Elysee 1 jvc dubaai"), "Dubai")

    def test_typo_abudhabi_no_space(self):
        self.assertEqual(normalize_city("802 argana building navygate abudhabi"), "Abu Dhabi")

    # Tiebreaker: first city wins
    def test_two_cities_picks_first(self):
        # Customer wrote "Fujairah" first then "Abu Dhabi" by mistake; pick Fujairah
        self.assertEqual(normalize_city("Mirbah Fujairah Abu Dhabi"), "Fujeriah")

    # No match
    def test_no_city_returns_none(self):
        self.assertIsNone(normalize_city("Random building street villa"))

    def test_empty_address(self):
        self.assertIsNone(normalize_city(""))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
python -m unittest tests.test_filex_city_normalizer -v
```

Expected: `ModuleNotFoundError: No module named 'filex_city_normalizer'`

- [ ] **Step 4: Write the implementation**

`execution/filex_city_normalizer.py`:

```python
"""Extract a Filex-spec city name from a free-text UAE address."""

import re
from rapidfuzz import fuzz

# Canonical key = exact spelling Filex's API expects.
# Values = lowercase aliases customers commonly write.
ALIAS_TO_FILEX: dict[str, list[str]] = {
    "Dubai":          ["dubai", "dxb", "doboi", "duba", "dubaai", "dubia"],
    "Abu Dhabi":      ["abu dhabi", "abudhabi", "abu-dhabi", "auh", "abudabi", "abu dabi"],
    "Sharjah":        ["sharjah", "shj", "sharja"],
    "Ajman":          ["ajman", "ajm", "ajaman"],
    "Al Ain":         ["al ain", "alain", "al-ain", "aln"],
    "Fujeriah":       ["fujairah", "fujarah", "fujaira", "fuj", "fujeriah"],
    "Um Al Qwain":    ["umm al quwain", "um al qwain", "uaq", "umalquwain"],
    "Ras Al Khaimah": ["ras al khaimah", "ras al-khaimah", "rak", "raskh"],
}

# Build flat list of (alias, filex_name) pairs for ordered scanning.
_FLAT_ALIASES: list[tuple[str, str]] = [
    (alias, filex_name)
    for filex_name, aliases in ALIAS_TO_FILEX.items()
    for alias in aliases
]
# Sort by alias length descending so "abu dhabi" matches before "abu" prefix attempts.
_FLAT_ALIASES.sort(key=lambda x: -len(x[0]))

FUZZY_THRESHOLD = 85  # rapidfuzz ratio out of 100


def normalize_city(address: str) -> str | None:
    """
    Return the Filex-spec city name found in `address`, or None.

    Strategy:
      1. Direct word-boundary alias match. If multiple cities are present,
         the FIRST one encountered (by position in the address) wins.
      2. Fuzzy fallback per-token using rapidfuzz ratio >= 85 against
         all aliases.
    """
    if not address:
        return None
    text = address.lower()

    # Step 1: find all alias hits with their position
    hits: list[tuple[int, str]] = []
    for alias, filex_name in _FLAT_ALIASES:
        for m in re.finditer(rf"\b{re.escape(alias)}\b", text):
            hits.append((m.start(), filex_name))
    if hits:
        hits.sort(key=lambda x: x[0])  # first-position wins
        return hits[0][1]

    # Step 2: fuzzy match per token
    tokens = re.findall(r"[a-z]+", text)
    best_score = 0
    best_filex_name: str | None = None
    for token in tokens:
        if len(token) < 4:
            continue  # avoid false positives on tiny tokens
        for alias, filex_name in _FLAT_ALIASES:
            if len(alias) < 4:
                continue
            score = fuzz.ratio(token, alias)
            if score >= FUZZY_THRESHOLD and score > best_score:
                best_score = score
                best_filex_name = filex_name
    return best_filex_name
```

- [ ] **Step 5: Run tests, verify all pass**

```bash
python -m unittest tests.test_filex_city_normalizer -v
```

Expected: `OK` with all 13 tests passing.

- [ ] **Step 6: Commit**

```bash
git add execution/filex_city_normalizer.py tests/test_filex_city_normalizer.py requirements.txt
git commit -m "Add Filex city normalizer with fuzzy matching"
```

---

## Task 4: Create `filex_client.py` — auth + token caching

**Files:**
- Create: `execution/filex_client.py`
- Create: `tests/test_filex_client.py`

- [ ] **Step 1: Write the failing test for auth**

`tests/test_filex_client.py`:

```python
"""
Integration tests against Filex's test sandbox.
Uses testapi/SH0052 — Filex's public test account.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_client import FilexClient

# Test sandbox credentials (public, documented in Postman collection)
SANDBOX_USERNAME = "testapi"
SANDBOX_PASSWORD = "123456"
SANDBOX_ACCOUNT  = "SH0052"
SANDBOX_BASE     = "http://filex-shipperapi.dispatchex.info"

class TestAuth(unittest.TestCase):
    def test_auth_returns_token(self):
        client = FilexClient(
            username=SANDBOX_USERNAME,
            password=SANDBOX_PASSWORD,
            account=SANDBOX_ACCOUNT,
            api_base=SANDBOX_BASE,
        )
        token = client.get_token()
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 50)

    def test_token_is_cached(self):
        client = FilexClient(
            username=SANDBOX_USERNAME,
            password=SANDBOX_PASSWORD,
            account=SANDBOX_ACCOUNT,
            api_base=SANDBOX_BASE,
        )
        t1 = client.get_token()
        t2 = client.get_token()
        self.assertEqual(t1, t2)  # second call should hit cache, return same token

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python -m unittest tests.test_filex_client.TestAuth -v
```

Expected: `ModuleNotFoundError: No module named 'filex_client'`

- [ ] **Step 3: Write the auth implementation**

`execution/filex_client.py`:

```python
"""Filex courier API client. Pure HTTP wrapper around their endpoints."""

import time
import requests


class FilexClient:
    """
    Thin client for Filex's REST API.

    Token caching: bearer token expires in 24h; we refresh after 23h
    or on 401 response from any endpoint.
    """

    TOKEN_TTL_SECONDS = 23 * 60 * 60  # 23 hours

    def __init__(self, username: str, password: str, account: str, api_base: str):
        self.username = username
        self.password = password
        self.account = account
        self.api_base = api_base.rstrip("/")
        self._token: str | None = None
        self._token_obtained_at: float = 0.0

    def get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed."""
        now = time.time()
        if self._token and (now - self._token_obtained_at) < self.TOKEN_TTL_SECONDS:
            return self._token
        r = requests.post(
            f"{self.api_base}/GetAuthToken",
            data={
                "Username": self.username,
                "Password": self.password,
                "grant_type": "password",
                "AccountNumber": self.account,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        if "access_token" not in body:
            raise RuntimeError(f"Filex auth failed: {body}")
        self._token = body["access_token"]
        self._token_obtained_at = now
        return self._token

    def _auth_headers(self, content_type: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.get_token()}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _invalidate_token(self):
        self._token = None
        self._token_obtained_at = 0.0
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python -m unittest tests.test_filex_client.TestAuth -v
```

Expected: both auth tests pass.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_client.py tests/test_filex_client.py
git commit -m "Add FilexClient with auth + token caching"
```

---

## Task 5: Add `placebulk` method to `FilexClient`

**Files:**
- Modify: `execution/filex_client.py` (append `place_orders` method)
- Modify: `tests/test_filex_client.py` (add `TestPlaceOrders` class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filex_client.py`:

```python
class TestPlaceOrders(unittest.TestCase):
    def test_place_single_order(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        result = client.place_orders([{
            "RecipientName": "Test Customer",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-PLAN-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test Address",
            "MobileNumber2": "",
            "Remarks": "TEST - DO NOT DISPATCH",
            "NumberOfPieces": "1",
            "Desc1": "Test item",
        }])
        self.assertEqual(result["data"], "success")
        self.assertEqual(len(result["trackingnos"]), 1)
        self.assertEqual(result["trackingnos"][0]["barcode"], "TEST-PLAN-001")
        self.assertTrue(result["trackingnos"][0]["tracking_no"].isdigit())
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python -m unittest tests.test_filex_client.TestPlaceOrders -v
```

Expected: `AttributeError: 'FilexClient' object has no attribute 'place_orders'`

- [ ] **Step 3: Add the `place_orders` method to `FilexClient`**

Append to `execution/filex_client.py`:

```python
    def place_orders(self, orders: list[dict]) -> dict:
        """
        Submit a batch of orders to Filex.

        Args:
            orders: list of dicts matching placebulk schema (RecipientName,
                    TotalCOG, MobileNumber, ShipperRef, AddressCountry,
                    City, Area, Street, MobileNumber2, Remarks,
                    NumberOfPieces, Desc1).

        Returns:
            dict with keys 'data' ('success' on OK) and 'trackingnos'
            (list of {'tracking_no': str, 'barcode': str} per ShipperRef).

        Raises:
            requests.HTTPError on 4xx/5xx
            RuntimeError on response body without 'data' key
        """
        r = requests.post(
            f"{self.api_base}/api/order/placebulk",
            headers=self._auth_headers("application/json"),
            json={"list": orders},
            timeout=120,
        )
        if r.status_code == 401:
            self._invalidate_token()
            r = requests.post(
                f"{self.api_base}/api/order/placebulk",
                headers=self._auth_headers("application/json"),
                json={"list": orders},
                timeout=120,
            )
        r.raise_for_status()
        body = r.json()
        if "data" not in body:
            raise RuntimeError(f"Unexpected placebulk response: {body}")
        return body
```

- [ ] **Step 4: Run test, verify it passes**

```bash
python -m unittest tests.test_filex_client.TestPlaceOrders -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_client.py tests/test_filex_client.py
git commit -m "Add place_orders method to FilexClient"
```

---

## Task 6: Add `get_status` method to `FilexClient`

**Files:**
- Modify: `execution/filex_client.py` (append `get_status` method)
- Modify: `tests/test_filex_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filex_client.py`:

```python
class TestGetStatus(unittest.TestCase):
    def test_get_status_for_known_tracking(self):
        # First place a test order, then query its status
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        place_result = client.place_orders([{
            "RecipientName": "Status Test",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-STAT-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test Address",
            "MobileNumber2": "",
            "Remarks": "TEST",
            "NumberOfPieces": "1",
            "Desc1": "Test",
        }])
        tn = place_result["trackingnos"][0]["tracking_no"]

        statuses = client.get_status([tn])
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["tracking_No"], tn)
        self.assertEqual(statuses[0]["shipperRef"], "TEST-STAT-001")
        self.assertIn("trackingStatus", statuses[0])
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python -m unittest tests.test_filex_client.TestGetStatus -v
```

Expected: `AttributeError: 'FilexClient' object has no attribute 'get_status'`

- [ ] **Step 3: Add `get_status` method**

Append to `execution/filex_client.py`:

```python
    def get_status(self, tracking_numbers: list[str]) -> list[dict]:
        """
        Fetch the latest status for one or more tracking numbers.

        Args:
            tracking_numbers: list of Filex AWB strings.

        Returns:
            list of dicts: {'tracking_No', 'shipperRef', 'trackingStatus',
                            'trackingStatusID', 'eventTime'}
        """
        if not tracking_numbers:
            return []
        r = requests.post(
            f"{self.api_base}/api/order/ShipmentLastStatus",
            headers=self._auth_headers("application/json"),
            json={"trackingNos": ",".join(tracking_numbers)},
            timeout=60,
        )
        if r.status_code == 401:
            self._invalidate_token()
            r = requests.post(
                f"{self.api_base}/api/order/ShipmentLastStatus",
                headers=self._auth_headers("application/json"),
                json={"trackingNos": ",".join(tracking_numbers)},
                timeout=60,
            )
        r.raise_for_status()
        return r.json().get("data", [])
```

- [ ] **Step 4: Run test, verify it passes**

```bash
python -m unittest tests.test_filex_client.TestGetStatus -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_client.py tests/test_filex_client.py
git commit -m "Add get_status method to FilexClient"
```

---

## Task 7: Add `get_label_pdf` method with empty-page detection

**Files:**
- Modify: `execution/filex_client.py`
- Modify: `tests/test_filex_client.py`

- [ ] **Step 1: Install pypdf**

```bash
pip install pypdf
```

Add to `requirements.txt`:
```
pypdf>=4.0
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_filex_client.py`:

```python
class TestLabelPdf(unittest.TestCase):
    def test_get_label_returns_clean_pdf(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        place_result = client.place_orders([{
            "RecipientName": "Label Test",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-LABEL-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test",
            "MobileNumber2": "",
            "Remarks": "TEST",
            "NumberOfPieces": "1",
            "Desc1": "Test",
        }])
        tn = place_result["trackingnos"][0]["tracking_no"]

        pdf_bytes = client.get_label_pdf([tn])
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 50_000)  # real label > 50KB
```

- [ ] **Step 3: Run test, verify it fails**

```bash
python -m unittest tests.test_filex_client.TestLabelPdf -v
```

Expected: `AttributeError: 'FilexClient' object has no attribute 'get_label_pdf'`

- [ ] **Step 4: Add `get_label_pdf` method**

Append to `execution/filex_client.py`:

```python
import io
import re
import zlib
from pypdf import PdfReader, PdfWriter


class FilexClient:
    # ... (existing methods)

    def get_label_pdf(self, tracking_numbers: list[str]) -> bytes:
        """
        Fetch a combined airway-bill PDF for the given tracking numbers,
        with empty-page detection (Filex's batch endpoint occasionally
        emits a near-empty page that we strip).

        Returns:
            Cleaned PDF as bytes.
        """
        if not tracking_numbers:
            raise ValueError("tracking_numbers must not be empty")
        url = (
            f"{self.api_base}/api/order/GetAirWayBill"
            f"?TrackingNos={','.join(tracking_numbers)}"
        )
        r = requests.get(url, headers=self._auth_headers(), timeout=180)
        if r.status_code == 401:
            self._invalidate_token()
            r = requests.get(url, headers=self._auth_headers(), timeout=180)
        r.raise_for_status()
        return self._strip_empty_pages(r.content)

    @staticmethod
    def _strip_empty_pages(raw_pdf: bytes) -> bytes:
        """Detect and drop pages whose content stream decompresses to <1000 bytes."""
        content_refs = re.findall(rb"/Contents\s+(\d+)", raw_pdf)
        empty_indices: set[int] = set()
        for i, ref in enumerate(content_refs):
            pat = (rf"^{ref.decode()} 0 obj.*?stream\n(.*?)\nendstream").encode()
            m = re.search(pat, raw_pdf, re.DOTALL | re.MULTILINE)
            if not m:
                continue
            try:
                if len(zlib.decompress(m.group(1))) < 1000:
                    empty_indices.add(i)
            except zlib.error:
                empty_indices.add(i)

        if not empty_indices:
            return raw_pdf

        reader = PdfReader(io.BytesIO(raw_pdf))
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i not in empty_indices:
                writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
```

(NOTE: keep the existing imports at top of file. Move the `import io, re, zlib` and pypdf import to the top of the file alongside `import requests`.)

- [ ] **Step 5: Run test, verify it passes**

```bash
python -m unittest tests.test_filex_client.TestLabelPdf -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add execution/filex_client.py tests/test_filex_client.py requirements.txt
git commit -m "Add get_label_pdf with empty-page detection"
```

---

## Task 8: Extend `notion_client.py` with Filex property setters and queries

**Files:**
- Modify: `execution/notion_client.py`

- [ ] **Step 1: Add new setters at the end of the file**

Append to `execution/notion_client.py`:

```python
def set_tracking_info(page_id: str, tracking_number: str, tracking_link: str) -> bool:
    """Write Tracking Number + Tracking Link in one PATCH."""
    return _patch_page_properties(page_id, {
        "Tracking Number": {"rich_text": [{"text": {"content": tracking_number}}]},
        "Tracking Link":   {"url": tracking_link},
    })

def set_filex_status(page_id: str, status: str) -> bool:
    """Update FILEX STATUS select."""
    return _patch_page_properties(page_id, {
        "FILEX STATUS": {"select": {"name": status}},
    })

def set_filex_notes(page_id: str, notes: str) -> bool:
    """Update FILEX NOTES rich_text. Skip if notes is empty."""
    if not notes:
        return True
    return _patch_page_properties(page_id, {
        "FILEX NOTES": {"rich_text": [{"text": {"content": notes}}]},
    })

def set_dispatched_at(page_id: str, dt_iso: str) -> bool:
    """Set Dispatched At date to ISO timestamp string."""
    return _patch_page_properties(page_id, {
        "Dispatched At": {"date": {"start": dt_iso}},
    })

def set_last_update(page_id: str, dt_iso: str) -> bool:
    """Set Last Update date to ISO timestamp string."""
    return _patch_page_properties(page_id, {
        "Last Update": {"date": {"start": dt_iso}},
    })

def mark_filex_submitted(page_id: str, submitted: bool = True) -> bool:
    """Toggle the Filex Submitted checkbox."""
    return _patch_page_properties(page_id, {
        "Filex Submitted": {"checkbox": submitted},
    })

def find_order_by_shipper_ref(shipper_ref: str) -> dict | None:
    """
    Look up a Notion order by its ORDER ID (which equals Filex's ShipperRef).
    Returns parsed order dict or None.
    """
    return find_order_by_id(shipper_ref)


def query_filex_eligible() -> list[dict]:
    """
    Orders ready to be submitted to Filex via /print all:
      ORDER STATUS == "Processed"
      AND SOURCING NOTIFIED == true
      AND FULFILLMENT NOTIFIED == true
      AND Filex Submitted == false
    """
    filter_ = {"and": [
        {"property": "ORDER STATUS",          "select":   {"equals": "Processed"}},
        {"property": "SOURCING NOTIFIED",     "checkbox": {"equals": True}},
        {"property": "FULFILLMENT NOTIFIED",  "checkbox": {"equals": True}},
        {"property": "Filex Submitted",       "checkbox": {"equals": False}},
    ]}
    return _query_data_source(filter_)


def query_filex_active(within_days: int = 14) -> list[dict]:
    """
    Orders dispatched within the last N days that haven't reached terminal:
      Filex Submitted == true
      AND FILEX STATUS != "Return to Origin"
      AND Dispatched At >= now - N days
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    filter_ = {"and": [
        {"property": "Filex Submitted", "checkbox": {"equals": True}},
        {"property": "FILEX STATUS",    "select":   {"does_not_equal": "Return to Origin"}},
        {"property": "Dispatched At",   "date":     {"on_or_after": cutoff}},
    ]}
    return _query_data_source(filter_)


def query_filex_stuck(hours: int = 24) -> list[dict]:
    """
    Orders stuck at 'Label Created' for more than N hours.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    filter_ = {"and": [
        {"property": "Filex Submitted", "checkbox": {"equals": True}},
        {"property": "FILEX STATUS",    "select":   {"equals": "Label Created"}},
        {"property": "Dispatched At",   "date":     {"before": cutoff}},
    ]}
    return _query_data_source(filter_)
```

(Note: `_query_data_source` and `_patch_page_properties` are existing internal helpers. If those exact names don't exist, find the equivalents in the current `notion_client.py` and adapt — they're whatever wraps `POST /data_sources/{id}/query` and `PATCH /pages/{id}`.)

- [ ] **Step 2: Manual smoke test**

Create `scripts/smoke_test_notion_setters.py`:

```python
"""Smoke test the new Filex setters against a single test page in Notion."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import notion_client as nc

# Use a known test order page ID from your CRM (replace TEST_PAGE_ID)
TEST_PAGE_ID = "REPLACE_WITH_REAL_PAGE_ID"

print("set_tracking_info:", nc.set_tracking_info(TEST_PAGE_ID, "9999999999", "https://example.com/track?awb=9999999999"))
print("set_filex_status:", nc.set_filex_status(TEST_PAGE_ID, "Label Created"))
print("set_filex_notes:", nc.set_filex_notes(TEST_PAGE_ID, "Smoke test note"))
print("mark_filex_submitted:", nc.mark_filex_submitted(TEST_PAGE_ID, True))
from datetime import datetime, timezone
print("set_dispatched_at:", nc.set_dispatched_at(TEST_PAGE_ID, datetime.now(timezone.utc).isoformat()))
print("set_last_update:", nc.set_last_update(TEST_PAGE_ID, datetime.now(timezone.utc).isoformat()))

# Query test
print("\nquery_filex_eligible:", len(nc.query_filex_eligible()), "orders")
print("query_filex_active:", len(nc.query_filex_active()), "orders")
print("query_filex_stuck:", len(nc.query_filex_stuck()), "orders")

# Cleanup
nc.mark_filex_submitted(TEST_PAGE_ID, False)
```

Replace `REPLACE_WITH_REAL_PAGE_ID` with a real test page from your CRM, then:

```bash
python scripts/smoke_test_notion_setters.py
```

Expected: each line prints `True` and the queries return reasonable counts. Check the page in Notion UI to confirm fields were set correctly.

- [ ] **Step 3: Commit**

```bash
git add execution/notion_client.py scripts/smoke_test_notion_setters.py
git commit -m "Extend notion_client with Filex property setters and queries"
```

---

## Task 9: Add `/filex_webhook` route to `order_bridge.py`

**Files:**
- Modify: `execution/order_bridge.py`

- [ ] **Step 1: Add the route alongside `/whatchimp_webhook`**

In `execution/order_bridge.py`, locate the existing webhook handler (search for `whatchimp_webhook`). Add a sibling route handler. Wire it into the same aiohttp/Flask app/router.

Add at the top of the file (with other imports):

```python
import os
import json
from datetime import datetime, timezone
import filex_status_mapper
import notion_client as nc

FILEX_WEBHOOK_TOKEN = os.getenv("FILEX_WEBHOOK_TOKEN")
```

Add the handler function:

```python
async def filex_webhook_handler(request):
    """
    POST /filex_webhook
    Receives Filex shipment status updates and pushes them to Notion.
    """
    # 1. Parse body
    try:
        payload = await request.json()
    except Exception as e:
        return web.json_response(
            {"code": 400, "isUpdeted": False, "message": f"Bad JSON: {e}"},
            status=400,
        )

    # 2. Verify token
    if payload.get("Token") != FILEX_WEBHOOK_TOKEN:
        log.warning(
            "filex_webhook: token mismatch from %s",
            request.headers.get("X-Forwarded-For", request.remote),
        )
        return web.json_response(
            {"code": 401, "isUpdeted": False, "message": "Unauthorized"},
            status=401,
        )

    shipper_ref = (payload.get("ShipperRef") or "").strip()
    status_text = (payload.get("Status") or "").strip()
    notes = (payload.get("Notes") or "").strip()
    order_date = (payload.get("OrderDate") or "").strip()

    # 3. Look up Notion order
    order = nc.find_order_by_shipper_ref(shipper_ref)
    if not order:
        log.warning("filex_webhook: unknown ShipperRef %s", shipper_ref)
        # Still 200 to avoid Filex retry loops
        return web.json_response(
            {"code": 200, "isUpdeted": False, "message": f"Unknown ShipperRef {shipper_ref}"},
        )

    # 4. Stale-event guard
    incoming_dt = _parse_filex_dt(order_date)
    stored_last_update = order.get("last_update")  # ISO string or None
    if incoming_dt and stored_last_update:
        try:
            stored_dt = datetime.fromisoformat(stored_last_update.replace("Z", "+00:00"))
            if incoming_dt < stored_dt:
                return web.json_response(
                    {"code": 200, "isUpdeted": True, "message": "Stale event ignored"},
                )
        except (ValueError, AttributeError):
            pass  # malformed stored date, proceed with update

    # 5. Map status
    current_filex_status = order.get("filex_status")
    new_status = filex_status_mapper.map_status(status_text, current_filex_status)

    # 6. Update Notion
    page_id = order["id"]
    nc.set_filex_status(page_id, new_status)
    nc.set_filex_notes(page_id, notes)
    if incoming_dt:
        nc.set_last_update(page_id, incoming_dt.isoformat())

    return web.json_response(
        {"code": 200, "isUpdeted": True, "message": "Updated successfully"},
    )


def _parse_filex_dt(dt_str: str) -> datetime | None:
    """Parse Filex's '2026-04-15T00:00:00' format. Returns None on failure."""
    if not dt_str:
        return None
    try:
        # Filex omits timezone; assume UTC
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
```

Then register the route. Find the line where `/whatchimp_webhook` is registered (likely something like `app.router.add_post('/whatchimp_webhook', whatchimp_handler)`) and add immediately after:

```python
app.router.add_post('/filex_webhook', filex_webhook_handler)
```

- [ ] **Step 2: Update `notion_client.parse_order` to expose `filex_status` and `last_update`**

In `execution/notion_client.py`, locate `parse_order` (the function that converts a Notion page dict into a flat dict). Add fields:

```python
# Inside parse_order, after existing fields:
out["filex_status"] = _get_select(props, "FILEX STATUS")
out["last_update"] = _get_date(props, "Last Update")
out["tracking_number"] = _get_rich_text(props, "Tracking Number")
out["filex_submitted"] = _get_checkbox(props, "Filex Submitted")
```

- [ ] **Step 3: Restart the order_bridge service locally for a quick test**

```bash
# In one terminal, run the service:
python execution/order_bridge.py
```

- [ ] **Step 4: Manual curl test against running service**

In another terminal:

```bash
# 1. Test bad token
curl -X POST http://localhost:8080/filex_webhook \
  -H "Content-Type: application/json" \
  -d '{"Token":"wrong","Status":"Delivered","ShipperRef":"AM2985","OrderDate":"2026-05-04T08:00:00"}'
```

Expected: `{"code":401,"isUpdeted":false,"message":"Unauthorized"}`

```bash
# 2. Test unknown ShipperRef (use real token from .env)
curl -X POST http://localhost:8080/filex_webhook \
  -H "Content-Type: application/json" \
  -d '{"Token":"REAL_TOKEN","Status":"Delivered","ShipperRef":"NONEXISTENT-99999","OrderDate":"2026-05-04T08:00:00"}'
```

Expected: 200 with message `"Unknown ShipperRef NONEXISTENT-99999"`. Check service logs for the warning.

```bash
# 3. Test real update on a real test order in CRM
curl -X POST http://localhost:8080/filex_webhook \
  -H "Content-Type: application/json" \
  -d '{"Token":"REAL_TOKEN","Status":"Assigned to Driver","TrackingNo":"X","Track_id":"X","ShipperRef":"REAL_TEST_ORDER_ID","Notes":"webhook test","OrderDate":"2026-05-04T08:00:00"}'
```

Expected: 200 with `"Updated successfully"`. Open the order in Notion UI, verify FILEX STATUS=`Shipped`, FILEX NOTES=`webhook test`, Last Update timestamp set.

- [ ] **Step 5: Commit**

```bash
git add execution/order_bridge.py execution/notion_client.py
git commit -m "Add /filex_webhook route to order_bridge"
```

---

## Task 10: Deploy webhook receiver and share URL with Filex

**Files:** none

- [ ] **Step 1: Push to production repo**

```bash
git push production main
```

(Per the existing CLAUDE.md, `production` remote is `bot-spam`. Check `git remote -v` if unsure.)

- [ ] **Step 2: SSH to GCP VM and pull + restart**

```bash
ssh user@34.30.125.177
cd /root/automation  # or wherever the repo lives — check existing systemd unit
git pull
sudo systemctl restart order-bridge
sudo systemctl status order-bridge
```

Verify status shows `active (running)` with no recent errors.

- [ ] **Step 3: Hit the production endpoint with a final smoke test**

```bash
# From your local machine
curl -X POST http://34.30.125.177:8080/filex_webhook \
  -H "Content-Type: application/json" \
  -d '{"Token":"wrong","Status":"x","ShipperRef":"x","OrderDate":"2026-05-04T08:00:00"}'
```

Expected: 401 response. Confirms the route is live in production.

- [ ] **Step 4: Confirm with Filex's developer whether HTTP works or HTTPS is required**

Send the developer:

> "Webhook URL ready: `http://34.30.125.177:8080/filex_webhook`. Please confirm if this works or if you need HTTPS — happy to set up Cloudflare Tunnel if needed."

If HTTPS required: pause here, set up Cloudflare Tunnel pointing at the same VM port 8080 with a public hostname, then share that URL instead.

- [ ] **Step 5: Have Filex register the URL under SH4866**

Once registered, status updates start flowing for **all 81 existing orders**. Monitor the service logs and Notion CRM for the next few hours to confirm webhooks land correctly.

```bash
# On GCP VM
sudo journalctl -u order-bridge -f | grep filex_webhook
```

- [ ] **Step 6: Commit deployment notes**

If any config changes were needed (Cloudflare Tunnel, env updates), commit them now.

---

## Task 11: Add piece-counting helper

**Files:**
- Modify: `execution/filex_client.py` (add module-level helper)
- Modify: `tests/test_filex_client.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filex_client.py`:

```python
class TestParsePieces(unittest.TestCase):
    def test_single_item(self):
        from filex_client import parse_pieces
        self.assertEqual(parse_pieces("Louis Vuitton Bag | Nano Speedy Monogram  1"), 1)

    def test_multiple_items_qty_one(self):
        from filex_client import parse_pieces
        text = "Hermès Bag | Birkin Pink  1\nBvlgari Jewelry | Set  1"
        self.assertEqual(parse_pieces(text), 2)

    def test_quantity_greater_than_one(self):
        from filex_client import parse_pieces
        self.assertEqual(parse_pieces("Amouage Perfume | Purpose 50 100Ml  4"), 4)

    def test_seven_watches(self):
        from filex_client import parse_pieces
        text = "\n".join([
            "Richard Mille Watch | Rafael Nadal    | 1",
            "AP Watch | Royal Oak    | 1",
            "Patek Watch | Nautilus    | 1",
            "Patek Watch | Cubitus    | 1",
            "RM Watch | RM 11-03 Ti    | 1",
            "RM Watch | RM 67-02    | 1",
            "RM Watch | RM 67-02 Ogier    | 1",
        ])
        self.assertEqual(parse_pieces(text), 7)

    def test_empty_input_defaults_to_one(self):
        from filex_client import parse_pieces
        self.assertEqual(parse_pieces(""), 1)

    def test_no_quantity_defaults_to_one(self):
        from filex_client import parse_pieces
        self.assertEqual(parse_pieces("Mystery item without quantity"), 1)
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python -m unittest tests.test_filex_client.TestParsePieces -v
```

Expected: `ImportError: cannot import name 'parse_pieces' from 'filex_client'`

- [ ] **Step 3: Add the helper**

At the module level (top of `execution/filex_client.py`, after imports):

```python
def parse_pieces(item_qty_field: str) -> int:
    """
    Parse the Notion 'ITEM | QTY' field into a NumberOfPieces total.

    Format: each item is on its own line; the trailing integer at end
    of each line is the quantity for that item. Sum across all lines.
    Defaults to 1 if parsing yields 0 or the field is empty.
    """
    if not item_qty_field or not item_qty_field.strip():
        return 1
    total = 0
    for line in item_qty_field.splitlines():
        m = re.search(r"(\d+)\s*$", line.strip())
        if m:
            total += int(m.group(1))
    return total if total > 0 else 1
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python -m unittest tests.test_filex_client.TestParsePieces -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_client.py tests/test_filex_client.py
git commit -m "Add parse_pieces helper for ITEM | QTY field"
```

---

## Task 12: Add address validation + payload builder helper

**Files:**
- Create: `execution/filex_payload_builder.py`
- Create: `tests/test_filex_payload_builder.py`

- [ ] **Step 1: Write the failing test**

`tests/test_filex_payload_builder.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_payload_builder import build_payload, ValidationError

class TestBuildPayload(unittest.TestCase):
    def _full_order(self):
        return {
            "id": "page-id-123",
            "order_id": "AM2985",
            "customer_name": "Mariska Hulley",
            "phone": "0547737735",
            "full_address": "Apt 916 Binghatti Views Silicon Oasis Dubai",
            "total": 295.00,
            "item_qty": "YSL Bag | Icare Maxi  1",
            "internal_note": "",
        }

    def test_valid_order_builds_payload(self):
        order = self._full_order()
        result = build_payload(order)
        self.assertEqual(result["RecipientName"], "Mariska Hulley")
        self.assertEqual(result["TotalCOG"], "295.00")
        self.assertEqual(result["MobileNumber"], "0547737735")
        self.assertEqual(result["ShipperRef"], "AM2985")
        self.assertEqual(result["City"], "Dubai")
        self.assertEqual(result["AddressCountry"], "United Arab Emirates")
        self.assertEqual(result["NumberOfPieces"], "1")

    def test_phone_normalized_from_plus971(self):
        order = self._full_order()
        order["phone"] = "+971547737735"
        result = build_payload(order)
        self.assertEqual(result["MobileNumber"], "0547737735")

    def test_total_zero_allowed_for_exchange(self):
        order = self._full_order()
        order["total"] = 0
        result = build_payload(order)
        self.assertEqual(result["TotalCOG"], "0.00")

    def test_missing_phone_raises(self):
        order = self._full_order()
        order["phone"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("phone", str(ctx.exception).lower())

    def test_missing_name_raises(self):
        order = self._full_order()
        order["customer_name"] = ""
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("name", str(ctx.exception).lower())

    def test_no_city_raises(self):
        order = self._full_order()
        order["full_address"] = "Random building street villa"
        with self.assertRaises(ValidationError) as ctx:
            build_payload(order)
        self.assertIn("city", str(ctx.exception).lower())

    def test_multiline_item_summed_into_pieces(self):
        order = self._full_order()
        order["item_qty"] = "Hermes Birkin  1\nBvlgari Set  1"
        result = build_payload(order)
        self.assertEqual(result["NumberOfPieces"], "2")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python -m unittest tests.test_filex_payload_builder -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`execution/filex_payload_builder.py`:

```python
"""Build Filex placebulk payloads from Notion order dicts, with validation."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from filex_city_normalizer import normalize_city
from filex_client import parse_pieces
from whatchimp_client import clean_phone_number


class ValidationError(ValueError):
    """Raised when an order is missing required fields for Filex."""


def build_payload(order: dict) -> dict:
    """
    Convert a parsed Notion order into a Filex placebulk item dict.

    Args:
        order: dict from notion_client.parse_order with keys
               'order_id', 'customer_name', 'phone', 'full_address',
               'total', 'item_qty', 'internal_note'.

    Returns:
        dict matching Filex placebulk schema.

    Raises:
        ValidationError if any required field is missing/invalid.
    """
    name = (order.get("customer_name") or "").strip()
    if not name:
        raise ValidationError("missing customer name")

    phone_raw = (order.get("phone") or "").strip()
    if not phone_raw:
        raise ValidationError("missing phone")
    phone = clean_phone_number(phone_raw)
    # clean_phone_number returns 971XXXXXXXXX; convert to UAE 0XXXXXXXXX
    if phone.startswith("971"):
        phone = "0" + phone[3:]
    if not phone or len(phone) < 10:
        raise ValidationError(f"invalid phone after normalization: {phone}")

    address = (order.get("full_address") or "").strip()
    if not address:
        raise ValidationError("missing address")

    city = normalize_city(address)
    if not city:
        raise ValidationError(f"could not extract city from address: {address[:50]}")

    total = order.get("total")
    if total is None or not isinstance(total, (int, float)):
        raise ValidationError(f"invalid total: {total!r}")
    total_str = f"{float(total):.2f}"

    pieces = parse_pieces(order.get("item_qty") or "")
    desc = (order.get("item_qty") or "").replace("\n", " + ").strip() or "Item"

    return {
        "RecipientName": name,
        "TotalCOG": total_str,
        "MobileNumber": phone,
        "ShipperRef": order["order_id"],
        "AddressCountry": "United Arab Emirates",
        "City": city,
        "Area": "",
        "Street": address[:200],  # Filex limits street length
        "MobileNumber2": "",
        "Remarks": (order.get("internal_note") or "")[:200],
        "NumberOfPieces": str(pieces),
        "Desc1": desc[:200],
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python -m unittest tests.test_filex_payload_builder -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/filex_payload_builder.py tests/test_filex_payload_builder.py
git commit -m "Add Filex payload builder with validation"
```

---

## Task 13: Add `/print all` Telegram command handler

**Files:**
- Modify: `execution/order_bridge.py`

- [ ] **Step 1: Add the handler function**

In `execution/order_bridge.py`, near the existing command handlers (search for `#ready`), add:

```python
import os
from datetime import datetime, timezone
from filex_client import FilexClient
from filex_payload_builder import build_payload, ValidationError
import notion_client as nc

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")
FILEX_TRACKING_BASE  = os.getenv("FILEX_TRACKING_BASE_URL", "https://www.filexexpress.ae/track?awb=")

# Single shared client (token cached across calls)
_filex_client: FilexClient | None = None

def get_filex_client() -> FilexClient:
    global _filex_client
    if _filex_client is None:
        _filex_client = FilexClient(
            FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE,
        )
    return _filex_client


async def cmd_print_all(update, context):
    """
    /print all
    Place all eligible Notion orders in Filex, write back tracking info,
    send combined PDF to the fulfillment group.
    """
    chat_id = update.effective_chat.id
    bot = context.bot

    # 1. Query eligible orders
    eligible = nc.query_filex_eligible()
    if not eligible:
        await bot.send_message(chat_id, "No orders eligible for /print all right now.")
        return

    # 2. Build payloads with validation
    payloads = []
    page_id_by_ref: dict[str, str] = {}
    for order in eligible:
        try:
            payload = build_payload(order)
        except ValidationError as e:
            await bot.send_message(
                chat_id,
                f"⚠️ Skipped {order.get('order_id', '?')}: {e}",
            )
            continue
        payloads.append(payload)
        page_id_by_ref[payload["ShipperRef"]] = order["id"]

    if not payloads:
        await bot.send_message(chat_id, "No valid orders after validation.")
        return

    # 3. Lock orders BEFORE API call
    for ref, page_id in page_id_by_ref.items():
        nc.mark_filex_submitted(page_id, True)

    # 4. Place orders via Filex
    client = get_filex_client()
    try:
        result = client.place_orders(payloads)
    except Exception as e:
        # Revert locks on failure
        for ref, page_id in page_id_by_ref.items():
            nc.mark_filex_submitted(page_id, False)
        await bot.send_message(chat_id, f"⚠️ Filex submission failed: {e}")
        log.error("filex placebulk failed", exc_info=True)
        return

    # 5. Update Notion with tracking info
    now_iso = datetime.now(timezone.utc).isoformat()
    tracking_pairs = result.get("trackingnos", [])
    for entry in tracking_pairs:
        ref = entry["barcode"]
        tn  = entry["tracking_no"]
        page_id = page_id_by_ref.get(ref)
        if not page_id:
            log.warning("Returned ref %s not in our locked set", ref)
            continue
        nc.set_tracking_info(page_id, tn, FILEX_TRACKING_BASE + tn)
        nc.set_filex_status(page_id, "Label Created")
        nc.set_dispatched_at(page_id, now_iso)
        nc.set_last_update(page_id, now_iso)

    # 6. Fetch combined PDF
    tracking_numbers = [e["tracking_no"] for e in tracking_pairs]
    try:
        pdf_bytes = client.get_label_pdf(tracking_numbers)
    except Exception as e:
        await bot.send_message(
            chat_id,
            f"✅ Placed {len(tracking_numbers)} orders, but label PDF fetch failed: {e}\nRetry manually.",
        )
        return

    # 7. Send PDF as document
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"filex_labels_{today}_{len(tracking_numbers)}.pdf"
    await bot.send_document(
        chat_id,
        document=pdf_bytes,
        filename=filename,
    )
```

- [ ] **Step 2: Wire up the command in the Telegram bot's command dispatcher**

Find where existing commands like `#ready` are wired up. Add:

```python
# python-telegram-bot v21+ uses CommandHandler from telegram.ext
from telegram.ext import CommandHandler, filters

# Inside Application.builder build:
application.add_handler(CommandHandler(
    "print",
    cmd_print_all,
    filters=filters.Chat(chat_id=int(os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID")))
))
```

(`/print all` is treated as `/print` with arg "all". For v1 we accept any arg or none — `/print` alone also triggers. Adjust if you want strict matching.)

- [ ] **Step 3: Local test**

Run the service locally and trigger `/print` from a test Telegram group whose ID matches `TELEGRAM_FULFILLMENT_GROUP_ID`. Expected:

- If no eligible orders: "No orders eligible for /print all right now."
- If eligible orders: validation messages for any skipped, then a PDF document arrives with N labels. Notion fields populated for each.

- [ ] **Step 4: Deploy and commit**

```bash
git add execution/order_bridge.py
git commit -m "Add /print all command handler"
git push production main
# SSH to VM, pull, restart service
```

---

## Task 14: Smoke test `/print all` end-to-end with a real test order

**Files:** none

- [ ] **Step 1: Create a test order in Notion**

Manually add an order in the CRM with:
- ORDER ID: `TEST-PRINT-001`
- CUSTOMER NAME: `Test Customer`
- PHONE: `0500000000`
- FULL ADDRESS: `Test Address Dubai`
- TOTAL: `1.00`
- ITEM | QTY: `Test Item  1`
- ORDER STATUS: `Processed`
- SOURCING NOTIFIED: ✓
- FULFILLMENT NOTIFIED: ✓
- Filex Submitted: unchecked

- [ ] **Step 2: Trigger `/print` in the fulfillment Telegram group**

Send `/print all` (or `/print`) in the group.

- [ ] **Step 3: Verify outputs**

Within ~10 seconds, expect:
- A PDF document with 1 label (named like `filex_labels_2026-05-04_1.pdf`)
- Notion test order updated: Tracking Number filled, Tracking Link filled, FILEX STATUS=`Label Created`, Filex Submitted=✓, Dispatched At=now, Last Update=now

- [ ] **Step 4: Verify the order appears in Filex**

Use the test sandbox or production dashboard to confirm the order was created. Status should be `To be Picked Up`.

- [ ] **Step 5: Cleanup**

Manually mark the order as cancelled in Filex's app (if possible), or just let it lapse. Update the Notion test order: ORDER STATUS to whatever you like, or delete the row.

---

## Task 15: Create `filex_reconcile.py` for nightly catch-up + stuck-order alerts

**Files:**
- Create: `execution/filex_reconcile.py`

- [ ] **Step 1: Write the script**

`execution/filex_reconcile.py`:

```python
"""
Nightly Filex reconciliation.

Two jobs in one run:
  1. Stuck-order alert: orders sitting at 'Label Created' for >24h get
     reported to the fulfillment Telegram group.
  2. Status reconciliation: for every active (non-RTO, dispatched within
     14 days) order, query Filex's ShipmentLastStatus and update Notion
     if the status has changed. Catches webhooks missed during downtime.

Run via systemd timer (filex-reconcile.timer) at 23:00 daily.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
import requests

load_dotenv()

import notion_client as nc
import filex_status_mapper
from filex_client import FilexClient

FILEX_USERNAME       = os.getenv("FILEX_USERNAME")
FILEX_PASSWORD       = os.getenv("FILEX_PASSWORD")
FILEX_ACCOUNT_NUMBER = os.getenv("FILEX_ACCOUNT_NUMBER")
FILEX_API_BASE       = os.getenv("FILEX_API_BASE", "https://filex-shipperapi.dispatchex.com")

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
FULFILLMENT_GROUP_ID = os.getenv("TELEGRAM_FULFILLMENT_GROUP_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("filex_reconcile")


def send_telegram(text: str) -> None:
    """Plain HTTP POST to Telegram Bot API. Avoids importing the full bot."""
    if not (TELEGRAM_BOT_TOKEN and FULFILLMENT_GROUP_ID):
        log.warning("Telegram not configured; skipping alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": FULFILLMENT_GROUP_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=20)


def alert_stuck_orders():
    """Find orders stuck at 'Label Created' for 24h+ and alert."""
    stuck = nc.query_filex_stuck(hours=24)
    if not stuck:
        log.info("No stuck orders.")
        return
    lines = [f"⚠️ *{len(stuck)} order(s) stuck at 'Label Created' for 24h+:*"]
    for order in stuck:
        lines.append(
            f"- `{order['order_id']}` (`{order.get('tracking_number') or '?'}`) "
            f"— {order.get('customer_name') or '?'}"
        )
    lines.append("\nInvestigate with Filex / fulfillment.")
    send_telegram("\n".join(lines))
    log.info("Sent stuck-order alert for %d orders.", len(stuck))


def reconcile_active_orders():
    """Poll Filex for status drift and update Notion when found."""
    active = nc.query_filex_active(within_days=14)
    if not active:
        log.info("No active orders to reconcile.")
        return

    client = FilexClient(FILEX_USERNAME, FILEX_PASSWORD, FILEX_ACCOUNT_NUMBER, FILEX_API_BASE)

    # Build tracking_no -> page lookup
    by_tn = {o["tracking_number"]: o for o in active if o.get("tracking_number")}
    if not by_tn:
        log.info("No tracking numbers in active orders.")
        return

    # Batch in groups of 50
    tracking_numbers = list(by_tn.keys())
    for i in range(0, len(tracking_numbers), 50):
        chunk = tracking_numbers[i : i + 50]
        try:
            results = client.get_status(chunk)
        except Exception as e:
            log.error("get_status batch failed: %s", e)
            continue
        for r in results:
            tn = r["tracking_No"]
            order = by_tn.get(tn)
            if not order:
                continue
            mapped = filex_status_mapper.map_status(
                r.get("trackingStatus", ""), order.get("filex_status"),
            )
            if mapped != order.get("filex_status"):
                log.info(
                    "Reconcile drift: %s %s -> %s",
                    order["order_id"], order.get("filex_status"), mapped,
                )
                nc.set_filex_status(order["id"], mapped)
                event_iso = r.get("eventTime")
                if event_iso:
                    if "T" in event_iso and "+" not in event_iso and "Z" not in event_iso:
                        event_iso = event_iso + "+00:00"
                    nc.set_last_update(order["id"], event_iso)


def main():
    log.info("=== Filex reconcile run starting ===")
    try:
        alert_stuck_orders()
    except Exception:
        log.exception("alert_stuck_orders crashed")
    try:
        reconcile_active_orders()
    except Exception:
        log.exception("reconcile_active_orders crashed")
    log.info("=== Filex reconcile run done ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual run to verify**

```bash
python execution/filex_reconcile.py
```

Expected: log lines showing "No stuck orders" (if nothing's stuck) and "No active orders to reconcile" (or count of active + drift updates). No crashes.

- [ ] **Step 3: Commit**

```bash
git add execution/filex_reconcile.py
git commit -m "Add Filex reconciliation script"
```

---

## Task 16: Create systemd unit + timer for the reconcile job

**Files:**
- Create: `execution/filex-reconcile.service`
- Create: `execution/filex-reconcile.timer`

- [ ] **Step 1: Write the service unit**

`execution/filex-reconcile.service`:

```ini
[Unit]
Description=Filex nightly reconciliation
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/automation
EnvironmentFile=/root/automation/.env
ExecStart=/usr/bin/python3 /root/automation/execution/filex_reconcile.py
StandardOutput=append:/root/automation/.tmp/filex_reconcile.log
StandardError=append:/root/automation/.tmp/filex_reconcile.log
```

- [ ] **Step 2: Write the timer unit**

`execution/filex-reconcile.timer`:

```ini
[Unit]
Description=Run Filex reconciliation nightly at 23:00

[Timer]
OnCalendar=*-*-* 23:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Install on production VM**

```bash
ssh user@34.30.125.177
sudo cp /root/automation/execution/filex-reconcile.service /etc/systemd/system/
sudo cp /root/automation/execution/filex-reconcile.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable filex-reconcile.timer
sudo systemctl start filex-reconcile.timer
sudo systemctl list-timers | grep filex
```

Expected: timer listed with next firing time around 23:00 today/tomorrow.

- [ ] **Step 4: Trigger one manual run**

```bash
sudo systemctl start filex-reconcile.service
sudo journalctl -u filex-reconcile.service -n 50
```

Expected: log lines from the script's run, no crashes.

- [ ] **Step 5: Commit**

```bash
git add execution/filex-reconcile.service execution/filex-reconcile.timer
git commit -m "Add systemd timer for Filex reconciliation"
```

---

## Task 17: Write the SOP directive

**Files:**
- Create: `directives/filex_automation.md`

- [ ] **Step 1: Write the SOP**

`directives/filex_automation.md`:

```markdown
# Filex Automation — SOP

## Purpose

This bot replaces manual Filex order entry and dashboard checking with one Telegram command (`/print all`) plus passive webhook updates to the Notion CRM. All shipments dispatched via Filex are tracked end-to-end inside Notion.

## Trigger

Type `/print all` (or just `/print`) in the fulfillment Telegram group.

## What it does

1. Pulls all Notion orders where:
   - `ORDER STATUS` = `Processed`
   - `SOURCING NOTIFIED` ✓
   - `FULFILLMENT NOTIFIED` ✓
   - `Filex Submitted` ✗
2. Validates each order (name, phone, address, city extractable, total).
3. Submits valid orders to Filex's `placebulk` API.
4. Writes back to Notion: `Tracking Number`, `Tracking Link`, sets `FILEX STATUS` = `Label Created`, ticks `Filex Submitted`.
5. Sends a single combined PDF of all generated labels back to the fulfillment group.

## Edge cases

- **Order skipped**: bot replies in Telegram with reason (e.g. missing phone, city not extractable). Fix the field in Notion and re-run.
- **API down**: bot rolls back the Filex Submitted lock on all orders in that batch and posts the error. Retry shortly.
- **Stuck orders** (>24h at Label Created): bot alerts the group nightly at 23:00.

## Webhook flow (passive)

Filex pushes status updates to `http://34.30.125.177:8080/filex_webhook`. The bot maps each status into the `FILEX STATUS` select in Notion and updates `Last Update`. No team action required.

## Status meanings in `FILEX STATUS`

| Status | Meaning |
|---|---|
| Label Created | Filex has the order; awaiting pickup. |
| Handed Off | Picked up by Filex driver, at hub. |
| Shipped | Out with delivery driver. |
| Delivered | Reached the customer (NOT terminal — can revert). |
| Cancelled | Refused at door / customer cancelled. |
| Receiver Cancelled With Money | Cancelled BUT money was collected — reconcile cash. |
| Received in CSA | At Filex's customer service area, awaiting return. |
| Return to Origin | Coming back to us. **Terminal.** |
| LC / SFT / MNA / FD | Sub-reasons (Location Changed, Schedule For Tomorrow, Mobile Not Answered, Future Delivery). |
| LC-OPS / SFT-OPS / MNA-OPS / FD-OPS | Sub-reason + back at OPS warehouse. |

## Notion field reference

| Field | Description |
|---|---|
| `Tracking Number` | Filex AWB (10-digit). |
| `Tracking Link` | Direct URL to Filex tracking page. |
| `FILEX STATUS` | Current state in pipeline. |
| `FILEX NOTES` | Reason text if Filex sent any. |
| `Filex Submitted` | Lock — bot won't re-submit. |
| `Dispatched At` | When `/print all` placed it. |
| `Last Update` | When the most recent webhook event arrived. |

## Reconciliation

Runs nightly at 23:00 via systemd timer. For every active (non-RTO) order dispatched in the last 14 days, the bot polls Filex for current status and updates Notion if it drifted from the last known state. This catches any webhooks missed during service restarts.

## Adding new statuses

If Filex sends a status text we haven't seen, the bot stores it raw in `FILEX STATUS` (Notion auto-creates the option) and logs to `.tmp/filex_unknown_statuses.log`. Add the new mapping to `execution/filex_status_mapper.py` when convenient.

## Failure recovery

| Symptom | Action |
|---|---|
| Stuck orders alerted | Check Filex dashboard, ask fulfillment if package was physically picked up. |
| Webhook returns 401 unexpectedly | `FILEX_WEBHOOK_TOKEN` may have changed — confirm with Filex dev. |
| `/print all` says "no eligible orders" | Verify orders have all 3 conditions met (Processed status, both checkboxes ticked, Filex Submitted false). |
| Notion drift | Run `python execution/filex_reconcile.py` manually; it'll resync. |
```

- [ ] **Step 2: Commit**

```bash
git add directives/filex_automation.md
git commit -m "Add Filex automation SOP directive"
```

---

## Self-Review Notes

- **Spec coverage**: each spec section maps to tasks (Notion schema → Task 1; webhook → Tasks 9–10; `/print all` → Tasks 11–14; reconciliation → Tasks 15–16; mapper/normalizer → Tasks 2–3; SOP → Task 17).
- **No placeholders**: every code step has actual code; no "TBD" or "implement later".
- **Type consistency**: function signatures (e.g. `map_status(filex_status, current_notion_status)`, `normalize_city(address)`, `parse_pieces(item_qty_field)`, `build_payload(order)`) used identically in their tests and call sites.
- **Open items from spec** (HTTPS requirement, exact tracking URL, real webhook token) handled in Task 1 (env placeholders), Task 10 (Filex dev confirmation step), Task 13 (config-driven base URL).

## Verification end-to-end

After all tasks complete, the full system passes when:

1. `python -m unittest discover tests -v` — all unit + integration tests pass.
2. `/print all` in Telegram with eligible orders → PDF returns + Notion fields populated.
3. Real Filex webhook arrives → Notion `FILEX STATUS` updates.
4. Manual `python execution/filex_reconcile.py` run after disabling webhooks for a few hours catches the drift.
5. Stuck-order test: leave one test order at Label Created for 24h+, run `filex_reconcile.py`, alert lands in Telegram.
