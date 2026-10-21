"""errlog_heartbeat.py — posts systemctl is-active for each always-on unit.

Dead-man's-switch input: a pg_cron watchdog on the collector alerts if these
stop arriving (whole-VM outage) or a service reports inactive. Run every ~2 min
by errlog-heartbeat.timer. filex-poll is intentionally excluded (it's a oneshot
timer, not always-on; its failures are covered by the OnFailure watchdog).
"""
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/home/bilal/automation/execution")
import error_reporter  # reused only to load ERRLOG_ENDPOINT / ERRLOG_INGEST_KEY

error_reporter.install("errlog-heartbeat", host="gcp-vm")

UNITS = [
    "order-bridge", "shopify-webhook", "grq-ac", "rpgrq-webhook",
    "grq-rescue", "grq-ofd", "whatsapp-bot", "caddy",
]
endpoint = error_reporter._ENDPOINT.replace("/report-error", "/heartbeat")
key = error_reporter._KEY


def is_active(unit):
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        return out.stdout.strip() == "active"
    except Exception:
        return False


# A restart is a second or two of "deactivating"/"activating" (order-bridge
# also git-pulls first). A check landing inside it reported the unit down and
# paged SERVICE DOWN for a deploy (2026-10-21 02:33:59). So a unit that is not
# active is asked again after RECHECK_S; only a second "no" is reported. A
# unit that is really down is still reported, RECHECK_S later.
RECHECK_S = 20

status = {u: is_active(u) for u in UNITS}
if not all(status.values()):
    time.sleep(RECHECK_S)
    status = {u: ok or is_active(u) for u, ok in status.items()}

payload = {"host": "gcp-vm", "services": status}
try:
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-errlog-key", key)
    urllib.request.urlopen(req, timeout=10).read()
except Exception:
    pass
