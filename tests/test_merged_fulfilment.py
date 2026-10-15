"""
A merged order goes to the fulfilment group as one album with every item.

The case that found it: AM5392 (a Monogram Black cap) was merged into AM5393
(a Jumbo Black cap), and the album showed the Jumbo Black alone, because only
AM5393's Shopify page was read.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import grq_os_fulfilment as grq  # noqa: E402
import telegram_client as tg  # noqa: E402

JUMBO = {"product_title": "Gucci Cap | Jumbo Black", "variant": None, "sku": None,
         "quantity": 1, "image_url": "https://grq/jumbo.jpg"}
MONO = {"product_title": "Gucci Cap | Monogram Black", "variant": None, "sku": None,
        "quantity": 1, "image_url": "https://grq/mono.jpg"}

URL_93 = "https://amarasroom.store/111/orders/aaa"
URL_92 = "https://amarasroom.store/111/orders/bbb"


def _claimed(merged=True):
    return {
        "order_id": "uuid-93", "order_code": "AM5393", "customer": "C", "phone": "971",
        "address": "Dubai", "total": 490, "order_source_url": URL_93,
        "items": [JUMBO, MONO] if merged else [JUMBO],
        "merged": [{"order_code": "AM5392", "order_source_url": URL_92, "items": [MONO]}] if merged else [],
    }


def _shopify(pages):
    """A stand-in for fetch_order_images_graphql over the given pages."""
    def fetch(url, holder):
        page = pages.get(url)
        if not page:
            return []
        holder["line_items"] = [{"title": t, "variant": "", "quantity": 1} for t in page]
        return [f"cdn:{t}" for t in page]
    return fetch


class MergedFulfilment(unittest.TestCase):
    def test_every_page_is_read_and_combined(self):
        order = grq.to_bridge_order(_claimed())
        self.assertEqual(order["own_items"], [JUMBO])
        self.assertTrue(grq.combine_merged(order, _shopify({URL_93: ["Jumbo"], URL_92: ["Mono"]})))
        self.assertEqual(order["image_urls"], ["cdn:Jumbo", "cdn:Mono"])
        self.assertEqual([l["title"] for l in order["line_items"]], ["Jumbo", "Mono"])

    def test_a_page_that_gives_nothing_falls_back_for_that_part_only(self):
        order = grq.to_bridge_order(_claimed())
        grq.combine_merged(order, _shopify({URL_93: ["Jumbo"]}))
        self.assertEqual(order["image_urls"], ["cdn:Jumbo", "https://grq/mono.jpg"])
        self.assertEqual(order["line_items"][1]["title"], "Gucci Cap | Monogram Black")

    def test_a_page_that_throws_does_not_sink_the_rest(self):
        def fetch(url, holder):
            if url == URL_92:
                raise RuntimeError("shopify down")
            return _shopify({URL_93: ["Jumbo"]})(url, holder)
        order = grq.to_bridge_order(_claimed())
        grq.combine_merged(order, fetch)
        self.assertEqual(len(order["image_urls"]), 2)

    def test_an_ordinary_order_is_left_to_the_sender(self):
        order = grq.to_bridge_order(_claimed(merged=False))
        self.assertFalse(grq.combine_merged(order, _shopify({URL_93: ["Jumbo"]})))
        self.assertNotIn("line_items", order)

    def test_identical_lines_are_subtracted_one_for_one(self):
        self.assertEqual(grq._without([MONO, MONO, JUMBO], [MONO]), [MONO, JUMBO])

    def test_the_caption_does_not_mention_the_merged_order(self):
        # The packers see one order; the merged code is the CRM's business.
        order = grq.to_bridge_order(_claimed())
        grq.combine_merged(order, _shopify({URL_93: ["Jumbo"], URL_92: ["Mono"]}))
        caption = tg.format_fulfillment_message(order)
        self.assertIn("AM5393", caption)
        self.assertNotIn("AM5392", caption)
        self.assertNotIn("erged", caption)


if __name__ == "__main__":
    unittest.main()
