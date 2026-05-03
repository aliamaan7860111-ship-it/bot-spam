# Filex Automation — SOP

## Purpose

This bot replaces manual Filex order entry and dashboard checking with one Telegram command (`/print all`) plus passive webhook updates to the Notion CRM. All shipments dispatched via Filex are tracked end-to-end inside Notion.

## Trigger

Type `/print all` (or just `/print`) in the fulfillment Telegram group.

## What it does

1. Pulls all Notion orders where:
   - `ORDER STATUS` = `Processed`
   - `SOURCING NOTIFIED` ✓
   - `FULFILLMENT NOTIFIED` ✓
   - `Filex Submitted` ✗
2. Validates each order (name, phone, address, city extractable, total).
3. Submits valid orders to Filex's `placebulk` API.
4. Writes back to Notion: `Tracking Number`, `Tracking Link`, sets `FILEX STATUS` = `Label Created`, ticks `Filex Submitted`.
5. Sends a single combined PDF of all generated labels back to the fulfillment group.

## Edge cases

- **Order skipped**: bot replies in Telegram with reason (e.g. missing phone, city not extractable). Fix the field in Notion and re-run.
- **API down**: bot rolls back the Filex Submitted lock on all orders in that batch and posts the error. Retry shortly.
- **Stuck orders** (>24h at Label Created): bot alerts the group nightly at 23:00.

## Webhook flow (passive)

Filex pushes status updates to `http://34.30.125.177:8080/filex_webhook`. The bot maps each status into the `FILEX STATUS` select in Notion and updates `Last Update`. No team action required.

## Status meanings in `FILEX STATUS`

| Status | Meaning |
|---|---|
| Label Created | Filex has the order; awaiting pickup. |
| Handed Off | Picked up by Filex driver, at hub. |
| Shipped | Out with delivery driver. |
| Delivered | Reached the customer (NOT terminal — can revert). |
| Cancelled | Refused at door / customer cancelled. |
| Receiver Cancelled With Money | Cancelled BUT money was collected — reconcile cash. |
| Received in CSA | At Filex's customer service area, awaiting return. |
| Return to Origin | Coming back to us. **Terminal.** |
| LC / SFT / MNA / FD | Sub-reasons (Location Changed, Schedule For Tomorrow, Mobile Not Answered, Future Delivery). |
| LC-OPS / SFT-OPS / MNA-OPS / FD-OPS | Sub-reason + back at OPS warehouse. |

## Notion field reference

| Field | Description |
|---|---|
| `Tracking Number` | Filex AWB (10-digit). |
| `Tracking Link` | Direct URL to Filex tracking page. |
| `FILEX STATUS` | Current state in pipeline. |
| `FILEX NOTES` | Reason text if Filex sent any. |
| `Filex Submitted` | Lock — bot won't re-submit. |
| `Dispatched At` | When `/print all` placed it. |
| `Last Update` | When the most recent webhook event arrived. |

## Reconciliation

Runs nightly at 23:00 via systemd timer. For every active (non-RTO) order dispatched in the last 14 days, the bot polls Filex for current status and updates Notion if it drifted from the last known state. This catches any webhooks missed during service restarts.

## Adding new statuses

If Filex sends a status text we haven't seen, the bot stores it raw in `FILEX STATUS` (Notion auto-creates the option) and logs to `.tmp/filex_unknown_statuses.log`. Add the new mapping to `execution/filex_status_mapper.py` when convenient.

## Failure recovery

| Symptom | Action |
|---|---|
| Stuck orders alerted | Check Filex dashboard, ask fulfillment if package was physically picked up. |
| Webhook returns 401 unexpectedly | `FILEX_WEBHOOK_TOKEN` may have changed — confirm with Filex dev. |
| `/print all` says "no eligible orders" | Verify orders have all 3 conditions met (Processed status, both checkboxes ticked, Filex Submitted false). |
| Notion drift | Run `python execution/filex_reconcile.py` manually; it'll resync. |
