import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import whatchimp_client as wc


class TestResolveOfdPrefix(unittest.TestCase):
    def test_two_char_prefixes(self):
        self.assertEqual(wc.resolve_ofd_prefix("PT1793"), "PT")
        self.assertEqual(wc.resolve_ofd_prefix("Di500"), "Di")
        self.assertEqual(wc.resolve_ofd_prefix("LU10"), "LU")
        self.assertEqual(wc.resolve_ofd_prefix("PV7"), "PV")
        self.assertEqual(wc.resolve_ofd_prefix("VX9"), "VX")
        self.assertEqual(wc.resolve_ofd_prefix("AM55"), "AM")

    def test_one_char_orlento_not_shadowed(self):
        # 'O' must resolve to Orlento, and must NOT swallow 2-char prefixes.
        self.assertEqual(wc.resolve_ofd_prefix("O123"), "O")
        self.assertEqual(wc.resolve_ofd_prefix("VX1"), "VX")

    def test_unknown_and_empty(self):
        self.assertIsNone(wc.resolve_ofd_prefix("ZZ9"))
        self.assertIsNone(wc.resolve_ofd_prefix(""))
        self.assertIsNone(wc.resolve_ofd_prefix(None))


class TestGetOfdConfig(unittest.TestCase):
    def test_pelvini_shares_lune(self):
        pv = wc.get_ofd_config("PV1")
        lu = wc.get_ofd_config("LU1")
        self.assertEqual(pv["phone_number_id"], lu["phone_number_id"])
        self.assertEqual(pv["ofd_template_id"], lu["ofd_template_id"])
        self.assertEqual(pv["brand_display"], "Pelvini")

    def test_orlento_shares_virex(self):
        o = wc.get_ofd_config("O5")
        vx = wc.get_ofd_config("VX5")
        self.assertEqual(o["phone_number_id"], vx["phone_number_id"])
        self.assertEqual(o["ofd_template_id"], vx["ofd_template_id"])
        self.assertEqual(o["brand_display"], "Orlento")

    def test_amara_no_vars(self):
        am = wc.get_ofd_config("AM9")
        self.assertTrue(am.get("no_vars"))
        self.assertEqual(am["ofd_template_id"], "377951")
        # Amara is currently disabled (template not approved in en_US).
        self.assertTrue(am.get("pending"))

    def test_unknown_is_none(self):
        self.assertIsNone(wc.get_ofd_config("ZZ1"))


class TestBuildOfdPayload(unittest.TestCase):
    def test_standard_brand_includes_variables(self):
        cfg = wc.get_ofd_config("PT1")
        p = wc.build_ofd_payload(cfg, "PT1793", "971500000000", "TOK")
        self.assertEqual(p["apiToken"], "TOK")
        self.assertEqual(p["template_id"], "377952")
        self.assertEqual(p["phone_number_id"], "1031340813395459")
        self.assertEqual(p["phone_number"], "971500000000")
        self.assertEqual(p["templateVariable-brand-2"], "Elara UAE")
        self.assertEqual(p["templateVariable-id-3"], "PT1793")

    def test_amara_omits_variables(self):
        cfg = wc.get_ofd_config("AM9")
        p = wc.build_ofd_payload(cfg, "AM9", "971500000000", "TOK")
        self.assertNotIn("templateVariable-brand-2", p)
        self.assertNotIn("templateVariable-id-3", p)
        self.assertEqual(p["template_id"], "377951")


import notion_client as nc
import filex_status_mapper


class TestStatusShippedConstant(unittest.TestCase):
    def test_matches_filex_mapper(self):
        # The at-source hook compares the promoted status to nc.STATUS_SHIPPED;
        # it must stay identical to what the mapper actually writes.
        self.assertEqual(
            nc.STATUS_SHIPPED,
            filex_status_mapper.ORDER_STATUS_FROM_FILEX["Shipped"],
        )


class TestParseOutForDeliverySent(unittest.TestCase):
    def test_absent_checkbox_is_false(self):
        page = {"id": "p1", "properties": {}}
        order = nc.parse_order(page)
        self.assertFalse(order["out_for_delivery_sent"])

    def test_checked_is_true(self):
        page = {"id": "p1", "properties": {
            nc.FIELD_OUT_FOR_DELIVERY_SENT: {"type": "checkbox", "checkbox": True},
        }}
        order = nc.parse_order(page)
        self.assertTrue(order["out_for_delivery_sent"])


from unittest import mock

import out_for_delivery as ofd


def _order(**over):
    base = {
        "page_id": "pg1", "order_id": "PT1793", "phone": "971500000000",
        "out_for_delivery_sent": False,
    }
    base.update(over)
    return base


class TestSendOutForDeliveryGate(unittest.TestCase):
    def setUp(self):
        ofd._skip_this_run.clear()

    def test_skips_pending_brand(self):
        # Amara is pending (template not approved in en_US) — must not attempt a send.
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(order_id="AM3577", page_id="am1"))
        self.assertFalse(result)
        send.assert_not_called()

    def test_failed_send_not_retried_same_run(self):
        # A rejected send must be attempted ONCE, then skipped for the rest of the run.
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template", return_value=False) as send, \
             mock.patch.object(ofd.nc, "mark_out_for_delivery_sent") as mark:
            o = _order()
            r1 = ofd.send_out_for_delivery(o)
            r2 = ofd.send_out_for_delivery(o)
        self.assertFalse(r1)
        self.assertFalse(r2)
        self.assertEqual(send.call_count, 1)
        mark.assert_not_called()

    def test_skips_when_already_sent(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(out_for_delivery_sent=True))
        self.assertFalse(result)
        send.assert_not_called()

    def test_skips_unknown_brand(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(order_id="ZZ9"))
        self.assertFalse(result)
        send.assert_not_called()

    def test_skips_when_no_phone(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template") as send:
            result = ofd.send_out_for_delivery(_order(phone=""))
        self.assertFalse(result)
        send.assert_not_called()

    def test_sends_and_marks_on_success(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template", return_value=True) as send, \
             mock.patch.object(ofd.nc, "mark_out_for_delivery_sent") as mark:
            result = ofd.send_out_for_delivery(_order())
        self.assertTrue(result)
        send.assert_called_once()
        mark.assert_called_once_with("pg1")

    def test_does_not_mark_on_send_failure(self):
        with mock.patch.object(ofd.wc, "send_out_for_delivery_template", return_value=False), \
             mock.patch.object(ofd.nc, "mark_out_for_delivery_sent") as mark:
            result = ofd.send_out_for_delivery(_order())
        self.assertFalse(result)
        mark.assert_not_called()


if __name__ == "__main__":
    unittest.main()
