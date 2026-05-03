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
        self.assertGreater(len(token), 50)

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

if __name__ == "__main__":
    unittest.main()
