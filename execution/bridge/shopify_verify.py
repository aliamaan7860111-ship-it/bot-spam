"""
Shopify webhook HMAC verification.

If the brand has SHOPIFY_API_SECRET configured, we verify the X-Shopify-Hmac-SHA256
header against the raw request body. If not configured, we fall back to path-based
trust (the <brand> path segment is the only identifier — keep the URL secret).
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac


def verify_shopify_hmac(secret: str | None, body: bytes, header_hmac: str | None) -> bool:
    """Return True if HMAC matches OR if secret is None (skip verification)."""
    if not secret:
        return True  # no secret configured — skip verification (path-trust mode)
    if not header_hmac:
        return False
    digest = _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(digest).decode()
    return _hmac.compare_digest(expected_b64, header_hmac)
