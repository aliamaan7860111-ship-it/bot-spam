"""Discord and GRQ OS take the photo Telegram used - the variant bought."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

from grq_os_fulfilment import match_line_photos  # noqa: E402
from telegram_client import _build_images_and_line_items  # noqa: E402


def test_telegram_line_items_carry_their_photo():
    edges = [{"node": {"title": "Numero Neuf", "variantTitle": "Blush", "quantity": 1,
                       "image": {"url": "https://cdn/blush.webp"}}}]
    _, lines = _build_images_and_line_items(edges)
    assert lines[0]["image"] == "https://cdn/blush.webp"


def test_matched_on_title_and_variant():
    items = [{"id": "a", "product_title": "Cartier LOVE", "variant": "Rose Gold / 18", "image_url": "https://cdn/white.webp"},
             {"id": "b", "product_title": "Cartier LOVE", "variant": "White / 20", "image_url": "https://cdn/white.webp"}]
    lines = [{"title": "Cartier LOVE", "variant": "White / 20", "image": "https://cdn/white.webp"},
             {"title": "Cartier LOVE", "variant": "Rose Gold / 18", "image": "https://cdn/rose.webp"}]
    assert match_line_photos(items, lines) == {"a": "https://cdn/rose.webp", "b": "https://cdn/white.webp"}


def test_title_then_position():
    items = [{"id": "a", "product_title": "Bag (edited)", "variant": None},
             {"id": "b", "product_title": "Shoe", "variant": "40"}]
    lines = [{"title": "Shoe", "variant": "EU 40", "image": "https://cdn/shoe.jpg"},
             {"title": "Bag", "variant": "", "image": "https://cdn/bag.jpg"}]
    assert match_line_photos(items, lines) == {"b": "https://cdn/shoe.jpg", "a": "https://cdn/bag.jpg"}


def test_no_photos_no_matches():
    assert match_line_photos([{"id": "a", "product_title": "X"}], [{"title": "X", "image": None}]) == {}
