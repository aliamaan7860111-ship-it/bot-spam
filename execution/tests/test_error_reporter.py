import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import error_reporter as er


def test_fingerprint_is_stable_and_short():
    fp1 = er._fingerprint("grq-ac", "KeyError", "sender.py:88")
    fp2 = er._fingerprint("grq-ac", "KeyError", "sender.py:88")
    fp3 = er._fingerprint("grq-ac", "KeyError", "sender.py:99")
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 16


def test_report_dispatches_expected_payload(monkeypatch):
    sent = {}
    monkeypatch.setattr(er, "_dispatch", lambda p: sent.update(p))
    monkeypatch.setattr(er, "_ENDPOINT", "http://x")  # non-empty so it sends
    monkeypatch.setattr(er, "_SERVICE", "grq-ac")
    er.report("send failed", error_type="whatchimp_status_0", context={"brand": "amara"})
    assert sent["service"] == "grq-ac"
    assert sent["error_type"] == "whatchimp_status_0"
    assert sent["severity"] == "error"
    assert sent["context"] == {"brand": "amara"}
    assert sent["fingerprint"]


def test_report_noop_without_endpoint(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(er, "_dispatch", lambda p: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(er, "_ENDPOINT", "")  # no endpoint configured
    er.report("x")
    assert called["n"] == 0


def test_report_never_raises(monkeypatch):
    def boom(_):
        raise RuntimeError("network down")

    monkeypatch.setattr(er, "_dispatch", boom)
    monkeypatch.setattr(er, "_ENDPOINT", "http://x")
    er.report("x")  # must not raise


def test_logging_handler_captures_error(monkeypatch):
    sent = []
    monkeypatch.setattr(er, "_dispatch", lambda p: sent.append(p))
    monkeypatch.setattr(er, "_ENDPOINT", "http://x")
    monkeypatch.setattr(er, "_SERVICE", "shopify-webhook")
    h = er._ErrlogHandler()
    h.setLevel(logging.ERROR)
    lg = logging.getLogger("test.capture")
    lg.addHandler(h)
    lg.setLevel(logging.ERROR)
    try:
        lg.error("Failed to create order page in Notion")
    finally:
        lg.removeHandler(h)
    assert len(sent) == 1
    assert sent[0]["service"] == "shopify-webhook"
    assert "Notion" in sent[0]["message"]


def test_logging_handler_ignores_info(monkeypatch):
    sent = []
    monkeypatch.setattr(er, "_dispatch", lambda p: sent.append(p))
    monkeypatch.setattr(er, "_ENDPOINT", "http://x")
    h = er._ErrlogHandler()
    h.setLevel(logging.ERROR)
    lg = logging.getLogger("test.info")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    try:
        lg.info("all good")
    finally:
        lg.removeHandler(h)
    assert sent == []


def test_env_fallback_reads_file(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    envfile.write_text('ERRLOG_ENDPOINT=http://collector/x\nERRLOG_INGEST_KEY="k123"\n', encoding="utf-8")
    monkeypatch.delenv("ERRLOG_ENDPOINT", raising=False)
    monkeypatch.delenv("ERRLOG_INGEST_KEY", raising=False)
    endpoint, key = er._load_env_fallback(str(envfile))
    assert endpoint == "http://collector/x"
    assert key == "k123"  # quotes stripped


def test_install_sets_service_and_excepthook(monkeypatch):
    monkeypatch.setenv("ERRLOG_ENDPOINT", "http://x")
    monkeypatch.setenv("ERRLOG_INGEST_KEY", "k")
    er.install("grq-rescue", host="gcp-vm")
    assert er._SERVICE == "grq-rescue"
    assert er._ENDPOINT == "http://x"
    assert sys.excepthook is er._excepthook
