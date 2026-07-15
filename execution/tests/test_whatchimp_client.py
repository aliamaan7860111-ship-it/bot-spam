import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import whatchimp_client as wc


def test_clean_phone_971_with_leading_zero():
    # 971 + local number that still carries its leading 0 (real CRM data that was
    # being skipped: AM2843, LU1227, LU1302). Must strip the redundant 0.
    assert wc.clean_phone_number("+9710547071211") == "971547071211"
    assert wc.clean_phone_number("+9710506791556") == "971506791556"
    assert wc.clean_phone_number("+9710563646559") == "971563646559"


def test_clean_phone_existing_shapes_still_work():
    # Regression guard: previously-working shapes must keep normalizing.
    assert wc.clean_phone_number("+971547071211") == "971547071211"
    assert wc.clean_phone_number("0547071211") == "971547071211"
    assert wc.clean_phone_number("547071211") == "971547071211"
    assert wc.clean_phone_number("00971547071211") == "971547071211"


def test_clean_template_param_strips_newlines_tabs_and_space_runs():
    # WhatsApp rejects template params with newline/tab or >4 consecutive spaces.
    assert wc.clean_template_param("John\nDoe") == "John Doe"
    assert wc.clean_template_param("A\tB") == "A B"
    assert wc.clean_template_param("x" + " " * 6 + "y") == "x y"
    assert wc.clean_template_param("  padded  ") == "padded"
    assert wc.clean_template_param(None) == ""
