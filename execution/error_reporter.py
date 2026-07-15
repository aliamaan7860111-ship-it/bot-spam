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


def _post(payload: dict) -> None:
    # Runs on a background thread. Never raises.
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(_ENDPOINT, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("x-errlog-key", _KEY)
        urllib.request.urlopen(req, timeout=_TIMEOUT).read()
    except Exception:
        pass  # fail-safe: swallow everything


def _dispatch(payload: dict) -> None:
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def _send(severity, error_type, message, detail="", context=None, location=""):
    if not _ENDPOINT:
        return
    payload = {
        "service": _SERVICE,
        "host": _HOST,
        "severity": severity,
        "error_type": error_type or "",
        "message": (message or "")[:2000],
        "detail": (detail or "")[:8000],
        "context": context or {},
        "fingerprint": _fingerprint(_SERVICE, error_type or "", location),
    }
    _dispatch(payload)


def report(message, error_type=None, context=None, exc=None, severity="error"):
    """Report a flagged failure. Safe to call anywhere; never raises."""
    try:
        detail, location = "", ""
        if exc is not None:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            error_type = error_type or type(exc).__name__
            tb = exc.__traceback__
            while tb and tb.tb_next:
                tb = tb.tb_next
            if tb:
                location = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}"
        _send(severity, error_type or "error", message, detail, context, location)
    except Exception:
        pass  # reporting must never break the caller
