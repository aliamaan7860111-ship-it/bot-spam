import sys
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
