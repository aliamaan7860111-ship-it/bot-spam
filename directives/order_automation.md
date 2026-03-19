# Directive: Order Automation — Notion → Telegram Bridge

## Goal
Automate the order sourcing and fulfillment workflow across 5 Shopify stores by bridging Notion CRM with Telegram groups.

## Stores Connected
- Amara's Room
- Pretty by Shahid
- Dera Digital
- VDX
- Dilo

## Workflow

### Trigger: Order status changes to "Confirmed" in Notion
1. Script detects the change via polling (every 30s)
2. Sends order details + product images to **Telegram Sourcing Group**
3. Checks `SOURCING NOTIFIED` in Notion to prevent duplicates

### Trigger: Sourcing replies with `#ready ORDERID` in Telegram
1. Bot receives the command
2. Updates Notion status to **"Processed"**
3. Forwards order details + images to **Telegram Fulfillment Group**
4. Checks `FULFILLMENT NOTIFIED` in Notion

### Trigger: Sourcing replies with `#cost ORDERID amount`
1. Bot stores the cost in the **COST** field in Notion

### Trigger: Sourcing replies with `#note ORDERID text`
1. Bot appends the note to **INTERNAL NOTE** field in Notion

## Execution Scripts

| Script | Purpose |
|--------|---------|
| `execution/order_bridge.py` | Main bridge — run this to start |
| `execution/notion_client.py` | Notion API wrapper |
| `execution/telegram_client.py` | Telegram bot wrapper |

### Usage
```bash
# Start the bridge (runs continuously)
python execution/order_bridge.py

# Test connections first
python execution/order_bridge.py --test

# Poll once and exit (useful for debugging)
python execution/order_bridge.py --poll-once
```

## Notion Database Fields Used

| Field | Type | Used For |
|-------|------|----------|
| ORDER ID | Title | Primary identifier, used for #ready/#cost matching |
| CUSTOMER NAME | Text | Displayed in Telegram messages |
| ITEM │ QTY | Text | Displayed in Telegram messages |
| TOTAL (AED) | Number | Displayed in Telegram messages |
| COST | Number | Updated by #cost command |
| INTERNAL NOTE | Text | Updated by #note command |
| ORDER STATUS | Select | Changed from Confirmed → Processed |
| IMAGE URL | Text/URL | Downloaded and sent as Telegram images |
| PLATFORM SOURCE | Text/Select | Store name shown in messages |
| SOURCING NOTIFIED | Checkbox | **NEW** — prevents duplicate sends |
| FULFILLMENT NOTIFIED | Checkbox | **NEW** — prevents duplicate forwards |

## Environment Variables (.env)
```
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_SOURCING_GROUP_ID=-100xxx
TELEGRAM_FULFILLMENT_GROUP_ID=-100xxx
```

## Edge Cases
- **Multiple image URLs**: Parsed from single text field (comma/newline separated), sent as Telegram album
- **Order not found**: Bot replies with ❌ error in Telegram
- **Network failure**: Logged to `.tmp/order_bridge.log`, retried next polling cycle
- **Duplicate prevention**: `SOURCING NOTIFIED` checkbox prevents re-sending
- **Script restart**: Safe to restart at any time — picks up where it left off

## Logs
All actions logged to `.tmp/order_bridge.log`
