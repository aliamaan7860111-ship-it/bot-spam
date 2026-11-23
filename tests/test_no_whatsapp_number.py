"""A store with no WhatsApp number is blocked once with a reason, not retried with an error each poll."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import whatchimp_client as wc  # noqa: E402
import whatsapp_confirmation_bot as bot  # noqa: E402


def test_brand_config_for_keeps_two_letters_for_an_unknown_store():
    assert wc.brand_config_for("AD1005")[0] == "AD"
    assert wc.brand_config_for("AD1005")[1] is None


def test_brand_config_for_finds_one_and_two_letter_brands():
    assert wc.brand_config_for("R1234")[0] == "R"
    assert wc.brand_config_for("AM5700")[0] == "AM"
    assert wc.brand_config_for("LN1003")[1] is wc.BRAND_CONFIG["LU"]


def test_an_al_dana_order_is_blocked_once_and_nothing_is_sent(monkeypatch, caplog):
    blocked, sent = [], []
    monkeypatch.setattr(bot.grq, "block_confirmation", lambda oid, reason: blocked.append((oid, reason)) or True)
    monkeypatch.setattr(bot.wc, "send_template_message", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(bot.asyncio, "sleep", lambda s: asyncio.sleep(0))
    order = {"order_id": "AD1005", "phone": "+971500000000", "grq_os_order_id": "g-1",
             "brand_name": "Al Dana", "payment": "Cash on Delivery"}
    with caplog.at_level(logging.WARNING):
        n = asyncio.run(bot._send_all([order]))
    assert n == 0 and sent == []
    assert blocked == [("g-1", "Al Dana has no WhatsApp number set up yet - confirm this order by hand")]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
