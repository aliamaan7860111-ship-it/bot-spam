"""Filex courier API client. Pure HTTP wrapper around their endpoints."""

import io
import re
import time
import logging
import zlib

import requests
from pypdf import PdfReader, PdfWriter

log = logging.getLogger("filex_client")


class FilexClient:
    """
    Thin client for Filex's REST API.

    Token caching: bearer token expires in 24h; refreshed lazily after
    23h. On 401 from a downstream call, callers should invoke
    `_invalidate_token()` and retry — the wired-up retry happens in the
    methods added in Tasks 5+ (place_orders, get_status, get_label_pdf).

    Not thread- or async-safe; intended for single-threaded callers.
    """

    TOKEN_TTL_SECONDS = 23 * 60 * 60  # 23 hours

    def __init__(self, username: str, password: str, account: str, api_base: str):
        self.username = username
        self.password = password
        self.account = account
        self.api_base = api_base.rstrip("/")
        self._token: str | None = None
        self._token_obtained_at: float = 0.0

    def get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed."""
        now = time.time()
        if self._token and (now - self._token_obtained_at) < self.TOKEN_TTL_SECONDS:
            return self._token
        r = requests.post(
            f"{self.api_base}/GetAuthToken",
            data={
                "Username": self.username,
                "Password": self.password,
                "grant_type": "password",
                "AccountNumber": self.account,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(
                f"Filex auth failed (HTTP {r.status_code}) at {r.url}: {r.text[:300]}"
            ) from e
        body = r.json()
        token = body.get("access_token")
        if not token or not isinstance(token, str):
            raise RuntimeError(f"Filex auth returned no usable token: {body}")
        self._token = token
        self._token_obtained_at = now
        log.info("Filex token refreshed (account=%s)", self.account)
        return self._token

    def _auth_headers(self, content_type: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.get_token()}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _invalidate_token(self):
        self._token = None
        self._token_obtained_at = 0.0
        log.info("Filex token invalidated (account=%s)", self.account)

    def _post_with_401_retry(self, path: str, *, json=None, timeout: int = 120) -> "requests.Response":
        """
        POST helper that auto-refreshes the token on a single 401 response.
        Used by place_orders, get_status, etc. so each method doesn't
        duplicate the 401 retry boilerplate.
        """
        url = f"{self.api_base}{path}"
        headers = self._auth_headers("application/json")
        r = requests.post(url, headers=headers, json=json, timeout=timeout)
        if r.status_code == 401:
            self._invalidate_token()
            headers = self._auth_headers("application/json")
            r = requests.post(url, headers=headers, json=json, timeout=timeout)
        return r

    def place_orders(self, orders: list[dict]) -> dict:
        """
        Submit a batch of orders to Filex.

        Args:
            orders: list of dicts matching placebulk schema (RecipientName,
                    TotalCOG, MobileNumber, ShipperRef, AddressCountry,
                    City, Area, Street, MobileNumber2, Remarks,
                    NumberOfPieces, Desc1).

        Returns:
            dict with keys 'data' ('success' on OK) and 'trackingnos'
            (list of {'tracking_no': str, 'barcode': str} per ShipperRef).
            For empty input, returns {'data': 'success', 'trackingnos': []}
            without a network call.

        Raises:
            requests.HTTPError on 4xx/5xx (incl. persistent 401 after re-auth retry)
            RuntimeError if response body's 'data' is not 'success'
        """
        if not orders:
            return {"data": "success", "trackingnos": []}
        r = self._post_with_401_retry(
            "/api/order/placebulk",
            json={"list": orders},
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("data") != "success":
            raise RuntimeError(f"Filex placebulk did not return success: {body}")
        log.info("Filex placed %d order(s)", len(orders))
        return body

    def get_status(self, tracking_numbers: list[str]) -> list[dict]:
        """
        Fetch the latest status for one or more tracking numbers.

        Args:
            tracking_numbers: list of Filex AWB strings.

        Returns:
            list of dicts: {'tracking_No', 'shipperRef', 'trackingStatus',
                            'trackingStatusID', 'eventTime'}
            For empty input, returns [] without a network call.

        Raises:
            requests.HTTPError on 4xx/5xx (incl. persistent 401 after re-auth retry)
        """
        if not tracking_numbers:
            return []
        r = self._post_with_401_retry(
            "/api/order/ShipmentLastStatus",
            json={"trackingNos": ",".join(tracking_numbers)},
            timeout=60,
        )
        r.raise_for_status()
        log.info("Filex status fetched for %d tracking number(s)", len(tracking_numbers))
        return r.json().get("data", [])

    def get_label_pdf(self, tracking_numbers: list[str]) -> bytes:
        """
        Fetch a combined airway-bill PDF for the given tracking numbers,
        with empty-page detection (Filex's batch endpoint occasionally
        emits a near-empty page that we strip).

        Args:
            tracking_numbers: list of Filex AWB strings (must be non-empty).

        Returns:
            Cleaned PDF as bytes, with any empty pages removed.

        Raises:
            ValueError if tracking_numbers is empty
            requests.HTTPError on 4xx/5xx (incl. persistent 401 after re-auth retry)
        """
        if not tracking_numbers:
            raise ValueError("tracking_numbers must not be empty")
        url = (
            f"{self.api_base}/api/order/GetAirWayBill"
            f"?TrackingNos={','.join(tracking_numbers)}"
        )
        r = requests.get(url, headers=self._auth_headers(), timeout=180)
        if r.status_code == 401:
            self._invalidate_token()
            r = requests.get(url, headers=self._auth_headers(), timeout=180)
        r.raise_for_status()
        log.info("Filex label PDF fetched for %d tracking number(s)", len(tracking_numbers))
        return self._strip_empty_pages(r.content)

    @staticmethod
    def _strip_empty_pages(raw_pdf: bytes) -> bytes:
        """
        Detect and drop pages whose content stream decompresses to <1000 bytes.

        Filex's GetAirWayBill batch endpoint occasionally inserts a near-empty
        page in multi-label PDFs (only contains a footer string). This helper
        identifies those by content-stream size and reassembles a clean PDF.
        """
        content_refs = re.findall(rb"/Contents\s+(\d+)", raw_pdf)
        empty_indices: set[int] = set()
        for i, ref in enumerate(content_refs):
            pat = (rf"^{ref.decode()} 0 obj.*?stream\n(.*?)\nendstream").encode()
            m = re.search(pat, raw_pdf, re.DOTALL | re.MULTILINE)
            if not m:
                continue
            try:
                if len(zlib.decompress(m.group(1))) < 1000:
                    empty_indices.add(i)
            except zlib.error:
                empty_indices.add(i)

        if not empty_indices:
            return raw_pdf

        reader = PdfReader(io.BytesIO(raw_pdf))
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i not in empty_indices:
                writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
