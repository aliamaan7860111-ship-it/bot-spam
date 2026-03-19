# Deploy Order Bridge to Google Cloud (Free Forever)

This guide deploys your Notion ↔ Telegram Order Bridge to a Google Cloud VM
so it runs 24/7 without your PC. **No VPN needed** on the server.

---

## Step 1: Create a Google Cloud Account

1. Go to **[cloud.google.com/free](https://cloud.google.com/free)**
2. Click **"Get started for free"**
3. Sign in with your Google account
4. Enter your credit card (you will **NOT be charged** — it's for verification only)
5. You'll get **$300 free credits** + the **Always Free tier**

---

## Step 2: Create a Free VM

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)**
2. In the top search bar, type **"Compute Engine"** → click it
3. Click **"Enable"** if prompted (wait ~2 minutes)
4. Click **"CREATE INSTANCE"**
5. Fill in:

| Setting | Value |
|---------|-------|
| **Name** | `order-bridge` |
| **Region** | `us-west1` (Oregon) or `us-central1` (Iowa) — **these are Always Free eligible** |
| **Zone** | Any (e.g., `us-west1-b`) |
| **Machine type** | `e2-micro` **(Always Free!)** |
| **Boot disk** | Click "Change" → **Ubuntu 22.04 LTS** → Size: **30 GB** → Click "Select" |
| **Firewall** | Leave unchecked (we don't need web traffic) |

6. Click **"Create"** — wait 1-2 minutes

> **IMPORTANT:** Only `e2-micro` in `us-west1`, `us-central1`, or `us-east1` is Always Free.
> Other regions/sizes will cost money.

---

## Step 3: Connect to Your VM

1. In the VM instances list, find your `order-bridge` VM
2. Click the **"SSH"** button (opens a browser terminal)
3. You're now inside your Linux server!

---

## Step 4: Install Python and Dependencies

Run these commands one by one in the SSH terminal:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11 and pip
sudo apt install -y python3.11 python3.11-venv python3-pip

# Create a project folder
mkdir -p ~/automation/execution ~/automation/.tmp

# Create a virtual environment
cd ~/automation
python3.11 -m venv venv
source venv/bin/activate

# Install required packages
pip install python-dotenv python-telegram-bot httpx
```

---

## Step 5: Upload Your Scripts

### Option A: Copy-paste (simplest)

For each file, run `nano` to create it, paste the content, then save:

```bash
# 1. Create .env
nano ~/automation/.env
# Paste your .env content, then press Ctrl+X → Y → Enter

# 2. Create notion_client.py
nano ~/automation/execution/notion_client.py
# Paste content, Ctrl+X → Y → Enter

# 3. Create telegram_client.py
nano ~/automation/execution/telegram_client.py
# Paste content, Ctrl+X → Y → Enter

# 4. Create order_bridge.py
nano ~/automation/execution/order_bridge.py
# Paste content, Ctrl+X → Y → Enter
```

### Option B: Upload via SCP (from your PC's PowerShell)

```powershell
# First, download the SSH key from GCP Console:
# Go to Compute Engine → Settings → Metadata → SSH Keys
# Or use gcloud CLI:

gcloud compute scp --recurse C:\Users\PMLS\Desktop\Automation\* order-bridge:~/automation/
```

### Option C: Upload via GCP Console

1. In the SSH browser window, click the **gear icon** (top right) → **"Upload file"**
2. Upload each file one by one
3. Then move them to the right location:
```bash
mv ~/notion_client.py ~/automation/execution/
mv ~/telegram_client.py ~/automation/execution/
mv ~/order_bridge.py ~/automation/execution/
mv ~/*.env ~/automation/
```

---

## Step 6: Test the Connection

```bash
cd ~/automation
source venv/bin/activate
python execution/order_bridge.py --test
```

You should see:
```
✓ Connected to Notion database: ALL ORDERS
✓ Connected to Telegram bot: @RPGRQbot
✅ All connections OK — ready to run!
```

**No VPN needed!** Telegram connects directly from GCP.

---

## Step 7: Run It Forever (Background Service)

### Option A: Quick background run

```bash
cd ~/automation
source venv/bin/activate
nohup python execution/order_bridge.py >> .tmp/bridge.log 2>&1 &
echo $! > .tmp/bridge.pid
```

This runs it in the background. You can close the SSH window and it keeps running.

**Check logs:**
```bash
tail -f ~/automation/.tmp/bridge.log
```

**Stop it:**
```bash
kill $(cat ~/automation/.tmp/bridge.pid)
```

### Option B: systemd service (auto-restarts on crash or reboot) ⭐ Recommended

```bash
# Create a service file
sudo nano /etc/systemd/system/order-bridge.service
```

Paste this content:

```ini
[Unit]
Description=Notion-Telegram Order Bridge
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/automation
ExecStart=/home/YOUR_USERNAME/automation/venv/bin/python execution/order_bridge.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Replace `YOUR_USERNAME`** with your actual username (run `whoami` to check).

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable order-bridge
sudo systemctl start order-bridge
```

**Check status:**
```bash
sudo systemctl status order-bridge
```

**View logs:**
```bash
sudo journalctl -u order-bridge -f
```

**This auto-starts on reboot and auto-restarts if it crashes!**

---

## Step 8: Verify It's Working

1. Go to Notion → change an order's status to **CONFIRMED | PROCESSING**
2. Check your Telegram sourcing group — the order should appear within 30 seconds
3. Close the SSH window — it keeps running!

---

## Maintenance

| Task | Command |
|------|---------|
| Check if running | `sudo systemctl status order-bridge` |
| View live logs | `sudo journalctl -u order-bridge -f` |
| Restart | `sudo systemctl restart order-bridge` |
| Stop | `sudo systemctl stop order-bridge` |
| Update scripts | Edit files, then `sudo systemctl restart order-bridge` |
| Clear tracking | `echo '{"sourcing_notified":[],"fulfillment_notified":[]}' > ~/automation/.tmp/notified_orders.json` |

---

## Cost: $0

The `e2-micro` VM in `us-west1`/`us-central1`/`us-east1` is part of Google Cloud's
**Always Free** tier. As long as you stay within these limits, you will never be charged.
