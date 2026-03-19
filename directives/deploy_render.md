# Deploy Order Bridge to Render (Free — No Credit Card)

Runs your Notion ↔ Telegram Order Bridge 24/7 on Render's free tier.
**No VPN needed** — Telegram works directly from Render's servers.

---

## Step 1: Push Code to GitHub

1. Create a **free GitHub account** at [github.com](https://github.com) (if you don't have one)
2. Create a **New Repository** → name it `automation` → **Private** → Create
3. On your PC, open PowerShell in the Automation folder and run:

```powershell
cd C:\Users\PMLS\Desktop\Automation
git init
git add execution/notion_client.py execution/telegram_client.py execution/order_bridge.py requirements.txt render.yaml
git commit -m "Order bridge"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/automation.git
git push -u origin main
```

> ⚠️ Do NOT push `.env` — your API keys should stay private.
> Render has its own environment variable system.

---

## Step 2: Create a Render Account

1. Go to **[render.com](https://render.com)**
2. Click **"Get Started for Free"**
3. Sign up with **GitHub** (easiest — links your repos automatically)

---

## Step 3: Create a Web Service

1. In the Render dashboard, click **"New +"** → **"Web Service"**
2. Connect your **`automation`** GitHub repo
3. Fill in:

| Setting | Value |
|---------|-------|
| **Name** | `order-bridge` |
| **Region** | `Oregon (US West)` or any |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python execution/order_bridge.py` |
| **Instance Type** | **Free** |

4. Click **"Advanced"** → **"Add Environment Variable"** and add these:

| Key | Value |
|-----|-------|
| `NOTION_API_KEY` | `[REDACTED]` |
| `NOTION_DATABASE_ID` | `[REDACTED]` |
| `TELEGRAM_BOT_TOKEN` | `[REDACTED]` |
| `TELEGRAM_SOURCING_GROUP_ID` | `[REDACTED]` |
| `TELEGRAM_FULFILLMENT_GROUP_ID` | `[REDACTED]` |
| `POLL_INTERVAL_SECONDS` | `30` |
| `ORDER_CUTOFF_DATE` | `2026-02-23T17:23:00+05:00` |

5. Click **"Create Web Service"**

Render will build and deploy automatically. Wait 2-3 minutes.

---

## Step 4: Keep It Alive with UptimeRobot (Free)

Render's free tier spins down after 15 minutes of no traffic.
UptimeRobot pings your service every 5 minutes to keep it running.

1. Go to **[uptimerobot.com](https://uptimerobot.com)** → sign up (free)
2. Click **"Add New Monitor"**
3. Fill in:

| Setting | Value |
|---------|-------|
| **Monitor Type** | HTTP(s) |
| **Friendly Name** | `Order Bridge` |
| **URL** | `https://order-bridge.onrender.com` (your Render URL) |
| **Monitoring Interval** | `5 minutes` |

4. Click **"Create Monitor"**

Now your bridge will **never spin down!**

---

## Step 5: Verify

1. Check the Render dashboard → **Logs** tab → you should see:
   ```
   ✓ Telegram bot: @RPGRQbot
   ✓ Connected to Notion database: ALL ORDERS
   🚀 Bridge is running!
   ```
2. Change an order to **CONFIRMED | PROCESSING** in Notion
3. It should appear in your Telegram sourcing group within 30 seconds
4. **Turn off your PC** — the bridge keeps running on Render!

---

## Maintenance

| Task | How |
|------|-----|
| **View logs** | Render dashboard → your service → Logs tab |
| **Restart** | Render dashboard → Manual Deploy → "Deploy latest commit" |
| **Update code** | ALWAYS natively run `git add .` and `git commit -m "update"` and `git push -u origin main` whenever scripts are modified. Render will auto-deploy. |
| **Clear tracking** | Render dashboard → Shell tab → `echo '{}' > .tmp/notified_orders.json` |
| **Stop** | Render dashboard → Settings → "Suspend Service" |

---

## Cost: $0

Free tier includes **750 hours/month** — enough for 24/7 (720 hrs/month).
UptimeRobot free tier is also $0.
