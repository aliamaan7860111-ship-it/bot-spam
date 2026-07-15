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


class _ErrlogHandler(logging.Handler):
    """Reports any log record at its level (ERROR+) to the collector."""

    def emit(self, record):
        try:
            if getattr(_local, "active", False):
                return  # don't report errors caused by our own reporting
            _local.active = True
            try:
                location = f"{os.path.basename(record.pathname)}:{record.lineno}"
                detail = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
                _send("error", record.name or "error", record.getMessage(), detail, None, location)
            finally:
                _local.active = False
        except Exception:
            pass


def _load_env_fallback(path="/home/bilal/automation/.env"):
    """Read ERRLOG_ENDPOINT / ERRLOG_INGEST_KEY straight from a .env file.

    Lets the reporter work regardless of whether the host service loaded
    its .env into os.environ. Never raises.
    """
    endpoint, key = "", ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == "ERRLOG_ENDPOINT":
                    endpoint = v
                elif k.strip() == "ERRLOG_INGEST_KEY":
                    key = v
    except Exception:
        pass
    return endpoint, key


def _excepthook(exc_type, exc_value, exc_tb):
    try:
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        location = ""
        tb = exc_tb
        while tb and tb.tb_next:
            tb = tb.tb_next
        if tb:
            location = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}"
        _send("crash", exc_type.__name__, str(exc_value), detail, None, location)
    except Exception:
        pass
    finally:
        sys.__excepthook__(exc_type, exc_value, exc_tb)


def install(service, host="gcp-vm", level=logging.ERROR, env_path="/home/bilal/automation/.env"):
    """Wire up auto-capture for a service. Call once, at startup, after env load."""
    global _SERVICE, _HOST, _ENDPOINT, _KEY
    _SERVICE, _HOST = service, host
    _ENDPOINT = os.environ.get("ERRLOG_ENDPOINT", "")
    _KEY = os.environ.get("ERRLOG_INGEST_KEY", "")
    if not _ENDPOINT or not _KEY:
        fe, fk = _load_env_fallback(env_path)
        _ENDPOINT = _ENDPOINT or fe
        _KEY = _KEY or fk
    handler = _ErrlogHandler()
    handler.setLevel(level)
    logging.getLogger().addHandler(handler)
    sys.excepthook = _excepthook
    try:  # best-effort asyncio capture
        import asyncio

        def _async_handler(loop, ctx):
            report(ctx.get("message", "asyncio error"), exc=ctx.get("exception"), severity="crash")

        asyncio.get_event_loop().set_exception_handler(_async_handler)
    except Exception:
        pass
