"""Fire-and-forget error reporting to the central collector.

Stdlib-only. Fail-safe: never raises, never blocks the host process.

Usage (once, at service startup, after env is loaded):

    import error_reporter
    error_reporter.install("shopify-webhook", host="gcp-vm")

Then crashes and any ``log.error(...)`` are auto-reported. For silent
business failures, call explicitly:

    error_reporter.report("send failed", error_type="whatchimp_status_0",
                          context={"brand": "amara"})
"""
import os
import sys
import json
import logging
import threading
import traceback
import hashlib
import urllib.request

_SERVICE = "unknown"
_HOST = "gcp-vm"
_TIMEOUT = 5
_ENDPOINT = ""
_KEY = ""
_local = threading.local()  # recursion guard for the logging handler


def _fingerprint(service: str, error_type: str, location: str) -> str:
    raw = f"{service}|{error_type}|{location}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
