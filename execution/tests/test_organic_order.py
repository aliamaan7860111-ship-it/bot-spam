import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notion_client as n


def test_is_organic_order():
    # Organic orders: a space between the brand initials and the number
    # (e.g. "LU 231") — these must NOT get an auto WhatsApp confirmation.
    assert n.is_organic_order("LU 23") is True
    assert n.is_organic_order("LU 231") is True
    assert n.is_organic_order("AM 45") is True
    assert n.is_organic_order("LU  231") is True    # multiple spaces
    assert n.is_organic_order(" LU 231 ") is True   # padded

    # Normal orders: initials immediately followed by digits — confirm as usual.
    assert n.is_organic_order("LU1227") is False
    assert n.is_organic_order("AM2843") is False
    assert n.is_organic_order("LU1302") is False
    assert n.is_organic_order("Di1699") is False
    assert n.is_organic_order("") is False
    assert n.is_organic_order(None) is False
