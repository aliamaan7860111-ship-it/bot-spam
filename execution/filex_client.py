"""Filex courier API client. Pure HTTP wrapper around their endpoints."""

import time
import requests


class FilexClient:
    """
    Thin client for Filex's REST API.

    Token caching: bearer token expires in 24h; we refresh after 23h
    or on 401 response from any endpoint.
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
        r.raise_for_status()
        body = r.json()
        if "access_token" not in body:
            raise RuntimeError(f"Filex auth failed: {body}")
        self._token = body["access_token"]
        self._token_obtained_at = now
        return self._token

    def _auth_headers(self, content_type: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.get_token()}"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _invalidate_token(self):
        self._token = None
        self._token_obtained_at = 0.0
