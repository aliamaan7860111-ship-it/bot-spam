import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import order_dedup as dedup


def test_claim_is_atomic_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "_DB_PATH", str(tmp_path / "d.db"))
    assert dedup.claim("VS1017") is True     # first claim wins
    assert dedup.claim("VS1017") is False    # second is a duplicate -> skip
    assert dedup.claim("OTHER") is True       # a different order is independent
    dedup.release("VS1017")                    # failed insert -> allow retry
    assert dedup.claim("VS1017") is True       # claimable again after release
    assert dedup.claim("") is False            # empty id never claims


def test_claim_is_race_safe_under_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "_DB_PATH", str(tmp_path / "race.db"))
    results = []
    lock = threading.Lock()

    def worker():
        r = dedup.claim("RACE1")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly ONE concurrent claimer wins; the rest see it's already taken.
    assert results.count(True) == 1
    assert results.count(False) == 11
