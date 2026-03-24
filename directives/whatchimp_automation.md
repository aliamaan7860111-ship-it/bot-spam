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

## Manual Button Routing (Advanced)

To ensure template buttons reliably trigger correct flows without looping, we use manual button action mapping in `whatchimp_client.py`.

### **How to find Internal Action IDs:**
If you create a new flow and want to link a template button to it:
1.  Open **WhatChimp Flow Builder**.
2.  Open **Browser DevTools** (F12) and go to the **Network** tab.
3.  Click **Save** in the Flow Builder.
4.  Look for the `submit` or `update` request in the Network list.
5.  In the **Payload / Request Body**, look for `botTemplateQuickreplyButtonValues`.
6.  The IDs in that array (e.g., `Nop_RZOKKksN72n`) are the internal IDs needed for the Python script.

### **Updating the Python Code:**
Once you have the new ID, update the `button_values` list in `whatchimp_client.py`:
```python
# Order matches the button order in your WhatChimp Template
button_values = '["YES_START_CHAT_WITH_BOT","YOUR_NEW_ID_HERE"]'
```
