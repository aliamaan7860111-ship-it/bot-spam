# grq-ac — Abandoned Checkout Recovery bridge

FastAPI service that receives Shopify abandoned-checkout webhooks for 6 brands,
writes them to Notion, schedules a 30-min delayed WhatChimp template send, and
suppresses pending sends when the customer completes the order naturally.

## Brands

`amara`, `pelvini`, `elara`, `lune`, `virex`, `dialo` (Pelvini shares Virex's WABA)

## Required env vars (`.env`)

Shared:
```
NOTION_API_KEY=...
RECOVERY_NOTION_DATABASE_ID=365c320e-ba59-8144-b061-cd4250d222a5
RECOVERY_DELAY_MINUTES=30
WHATCHIMP_API_TOKEN=...
BRIDGE_BASE_URL=https://grqholdings.duckdns.org
```

Per brand (replace `<BRAND>` with `AMARA`, `PELVINI`, etc.):
```
SHOPIFY_DOMAIN_<BRAND>=<handle>.myshopify.com
SHOPIFY_TOKEN_<BRAND>=shpca_...
SHOPIFY_API_SECRET_<BRAND>=...           # optional, enables HMAC verification
WHATCHIMP_PHONE_NUMBER_ID_<BRAND>=...    # for sending
WHATCHIMP_TEMPLATE_ID_<BRAND>=...        # set once Meta approves the template
```

Brands missing `WHATCHIMP_TEMPLATE_ID_<BRAND>` will still record to Notion but
the actual send is stubbed (logged + row marked "Recovery Pending").

## Deploy to GCP VM

```bash
# 1. Clone (or pull) bot-spam onto the VM
cd /home/bilal/automation
git pull

# 2. Set up venv & install deps
cd bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Install systemd unit
sudo cp grq-ac.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable grq-ac
sudo systemctl start grq-ac
sudo systemctl status grq-ac

# 4. Verify
curl http://127.0.0.1:8084/health
curl -I https://grqholdings.duckdns.org/health  # via Caddy
```

## Register Shopify webhooks

After the service is live and reachable via HTTPS:

```bash
# From your local machine (uses .env tokens):
cd execution/bridge
python register_shopify_webhooks.py
```

This creates 3 webhooks per brand (18 total). Idempotent — re-running is safe.

## Endpoints

```
GET  /health
GET  /

POST /webhooks/<brand>/checkout-created    # Shopify → bridge
POST /webhooks/<brand>/checkout-updated    # Shopify → bridge
POST /webhooks/<brand>/order-created       # Shopify → bridge (suppression)
```

## Status lifecycle

```
checkout/create → status=New, scheduled send +30 min
   ↓
30 min later → send WhatChimp template → status=Recovery Sent
   ↓
   ├── customer responds via button click → (later — flow webhooks not yet wired)
   └── customer self-completes → orders/create fires → status=Order Placed
                                                        (scheduled send cancelled)
```

## On restart

`_backfill_pending` runs at startup: scans Notion for rows with Status=New,
reschedules any whose delay hasn't fully elapsed, fires immediately any that
are overdue. No scheduled work is lost across restarts.
