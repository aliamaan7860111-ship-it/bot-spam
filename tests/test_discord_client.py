"""The Discord #fulfillment message for an order."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import discord_client as dc  # noqa: E402


def raw(n_items=2, **over):
    base = {
        "order_code": "AM5459", "customer": "Mahmoud @everyone", "phone": "0501234567",
        "address": "Villa 1", "city": "Dubai", "total": 259, "total_known": True, "payment_method": "COD",
        "items": [{"id": f"id-{i}", "product_title": f"Bag *{i}*", "variant": "Blush Pink", "quantity": 1,
                   "unit_cost": None, "image_url": f"https://cdn/{i}.png"} for i in range(n_items)],
    }
    base.update(over)
    return base


class DiscordMessage(unittest.TestCase):
    def test_one_button_per_item_carrying_the_item_id(self):
        msg = dc.build_messages(raw(), ["https://cdn/a.png", "https://cdn/b.png"])
        self.assertEqual(len(msg), 1)
        inner = msg[0]["components"][0]["components"]
        buttons = [b for c in inner if c["type"] == 1 for b in c["components"]]
        self.assertEqual([b["custom_id"] for b in buttons], ["cost:id-0", "cost:id-1"])
        self.assertTrue(all(b["label"] == "Enter cost" for b in buttons))
        self.assertEqual(msg[0]["flags"], 1 << 15)

    def test_each_item_has_its_own_photo_beside_it(self):
        inner = dc.build_messages(raw(), [])[0]["components"][0]["components"]
        photos = [c["accessory"]["media"]["url"] for c in inner if c["type"] == 9]
        self.assertEqual(photos, ["https://cdn/0.png", "https://cdn/1.png"])

    def test_an_item_without_its_own_photo_uses_the_order_photos(self):
        r = raw(2)
        for it in r["items"]:
            it["image_url"] = None
        inner = dc.build_messages(r, ["https://shop/a.png"])[0]["components"][0]["components"]
        self.assertEqual([c["accessory"]["media"]["url"] for c in inner if c["type"] == 9], ["https://shop/a.png"])
        self.assertEqual(sum(1 for c in inner if c["type"] == 1), 2)  # both still get a button

    def test_never_more_than_forty_components(self):
        for n in (1, 10, 11, 25):
            for m in dc.build_messages(raw(n), ["https://cdn/x.png"]):
                self.assertLessEqual(dc._count(m["components"]), 40, f"{n} items")

    def test_a_big_order_is_split_and_every_item_gets_a_button(self):
        msgs = dc.build_messages(raw(25), [])
        ids = [b["custom_id"] for m in msgs for c in m["components"][0]["components"] if c["type"] == 1 for b in c["components"]]
        self.assertEqual(len(ids), 25)
        self.assertGreater(len(msgs), 1)

    def test_customer_text_cannot_mention_or_format(self):
        text = str(dc.build_messages(raw(), []))
        self.assertNotIn("@everyone", text)        # broken by a zero-width space
        self.assertIn("Bag \\\\*0\\\\*", text)     # asterisks escaped
        self.assertEqual(dc.build_messages(raw(), [])[0]["allowed_mentions"], {"parse": []})

    def test_an_item_already_costed_shows_it_and_offers_edit(self):
        r = raw(1)
        r["items"][0]["unit_cost"] = 120
        inner = dc.build_messages(r, [])[0]["components"][0]["components"]
        sec = [c for c in inner if c["type"] == 9][0]
        self.assertIn("AED 120", sec["components"][0]["content"])
        self.assertEqual([c for c in inner if c["type"] == 1][0]["components"][0]["label"], "Edit cost")

    def test_disabled_without_token_or_channel(self):
        import os
        old = os.environ.pop("DISCORD_FULFILMENT_CHANNEL_ID", None)
        try:
            self.assertFalse(dc.enabled())
            self.assertEqual(dc.post_order(raw(), []), [])
        finally:
            if old is not None:
                os.environ["DISCORD_FULFILMENT_CHANNEL_ID"] = old


if __name__ == "__main__":
    unittest.main()
