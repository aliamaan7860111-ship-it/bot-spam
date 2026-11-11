"""A payment on a CRM Payment links link marks it paid and does not go looking for an order."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import payment_bridge as pb  # noqa: E402


def _event(link="plink_1", order=None):
    obj = {"id": "cs_1", "payment_status": "paid", "amount_total": 29900, "payment_link": link,
           "customer_details": {"email": "a@b.c"}}
    if order:
        obj["client_reference_id"] = order
    return {"id": "evt_" + (order or link or "x"), "type": "checkout.session.completed", "data": {"object": obj}}


def test_crm_link_is_marked_paid_and_no_order_is_touched(monkeypatch):
    calls, orders = [], []
    monkeypatch.setattr(pb.grq_os_db, "rpc", lambda name, args: calls.append((name, args)) or True)
    monkeypatch.setattr(pb.grq_os_ingest, "payment", lambda p: orders.append(p) or True)
    pb.handle_event(_event("plink_crm"))
    assert calls == [("mark_payment_link_paid", {"p_link": "plink_crm", "p_session": "cs_1", "p_amount": 299.0, "p_email": "a@b.c"})]
    assert orders == []


def test_a_link_the_crm_does_not_know_goes_on_to_the_order(monkeypatch):
    orders = []
    monkeypatch.setattr(pb.grq_os_db, "rpc", lambda name, args: False)
    monkeypatch.setattr(pb.grq_os_ingest, "payment", lambda p: orders.append(p) or True)
    monkeypatch.setattr(pb.cutover, "write_notion", lambda: False)
    pb.handle_event(_event("plink_bot", order="AM 1"))
    assert orders and orders[0]["order_code"] == "AM 1"
