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


def test_clean_phone_international_kept_as_e164():
    # Real non-UAE customers (UK, Egypt, Morocco, Ghana) — keep their country code.
    assert wc.clean_phone_number("+447915814716") == "447915814716"
    assert wc.clean_phone_number("+201035542055") == "201035542055"
    assert wc.clean_phone_number("+212693282484") == "212693282484"
    assert wc.clean_phone_number("+233246666095") == "233246666095"
    assert wc.clean_phone_number("00447915814716") == "447915814716"


def test_is_valid_msisdn():
    # Accepts any plausible E.164 subscriber number (8–15 digits), UAE or not.
    assert wc.is_valid_msisdn("971547071211") is True   # UAE (12)
    assert wc.is_valid_msisdn("447915814716") is True    # UK (12)
    assert wc.is_valid_msisdn("201035542055") is True    # Egypt (12)
    assert wc.is_valid_msisdn("12025550123") is True      # US (11)
    assert wc.is_valid_msisdn("123") is False             # too short
    assert wc.is_valid_msisdn("1234567890123456") is False  # 16, too long
    assert wc.is_valid_msisdn("") is False
    assert wc.is_valid_msisdn("97154707a211") is False   # non-digit
