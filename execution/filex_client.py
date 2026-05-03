"""Filex courier API client. Pure HTTP wrapper around their endpoints."""

import time
import logging
import requests

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
