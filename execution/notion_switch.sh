#!/usr/bin/env bash
#
# notion_switch.sh — one command for "is Notion the book, or is GRQ OS?"
#
#   sudo ./execution/notion_switch.sh on      Notion is the book. Mirror runs.
#   sudo ./execution/notion_switch.sh off     Notion is retired. GRQ OS only.
#   ./execution/notion_switch.sh status       which it currently is
#
# There were six environment flags and a systemd unit behind this. Six things
# that have to move together is not a switch, it is a checklist, and a
# checklist gets half-done at two in the morning with the fulfilment team
# waiting. This moves all seven or none.
#
# ON  — the parallel run. Every bot reads Notion, every result is written to
#       both systems, and `notion-mirror` copies Notion into GRQ OS every
#       sixty seconds so the two agree. Before the mirror starts, anything
#       GRQ OS took while Notion was off is written into Notion, carrying
#       the marks that stop Notion's automations redoing work — otherwise
#       those customers get a second confirmation template.
#
# OFF — GRQ OS is the book. The mirror is stopped and disabled so a reboot
#       does not quietly restart it, every bot reads GRQ OS, and no order
#       path writes to Notion at all.
#
# The order matters in both directions and is the opposite each way. Going
# off: stop the mirror BEFORE the bots switch, or the mirror undoes their
# first results. Going on: catch Notion up BEFORE the mirror starts, or it
# has nothing to mirror those orders onto.
#
set -euo pipefail

ROOT=/home/bilal/automation
ENV_FILE="$ROOT/.env"
PY="$ROOT/venv/bin/python"

FLAGS=(LABELS_FROM_GRQ_OS FILEX_FROM_GRQ_OS CONFIRM_FROM_GRQ_OS OFD_FROM_GRQ_OS FULFIL_FROM_GRQ_OS NOTION_RETIRED)
SERVICES=(order-bridge whatsapp-bot grq-ofd shopify-webhook payment-bridge)

usage() { sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

# Replace the flag if it is there, append it if it is not. Appending a second
# copy would be worse than useless: python-dotenv keeps the last value, so the
# file would say one thing and the process would do another.
set_flag() {
  local key=$1 value=$2
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

show_status() {
  local retired mirror
  retired=$(grep -E '^NOTION_RETIRED=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)
  mirror=$(systemctl is-active notion-mirror 2>/dev/null || true)
  echo
  if [[ "${retired:-0}" =~ ^(1|true|yes|on)$ ]]; then
    echo "  Notion is RETIRED. GRQ OS is the book."
  else
    echo "  Notion is THE BOOK. GRQ OS runs alongside it."
  fi
  echo "  notion-mirror: $mirror ($(systemctl is-enabled notion-mirror 2>/dev/null || echo unknown) at boot)"
  echo
  for f in "${FLAGS[@]}"; do
    printf '    %-22s %s\n' "$f" "$(grep -E "^${f}=" "$ENV_FILE" | tail -1 | cut -d= -f2- || echo '(unset)')"
  done
  echo
  for s in "${SERVICES[@]}"; do
    printf '    %-22s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || echo '?')"
  done
  echo
}

restart_all() {
  echo "  restarting: ${SERVICES[*]}"
  systemctl restart "${SERVICES[@]}"
  sleep 10
  local bad=0
  for s in "${SERVICES[@]}"; do
    if [[ "$(systemctl is-active "$s")" != "active" ]]; then
      echo "  !! $s did not come back — check: journalctl -u $s -n 40"
      bad=1
    fi
  done
  return $bad
}

case "${1:-}" in
  status) show_status ;;

  off)
    echo
    echo "  Retiring Notion. GRQ OS becomes the book."
    # The mirror first. A bot that writes only to GRQ OS while the mirror is
    # still running has its result read back off Notion within the minute and
    # undone - and then redone, and undone, forever.
    echo "  stopping the mirror"
    systemctl stop notion-mirror || true
    systemctl disable notion-mirror 2>/dev/null || true
    for f in "${FLAGS[@]}"; do set_flag "$f" 1; done
    restart_all
    echo "  Done. Nothing on the order path writes to Notion."
    echo "  Leads still dual-write: their Notion database holds the roster."
    show_status
    ;;

  on)
    echo
    echo "  Handing the book back to Notion."
    # Catch-up first, while the flags still say GRQ OS is the book, so the
    # order of operations cannot leave a window where Notion is authoritative
    # but does not know about the last few orders.
    echo "  telling Notion what it missed"
    if ! "$PY" "$ROOT/execution/notion_catchup.py"; then
      echo
      echo "  !! The catch-up did not finish cleanly. STOPPING."
      echo "     Switching on now would leave Notion authoritative and missing"
      echo "     orders, which is the one state this script exists to prevent."
      echo "     Read the errors above, then run this again."
      exit 1
    fi
    for f in "${FLAGS[@]}"; do set_flag "$f" 0; done
    restart_all
    echo "  starting the mirror"
    systemctl enable notion-mirror 2>/dev/null || true
    systemctl start notion-mirror
    echo "  Done. Notion is the book again and the mirror is copying it across."
    show_status
    ;;

  *) usage ;;
esac
