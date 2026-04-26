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
