import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import filex_city_normalizer as f


def test_dash_between_words_normalizes_to_filex_city():
    # Reported bug: "Umm al-Quwain" (dash) was skipped -> label never generated.
    assert f.normalize_city("Villa 4, Umm al-Quwain") == "Um Al Qwain"
    assert f.normalize_city("Umm al-Quwain") == "Um Al Qwain"
    # Any dash form should resolve, uniformly.
    assert f.normalize_city("ras al-khaimah") == "Ras Al Khaimah"
    assert f.normalize_city("al-ain") == "Al Ain"
    assert f.normalize_city("abu-dhabi") == "Abu Dhabi"


def test_space_and_plain_forms_still_work():
    assert f.normalize_city("Umm Al Quwain") == "Um Al Qwain"
    assert f.normalize_city("Dubai Marina, Dubai") == "Dubai"
    assert f.normalize_city("Sharjah") == "Sharjah"
    assert f.normalize_city("no city in this text") is None
