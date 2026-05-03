"""
Integration tests against Filex's test sandbox.
Uses testapi/SH0052 — Filex's public test account.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_client import FilexClient

# Test sandbox credentials (public, documented in Postman collection)
SANDBOX_USERNAME = "testapi"
SANDBOX_PASSWORD = "123456"
SANDBOX_ACCOUNT  = "SH0052"
SANDBOX_BASE     = "http://filex-shipperapi.dispatchex.info"

import socket

def _sandbox_reachable() -> bool:
    try:
        socket.create_connection(("filex-shipperapi.dispatchex.info", 80), timeout=3)
        return True
    except OSError:
        return False

SANDBOX_UNREACHABLE_MSG = "Filex sandbox unreachable"


@unittest.skipUnless(_sandbox_reachable(), SANDBOX_UNREACHABLE_MSG)
class TestAuth(unittest.TestCase):
    def test_auth_returns_token(self):
        client = FilexClient(
            username=SANDBOX_USERNAME,
            password=SANDBOX_PASSWORD,
            account=SANDBOX_ACCOUNT,
            api_base=SANDBOX_BASE,
        )
        token = client.get_token()
        self.assertIsInstance(token, str)
        self.assertTrue(token, "Token should be non-empty")
        # The cache test below proves the token works for repeated use;
        # we don't need a magic length check here.

    def test_token_is_cached(self):
        client = FilexClient(
            username=SANDBOX_USERNAME,
            password=SANDBOX_PASSWORD,
            account=SANDBOX_ACCOUNT,
            api_base=SANDBOX_BASE,
        )
        t1 = client.get_token()
        t2 = client.get_token()
        self.assertEqual(t1, t2)  # second call should hit cache, return same token


@unittest.skipUnless(_sandbox_reachable(), SANDBOX_UNREACHABLE_MSG)
class TestPlaceOrders(unittest.TestCase):
    def test_place_single_order(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        result = client.place_orders([{
            "RecipientName": "Test Customer",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-PLAN-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test Address",
            "MobileNumber2": "",
            "Remarks": "TEST - DO NOT DISPATCH",
            "NumberOfPieces": "1",
            "Desc1": "Test item",
        }])
        self.assertEqual(result["data"], "success")
        self.assertEqual(len(result["trackingnos"]), 1)
        self.assertEqual(result["trackingnos"][0]["barcode"], "TEST-PLAN-001")
        self.assertTrue(result["trackingnos"][0]["tracking_no"].isdigit())


@unittest.skipUnless(_sandbox_reachable(), SANDBOX_UNREACHABLE_MSG)
class TestGetStatus(unittest.TestCase):
    def test_get_status_for_known_tracking(self):
        # First place a test order, then query its status
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        place_result = client.place_orders([{
            "RecipientName": "Status Test",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-STAT-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test Address",
            "MobileNumber2": "",
            "Remarks": "TEST",
            "NumberOfPieces": "1",
            "Desc1": "Test",
        }])
        tn = place_result["trackingnos"][0]["tracking_no"]

        statuses = client.get_status([tn])
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["tracking_No"], tn)
        self.assertEqual(statuses[0]["shipperRef"], "TEST-STAT-001")
        self.assertIn("trackingStatus", statuses[0])

    def test_get_status_empty_input_returns_empty_list(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        self.assertEqual(client.get_status([]), [])


@unittest.skipUnless(_sandbox_reachable(), SANDBOX_UNREACHABLE_MSG)
class TestLabelPdf(unittest.TestCase):
    def test_get_label_returns_clean_pdf(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        place_result = client.place_orders([{
            "RecipientName": "Label Test",
            "TotalCOG": "1.00",
            "MobileNumber": "0500000000",
            "ShipperRef": "TEST-LABEL-001",
            "AddressCountry": "United Arab Emirates",
            "City": "Dubai",
            "Area": "",
            "Street": "Test",
            "MobileNumber2": "",
            "Remarks": "TEST",
            "NumberOfPieces": "1",
            "Desc1": "Test",
        }])
        tn = place_result["trackingnos"][0]["tracking_no"]

        pdf_bytes = client.get_label_pdf([tn])
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 50_000)  # real label > 50KB

    def test_get_label_empty_input_raises(self):
        client = FilexClient(SANDBOX_USERNAME, SANDBOX_PASSWORD, SANDBOX_ACCOUNT, SANDBOX_BASE)
        with self.assertRaises(ValueError):
            client.get_label_pdf([])


if __name__ == "__main__":
    unittest.main()
