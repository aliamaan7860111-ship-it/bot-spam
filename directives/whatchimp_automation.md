# Directive: WhatChimp WhatsApp Automation

## Goal
Automate WhatsApp message sending to customers through the WhatChimp API. The primary use case is sending order confirmation messages as soon as an order is processed from Shopify into Notion.

## Triggers
- **Order Confirmation**: When a new order specifically for the store **Pretty by Shahid** (Order ID starts with `PT`) comes into Notion with status `CONFIRMED | PROCESSING`, a WhatsApp message is sent to the customer.

## Execution Scripts

| Script | Purpose |
|--------|---------|
| `execution/whatchimp_client.py` | WhatChimp API wrapper for sending messages |
| `execution/order_bridge.py` | Main script that monitors Notion and calls `whatchimp_client` |

### Environment Variables (.env)
```env
WHATCHIMP_API_TOKEN=18978|vAaZPtFdNJLVIPBQu3hBnw0dHchzeI42wcIIfq8N28095327
WHATCHIMP_PHONE_NUMBER_ID=your_phone_number_id_here
```

## API Details
- **Endpoint Used**: `https://app.whatchimp.com/api/v1/whatsapp/send`
- **Method**: POST
- **Payload Requirements**:
  - `apiToken`
  - `phone_number_id`
  - `message` (Text content)
  - `phone_number` (Must start with country code, digits only, no + sign)

## Edge Cases & Formatting
- **Phone Number Parsing**: Phone numbers from Shopify/Notion might contain spaces, dashes, or `+` signs. They must be stripped down to purely numeric characters starting with the country code before hitting the API.
- **Duplicate Prevention**: In Notion, a new checkbox (e.g., `WHATSAPP_NOTIFIED`) or an internal tracking mechanism must be used to ensure the customer only receives one confirmation message, even if the bridge script restarts.
