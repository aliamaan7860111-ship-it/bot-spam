"""The photo GRQ OS stores for a line item is the variant's, not the product's featured one."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

from shopify_webhook_server import _product_photos, pick_line_image  # noqa: E402

CYME = {
    "id": 15436877988076,
    "image": {"src": "https://cdn/cyme-cognac.png"},
    "images": [
        {"id": 1, "src": "https://cdn/cyme-cognac.png", "variant_ids": []},
        {"id": 2, "src": "https://cdn/cyme-taupe.png", "variant_ids": []},
        {"id": 3, "src": "https://cdn/cyme-black.png", "variant_ids": [903]},
    ],
    "variants": [
        {"id": 901, "image_id": 1},
        {"id": 902, "image_id": 2},
        {"id": 903, "image_id": None},
        {"id": 904, "image_id": None},
    ],
}


def test_the_variant_bought_is_the_photo():
    assert pick_line_image(_product_photos(CYME), 902) == "https://cdn/cyme-taupe.png"


def test_a_photo_linked_from_the_image_side_counts():
    assert pick_line_image(_product_photos(CYME), "903") == "https://cdn/cyme-black.png"


def test_a_variant_with_no_photo_falls_back_to_the_featured_one():
    assert pick_line_image(_product_photos(CYME), 904) == "https://cdn/cyme-cognac.png"


def test_nothing_known_is_none():
    assert pick_line_image(None, 902) is None
    assert pick_line_image({"main": "", "variants": {}}, 902) is None
