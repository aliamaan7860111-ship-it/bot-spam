"""errlog_down.py — posts a 'down' event for a systemd unit that failed.

Invoked by the errlog-alert@.service oneshot via each service's OnFailure hook.
Argument is the failing unit name (e.g. "order-bridge.service"), passed by systemd.
"""
import sys
import time

sys.path.insert(0, "/home/bilal/automation/execution")
import error_reporter

unit = sys.argv[1] if len(sys.argv) > 1 else "unknown"
service = unit.replace(".service", "").replace(".timer", "")
error_reporter.install(service, host="gcp-vm")
error_reporter.report(
    "systemd reports the process exited. Auto-restart may be attempted.",
    error_type="service_exit",
    severity="down",
    context={"unit": unit},
)
time.sleep(3)  # let the fire-and-forget daemon thread flush before this oneshot exits
