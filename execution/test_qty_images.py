"""Framework-free tests for per-unit image replication in telegram_client.

Bug fixed here: an order with two of the SAME item (one Shopify line item, quantity 2)
used to send only ONE image to the fulfillment group. Fulfillment needs one photo per
physical unit, so a quantity-N line item must emit N images.

Run:  python execution/test_qty_images.py   (exits non-zero on any failure; no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import telegram_client as tg

WATCH = "https://cdn.shopify.com/s/files/1/0/watch.jpg"
WATCH_800 = "https://cdn.shopify.com/s/files/1/0/watch_800x.jpg"
RING = "https://cdn.shopify.com/s/files/1/0/ring.png"
RING_800 = "https://cdn.shopify.com/s/files/1/0/ring_800x.png"


def test_quantity_two_yields_two_images():
    edges = [{"node": {"title": "Gold Watch", "variantTitle": "Gold", "quantity": 2,
                       "image": {"url": WATCH}}}]
    imgs, items = tg._build_images_and_line_items(edges)
    assert len(imgs) == 2, f"expected 2 images for qty 2, got {len(imgs)}: {imgs}"
    assert imgs == [WATCH_800, WATCH_800], imgs
    assert len(items) == 1 and items[0]["quantity"] == 2, items


def test_mixed_quantities_sum_to_total_units():
    edges = [
        {"node": {"title": "Watch", "variantTitle": "", "quantity": 2, "image": {"url": WATCH}}},
        {"node": {"title": "Ring", "variantTitle": "M", "quantity": 1, "image": {"url": RING}}},
    ]
    imgs, items = tg._build_images_and_line_items(edges)
    assert len(imgs) == 3, f"expected 3 images (2+1), got {len(imgs)}: {imgs}"
    assert imgs.count(WATCH_800) == 2 and imgs.count(RING_800) == 1, imgs
    assert [it["quantity"] for it in items] == [2, 1], items


def test_single_quantity_unchanged():
    edges = [{"node": {"title": "Ring", "quantity": 1, "image": {"url": RING}}}]
    imgs, items = tg._build_images_and_line_items(edges)
    assert imgs == [RING_800], imgs


def test_item_without_image_records_line_item_but_no_photo():
    edges = [{"node": {"title": "NoImg", "quantity": 3}}]
    imgs, items = tg._build_images_and_line_items(edges)
    assert imgs == [], imgs
    assert len(items) == 1 and items[0]["quantity"] == 3, items


def test_bad_or_missing_quantity_defaults_to_one():
    edges = [
        {"node": {"title": "A", "quantity": None, "image": {"url": WATCH}}},
        {"node": {"title": "B", "image": {"url": RING}}},
    ]
    imgs, _ = tg._build_images_and_line_items(edges)
    assert imgs == [WATCH_800, RING_800], imgs


def test_compress_url_injects_800x_and_strips_existing_size():
    assert tg._compress_image_url(WATCH) == WATCH_800
    assert tg._compress_image_url("https://cdn.shopify.com/x/a_1024x1024.jpg") == "https://cdn.shopify.com/x/a_800x.jpg"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - report any failure clearly
            failed += 1
            print(f"FAIL {name}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
