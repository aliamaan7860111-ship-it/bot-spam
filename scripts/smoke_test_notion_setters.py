"""Smoke check that new notion_client functions are wired up correctly.

Verifies that the Filex setters and queries added in Task 8 are importable
and callable, then exercises the read-only queries against the real CRM
to confirm wiring (no writes performed).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import notion_client as nc

# Verify all new callables are exposed
assert callable(nc.set_tracking_info)
assert callable(nc.set_filex_status)
assert callable(nc.set_filex_notes)
assert callable(nc.set_dispatched_at)
assert callable(nc.set_last_update)
assert callable(nc.mark_filex_submitted)
assert callable(nc.find_order_by_shipper_ref)
assert callable(nc.query_filex_processed)
assert callable(nc.query_filex_active)
assert callable(nc.query_filex_stuck)

# Run real read-only queries against the database
eligible = nc.query_filex_processed()
print(f"query_filex_processed: {len(eligible)} orders")

active = nc.query_filex_active()
print(f"query_filex_active: {len(active)} orders")

stuck = nc.query_filex_stuck()
print(f"query_filex_stuck: {len(stuck)} orders")

# Confirm parse_order exposes the new Filex fields without breaking existing keys
sample = eligible[0] if eligible else (active[0] if active else (stuck[0] if stuck else None))
if sample is not None:
    expected_new_keys = {"filex_status", "last_update", "tracking_number", "filex_submitted"}
    missing = expected_new_keys - set(sample.keys())
    assert not missing, f"parse_order missing new Filex keys: {missing}"
    # Spot-check a few existing keys still exist
    for k in ("page_id", "order_id", "order_status", "sourcing_notified"):
        assert k in sample, f"parse_order missing existing key: {k}"
    print("parse_order exposes new Filex fields and preserves existing ones.")
else:
    print("No orders returned; skipped parse_order key check.")

print("All new functions wired up.")
