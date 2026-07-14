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
